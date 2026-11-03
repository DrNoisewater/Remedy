"""Reversible system tweaks. Every change records the original value so Revert restores it exactly."""
from __future__ import annotations

import json
import os
import re

from . import registry, services
from .registry import REG_DWORD, REG_SZ
from .utils import BACKUP_FILE, IS_WINDOWS, log, run_command

ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"


# --------------------------------------------------------------------------- backup store
def _load_backup() -> dict:
    try:
        return json.loads(BACKUP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_backup(data: dict) -> None:
    tmp = BACKUP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, BACKUP_FILE)


def _encode(value):
    return {"hex": value.hex()} if isinstance(value, (bytes, bytearray)) else value


def _decode(value):
    return bytes.fromhex(value["hex"]) if isinstance(value, dict) and "hex" in value else value


# --------------------------------------------------------------------------- tweak types
class Tweak:
    """target: "" (any Windows), "win11" or "win10". laptop_note: why to think twice on a laptop
    (such tweaks are never pre-selected on laptops)."""

    def __init__(self, id, name, category, description, risk="Safe", admin=False, restart="",
                 recommended=False, target="", laptop_note=""):
        self.id, self.name, self.category, self.description = id, name, category, description
        self.risk, self.admin, self.restart, self.recommended = risk, admin, restart, recommended
        self.target, self.laptop_note = target, laptop_note

    def availability(self, info=None) -> tuple:
        """(usable_here, reason) for the detected Windows version."""
        from .osinfo import applies
        return applies(self.target, info)

    def suggested(self, info=None) -> bool:
        """Recommended for THIS PC: recommended, meant for this Windows, and laptop-safe on laptops."""
        from .osinfo import detect
        info = info or detect()
        return self.recommended and self.availability(info)[0] and not (info.laptop and self.laptop_note)

    def status(self):
        return None

    def apply(self) -> str:
        raise NotImplementedError

    def revert(self) -> str:
        raise NotImplementedError


class RegTweak(Tweak):
    """values: list of (hive, key, name, type, on_value, default_value_or_None_to_delete)"""

    def __init__(self, *args, values, **kw):
        super().__init__(*args, **kw)
        self.values = values

    def status(self):
        if not IS_WINDOWS:
            return None
        for hive, key, name, _typ, on, _default in self.values:
            cur = registry.get_value(hive, key, name)
            if cur is None or str(cur[0]) != str(on):
                return False
        return True

    def apply(self) -> str:
        backup = _load_backup()
        if self.id not in backup:
            entries = []
            for hive, key, name, _typ, _on, _default in self.values:
                cur = registry.get_value(hive, key, name)
                entries.append([hive, key, name, None if cur is None else cur[1],
                                None if cur is None else _encode(cur[0])])
            backup[self.id] = {"reg": entries}
            _save_backup(backup)
        for hive, key, name, typ, on, _default in self.values:
            registry.set_value(hive, key, name, typ, on)
        return "Applied"

    def revert(self) -> str:
        backup = _load_backup()
        saved = backup.pop(self.id, None)
        if saved and "reg" in saved:
            for hive, key, name, typ, value in saved["reg"]:
                if typ is None:
                    registry.delete_value(hive, key, name)
                else:
                    registry.set_value(hive, key, name, typ, _decode(value))
        else:
            for hive, key, name, typ, _on, default in self.values:
                if default is None:
                    registry.delete_value(hive, key, name)
                else:
                    registry.set_value(hive, key, name, typ, default)
        _save_backup(backup)
        return "Reverted"


class ClassicMenuTweak(RegTweak):
    CLSID = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"

    def __init__(self, *args, **kw):
        super().__init__(*args, values=[("HKCU", self.CLSID + r"\InprocServer32", "", REG_SZ, "", None)], **kw)

    def status(self):
        if not IS_WINDOWS:
            return None
        return registry.key_exists("HKCU", self.CLSID + r"\InprocServer32")

    def revert(self) -> str:
        registry.delete_key_tree("HKCU", self.CLSID)
        backup = _load_backup()
        backup.pop(self.id, None)
        _save_backup(backup)
        return "Reverted"


