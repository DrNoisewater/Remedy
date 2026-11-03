"""Windows services listing and control."""
from __future__ import annotations

from .utils import IS_WINDOWS, log, powershell, ps_quote, run_command

SERVICE_NOTES = {
    "DiagTrack": "Telemetry: safe to disable",
    "dmwappushservice": "Telemetry message routing: safe to disable",
    "Fax": "Fax: safe to disable if you don't fax",
    "RetailDemo": "Store demo mode: safe to disable",
    "MapsBroker": "Offline maps downloads: safe to set Manual",
    "WMPNetworkSvc": "Media Player network sharing: safe to disable",
    "wisvc": "Windows Insider: safe to disable if not an Insider",
    "RemoteRegistry": "Remote registry editing: keep disabled",
    "XblAuthManager": "Xbox Live sign-in: needed for Xbox/Game Pass games",
    "XblGameSave": "Xbox cloud saves: needed for Xbox/Game Pass games",
    "XboxNetApiSvc": "Xbox networking: needed for Xbox multiplayer",
    "XboxGipSvc": "Xbox controller accessories",
    "WSearch": "Search indexing: disabling makes Start/Explorer search slower",
    "SysMain": "Superfetch: usually best left on",
    "Spooler": "Print spooler: needed for printing",
    "lfsvc": "Geolocation: needed for location features",
    "PhoneSvc": "Telephony: needed for Phone Link calls",
    "WbioSrvc": "Biometrics: needed for Windows Hello face/fingerprint",
    "bthserv": "Bluetooth: needed for Bluetooth devices",
    "wuauserv": "Windows Update: keep enabled",
    "WinDefend": "Microsoft Defender: keep enabled",
}

START_MAP = {"automatic": "auto", "manual": "demand", "disabled": "disabled"}

# Services Windows can't do without. Remedy never stops or disables these (Start, Restart and Automatic stay allowed).
ESSENTIAL = {
    "AudioSrv": "Windows Audio: without it there's no sound",
    "AudioEndpointBuilder": "Audio devices: without it there's no sound",
    "Dhcp": "Gets your network address: without it there's no internet",
    "Dnscache": "Looks up website names: without it there's no internet",
    "NlaSvc": "Detects your network: needed for internet",
    "nsi": "Network information: needed for internet",
    "netprofm": "Network list: needed for internet",
    "WlanSvc": "Wi-Fi: without it Wi-Fi stops working",
    "Wcmsvc": "Connection manager: needed for internet",
    "BFE": "Firewall foundation: needed for the firewall and VPNs",
    "mpssvc": "Windows Firewall: protects you from network attacks",
    "WinDefend": "Microsoft Defender: your antivirus",
    "SecurityHealthService": "Windows Security: protection status",
    "wscsvc": "Security Center: warns you about protection problems",
    "wuauserv": "Windows Update: security fixes",
    "BITS": "Background downloads: Windows Update needs it",
    "TrustedInstaller": "Installs Windows updates",
    "CryptSvc": "Certificates: needed for updates and secure websites",
    "KeyIso": "Encryption keys: needed for Wi-Fi passwords and sign-in",
    "VaultSvc": "Credential Manager: your saved passwords",
    "EventLog": "Windows event logs: needed to diagnose problems",
    "PlugPlay": "Detects hardware: without it new devices don't work",
    "DeviceInstall": "Installs device drivers",
    "Power": "Power management: sleep, battery and shutdown",
    "ProfSvc": "User profiles: without it you can't sign in",
    "UserManager": "User manager: Start menu and sign-in need it",
    "LSM": "Sign-in sessions: without it you can't sign in",
    "SamSs": "Security accounts: without it you can't sign in",
    "RpcSs": "Core Windows service: almost everything depends on it",
    "RpcEptMapper": "Core Windows service: almost everything depends on it",
    "DcomLaunch": "Core Windows service: almost everything depends on it",
    "BrokerInfrastructure": "Core Windows service: apps and Start need it",
    "CoreMessagingRegistrar": "Core Windows service: windows and input need it",
    "SystemEventsBroker": "Core Windows service: scheduled events need it",
    "TimeBrokerSvc": "Core Windows service: background tasks need it",
    "StateRepository": "App data: Start menu and apps need it",
    "Schedule": "Task Scheduler: Windows maintenance and many apps need it",
    "Winmgmt": "System information (WMI): Windows, apps and Remedy need it",
    "gpsvc": "Group Policy: Windows settings need it",
    "Appinfo": "'Run as administrator' prompts: Remedy needs it",
    "sppsvc": "Windows activation",
    "AppXSvc": "Store app installs and updates",
    "ClipSVC": "Store app licences: Store apps won't open without it",
}


def protection(name: str, action: str, current_start: str = "") -> str:
    """Why an action on a service is blocked ("" when allowed). Blocks stopping, disabling and switching
    an essential, automatic service to manual."""
    reason = ESSENTIAL.get(name) or next((v for k, v in ESSENTIAL.items() if k.lower() == name.lower()), "")
    if not reason:
        return ""
    if action in ("stop", "disabled") or (action == "manual" and current_start == "automatic"):
        return reason
    return ""


def _guard(name: str, action: str) -> None:
    reason = protection(name, action, get_start_type(name) or "" if action == "manual" else "")
    if reason:
        raise PermissionError(f"Blocked to protect Windows. {reason}.")


def list_services() -> list:
    if not IS_WINDOWS:
        return []
    import psutil
    out = []
    for svc in psutil.win_service_iter():
        try:
            d = svc.as_dict()
        except Exception:  # noqa: BLE001 - some services deny config queries
            continue
        out.append({"name": d.get("name", ""), "display": d.get("display_name", ""),
                    "status": d.get("status", ""), "start": d.get("start_type", ""),
                    "description": d.get("description", "") or "", "binpath": d.get("binpath", "") or "",
                    "note": SERVICE_NOTES.get(d.get("name", ""), "")})
    out.sort(key=lambda s: s["display"].lower())
    return out


def get_start_type(name: str):
    if not IS_WINDOWS:
        return None
    import psutil
    try:
        return psutil.win_service_get(name).start_type()
    except Exception:  # noqa: BLE001
        return None


def set_start_type(name: str, start: str) -> None:
    """start: automatic | manual | disabled"""
    _guard(name, start)
    res = run_command(["sc.exe", "config", name, "start=", START_MAP[start]], timeout=60)
    if not res.ok:
        raise RuntimeError(res.output or f"sc config failed for {name}")
    log.info("Service %s start type -> %s", name, start)


def control(name: str, action: str) -> None:
    verbs = {"start": "Start-Service", "stop": "Stop-Service -Force", "restart": "Restart-Service -Force"}
    _guard(name, action)
    res = powershell(f"{verbs[action]} -Name {ps_quote(name)} -ErrorAction Stop", timeout=120)
    if not res.ok:
        raise RuntimeError(res.output or f"Could not {action} {name}")
    log.info("Service %s: %s", name, action)
