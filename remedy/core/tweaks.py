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
    def __init__(self, id, name, category, description, risk="Safe", admin=False, restart="",
                 recommended=False):
        self.id, self.name, self.category, self.description = id, name, category, description
        self.risk, self.admin, self.restart, self.recommended = risk, admin, restart, recommended

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
def build_tweaks() -> list:
    D, S = REG_DWORD, REG_SZ
    T = []
    # ---- Performance
    T.append(PowerPlanTweak("power_plan", "Ultimate Performance power plan", "Performance",
                            "Unhides and activates Windows' Ultimate Performance plan (falls back to High "
                            "Performance). The CPU stays ready for bursts. Ideal for desktops; laptops use more battery.",
                            risk="Moderate", admin=True))
    T.append(RegTweak("animations", "Reduce animations", "Performance",
                      "Turns off window minimize/maximize and taskbar animations so the desktop feels snappier.",
                      restart="Sign out", recommended=True, values=[
                          ("HKCU", r"Control Panel\Desktop\WindowMetrics", "MinAnimate", S, "0", "1"),
                          ("HKCU", ADV, "TaskbarAnimations", D, 0, 1),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                           "VisualFXSetting", D, 3, None)]))
    T.append(RegTweak("menu_delay", "Instant menus", "Performance",
                      "Removes the 400 ms delay before sub-menus open.", restart="Sign out", recommended=True,
                      values=[("HKCU", r"Control Panel\Desktop", "MenuShowDelay", S, "0", "400")]))
    T.append(RegTweak("startup_delay", "Remove startup app delay", "Performance",
                      "Windows waits ~10 seconds after sign-in before launching startup apps. This removes the wait.",
                      recommended=True, values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize",
                           "StartupDelayInMSec", D, 0, None)]))
    T.append(RegTweak("transparency", "Disable transparency effects", "Performance",
                      "Turns off acrylic/blur effects. Small GPU saving on older or integrated graphics.",
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                               "EnableTransparency", D, 0, 1)]))
    T.append(HibernationTweak("hibernation", "Turn off hibernation", "Performance",
                              "Deletes hiberfil.sys (often 40% of your RAM size) and disables Hibernate and "
                              "Fast Startup. Great on desktops; laptop users may want to keep it.",
                              risk="Moderate", admin=True))
    T.append(RegTweak("background_apps", "Block background apps", "Performance",
                      "Stops Store apps from running in the background (Windows 10; Windows 11 manages this per app).",
                      risk="Moderate", values=[
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
                           "GlobalUserDisabled", D, 1, None)]))
    T.append(ServiceTweak("sysmain", "Disable SysMain (Superfetch)", "Performance",
                          "Stops RAM pre-loading. Only helps if you see constant disk activity on an HDD; "
                          "usually best left on.", risk="Advanced", admin=True, service_names=["SysMain"]))
    T.append(ServiceTweak("wsearch", "Disable Windows Search indexing", "Performance",
                          "Stops background indexing. Saves disk/CPU activity but Start and Explorer search become slow.",
                          risk="Advanced", admin=True, service_names=["WSearch"], default_start="automatic"))
    # ---- Gaming
    T.append(RegTweak("game_mode", "Enable Game Mode", "Gaming",
                      "Prioritizes the game you're playing and pauses Windows Update installs during play.",
                      recommended=True, values=[("HKCU", r"Software\Microsoft\GameBar", "AutoGameModeEnabled", D, 1, None)]))
    T.append(RegTweak("game_dvr", "Disable background game recording", "Gaming",
                      "Turns off Xbox Game DVR background capture, which costs FPS. Game Bar screenshots still work.",
                      recommended=True, values=[
                          ("HKCU", r"System\GameConfigStore", "GameDVR_Enabled", D, 0, 1),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", D, 0, None)]))
    T.append(RegTweak("hags", "Hardware-accelerated GPU scheduling", "Gaming",
                      "Lets the GPU manage its own memory scheduling. Can lower latency on recent NVIDIA/AMD GPUs.",
                      risk="Moderate", admin=True, restart="Restart PC", values=[
                          ("HKLM", r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", D, 2, None)]))
    T.append(RegTweak("mouse_accel", "Disable mouse acceleration", "Gaming",
                      "Turns off 'Enhance pointer precision' for 1:1 raw-feeling aim.", restart="Sign out", values=[
                          ("HKCU", r"Control Panel\Mouse", "MouseSpeed", S, "0", "1"),
                          ("HKCU", r"Control Panel\Mouse", "MouseThreshold1", S, "0", "6"),
                          ("HKCU", r"Control Panel\Mouse", "MouseThreshold2", S, "0", "10")]))
    T.append(RegTweak("sticky_keys", "Disable Sticky Keys shortcut", "Gaming",
                      "Stops the Sticky Keys pop-up when you press Shift five times mid-game.", recommended=True,
                      values=[("HKCU", r"Control Panel\Accessibility\StickyKeys", "Flags", S, "506", "510")]))
    T.append(RegTweak("net_throttle", "Disable network throttling", "Gaming",
                      "Removes Windows' multimedia network throttle and reserves less CPU for background tasks.",
                      risk="Moderate", admin=True, restart="Restart PC", values=[
                          ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                           "NetworkThrottlingIndex", D, 0xFFFFFFFF, 10),
                          ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile",
                           "SystemResponsiveness", D, 10, 20)]))
    # ---- Privacy
    T.append(RegTweak("ad_id", "Disable advertising ID", "Privacy",
                      "Stops apps using your advertising ID for personalized ads.", recommended=True,
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled", D, 0, 1)]))
    T.append(RegTweak("tailored", "Disable tailored experiences", "Privacy",
                      "Stops Microsoft using diagnostic data for tips, ads and recommendations.", recommended=True,
                      values=[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Privacy",
                               "TailoredExperiencesWithDiagnosticDataEnabled", D, 0, 1)]))
    T.append(RegTweak("suggestions", "Disable tips, suggestions & auto-installed apps", "Privacy",
                      "Removes Start menu suggestions, lock-screen tips, Settings ads and silently installed promo apps.",
                      recommended=True, values=[
                          ("HKCU", CDM, name, D, 0, 1) for name in (
                              "SubscribedContent-338387Enabled", "SubscribedContent-338388Enabled",
                              "SubscribedContent-338389Enabled", "SubscribedContent-353694Enabled",
                              "SubscribedContent-353696Enabled", "SystemPaneSuggestionsEnabled",
                              "SoftLandingEnabled", "SilentInstalledAppsEnabled")]))
    T.append(RegTweak("web_search", "Disable Bing in Start search", "Privacy",
                      "Start menu searches stay on your PC instead of going to Bing. Faster, too.",
                      recommended=True, restart="Restart Explorer", values=[
                          ("HKCU", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", D, 1, None),
                          ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", D, 0, None)]))
    T.append(RegTweak("copilot", "Turn off Copilot", "Privacy",
                      "Hides the Copilot button and disables the Windows Copilot sidebar.", restart="Restart Explorer",
                      values=[("HKCU", r"Software\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot", D, 1, None),
                              ("HKCU", ADV, "ShowCopilotButton", D, 0, None)]))
    T.append(RegTweak("activity", "Disable activity history", "Privacy",
                      "Stops Windows recording and uploading your app/file activity timeline.", admin=True,
                      recommended=True, values=[
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "PublishUserActivities", D, 0, None),
                          ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\System", "UploadUserActivities", D, 0, None)]))
    T.append(CompositeTweak("telemetry", "Minimize telemetry", "Privacy",
                            "Sets diagnostic data to the minimum allowed and disables the Connected User "
                            "Experiences service. (Home/Pro editions treat 'off' as 'Required'.)",
                            risk="Moderate", admin=True, parts=[
                                RegTweak("telemetry_reg", "", "", "", values=[
                                    ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", D, 0, None)]),
                                ServiceTweak("telemetry_svc", "", "", "", service_names=["DiagTrack", "dmwappushservice"])]))
    # ---- Explorer & UI
    T.append(RegTweak("file_ext", "Show file extensions", "Explorer & UI",
                      "Shows .exe, .pdf etc. This makes disguised malware like 'invoice.pdf.exe' obvious.",
                      recommended=True, restart="Restart Explorer", values=[("HKCU", ADV, "HideFileExt", D, 0, 1)]))
    T.append(RegTweak("hidden_files", "Show hidden files", "Explorer & UI",
                      "Shows hidden files and folders like AppData.", restart="Restart Explorer",
                      values=[("HKCU", ADV, "Hidden", D, 1, 2)]))
    T.append(RegTweak("this_pc", "Open Explorer to 'This PC'", "Explorer & UI",
                      "File Explorer opens on your drives instead of Home/Quick access.",
                      values=[("HKCU", ADV, "LaunchTo", D, 1, None)]))
    T.append(ClassicMenuTweak("classic_menu", "Classic right-click menu (Windows 11)", "Explorer & UI",
                              "Brings back the full context menu without 'Show more options'.",
                              restart="Restart Explorer"))
    T.append(RegTweak("end_task", "'End task' in taskbar right-click (Windows 11)", "Explorer & UI",
                      "Adds End task to taskbar app menus to kill frozen apps instantly.",
                      recommended=True, values=[("HKCU", ADV + r"\TaskbarDeveloperSettings", "TaskbarEndTask", D, 1, None)]))
    return T


def restart_explorer() -> None:
    run_command(["taskkill", "/f", "/im", "explorer.exe"], timeout=30)
    import subprocess
    subprocess.Popen(["explorer.exe"])
    log.info("Restarted Explorer")