class ServiceTweak(Tweak):
    def __init__(self, *args, service_names, default_start="automatic", **kw):
        super().__init__(*args, **kw)
        self.service_names = service_names
        self.default_start = default_start

    def _present(self):
        return [s for s in self.service_names if services.get_start_type(s) is not None]

    def status(self):
        present = self._present()
        if not present:
            return None
        return all(services.get_start_type(s) == "disabled" for s in present)

    def apply(self) -> str:
        backup = _load_backup()
        present = self._present()
        if self.id not in backup:
            backup[self.id] = {"svc": {s: services.get_start_type(s) for s in present}}
            _save_backup(backup)
        for s in present:
            try:
                services.control(s, "stop")
            except RuntimeError:
                pass
            services.set_start_type(s, "disabled")
        return "Applied"

    def revert(self) -> str:
        backup = _load_backup()
        saved = (backup.pop(self.id, None) or {}).get("svc", {})
        for s in self._present():
            services.set_start_type(s, saved.get(s) or self.default_start)
            if (saved.get(s) or self.default_start) == "automatic":
                try:
                    services.control(s, "start")
                except RuntimeError:
                    pass
        _save_backup(backup)
        return "Reverted"


class CompositeTweak(Tweak):
    def __init__(self, *args, parts, **kw):
        super().__init__(*args, **kw)
        self.parts = parts

    def status(self):
        states = [p.status() for p in self.parts]
        states = [s for s in states if s is not None]
        return all(states) if states else None

    def apply(self) -> str:
        for p in self.parts:
            p.apply()
        return "Applied"

    def revert(self) -> str:
        for p in self.parts:
            p.revert()
        return "Reverted"


class HibernationTweak(Tweak):
    def status(self):
        cur = registry.get_value("HKLM", r"SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled")
        return None if cur is None else cur[0] == 0

    def apply(self) -> str:
        res = run_command(["powercfg", "/hibernate", "off"])
        if not res.ok:
            raise RuntimeError(res.output)
        return "Hibernation off, hiberfil.sys removed"

    def revert(self) -> str:
        res = run_command(["powercfg", "/hibernate", "on"])
        if not res.ok:
            raise RuntimeError(res.output)
        return "Hibernation on"


class PowerPlanTweak(Tweak):
    ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
    HIGH = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
    BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
    GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

    def _active(self):
        res = run_command(["powercfg", "/getactivescheme"])
        m = self.GUID.search(res.output)
        return (m.group(0).lower() if m else ""), res.output

    def status(self):
        if not IS_WINDOWS:
            return None
        guid, text = self._active()
        return guid == self.HIGH or "ultimate" in text.lower()

    def apply(self) -> str:
        previous, _ = self._active()
        backup = _load_backup()
        backup.setdefault(self.id, {"plan": previous})
        _save_backup(backup)
        # reuse an existing Ultimate plan if one was already unhidden
        listing = run_command(["powercfg", "/list"]).output
        target = ""
        for line in listing.splitlines():
            if "ultimate" in line.lower():
                m = self.GUID.search(line)
                target = m.group(0) if m else ""
        if not target:
            dup = run_command(["powercfg", "-duplicatescheme", self.ULTIMATE])
            m = self.GUID.search(dup.output) if dup.ok else None
            target = m.group(0) if m else self.HIGH
        res = run_command(["powercfg", "/setactive", target])
        if not res.ok:
            raise RuntimeError(res.output or "powercfg failed")
        return "Ultimate Performance active" if target != self.HIGH else "High Performance active"

    def revert(self) -> str:
        backup = _load_backup()
        plan = (backup.pop(self.id, None) or {}).get("plan") or self.BALANCED
        res = run_command(["powercfg", "/setactive", plan])
        if not res.ok:
            run_command(["powercfg", "/setactive", self.BALANCED])
        _save_backup(backup)
        return "Previous power plan restored"


