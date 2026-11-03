"""One-click maintenance and repair actions."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .utils import APP_DIR, ps_args, powershell


@dataclass
class Action:
    key: str
    title: str
    description: str
    group: str
    cmd: list = field(default_factory=list)   # streamed command
    launch: str = ""                          # or: something to open (URI / exe)
    admin: bool = False
    note: str = ""
    progress: bool = False                    # output contains "NN%" we can turn into a real progress bar
    confirm: str = ""                         # ask before running, with this explanation
    target: str = ""                          # "" any Windows, "win11" or "win10"


GROUP_ORDER = ["Safety", "Repair", "Cleanup", "Security", "Updates", "Diagnostics", "Network"]

_PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")


def percent_in(line: str):
    """Last percentage in a line of tool output ("Verification 45% complete", "[==== 62.3% ====]"), or None."""
    found = _PCT.findall(line or "")
    if not found:
        return None
    try:
        value = float(found[-1].replace(",", "."))
    except ValueError:
        return None
    return int(value) if 0 <= value <= 100 else None


RESTORE_POINT_SCRIPT = (
    "$k='HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SystemRestore'; "
    "New-ItemProperty -Path $k -Name SystemRestorePointCreationFrequency -Value 0 -PropertyType DWord -Force | Out-Null; "
    "Enable-ComputerRestore -Drive \"$env:SystemDrive\\\" -ErrorAction SilentlyContinue; "
    "Checkpoint-Computer -Description 'Remedy' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop; "
    "'Restore point created.'")


def create_restore_point() -> tuple:
    res = powershell(RESTORE_POINT_SCRIPT, timeout=600)
    return res.ok, res.output


def build_actions() -> list:
    battery = os.path.join(str(APP_DIR), "battery-report.html")
    A = []
    A.append(Action("restore", "Create restore point", "Snapshot system settings so you can roll back any change.",
                    "Safety", cmd=ps_args(RESTORE_POINT_SCRIPT), admin=True))
    A.append(Action("sfc", "Repair system files (SFC)", "Scans protected Windows files and replaces corrupted ones. ~10 min.",
                    "Repair", cmd=["sfc", "/scannow"], admin=True, progress=True))
    A.append(Action("dism", "Repair Windows image (DISM)", "Fixes the component store SFC repairs from. Run this if SFC fails.",
                    "Repair", cmd=["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], admin=True, progress=True))
    A.append(Action("chkdsk", "Scan drive for errors", "Checks the system drive's file system for problems while you "
                    "keep working. Read-only: nothing is changed.", "Repair",
                    cmd=["chkdsk", os.environ.get("SystemDrive", "C:"), "/scan"], admin=True, progress=True))
    A.append(Action("component", "Clean up component store", "Removes superseded Windows Update components (WinSxS).",
                    "Cleanup", cmd=["DISM", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], admin=True,
                    progress=True))
    A.append(Action("optimize", "Optimize drives (TRIM / defrag)",
                    "Runs TRIM on SSDs and defragments HDDs. Windows picks the right one per drive.", "Cleanup",
                    cmd=ps_args("Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' } | "
                                "ForEach-Object { Write-Output \"Optimizing $($_.DriveLetter):\"; "
                                "Optimize-Volume -DriveLetter $_.DriveLetter -Verbose 4>&1 }"), admin=True,
                    progress=True))
    A.append(Action("cleanmgr", "Windows Disk Cleanup", "Opens Microsoft's tool. Use 'Clean up system files' to remove Windows.old.",
                    "Cleanup", launch="cleanmgr.exe"))
    A.append(Action("health", "Check drive health", "SMART status, temperature, wear and error counters for each physical disk.",
                    "Diagnostics", cmd=ps_args(
                        "Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
                        "[pscustomobject]@{Disk=$_.FriendlyName; Type=$_.MediaType; Bus=$_.BusType; Health=$_.HealthStatus; "
                        "SizeGB=[math]::Round($_.Size/1GB); TempC=$r.Temperature; WearPct=$r.Wear; "
                        "ReadErrors=$r.ReadErrorsTotal; WriteErrors=$r.WriteErrorsTotal; PowerOnHours=$r.PowerOnHours} } | "
                        "Format-List | Out-String -Width 200"), admin=True))
    A.append(Action("battery", "Battery health report", "Design vs. current capacity and usage history (laptops).",
                    "Diagnostics", cmd=["powercfg", "/batteryreport", "/output", battery], note=battery))
    A.append(Action("memtest", "Memory diagnostic", "Schedules Windows Memory Diagnostic on next restart.",
                    "Diagnostics", launch="mdsched.exe"))
    wlan = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "WlanReport",
                        "wlan-report-latest.html")
    A.append(Action("wifi", "Wi-Fi report", "Shows drops, disconnects and signal problems from the last three days.",
                    "Diagnostics", cmd=["netsh", "wlan", "show", "wlanreport"], admin=True, note=wlan))
    A.append(Action("reliability", "Reliability history", "Timeline of crashes, failed updates and errors.",
                    "Diagnostics", launch="perfmon.exe /rel"))
    A.append(Action("dns", "Flush DNS cache", "Fixes sites not loading after DNS/IP changes.", "Network",
                    cmd=["ipconfig", "/flushdns"]))
    A.append(Action("netreset", "Reset network stack", "Resets Winsock and TCP/IP. Fixes stubborn connection issues. Restart required.",
                    "Network", cmd=["cmd", "/c", "netsh winsock reset && netsh int ip reset"], admin=True,
                    confirm="Your network settings go back to defaults (saved Wi-Fi passwords stay). "
                            "You'll need to restart the PC afterwards."))
    A.append(Action("ipconfig", "Show network details", "Your IP addresses, DNS servers and adapters.", "Network",
                    cmd=["ipconfig", "/all"]))
    A.append(Action("timesync", "Sync the clock", "Re-syncs Windows time with the internet. Fixes sign-in and "
                    "certificate errors caused by a wrong clock.", "Network",
                    cmd=["cmd", "/c", "net start w32time >nul 2>&1 & w32tm /resync /force"], admin=True))
    A.append(Action("ping", "Connection test", "Pings Cloudflare 10 times to check latency and packet loss.",
                    "Network", cmd=["ping", "-n", "10", "1.1.1.1"]))
    A.append(Action("winget_list", "Check for app updates", "Lists installed apps with newer versions available (winget).",
                    "Updates", cmd=["winget", "upgrade", "--include-unknown", "--accept-source-agreements"]))
    A.append(Action("winget_all", "Update all apps", "Silently upgrades every app winget can update.", "Updates",
                    cmd=["winget", "upgrade", "--all", "--silent", "--accept-package-agreements",
                         "--accept-source-agreements", "--disable-interactivity"], admin=True,
                    confirm="Every app with an update will be upgraded silently. Save your work and close apps first."))
    A.append(Action("onedrive", "Uninstall OneDrive", "Removes the OneDrive app. Files already on this PC stay in "
                    "your OneDrive folder; online-only files stay in the cloud.", "Cleanup",
                    cmd=["winget", "uninstall", "--id", "Microsoft.OneDrive", "--silent", "--accept-source-agreements",
                         "--disable-interactivity"],
                    confirm="OneDrive will stop syncing. Files that only live online won't be downloaded. "
                            "You can reinstall OneDrive from microsoft.com any time."))
    A.append(Action("defender_scan", "Virus quick scan", "Runs a Microsoft Defender quick scan of the places "
                    "malware usually hides.", "Security",
                    cmd=ps_args("Start-MpScan -ScanType QuickScan; 'Quick scan finished. Open Windows Security to see results.'"),
                    admin=True))
    A.append(Action("defender_update", "Update virus definitions", "Downloads the latest Microsoft Defender "
                    "definitions.", "Security", cmd=ps_args("Update-MpSignature; 'Definitions are up to date.'"),
                    admin=True))
    A.append(Action("security", "Windows Security", "Open Windows Security for firewall, protection history and "
                    "device security.", "Security", launch="windowsdefender:"))
    A.append(Action("wsreset", "Reset Microsoft Store", "Clears the Store's cache. Fixes Store downloads that hang.",
                    "Repair", launch="wsreset.exe"))
    A.append(Action("wu", "Windows Update", "Open Windows Update to install system and driver updates.", "Updates",
                    launch="ms-settings:windowsupdate"))
    A.append(Action("explorer", "Restart Explorer", "Fixes a frozen taskbar/desktop and applies Explorer tweaks.",
                    "Repair", cmd=["cmd", "/c", "taskkill /f /im explorer.exe & start explorer.exe"]))
    A.append(Action("iconcache", "Rebuild icon cache", "Fixes blank or wrong icons.", "Repair",
                    cmd=["ie4uinit.exe", "-show"]))
    A.sort(key=lambda a: GROUP_ORDER.index(a.group) if a.group in GROUP_ORDER else len(GROUP_ORDER))
    return A
