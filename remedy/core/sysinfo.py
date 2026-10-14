"""Hardware and OS inventory."""
from __future__ import annotations

import platform
import time

import psutil

from .utils import IS_WINDOWS, as_list, fmt_duration, human_size, list_drives, powershell_json

_WMI_SCRIPT = r"""
$o = [ordered]@{}
$os = Get-CimInstance Win32_OperatingSystem
$o.OS = [ordered]@{ Name=$os.Caption; Version=$os.Version; Build=$os.BuildNumber; Architecture=$os.OSArchitecture; Installed=[string]$os.InstallDate }
$o.System = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer, Model
$o.CPU = Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed
$o.GPU = Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, @{n='DriverDate';e={[string]$_.DriverDate}}, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate
$o.Board = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer, Product
$o.BIOS = Get-CimInstance Win32_BIOS | Select-Object Manufacturer, SMBIOSBIOSVersion, @{n='ReleaseDate';e={[string]$_.ReleaseDate}}
$o.RAM = Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer, PartNumber, Capacity, Speed, ConfiguredClockSpeed
$o.Disks = Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, @{n='Health';e={[string]$_.HealthStatus}}, Size
$o | ConvertTo-Json -Depth 4 -Compress
"""


def get_system_info() -> list:
    """Return [(section, [(label, value), ...]), ...]."""
    sections = []
    wmi = powershell_json(_WMI_SCRIPT, timeout=90) if IS_WINDOWS else None
    wmi = wmi if isinstance(wmi, dict) else {}

    osd = wmi.get("OS") or {}
    rows = [("Operating system", osd.get("Name") or f"{platform.system()} {platform.release()}"),
            ("Version / build", f"{osd.get('Version', platform.version())}"),
            ("Architecture", osd.get("Architecture") or platform.machine()),
            ("Computer name", platform.node()),
            ("Uptime", fmt_duration(time.time() - psutil.boot_time()))]
    for s in as_list(wmi.get("System")):
        rows.append(("Manufacturer / model", f"{s.get('Manufacturer', '')} {s.get('Model', '')}".strip()))
    sections.append(("System", rows))

    rows = []
    for c in as_list(wmi.get("CPU")):
        rows += [("Processor", (c.get("Name") or "").strip()),
                 ("Cores / threads", f"{c.get('NumberOfCores')} / {c.get('NumberOfLogicalProcessors')}"),
                 ("Max clock", f"{(c.get('MaxClockSpeed') or 0) / 1000:.2f} GHz")]
    if not rows:
        rows = [("Processor", platform.processor() or "Unknown"),
                ("Cores / threads", f"{psutil.cpu_count(False)} / {psutil.cpu_count()}")]
    sections.append(("Processor", rows))

    vm = psutil.virtual_memory()
    rows = [("Installed", human_size(vm.total)), ("In use", f"{human_size(vm.used)} ({vm.percent:.0f}%)")]
    for i, m in enumerate(as_list(wmi.get("RAM")), 1):
        speed = m.get("ConfiguredClockSpeed") or m.get("Speed")
        rows.append((f"Module {i}", f"{human_size(m.get('Capacity') or 0)} @ {speed} MT/s, "
                                    f"{(m.get('Manufacturer') or '').strip()} {(m.get('PartNumber') or '').strip()}"))
    sections.append(("Memory", rows))

    rows = []
    for g in as_list(wmi.get("GPU")):
        res = ""
        if g.get("CurrentHorizontalResolution"):
            res = f", {g['CurrentHorizontalResolution']}×{g['CurrentVerticalResolution']} @ {g.get('CurrentRefreshRate')} Hz"
        rows.append((g.get("Name") or "GPU", f"Driver {g.get('DriverVersion', '?')}{res}"))
    if rows:
        sections.append(("Graphics", rows))

    rows = []
    for b in as_list(wmi.get("Board")):
        rows.append(("Motherboard", f"{b.get('Manufacturer', '')} {b.get('Product', '')}".strip()))
    for b in as_list(wmi.get("BIOS")):
        rows.append(("BIOS", f"{b.get('Manufacturer', '')} {b.get('SMBIOSBIOSVersion', '')}".strip()))
    if rows:
        sections.append(("Motherboard", rows))

    rows = [(d.get("FriendlyName") or "Disk",
             f"{human_size(d.get('Size') or 0)} {d.get('MediaType') or ''} ({d.get('BusType') or ''}), "
             f"health: {d.get('Health') or 'Unknown'}") for d in as_list(wmi.get("Disks"))]
    rows += [(d["mount"], f"{d['fstype']}, {human_size(d['free'])} free of {human_size(d['total'])}")
             for d in list_drives()]
    sections.append(("Storage", rows))

    rows = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if not st or not st.isup:
            continue
        ips = [a.address for a in addrs if a.family.name in ("AF_INET",)]
        if ips and not ips[0].startswith("127."):
            speed = f" ({st.speed} Mbps)" if st.speed else ""
            rows.append((name, ", ".join(ips) + speed))
    if rows:
        sections.append(("Network", rows))

    batt = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if batt is not None:
        sections.append(("Battery", [("Charge", f"{batt.percent:.0f}%"),
                                     ("Power", "Plugged in" if batt.power_plugged else "On battery")]))
    return sections


def as_text(sections) -> str:
    lines = []
    for title, rows in sections:
        lines.append(f"== {title} ==")
        lines += [f"{k}: {v}" for k, v in rows]
        lines.append("")
    return "\n".join(lines)