# --------------------------------------------------------------------------- catalog
W11, PERF, GAME, PRIV, UI, SVC = ("Windows 11", "Performance", "Gaming", "Privacy & AI", "Taskbar & Explorer",
                                  "Background services")
CATEGORY_ORDER = [W11, PERF, GAME, PRIV, UI, SVC]
SEARCH = r"Software\Microsoft\Windows\CurrentVersion\Search"
WINAI = r"Software\Policies\Microsoft\Windows\WindowsAI"


def build_tweaks() -> list:
    D, S = REG_DWORD, REG_SZ
    T = []
    # ---- Windows 11
    T.append(ClassicMenuTweak("classic_menu", "Classic right-click menu", W11,
                              "Brings back the full right-click menu, so you don't have to click "
                              "'Show more options' every time.", restart="Restart Explorer", recommended=True,
                              target="win11"))
    T.append(RegTweak("end_task", "'End task' on taskbar right-click", W11,
                      "Adds End task to taskbar app menus, so a frozen app can be closed in one click.",
                      recommended=True, target="win11",
                      values=[("HKCU", ADV + r"\TaskbarDeveloperSettings", "TaskbarEndTask", D, 1, None)]))
    T.append(RegTweak("w11_start_recs", "Remove Start menu recommendations", W11,
                      "Stops Start suggesting tips, shortcuts and new apps in the Recommended area.",
                      recommended=True, target="win11", values=[("HKCU", ADV, "Start_IrisRecommendations", D, 0, None)]))
    T.append(RegTweak("w11_start_account", "Stop Microsoft account nags in Start", W11,
                      "Hides 'Back up your PC' / 'Sign in' style prompts on the Start menu's account button.",
                      recommended=True, target="win11", values=[("HKCU", ADV, "Start_AccountNotifications", D, 0, None)]))
    T.append(RegTweak("w11_hide_widgets", "Hide Widgets (news feed)", W11,
                      "Removes the Widgets button and news board from the taskbar. Uses a system policy, "
                      "so Settings may show 'managed by your organization' for that switch.",
                      admin=True, restart="Restart Explorer", recommended=True, target="win11", values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", D, 0, None)]))
    T.append(RegTweak("w11_hide_chat", "Hide the Chat (Teams) taskbar button", W11,
                      "Removes the Chat/Teams icon some Windows 11 versions put on the taskbar.",
                      restart="Restart Explorer", target="win11", values=[("HKCU", ADV, "TaskbarMn", D, 0, None)]))
    T.append(RegTweak("w11_taskbar_left", "Taskbar icons on the left", W11,
                      "Moves Start and your taskbar icons to the left edge, like Windows 10.",
                      target="win11", values=[("HKCU", ADV, "TaskbarAl", D, 0, None)]))
    T.append(RegTweak("w11_never_combine", "Never combine taskbar buttons", W11,
                      "Shows each window as its own labelled taskbar button (Windows 11 23H2 and newer).",
                      restart="Restart Explorer", target="win11", values=[("HKCU", ADV, "TaskbarGlomLevel", D, 2, None)]))
    T.append(RegTweak("w11_more_pins", "More pins in Start", W11,
                      "Uses the 'More pins' Start layout: more room for your apps, less for recommendations.",
                      target="win11", values=[("HKCU", ADV, "Start_Layout", D, 1, None)]))
    T.append(RegTweak("w11_snap_flyout", "No snap layouts pop-up on Maximize", W11,
                      "Stops the layout picker appearing when you hover over a window's Maximize button. "
                      "Win+Z still opens it.", target="win11", values=[("HKCU", ADV, "EnableSnapAssistFlyout", D, 0, None)]))
    T.append(RegTweak("w11_spotlight_icon", "Remove 'Learn about this picture' icon", W11,
                      "Removes the desktop icon Windows Spotlight adds when it manages your wallpaper.",
                      restart="Restart Explorer", target="win11", values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel",
                           "{2cc5ca98-6485-489a-920e-b3e88a6ccce3}", D, 1, None)]))
    T.append(RegTweak("w11_clock_seconds", "Show seconds in the taskbar clock", W11,
                      "Adds seconds to the clock. Uses a tiny bit more power.", target="win11",
                      values=[("HKCU", ADV, "ShowSecondsInSystemClock", D, 1, None)]))
    # ---- Performance
    T.append(PowerPlanTweak("power_plan", "Ultimate Performance power plan", PERF,
                            "Unhides and switches to Windows' Ultimate Performance plan (falls back to High "
                            "Performance). The CPU stays ready for bursts of work.",
                            risk="Moderate", admin=True, laptop_note="Drains the battery noticeably faster."))
    T.append(RegTweak("animations", "Reduce animations", PERF,
                      "Turns off window minimize/maximize and taskbar animations so the desktop feels snappier.",
                      restart="Sign out", recommended=True, values=[
                          ("HKCU", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", S, "0", "1"),
                          ("HKCU", ADV, "TaskbarAnimations", D, 0, 1),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                           "VisualFXSetting", D, 3, None)]))
    T.append(RegTweak("menu_delay", "Instant menus", PERF,
                      "Removes the 400 ms delay before sub-menus open.", restart="Sign out", recommended=True,
                      values=[("HKCU", r"Control Panel\Desktop", "MenuShowDelay", S, "0", "400")]))
    T.append(RegTweak("startup_delay", "Remove startup app delay", PERF,
                      "Windows waits ~10 seconds after sign-in before launching startup apps. This removes the wait.",
                      recommended=True, values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize",
                           "StartupDelayInMSec", D, 0, None)]))
    T.append(RegTweak("transparency", "Disable transparency effects", PERF,
                      "Turns off acrylic/blur effects. Small GPU saving on older or integrated graphics.",
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                               "EnableTransparency", D, 0, 1)]))
    T.append(HibernationTweak("hibernation", "Turn off hibernation", PERF,
                              "Deletes hiberfil.sys (often 40% of your RAM size) and disables Hibernate and "
                              "Fast Startup. Frees several GB on the system drive.",
                              risk="Moderate", admin=True,
                              laptop_note="Laptops use Hibernate to save your work when the battery runs low."))
    T.append(RegTweak("fast_startup", "Turn off Fast Startup", PERF,
                      "Makes Shut down a real shut down. Fixes updates that never finish, drivers acting up "
                      "and dual-boot problems. Cold boots take a few seconds longer.",
                      admin=True, restart="Restart PC", values=[
                          ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled",
                           D, 0, 1)]))
    T.append(RegTweak("edge_background", "Stop Edge running in the background", PERF,
                      "Stops Microsoft Edge preloading at sign-in and staying open after you close it. "
                      "Frees memory if you use another browser. Uses an Edge policy.", admin=True, values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Edge", "StartupBoostEnabled", D, 0, None),
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Edge", "BackgroundModeEnabled", D, 0, None)]))
    T.append(RegTweak("delivery_p2p", "Stop uploading updates to other PCs", PERF,
                      "Windows Update downloads straight from Microsoft and stops using your upload bandwidth "
                      "to share update files with other computers.", admin=True, recommended=True, values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization", "DODownloadMode",
                           D, 0, None)]))
    T.append(RegTweak("storage_sense", "Turn on Storage Sense", PERF,
                      "Lets Windows clear temporary files automatically when space runs low. Its default "
                      "settings also empty Recycle Bin items older than 30 days.", values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy",
                           "01", D, 1, None)]))
    T.append(RegTweak("background_apps", "Block background apps", PERF,
                      "Stops Store apps from running in the background. (Windows 11 manages this per app.)",
                      risk="Moderate", target="win10", values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
                           "GlobalUserDisabled", D, 1, None)]))
    # ---- Gaming
    T.append(RegTweak("game_mode", "Enable Game Mode", GAME,
                      "Prioritizes the game you're playing and pauses Windows Update installs during play.",
                      recommended=True, values=[("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", D, 1, None)]))
    T.append(RegTweak("game_dvr", "Disable background game recording", GAME,
                      "Turns off Xbox Game DVR background capture, which costs FPS. Game Bar screenshots still work.",
                      recommended=True, values=[
                          ("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", D, 0, 1),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", D, 0, None)]))
    T.append(RegTweak("hags", "Hardware-accelerated GPU scheduling", GAME,
                      "Lets the GPU manage its own memory scheduling. Can lower latency on recent NVIDIA/AMD GPUs.",
                      risk="Moderate", admin=True, restart="Restart PC", values=[
                          ("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", D, 2, None)]))
    T.append(RegTweak("mouse_accel", "Disable mouse acceleration", GAME,
                      "Turns off 'Enhance pointer precision' for 1:1 raw-feeling aim.", restart="Sign out", values=[
                          ("HKCU", r"Control Panel\Mouse", "MouseSpeed", S, "0", "1"),
                          ("HKCU", r"Control Panel\Mouse", "MouseThreshold1", S, "0", "6"),
                          ("HKCU", r"Control Panel\Mouse", "MouseThreshold2", S, "0", "10")]))
    T.append(RegTweak("sticky_keys", "Disable Sticky Keys shortcut", GAME,
                      "Stops the Sticky Keys pop-up when you press Shift five times mid-game.", recommended=True,
                      values=[("HKCU", r"Control Panel\Accessibility\StickyKeys", "Flags", S, "506", "510")]))
    T.append(RegTweak("net_throttle", "Disable network throttling", GAME,
                      "Removes Windows' multimedia network throttle and reserves less CPU for background tasks.",
                      risk="Moderate", admin=True, restart="Restart PC", values=[
                          ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                           "NetworkThrottlingIndex", D, 0xFFFFFFFF, 10),
                          ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                           "SystemResponsiveness", D, 10, 20)]))
    T.append(ServiceTweak("xbox_services", "Disable Xbox online services", GAME,
                          "Turns off Xbox sign-in and cloud-save services. Only for PCs that never use the Xbox "
                          "app, Game Pass or Xbox Live games.", risk="Advanced", admin=True, default_start="manual",
                          service_names=["XblAuthManager", "XblGameSave", "XboxNetApiSvc"]))
    # ---- Privacy & AI
    T.append(RegTweak("copilot", "Turn off Copilot", PRIV,
                      "Hides the Copilot button and disables the Windows Copilot sidebar.", restart="Restart Explorer",
                      values=[("HKCU", r"Software\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot", D, 1, None),
                              ("HKCU", ADV, "ShowCopilotButton", D, 0, None)]))
    T.append(RegTweak("w11_recall", "Turn off Recall snapshots", PRIV,
                      "Stops Recall saving screenshots of everything you do (Copilot+ PCs). Harmless elsewhere.",
                      recommended=True, target="win11", values=[("HKCU", WINAI, "DisableAIDataAnalysis", D, 1, None)]))
    T.append(RegTweak("w11_click_to_do", "Turn off Click to Do", PRIV,
                      "Disables the AI 'Click to Do' screen-analysis feature (Copilot+ PCs).",
                      target="win11", values=[("HKCU", WINAI, "DisableClickToDo", D, 1, None)]))
    T.append(RegTweak("ad_id", "Disable advertising ID", PRIV,
                      "Stops apps using your advertising ID for personalized ads.", recommended=True,
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", D, 0, 1)]))
    T.append(RegTweak("tailored", "Disable tailored experiences", PRIV,
                      "Stops Microsoft using diagnostic data for tips, ads and recommendations.", recommended=True,
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Privacy",
                               "TailoredExperiencesWithDiagnosticDataEnabled", D, 0, 1)]))
    T.append(RegTweak("suggestions", "Disable tips, suggestions & auto-installed apps", PRIV,
                      "Removes Start menu suggestions, lock-screen tips, Settings ads and silently installed promo apps.",
                      recommended=True, values=[
                          ("HKCU", CDM, name, D, 0, 1) for name in (
                              "SubscribedContent-338387Enabled", "SubscribedContent-338388Enabled",
                              "SubscribedContent-338389Enabled", "SubscribedContent-353694Enabled",
                              "SubscribedContent-353696Enabled", "SystemPaneSuggestionsEnabled",
                              "SoftLandingEnabled", "SilentInstalledAppsEnabled")]))
    T.append(RegTweak("web_search", "Disable Bing in Start search", PRIV,
                      "Start menu searches stay on your PC instead of going to Bing. Faster, too.",
                      recommended=True, restart="Restart Explorer", values=[
                          ("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", D, 1, None),
                          ("HKCU", SEARCH, "BingSearchEnabled", D, 0, None)]))
    T.append(RegTweak("feedback", "Stop feedback requests", PRIV,
                      "Windows stops popping up 'How likely are you to recommend…' surveys.", recommended=True,
                      values=[("HKCU", r"Software\Microsoft\Siuf\Rules", "NumberOfSIUFInPeriod", D, 0, None)]))
    T.append(RegTweak("lang_list", "Hide your language list from websites", PRIV,
                      "Websites can no longer read your installed languages to fingerprint you.", recommended=True,
                      values=[("HKCU", r"Control Panel\International\User Profile", "HttpAcceptLanguageOptOut", D, 1, None)]))
    T.append(RegTweak("track_progs", "Don't track app launches", PRIV,
                      "Windows stops recording which apps you open. The 'Most used' list in Start goes away.",
                      values=[("HKCU", ADV, "Start_TrackProgs", D, 0, None)]))
    T.append(RegTweak("activity", "Disable activity history", PRIV,
                      "Stops Windows recording and uploading your app/file activity timeline.", admin=True,
                      recommended=True, values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "PublishUserActivities", D, 0, None),
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "UploadUserActivities", D, 0, None)]))
    T.append(RegTweak("error_reporting", "Turn off error reporting", PRIV,
                      "Crash details are no longer sent to Microsoft. Crashes are still logged on this PC.",
                      admin=True, values=[("HKLM", r"SOFTWARE\Microsoft\Windows\Windows Error Reporting", "Disabled",
                                           D, 1, None)]))
    T.append(CompositeTweak("telemetry", "Minimize telemetry", PRIV,
                            "Sets diagnostic data to the minimum allowed and disables the Connected User "
                            "Experiences service. (Home and Pro treat 'off' as 'Required'.)",
                            risk="Moderate", admin=True, parts=[
                                RegTweak("telemetry_reg", "", "", "", values=[
                                    ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", D, 0, None)]),
                                ServiceTweak("telemetry_svc", "", "", "", service_names=["DiagTrack", "dmwappushservice"])]))
    # ---- Taskbar & Explorer
    T.append(RegTweak("file_ext", "Show file extensions", UI,
                      "Shows .exe, .pdf etc. This makes disguised malware like 'invoice.pdf.exe' obvious.",
                      recommended=True, restart="Restart Explorer", values=[("HKCU", ADV, "HideFileExt", D, 0, 1)]))
    T.append(RegTweak("hidden_files", "Show hidden files", UI,
                      "Shows hidden files and folders like AppData.", restart="Restart Explorer",
                      values=[("HKCU", ADV, "Hidden", D, 1, 2)]))
    T.append(RegTweak("this_pc", "Open Explorer to 'This PC'", UI,
                      "File Explorer opens on your drives instead of Home/Quick access.",
                      values=[("HKCU", ADV, "LaunchTo", D, 1, None)]))
    T.append(RegTweak("search_icon", "Search as an icon on the taskbar", UI,
                      "Shrinks the big taskbar search box to a magnifying-glass icon, freeing taskbar space.",
                      restart="Restart Explorer", values=[("HKCU", SEARCH, "SearchboxTaskbarMode", D, 1, None)]))
    T.append(RegTweak("task_view", "Hide the Task View button", UI,
                      "Removes the Task View button from the taskbar. Win+Tab still works.",
                      values=[("HKCU", ADV, "ShowTaskViewButton", D, 0, None)]))
    T.append(RegTweak("news_interests", "Hide News and Interests", UI,
                      "Removes the weather/news widget from the Windows 10 taskbar.", admin=True,
                      restart="Restart Explorer", target="win10", values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\Windows Feeds", "EnableFeeds", D, 0, None)]))
    T.append(RegTweak("clipboard_history", "Turn on clipboard history", UI,
                      "Win+V shows the last things you copied, not just the latest one.",
                      values=[("HKCU", r"Software\Microsoft\Clipboard", "EnableClipboardHistory", D, 1, None)]))
    T.append(RegTweak("dark_mode", "Dark mode for Windows and apps", UI,
                      "Switches Windows and supporting apps to the dark theme.", values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                           "AppsUseLightTheme", D, 0, 1),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                           "SystemUsesLightTheme", D, 0, 1)]))
    T.append(RegTweak("long_paths", "Allow long file paths", UI,
                      "Lets apps use paths longer than 260 characters. Fixes 'path too long' errors when "
                      "copying deep folders.", admin=True, values=[
                          ("HKLM", r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled", D, 1, 0)]))
    # ---- Background services
    T.append(ServiceTweak("sysmain", "Disable SysMain (Superfetch)", SVC,
                          "Stops RAM pre-loading. Only helps if you see constant disk activity on an HDD; "
                          "usually best left on.", risk="Advanced", admin=True, service_names=["SysMain"]))
    T.append(ServiceTweak("wsearch", "Disable Windows Search indexing", SVC,
                          "Stops background indexing. Saves disk/CPU activity but Start and Explorer search become slow.",
                          risk="Advanced", admin=True, service_names=["WSearch"], default_start="automatic"))
    T.append(ServiceTweak("retail_demo", "Disable Retail Demo service", SVC,
                          "Only used by shop display PCs. Safe to turn off at home.", admin=True,
                          default_start="manual", service_names=["RetailDemo"]))
    T.append(ServiceTweak("fax", "Disable Fax service", SVC, "Not needed unless you send faxes from this PC.",
                          admin=True, default_start="manual", service_names=["Fax"]))
    T.append(ServiceTweak("maps_broker", "Disable offline maps manager", SVC,
                          "Stops the service that downloads maps for the Maps app.", admin=True,
                          default_start="automatic", service_names=["MapsBroker"]))
    T.append(ServiceTweak("spooler", "Disable printing (Print Spooler)", SVC,
                          "Only if you never print or save to PDF. Also closes a common attack target.",
                          risk="Advanced", admin=True, default_start="automatic", service_names=["Spooler"]))
    T.sort(key=lambda t: CATEGORY_ORDER.index(t.category))
    return T


# --------------------------------------------------------------------------- presets
PRESETS = {
    "Gaming": ["game_mode", "game_dvr", "hags", "mouse_accel", "sticky_keys", "net_throttle", "power_plan",
               "animations", "startup_delay", "edge_background"],
    "Privacy": ["ad_id", "tailored", "suggestions", "web_search", "feedback", "lang_list", "activity", "telemetry",
                "copilot", "w11_recall", "w11_click_to_do", "error_reporting", "track_progs"],
    "Windows 11 clean-up": ["classic_menu", "end_task", "w11_start_recs", "w11_start_account", "w11_hide_widgets",
                            "w11_hide_chat", "copilot", "w11_recall", "search_icon", "file_ext", "w11_spotlight_icon"],
    "Speed": ["animations", "menu_delay", "startup_delay", "transparency", "edge_background", "delivery_p2p",
              "power_plan", "game_dvr"],
}


def preset_ids(name: str, tweaks_list: list, info=None) -> list:
    """Tweak ids from a preset that make sense on this PC (right Windows, laptop-safe on laptops)."""
    from .osinfo import detect
    info = info or detect()
    by_id = {t.id: t for t in tweaks_list}
    out = []
    for tid in PRESETS.get(name, []):
        t = by_id.get(tid)
        if t and t.availability(info)[0] and not (info.laptop and t.laptop_note):
            out.append(tid)
    return out


def restart_explorer() -> None:
    run_command(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    import subprocess
    subprocess.Popen(["explorer.exe"])
    log.info("Restarted Explorer")
