"""New PC Setup: reusable setup profiles, PC inspection, a reviewed plan, a resumable deploy journal and verification.

Safety rules:
* Profiles are data, never code. Imported profiles may only name winget package IDs, known Remedy tweak IDs and
  known checklist items; anything else is dropped with a warning. No scripts, URLs or arbitrary installers.
* Built-in profiles only pick "Safe" tweaks. Anything riskier, or flagged for laptops, is "Needs review" and unticked.
* Nothing runs before the technician reviews the plan and confirms.
* The journal is saved after every step. Re-running skips anything already done or already in place, so a crash,
  restart or second click never installs twice. Resuming re-checks the real PC first.
* A failed restore point stops the deploy and asks (same rule as the rest of Remedy).
* Profiles and sessions never contain passwords, product keys, browser data, personal files or the computer name.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import appstore, diagnostics, osinfo, tweaks
from .utils import APP_DIR, IS_WINDOWS, TaskCancelled, as_list, human_size, is_admin, log, powershell_json, \
    run_stream, settings, system_drive

SCHEMA_VERSION = 1
PROFILE_EXT = ".remedyprofile"
WINGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,127}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")

SETUP_TYPES = {"new_oem": ("New OEM PC", "A new computer with the maker's apps preinstalled. Remedy finds what's "
                                          "already there first."),
               "clean_install": ("Clean install", "Windows was just installed. Configure it and add apps."),
               "existing": ("Existing PC", "Rebuild everyday setup on a PC already in use. Personal files are not "
                                           "touched.")}

CHECKLIST = {
    "updates": ("Windows Update finished", "Install all updates (Settings > Windows Update) and restart until none remain."),
    "activation": ("Windows is activated", "Settings > System > Activation shows 'active'. Remedy never enters keys."),
    "defender": ("Windows Security is green", "Open Windows Security: no actions recommended."),
    "restore": ("System Protection is on", "Restore points are enabled for the Windows drive."),
    "backup": ("Backup agreed with the client", "OneDrive or an external drive, if the client wants it."),
    "browser": ("Client signed in to their browser", "The client signs in themselves; Remedy never handles passwords."),
    "default_browser": ("Default browser chosen", "Settings > Apps > Default apps. Windows asks the user to confirm."),
    "printer": ("Printer works", "Installed and a test page printed."),
    "wifi": ("Wi-Fi connected", "Joined the right network, set to Private at home or Public elsewhere."),
    "peripherals": ("Devices work", "Monitor, sound, webcam, microphone and Bluetooth devices tested."),
    "oem": ("PC maker's updates checked", "BIOS and driver updates from the maker's support app or website."),
    "power": ("Power & sleep agreed", "Screen and sleep timings suit how the client uses the PC."),
    "recovery": ("Handover info given", "The client knows how to reach you and where their files are backed up."),
}


@dataclass
class SetupProfile:
    id: str
    name: str
    description: str = ""
    version: int = 1
    apps: list = field(default_factory=list)        # winget package IDs
    tweaks: list = field(default_factory=list)      # Remedy tweak IDs
    checklist: list = field(default_factory=list)   # CHECKLIST keys
    notes: str = ""
    builtin: bool = False
    schema: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin", None)
        return d


def _p(pid, name, desc, apps, tw, checks):
    return SetupProfile(pid, name, desc, 1, apps, tw, checks, builtin=True)


_BASE_RT = ["Microsoft.VCRedist.2015+.x64", "Microsoft.DotNet.DesktopRuntime.8"]
BUILTIN_PROFILES = [
    _p("everyday", "Everyday PC", "Clean, safe basics for home use: a browser, media and PDF tools, fewer ads and nags.",
       ["Google.Chrome", "7zip.7zip", "VideoLAN.VLC", "SumatraPDF.SumatraPDF"] + _BASE_RT,
       ["file_ext", "suggestions", "ad_id", "tailored", "w11_start_account", "end_task", "sticky_keys", "delivery_p2p",
        "storage_sense"],
       ["updates", "activation", "defender", "restore", "backup", "browser", "default_browser", "wifi", "peripherals"]),
    _p("gaming", "Gaming PC", "Game launchers and runtimes, Game Mode on, background recording off. No risky 'boosts'.",
       ["Valve.Steam", "Discord.Discord", "Google.Chrome", "7zip.7zip"] + _BASE_RT,
       ["game_mode", "game_dvr", "sticky_keys", "mouse_accel", "startup_delay", "edge_background", "file_ext",
        "suggestions"],
       ["updates", "activation", "defender", "oem", "peripherals", "wifi"]),
    _p("business", "Business PC", "Productivity and meetings, privacy-minded defaults, printer and backup checks.",
       ["Google.Chrome", "Adobe.Acrobat.Reader.64-bit", "7zip.7zip", "Zoom.Zoom"] + _BASE_RT,
       ["file_ext", "ad_id", "tailored", "suggestions", "w11_start_account", "web_search", "feedback", "delivery_p2p",
        "storage_sense"],
       ["updates", "activation", "defender", "restore", "backup", "printer", "browser", "wifi", "recovery"]),
    _p("laptop", "Laptop", "Battery-conscious: nothing that keeps the PC awake or busy; power and Wi-Fi checks.",
       ["Google.Chrome", "7zip.7zip", "VideoLAN.VLC", "SumatraPDF.SumatraPDF", "Microsoft.VCRedist.2015+.x64"],
       ["storage_sense", "suggestions", "ad_id", "file_ext", "edge_background", "startup_delay", "delivery_p2p"],
       ["updates", "activation", "defender", "power", "backup", "wifi", "oem", "peripherals"]),
    _p("creator", "Creator / Developer", "Creative and coding tools, file extensions and long paths on.",
       ["OBSProject.OBSStudio", "GIMP.GIMP", "Audacity.Audacity", "Microsoft.VisualStudioCode", "Git.Git",
        "Microsoft.PowerToys", "Notepad++.Notepad++", "7zip.7zip", "Microsoft.WindowsTerminal"] + _BASE_RT,
       ["file_ext", "hidden_files", "long_paths", "clipboard_history", "this_pc", "suggestions", "end_task"],
       ["updates", "activation", "defender", "backup", "peripherals"]),
    _p("technician", "My Technician Kit", "A starting point for your own standard setup: duplicate it and make it yours.",
       ["7zip.7zip", "voidtools.Everything", "CrystalDewWorld.CrystalDiskInfo"],
       ["file_ext", "suggestions"], list(CHECKLIST)),
]


# --------------------------------------------------------------------------- profile storage
def profile_dir() -> Path:
    """Portable mode: if a folder named 'RemedyData' sits next to Remedy.exe, profiles live there (so they travel on
    the USB stick). Customer sessions and logs always stay on the PC."""
    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / "RemedyData"
        if portable.is_dir():
            return portable / "profiles"
    return APP_DIR / "profiles"


def portable_mode() -> bool:
    return profile_dir().parent.name == "RemedyData"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40]
    return s if len(s) >= 2 else "profile"


def parse_profile(data, source: str = "import") -> tuple:
    """Validate untrusted profile data. Returns (SetupProfile, [warnings]). Raises ValueError if unusable."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ValueError(f"Not a Remedy profile (invalid JSON: {exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("Not a Remedy profile (expected an object)")
    warnings = []
    schema = data.get("schema", 1)
    if not isinstance(schema, int) or schema < 1:
        raise ValueError("Not a Remedy profile (bad schema number)")
    if schema > SCHEMA_VERSION:
        raise ValueError(f"This profile was made by a newer Remedy (format {schema}); update Remedy to open it")
    known = {"schema", "id", "name", "description", "version", "apps", "tweaks", "checklist", "notes"}
    for k in data:
        if k not in known:
            warnings.append(f"Ignored unknown field '{k}' (profiles can't contain scripts or commands)")
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("The profile has no name")
    version = data.get("version", 1)
    version = version if isinstance(version, int) and 0 < version < 100000 else 1
    apps = []
    for a in as_list(data.get("apps"))[:150]:
        if isinstance(a, str) and WINGET_ID.match(a):
            if a not in apps:
                apps.append(a)
        else:
            warnings.append(f"Skipped invalid app ID {str(a)[:40]!r}")
    tweak_ids = {t.id for t in tweaks.build_tweaks()}
    tw = []
    for t in as_list(data.get("tweaks")):
        if isinstance(t, str) and t in tweak_ids:
            if t not in tw:
                tw.append(t)
        else:
            warnings.append(f"Skipped unknown tweak {str(t)[:40]!r}")
    checks = [c for c in as_list(data.get("checklist")) if isinstance(c, str) and c in CHECKLIST]
    pid = str(data.get("id") or "")
    builtin_ids = {p.id for p in BUILTIN_PROFILES}
    if not _SLUG.match(pid) or (source == "import" and pid in builtin_ids):
        pid = slugify(name) + "-" + uuid.uuid4().hex[:4]
    p = SetupProfile(pid, name, str(data.get("description") or "")[:240], version, apps, tw,
                     list(dict.fromkeys(checks)), str(data.get("notes") or "")[:2000])
    return p, warnings


def save_profile(p: SetupProfile) -> Path:
    if p.builtin:
        raise ValueError("Built-in profiles can't be changed; duplicate it first")
    folder = profile_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{p.id}{PROFILE_EXT}"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def delete_profile(p: SetupProfile) -> None:
    if p.builtin:
        raise ValueError("Built-in profiles can't be deleted")
    try:
        (profile_dir() / f"{p.id}{PROFILE_EXT}").unlink()
    except FileNotFoundError:
        pass


def load_profiles() -> list:
    out = list(BUILTIN_PROFILES)
    folder = profile_dir()
    if folder.is_dir():
        for path in sorted(folder.glob(f"*{PROFILE_EXT}")):
            try:
                p, _w = parse_profile(path.read_text(encoding="utf-8"), source="local")
                out.append(p)
            except (OSError, ValueError) as exc:
                log.warning("Skipping profile %s: %s", path.name, exc)
    return out


def duplicate(p: SetupProfile, name: str = "") -> SetupProfile:
    name = name or f"{p.name} (copy)"
    return SetupProfile(slugify(name) + "-" + uuid.uuid4().hex[:4], name, p.description, 1, list(p.apps),
                        list(p.tweaks), list(p.checklist), p.notes)


def export_text(p: SetupProfile) -> str:
    return json.dumps(p.to_dict(), indent=2)


# --------------------------------------------------------------------------- inspection
_INSPECT_PS = r"""
$o = [ordered]@{}
try { $cs = Get-CimInstance Win32_ComputerSystem; $o.Maker = $cs.Manufacturer; $o.Model = $cs.Model; $o.RAM = [int64]$cs.TotalPhysicalMemory } catch {}
try { $o.CPU = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name } catch {}
try { $o.GPU = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch {}
try { $b = Get-CimInstance Win32_BIOS; $o.BIOS = "$($b.Manufacturer) $($b.SMBIOSBIOSVersion)" } catch {}
try { $l = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f'" | Select-Object -First 1; $o.License = [int]$l.LicenseStatus } catch { $o.License = -1 }
try { $o.TPM = [bool](Get-Tpm -ErrorAction Stop).TpmPresent } catch { $o.TPMError = $_.Exception.Message }
try { $o.SecureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { $o.SecureBootError = $_.Exception.Message }
try { $v = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction Stop; $o.BitLocker = [string]$v.ProtectionStatus } catch { $o.BitLockerError = $_.Exception.Message }
try { $o.Devices = @(Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode <> 0' | Select-Object -First 10 -ExpandProperty Name) } catch {}
$o | ConvertTo-Json -Depth 3 -Compress
"""

LICENSE_TEXT = {0: "Not activated", 1: "Activated", 2: "Grace period", 3: "Grace period", 4: "Grace period",
                5: "Notification mode (not activated)", 6: "Extended grace period"}


def _fact(key, label, value, state="Known", source=""):
    return {"key": key, "label": label, "value": value, "state": state, "source": source}


def internet_ok(timeout: float = 3.0) -> bool:
    for host, port in diagnostics.NET_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def inspect(ctx=None, emit=None) -> dict:
    """Read-only look at the PC. Returns {"facts": [...], "installed": set|None, "winget": bool, "admin": bool,
    "internet": bool, "laptop": bool, "on_ac": bool|None, "free": int}."""
    import psutil
    emit = emit or (lambda *_: None)
    info = osinfo.detect()
    admin = is_admin()
    facts = [_fact("os", "Windows", info.summary() if info.is_windows else f"{info.family} (Windows features off)",
                   "Known" if info.is_windows else "Not available", "Registry"),
             _fact("arch", "Architecture", info.arch or "Unknown", "Known", "platform"),
             _fact("admin", "Running as administrator", "Yes" if admin else "No (some steps will be blocked)", "Known")]
    emit("Reading hardware and Windows details…", 10)
    hw = powershell_json(_INSPECT_PS, timeout=120) if IS_WINDOWS else None
    hw = hw if isinstance(hw, dict) else {}
    win = "Known" if IS_WINDOWS else "Not available"
    facts.append(_fact("model", "Maker / model", f"{hw.get('Maker', '')} {hw.get('Model', '')}".strip() or "Unknown",
                       win if hw.get("Model") else "Not available", "Win32_ComputerSystem"))
    facts.append(_fact("cpu", "Processor", hw.get("CPU") or "Unknown", win if hw.get("CPU") else "Not available"))
    facts.append(_fact("ram", "Memory", human_size(hw.get("RAM") or psutil.virtual_memory().total), "Known"))
    gpus = as_list(hw.get("GPU"))
    facts.append(_fact("gpu", "Graphics", ", ".join(gpus) or "Unknown", "Known" if gpus else "Not available"))
    facts.append(_fact("bios", "BIOS", hw.get("BIOS") or "Unknown", "Known" if hw.get("BIOS") else "Not available"))
    lic = hw.get("License")
    facts.append(_fact("activation", "Activation", LICENSE_TEXT.get(lic, "Unknown"),
                       "Known" if lic in LICENSE_TEXT else "Not available", "SoftwareLicensingProduct"))
    for key, label, err in (("TPM", "TPM present", "TPMError"), ("SecureBoot", "Secure Boot on", "SecureBootError"),
                            ("BitLocker", "BitLocker on Windows drive", "BitLockerError")):
        if key in hw:
            v = hw[key]
            facts.append(_fact(key.lower(), label, ("Yes" if v else "No") if isinstance(v, bool) else v, "Known"))
        else:
            facts.append(_fact(key.lower(), label, "Unknown",
                               "Requires admin" if IS_WINDOWS and not admin else "Not available", "PowerShell"))
    usage = psutil.disk_usage(system_drive())
    facts.append(_fact("disk", "Free space on Windows drive", f"{human_size(usage.free)} ({100 - usage.percent:.0f}%)"))
    batt = None
    try:
        batt = psutil.sensors_battery()
    except (psutil.Error, OSError, AttributeError, NotImplementedError):
        pass
    on_ac = None if batt is None else bool(batt.power_plugged)
    facts.append(_fact("form", "Form factor", info.form if IS_WINDOWS else ("Laptop" if batt else "Desktop"),
                       "Known", "Battery present" if batt else "No battery"))
    if batt is not None:
        facts.append(_fact("power", "Power", f"{'Plugged in' if on_ac else 'On battery'} · {batt.percent:.0f}%"))
    devs = as_list(hw.get("Devices"))
    if IS_WINDOWS:
        facts.append(_fact("devices", "Devices with problems", ", ".join(devs) if devs else "None", "Known",
                           "Win32_PnPEntity"))
        reboot = diagnostics.pending_reboot()
        facts.append(_fact("reboot", "Restart pending", ", ".join(reboot) if reboot else "No",
                           "Requires restart" if reboot else "Known", "Registry"))
    if ctx is not None and ctx.cancelled():
        raise TaskCancelled()
    emit("Checking the internet connection…", 45)
    net = internet_ok()
    facts.append(_fact("internet", "Internet", "Connected" if net else "Not reachable", "Known"))
    emit("Checking winget and installed apps (can take a minute)…", 60)
    wg = appstore.winget_available()
    installed = appstore.installed_ids() if wg else None
    facts.append(_fact("winget", "App installer (winget)", "Available" if wg else "Not available",
                       "Known" if IS_WINDOWS else "Not available",
                       "winget --version"))
    if installed is not None:
        facts.append(_fact("apps", "Apps winget can see", f"{len(installed)} installed", "Known", "winget export"))
    emit("Done", 100)
    return {"facts": facts, "installed": installed, "winget": wg, "admin": admin, "internet": net,
            "laptop": info.laptop or batt is not None, "on_ac": on_ac, "free": usage.free, "inspected_at": time.time()}


# --------------------------------------------------------------------------- plan
@dataclass
class PlanTask:
    id: str                 # stable: "app:<winget id>", "tweak:<id>", "check:<key>"
    kind: str               # app | tweak | check
    ref: str
    title: str
    why: str
    selected: bool
    state: str              # todo | satisfied | blocked | review | manual
    risk: str = "Low"
    reversible: str = ""
    reboot: str = ""
    detail: str = ""
    category: str = ""

    @property
    def runnable(self) -> bool:
        return self.selected and self.state in ("todo", "review")


def _catalog() -> dict:
    return {a.winget_id: a for a in appstore.CATALOG}


def tweak_statuses(ids) -> dict:
    by_id = {t.id: t for t in tweaks.build_tweaks()}
    out = {}
    for i in ids:
        t = by_id.get(i)
        try:
            out[i] = t.status() if t is not None else None
        except Exception:  # noqa: BLE001 - unknown is honest
            out[i] = None
    return out


def build_plan(profile: SetupProfile, inspection: dict, info=None, tweak_status=None, selections=None) -> list:
    """Turn a profile into concrete tasks for THIS PC. selections: {task id: bool} saved from an earlier draft."""
    info = info or osinfo.detect()
    admin = bool(inspection.get("admin"))
    laptop = bool(inspection.get("laptop") or info.laptop)
    installed = inspection.get("installed")
    installed_l = {i.lower() for i in installed} if installed is not None else None
    tweak_status = tweak_status if tweak_status is not None else {}
    selections = selections or {}
    cat = _catalog()
    tasks = []
    for wid in profile.apps:
        app = cat.get(wid)
        name = app.name if app else wid
        t = PlanTask(f"app:{wid}", "app", wid, f"Install {name}",
                     app.description if app else "Not in Remedy's curated list: check the package ID is the one you mean",
                     True, "todo", "Low", "Yes: uninstall from Settings > Apps", "",
                     f"winget package {wid} from the official winget source", app.category if app else "Other apps")
        if not info.is_windows:
            t.state, t.selected, t.detail = "blocked", False, "Windows only"
        elif not inspection.get("winget"):
            t.state, t.selected, t.detail = "blocked", False, "winget (App Installer) isn't available on this PC"
        elif installed_l is not None and wid.lower() in installed_l:
            t.state, t.selected, t.detail = "satisfied", False, "Already installed"
        elif app is None:
            t.state, t.selected = "review", False
        tasks.append(t)
    by_id = {t.id: t for t in tweaks.attach_impact(tweaks.build_tweaks())}
    for tid in profile.tweaks:
        tw = by_id.get(tid)
        if tw is None:
            continue
        restart = tw.restart or ""
        t = PlanTask(f"tweak:{tid}", "tweak", tid, tw.name, getattr(tw, "gain", "") or tw.description, True, "todo",
                     {"Safe": "Low", "Moderate": "Medium", "Advanced": "High"}.get(tw.risk, "Medium"),
                     "Yes: undo on the Tweaks page (original value is backed up)", restart,
                     tw.description, tw.category)
        ok, why = tw.availability(info)
        if not ok:
            t.state, t.selected, t.detail = "blocked", False, why
        elif tw.admin and not admin:
            t.state, t.selected, t.detail = "blocked", False, "Needs administrator"
        elif tweak_status.get(tid) is True:
            t.state, t.selected, t.detail = "satisfied", False, "Already set this way"
        elif laptop and tw.laptop_note:
            t.state, t.selected, t.detail = "review", False, f"Laptop: {tw.laptop_note}"
        elif tw.risk != "Safe":
            t.state, t.selected, t.detail = "review", False, f"{tw.risk} tweak: read the trade-off first"
        tasks.append(t)
    for key in profile.checklist:
        title, detail = CHECKLIST[key]
        tasks.append(PlanTask(f"check:{key}", "check", key, title, detail, True, "manual", "None",
                              "Nothing is changed by Remedy", "", "You confirm this by hand on the Verify step",
                              "Checklist"))
    for t in tasks:
        if t.id in selections and t.state in ("todo", "review"):
            t.selected = bool(selections[t.id])
    return tasks


def plan_counts(tasks: list) -> dict:
    return {"selected": sum(1 for t in tasks if t.runnable),
            "satisfied": sum(1 for t in tasks if t.state == "satisfied"),
            "review": sum(1 for t in tasks if t.state == "review"),
            "blocked": sum(1 for t in tasks if t.state == "blocked"),
            "reboot": sum(1 for t in tasks if t.runnable and t.reboot),
            "manual": sum(1 for t in tasks if t.kind == "check")}


def dry_run(tasks: list, inspection: dict) -> list:
    """Exact list of what would happen, in order, plus prerequisites. Changes nothing."""
    lines = []
    run = [t for t in tasks if t.runnable and t.kind != "check"]
    tw = [t for t in run if t.kind == "tweak"]
    apps = [t for t in run if t.kind == "app"]
    if tw and settings.get("restore_point_before_tweaks") and inspection.get("admin"):
        lines.append("1. Create a restore point (stops and asks if Windows can't make one)")
    n = len(lines)
    for t in tw:
        n += 1
        lines.append(f"{n}. Setting: {t.title}" + (f" (then {t.reboot.lower()})" if t.reboot else ""))
    for t in apps:
        n += 1
        lines.append(f"{n}. Install {t.ref} via winget (official source, silent, one at a time)")
    skipped = [t for t in tasks if t.kind != "check" and not t.runnable]
    if skipped:
        lines.append("")
        lines.append("Skipped:")
        for t in skipped:
            if t.state in ("todo", "review"):
                why = "not ticked" + (f" ({t.detail})" if t.state == "review" and t.detail else "")
            else:
                why = t.detail or t.state
            lines.append(f"  – {t.title}: {why}")
    pre = preflight(tasks, inspection)
    lines.append("")
    lines.append("Preflight:")
    lines += [f"  {'OK ' if ok else '!! '} {name}: {detail}" for name, ok, detail in pre]
    return lines


def preflight(tasks: list, inspection: dict) -> list:
    run = [t for t in tasks if t.runnable and t.kind != "check"]
    apps = [t for t in run if t.kind == "app"]
    out = [("Administrator", bool(inspection.get("admin")) or not run,
            "Yes" if inspection.get("admin") else "No: settings needing admin are blocked")]
    if apps:
        out.append(("Internet", bool(inspection.get("internet")),
                    "Connected" if inspection.get("internet") else "Not reachable: app installs will fail"))
        free = inspection.get("free") or 0
        out.append(("Disk space", free > 10e9, f"{human_size(free)} free" + ("" if free > 10e9 else " (low)")))
    if inspection.get("laptop") and inspection.get("on_ac") is False:
        out.append(("Power", False, "On battery: plug the laptop in before a long setup"))
    return out


# --------------------------------------------------------------------------- session / journal
def new_session(profile: SetupProfile, setup_type: str, client_label: str = "") -> dict:
    info = osinfo.detect()
    return {"schema": SCHEMA_VERSION, "type": "setup", "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "started": time.time(), "status": "draft", "step": 0, "setup_type": setup_type,
            "client_label": client_label[:60], "profile": profile.to_dict(), "selections": {}, "inspection": [],
            "device": {"os": info.summary(), "form": info.form}, "tasks": {}, "note": "", "manual": {}}


def mark_interrupted(session: dict) -> bool:
    """After a crash/restart: anything left 'running' becomes 'interrupted' (it may or may not have finished; the
    resume re-checks the PC). Returns True if anything changed."""
    changed = False
    for t in (session.get("tasks") or {}).values():
        if t.get("status") == "running":
            t["status"], t["detail"] = "interrupted", "Remedy closed while this was running; it will be re-checked"
            changed = True
    if session.get("status") == "deploying":
        session["status"] = "interrupted"
        changed = True
    return changed


def journal_tasks(session: dict, tasks: list) -> None:
    """Record the plan in the journal. Finished results (done/verified) are never reset, so nothing re-runs."""
    j = session.setdefault("tasks", {})
    for t in tasks:
        e = j.get(t.id)
        if e is not None and e.get("status") in ("done", "verified"):
            continue
        if t.kind == "check":
            status = e["status"] if e and e.get("status", "").startswith("manual") else "pending"
        elif t.state == "satisfied":
            status = "satisfied"
        elif t.state == "blocked":
            status = "blocked"
        elif t.runnable:
            status = "pending"
        else:
            status = "skipped"
        j[t.id] = {"title": t.title, "kind": t.kind, "ref": t.ref, "status": status,
                   "detail": t.detail if status in ("satisfied", "blocked", "skipped") else (e or {}).get("detail", ""),
                   "reboot": t.reboot, "started": (e or {}).get("started", 0), "finished": (e or {}).get("finished", 0)}


class RestorePointFailed(Exception):
    pass


def _exec_app(ctx, task_id, ref, emit):
    code = run_stream(appstore.install_args(ref), lambda line: emit(line, -1), ctx.cancelled if ctx else (lambda: False))
    norm = appstore.normalize_code(code)
    if norm in (-1978335189, -1978335135):
        return "satisfied", "Already installed"
    if norm in appstore.OK_CODES:
        return "done", "Installed by winget"
    raise RuntimeError(appstore.describe_exit(code))


def _exec_tweak(ctx, task_id, ref, emit):
    tw = {t.id: t for t in tweaks.build_tweaks()}[ref]
    try:
        if tw.status() is True:
            return "satisfied", "Already set this way"
    except Exception:  # noqa: BLE001
        pass
    tw.apply()
    return "done", "Applied (original value backed up)"


EXECUTORS = {"app": _exec_app, "tweak": _exec_tweak}


def deploy(ctx, session: dict, tasks: list, save=None, emit=None, should_pause=None, executors=None,
           restore_point=None) -> dict:
    """Run the selected tasks in a safe order (restore point, settings, then apps one at a time).
    Idempotent: tasks already done/verified/satisfied in the journal are skipped. Saves after every step.
    Raises RestorePointFailed (nothing changed) so the UI can ask. Returns the session."""
    save = save or (lambda s: None)
    emit = emit or (lambda *_: None)
    should_pause = should_pause or (lambda: False)
    executors = executors or EXECUTORS
    journal_tasks(session, tasks)
    j = session["tasks"]
    order = [t for t in tasks if t.runnable and t.kind == "tweak"] + [t for t in tasks if t.runnable and t.kind == "app"]
    todo = [t for t in order if j[t.id]["status"] not in ("done", "verified", "satisfied")]
    session["status"] = "deploying"
    save(session)
    if any(t.kind == "tweak" for t in todo) and restore_point is not None and not session.get("restore_point_done"):
        emit("@restore|Creating a restore point…", 0)
        ok, msg = restore_point()
        if not ok:
            session["status"] = "paused"
            save(session)
            raise RestorePointFailed((msg or "Windows couldn't create a restore point").strip().splitlines()[-1])
        session["restore_point_done"] = True
        save(session)
    total = len(todo)
    for i, t in enumerate(todo):
        if ctx is not None and ctx.cancelled():
            break
        e = j[t.id]
        e.update(status="running", started=time.time(), detail="")
        save(session)
        emit(f"@{t.id}|{t.title}", int(i * 100 / max(1, total)))
        try:
            status, detail = executors[t.kind](ctx, t.id, t.ref, lambda line, pct=-1: emit(line, -1))
        except TaskCancelled:
            status, detail = "interrupted", "Stopped part-way: cancelled"
        except Exception as exc:  # noqa: BLE001 - a failed step is recorded, the rest continue
            status, detail = "failed", (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
        e.update(status=status, detail=detail, finished=time.time())
        log.info("Setup %s: %s -> %s (%s)", session["id"], t.id, status, detail)
        save(session)
        emit(f"@{t.id}|={status}|{detail}", int((i + 1) * 100 / max(1, total)))
        if should_pause():
            session["status"] = "paused"
            save(session)
            return session
    left = [t for t in todo if j[t.id]["status"] in ("pending", "running", "interrupted")]
    session["status"] = "paused" if left else "deployed"
    save(session)
    return session


def verify(session: dict, installed: set | None, tweak_status: dict) -> dict:
    """Check reality, not exit codes. done -> verified when the PC now shows the change."""
    inst = {i.lower() for i in installed} if installed is not None else None
    for tid, e in (session.get("tasks") or {}).items():
        if e.get("status") not in ("done", "verified", "satisfied"):
            continue
        if e["kind"] == "app":
            if inst is None:
                e["verify"] = "winget couldn't list installed apps"
            elif e["ref"].lower() in inst:
                if e["status"] == "done":
                    e["status"] = "verified"
                e["verify"] = "winget lists it as installed"
            else:
                e["verify"] = "winget doesn't list it yet (some apps appear only after a restart)"
        elif e["kind"] == "tweak":
            st = tweak_status.get(e["ref"])
            if st is True:
                if e["status"] == "done":
                    e["status"] = "verified"
                e["verify"] = "Setting reads back as applied"
            elif st is False:
                e["status"] = "failed"
                e["verify"] = "Setting did not stick" + (f" (may need: {e['reboot'].lower()})" if e.get("reboot") else "")
            else:
                e["verify"] = "Can't be read back automatically"
    for key, done in (session.get("manual") or {}).items():
        e = session["tasks"].get(f"check:{key}")
        if e is not None:
            e["status"] = "manual_done" if done else "manual_open"
    session["status"] = "verified"
    session["verified_at"] = time.time()
    return session


def outcome_counts(session: dict) -> dict:
    out = {}
    for e in (session.get("tasks") or {}).values():
        out[e.get("status")] = out.get(e.get("status"), 0) + 1
    return out
