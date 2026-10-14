"""One-click maintenance and repair actions."""
from __future__ import annotations

import os
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
                    "Repair", cmd=["sfc", "/scannow"], admin=True))
    A.append(Action("dism", "Repair Windows image (DISM)", "Fixes the component store SFC repairs from. Run this if SFC fails.",
                    "Repair", cmd=["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], admin=True))
    A.append(Action("component", "Clean up component store", "Removes superseded Windows Update components (WinSxS).",
                    "Cleanup", cmd=["DISM", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], admin=True))
    A.append(Action("optimize", "Optimize drives (TRIM / defrag)",
                    "Runs TRIM on SSDs and defragments HDDs. Windows picks the right one per drive.", "Cleanup",
                    cmd=ps_args("Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' } | "
                                "ForEach-Object { Write-Output \"Optimizing $($_.DriveLetter):\"; "
                                "Optimize-Volume -DriveLetter $_.DriveLetter -Verbose 4>&1 }"), admin=True))
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
    A.append(Action("reliability", "Reliability history", "Timeline of crashes, failed updates and errors.",
                    "Diagnostics", launch="perfmon.exe /rel"))
    A.append(Action("dns", "Flush DNS cache", "Fixes sites not loading after DNS/IP changes.", "Network",
                    cmd=["ipconfig", "/flushdns"]))
    A.append(Action("netreset", "Reset network stack", "Resets Winsock and TCP/IP. Fixes stubborn connection issues. Restart required.",
                    "Network", cmd=["cmd", "/c", "netsh winsock reset && netsh int ip reset"], admin=True))
    A.append(Action("ping", "Connection test", "Pings Cloudflare 10 times to check latency and packet loss.",
                    "Network", cmd=["ping", "-n", "10", "1.1.1.1"]))
    A.append(Action("winget_list", "Check for app updates", "Lists installed apps with newer versions available (winget).",
                    "Updates", cmd=["winget", "upgrade", "--include-unknown", "--accept-source-agreements"]))
    A.append(Action("winget_all", "Update all apps", "Silently upgrades every app winget can update.", "Updates",
                    cmd=["winget", "upgrade", "--all", "--silent", "--accept-package-agreements",
                         "--accept-source-agreements", "--disable-interactivity"], admin=True))
    A.append(Action("wu", "Windows Update", "Open Windows Update to install system and driver updates.", "Updates",
                    launch="ms-settings:windowsupdate"))
    A.append(Action("explorer", "Restart Explorer", "Fixes a frozen taskbar/desktop and applies Explorer tweaks.",
                    "Repair", cmd=["cmd", "/c", "taskkill /f /im explorer.exe & start explorer.exe"]))
    A.append(Action("iconcache", "Rebuild icon cache", "Fixes blank or wrong icons.", "Repair",
                    cmd=["ie4uinit.exe", "-show"]))
    return A
