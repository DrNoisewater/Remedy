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
    res = run_command(["sc.exe", "config", name, "start=", START_MAP[start]], timeout=60)
    if not res.ok:
        raise RuntimeError(res.output or f"sc config failed for {name}")
    log.info("Service %s start type -> %s", name, start)


def control(name: str, action: str) -> None:
    verbs = {"start": "Start-Service", "stop": "Stop-Service -Force", "restart": "Restart-Service -Force"}
    res = powershell(f"{verbs[action]} -Name {ps_quote(name)} -ErrorAction Stop", timeout=120)
    if not res.ok:
        raise RuntimeError(res.output or f"Could not {action} {name}")
    log.info("Service %s: %s", name, action)
