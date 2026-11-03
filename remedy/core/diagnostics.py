"""Diagnose & Repair engine: evidence providers, deterministic finding rules, and diagnostic session records.

Design rules (see IMPLEMENTATION_STATUS.md):
* Diagnosis only READS. Nothing here changes the PC.
* Every finding points at evidence (value, unit, source, sampling window, time) and says how sure we are.
* Providers are isolated: one failing or unavailable provider is reported honestly ("Not assessed") and never turns
  into a healthy result, and never stops the others.
* Thresholds are documented in THRESHOLDS and applied by plain rules (no scoring magic).
* Statuses: confirmed (measured here or reported by Windows), suspected (inferred / correlated), needs_review
  (a judgement call for the technician), not_assessed (provider unavailable / failed / skipped).
"""
from __future__ import annotations

import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field

import psutil

from . import junk, osinfo, registry
from .utils import IS_WINDOWS, TaskCancelled, as_list, human_size, is_admin, log, powershell_json, system_drive

SCHEMA_VERSION = 1

# Documented thresholds. Changing one changes what Remedy reports, so keep them conservative.
THRESHOLDS = {
    "cpu_sustained_high": 85.0,     # % average over the sampling window -> high
    "cpu_sustained_med": 60.0,      # % average -> medium
    "mem_high": 90.0,               # % average memory in use
    "mem_med": 80.0,
    "swap_med": 60.0,               # % of page file / swap in use
    "disk_latency_med": 25.0,       # ms average per I/O over the window (needs >= disk_min_ops)
    "disk_latency_high": 60.0,
    "disk_min_ops": 30,             # fewer I/Os than this in the window = not enough data to judge latency
    "sys_free_high": 10.0,          # % free on the system drive -> high
    "sys_free_med": 15.0,
    "other_free_low": 5.0,          # % free on other fixed drives -> low
    "junk_low": 1.0e9,              # bytes of safe-to-clean junk worth mentioning
    "junk_med": 5.0e9,
    "startup_med": 10,              # enabled startup entries
    "startup_low": 6,
    "uptime_days": 7.0,
    "sig_age_days": 3,              # Defender signature age considered stale
    "quick_scan_days": 14,
    "net_latency_ms": 150.0,        # average TCP connect time
    "crash_events": 1,              # unexpected shutdowns / stop errors in 14 days
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
STATUS_LABEL = {"confirmed": "Confirmed", "suspected": "Suspected", "needs_review": "Needs review",
                "not_assessed": "Not assessed"}
PROVIDER_LABEL = {"performance": "Performance", "storage": "Storage", "startup": "Startup", "windows": "Windows",
                  "security": "Security", "hardware": "Hardware", "network": "Network"}


class Unavailable(Exception):
    """A provider can't run here (wrong OS, needs admin, feature missing). The message is shown to the user."""


@dataclass
class Evidence:
    id: str
    provider: str
    label: str
    value: object                  # number, text, or list of short strings
    unit: str = ""
    source: str = ""               # what produced it, e.g. "psutil.cpu_percent" or "Get-MpComputerStatus"
    collected_at: float = 0.0
    window_s: float = 0.0          # sampling window in seconds (0 = single reading)
    samples: int = 1
    reliability: str = "measured"  # measured | reported (by Windows) | derived

    def display(self) -> str:
        v = self.value
        if isinstance(v, list):
            return ", ".join(str(x) for x in v) if v else "none"
        if isinstance(v, bool):
            return "Yes" if v else "No"
        if isinstance(v, float):
            v = f"{v:.1f}"
        if self.unit == "bytes":
            return human_size(float(self.value or 0))
        return f"{v}{' ' + self.unit if self.unit else ''}"


@dataclass
class Finding:
    id: str
    category: str
    title: str
    severity: str                  # high | medium | low | info
    status: str                    # confirmed | suspected | needs_review | not_assessed
    confidence: str                # high | medium | low
    what: str                      # what's happening, in plain words
    why: str                       # why we believe it (the evidence and thresholds)
    evidence_ids: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)   # other possible causes
    next_steps: list = field(default_factory=list)
    actions: list = field(default_factory=list)         # repair action ids (remedy.core.repair)
    links: list = field(default_factory=list)           # [label, page key, params]
    verify: list = field(default_factory=list)          # evidence ids to compare after a repair


@dataclass
class ProviderStatus:
    name: str
    status: str = "queued"         # queued | running | complete | partial | unavailable | failed | cancelled
    message: str = ""
    duration: float = 0.0


def _ev(provider, eid, label, value, unit="", source="", window=0.0, samples=1, reliability="measured"):
    return Evidence(f"{provider}.{eid}", provider, label, value, unit, source, time.time(), window, samples,
                    reliability)


def _check(ctx):
    if ctx is not None and ctx.cancelled():
        raise TaskCancelled()


# =========================================================================== providers
def provide_performance(ctx, opts, report):
    seconds = int(opts.get("seconds", 8))
    psutil.cpu_percent(None)
    procs = {}
    for p in psutil.process_iter(["name"]):
        try:
            p.cpu_percent(None)
            procs[p.pid] = p
        except (psutil.Error, OSError):
            pass
    io0 = psutil.disk_io_counters()
    cpu, mem, swap = [], [], []
    for i in range(seconds):
        _check(ctx)
        time.sleep(1)
        cpu.append(psutil.cpu_percent(None))
        mem.append(psutil.virtual_memory().percent)
        try:
            swap.append(psutil.swap_memory().percent)
        except (psutil.Error, OSError, RuntimeError):
            pass
        report(f"Sampling CPU, memory and disk activity ({i + 1} of {seconds} s)", int((i + 1) * 100 / seconds))
    io1 = psutil.disk_io_counters()
    n = len(cpu)
    out = [
        _ev("performance", "cpu_avg", "CPU use (average)", round(sum(cpu) / n, 1), "%", "psutil.cpu_percent", n, n),
        _ev("performance", "cpu_peak", "CPU use (peak 1-second sample)", round(max(cpu), 1), "%",
            "psutil.cpu_percent", n, n),
        _ev("performance", "cpu_busy_share", "Share of samples at 85% CPU or more",
            round(100 * sum(1 for c in cpu if c >= 85) / n), "%", "psutil.cpu_percent", n, n, "derived"),
        _ev("performance", "mem_avg", "Memory in use (average)", round(sum(mem) / n, 1), "%",
            "psutil.virtual_memory", n, n),
        _ev("performance", "mem_total", "Installed memory", psutil.virtual_memory().total, "bytes",
            "psutil.virtual_memory"),
    ]
    if swap:
        out.append(_ev("performance", "swap_avg", "Page file / swap in use (average)", round(sum(swap) / len(swap), 1),
                       "%", "psutil.swap_memory", n, len(swap)))
    if io0 is not None and io1 is not None and hasattr(io0, "read_time"):
        ops = (io1.read_count - io0.read_count) + (io1.write_count - io0.write_count)
        busy_ms = (io1.read_time - io0.read_time) + (io1.write_time - io0.write_time)
        out.append(_ev("performance", "disk_ops", "Disk operations per second", round(ops / n, 1), "ops/s",
                       "psutil.disk_io_counters", n, 2))
        if ops > 0:
            out.append(_ev("performance", "disk_latency", "Average disk response time per operation",
                           round(busy_ms / ops, 1), "ms", "psutil.disk_io_counters (read_time + write_time)",
                           n, 2, "derived"))
    cores = psutil.cpu_count() or 1
    tops = []
    for pid, p in procs.items():
        try:
            pct = p.cpu_percent(None) / cores
            if pct >= 1:
                tops.append((pct, p.info.get("name") or str(pid), pid))
        except (psutil.Error, OSError):
            pass
    tops.sort(reverse=True)
    out.append(_ev("performance", "top_cpu", "Busiest processes over the window (share of total CPU)",
                   [f"{name} (PID {pid}) {pct:.0f}%" for pct, name, pid in tops[:5]], "", "psutil.Process.cpu_percent",
                   n, 2))
    mems = []
    for pid, p in procs.items():
        try:
            mems.append((p.memory_info().rss, p.info.get("name") or str(pid), pid))
        except (psutil.Error, OSError):
            pass
    mems.sort(reverse=True)
    out.append(_ev("performance", "top_mem", "Largest processes by memory",
                   [f"{name} (PID {pid}) {human_size(rss)}" for rss, name, pid in mems[:5]], "",
                   "psutil.Process.memory_info"))
    return out


def junk_categories(admin=None):
    """Exactly the categories the 'Clean safe junk' repair cleans, so before/after compare like with like."""
    admin = is_admin() if admin is None else admin
    return [c for c in junk.build_categories()
            if c.default and c.special != "recycle" and (admin or not c.admin)]


def provide_storage(ctx, opts, report):
    out = []
    sd = os.path.normcase(system_drive())
    usage = psutil.disk_usage(system_drive())
    out.append(_ev("storage", "sys_free_pct", f"Free space on system drive {system_drive()}",
                   round(100 - usage.percent, 1), "%", "psutil.disk_usage"))
    out.append(_ev("storage", "sys_free_bytes", f"Free space on system drive {system_drive()}", usage.free, "bytes",
                   "psutil.disk_usage"))
    low = []
    from .utils import list_drives
    for d in list_drives():
        if os.path.normcase(d["mount"]) != sd and d["total"] > 2e9 and 100 - d["percent"] < THRESHOLDS["other_free_low"]:
            low.append(f"{d['mount']} {100 - d['percent']:.0f}% free ({human_size(d['free'])})")
    out.append(_ev("storage", "other_low", "Other drives under 5% free", low, "", "psutil.disk_usage"))
    if opts.get("junk", True):
        cats = junk_categories()
        total = 0
        for i, c in enumerate(cats):
            _check(ctx)
            report(f"Measuring {c.name}…", int(i * 100 / max(1, len(cats))))
            junk.scan_category(c, ctx)
            total += c.size
            c.files = []
        out.append(_ev("storage", "junk_bytes", "Safe-to-clean temp files and caches", total, "bytes",
                       "Remedy junk scanner (default categories, Recycle Bin excluded)"))
    return out


def provide_startup(ctx, opts, report):
    if not IS_WINDOWS:
        raise Unavailable("Startup entries can only be read on Windows")
    from . import startup
    report("Reading startup entries…", 30)
    items = startup.list_startup_items(include_tasks=bool(opts.get("tasks", False)))
    enabled = [s for s in items if s.enabled]
    return [
        _ev("startup", "enabled_count", "Enabled startup entries", len(enabled), "",
            "Registry Run keys + Startup folders" + (" + logon tasks" if opts.get("tasks") else ""),
            reliability="reported"),
        _ev("startup", "enabled_names", "Enabled startup entries", [s.name for s in enabled][:30], "",
            "Registry Run keys + Startup folders", reliability="reported"),
    ]


_PENDING_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending", None),
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired", None),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager", "PendingFileRenameOperations"),
]


def pending_reboot() -> list:
    """Reasons Windows is waiting for a restart (empty list = none found)."""
    reasons = []
    if not IS_WINDOWS:
        return reasons
    for hive, key, name in _PENDING_KEYS:
        try:
            if name is None:
                if registry.key_exists(hive, key):
                    reasons.append(key.split("\\")[-1])
            elif registry.get_value(hive, key, name):
                reasons.append(name)
        except OSError:
            pass
    return reasons


_WINDOWS_PS = r"""
$o = [ordered]@{}
$since = (Get-Date).AddDays(-14)
try { $o.KernelPower = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power'; Id=41; StartTime=$since} -ErrorAction Stop).Count } catch { $o.KernelPower = 0 }
try { $o.BugCheck = @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; StartTime=$since} -ErrorAction Stop).Count } catch { $o.BugCheck = 0 }
$o.Devices = @(Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode <> 0' -ErrorAction SilentlyContinue | Select-Object -First 15 Name, ConfigManagerErrorCode)
$o | ConvertTo-Json -Depth 3 -Compress
"""


def provide_windows(ctx, opts, report):
    up_days = (time.time() - psutil.boot_time()) / 86400
    out = [_ev("windows", "uptime_days", "Time since last restart", round(up_days, 1), "days", "psutil.boot_time")]
    if not IS_WINDOWS:
        return out, "Only uptime was checked: updates, crashes and devices need Windows"
    info = osinfo.detect()
    out.append(_ev("windows", "os", "Windows version", info.summary(), "", "Registry (CurrentVersion)", reliability="reported"))
    out.append(_ev("windows", "pending_reboot", "Pending restart signals", pending_reboot(), "", "Registry",
                   reliability="reported"))
    report("Reading recent crash events and device problems…", 50)
    data = powershell_json(_WINDOWS_PS, timeout=90)
    if isinstance(data, dict):
        out.append(_ev("windows", "unexpected_shutdowns", "Unexpected shutdowns (Kernel-Power 41), last 14 days",
                       int(data.get("KernelPower") or 0), "", "System event log", 14 * 86400, reliability="reported"))
        out.append(_ev("windows", "stop_errors", "Stop errors / blue screens (event 1001), last 14 days",
                       int(data.get("BugCheck") or 0), "", "System event log", 14 * 86400, reliability="reported"))
        devs = [f"{d.get('Name') or 'Unknown device'} (code {d.get('ConfigManagerErrorCode')})"
                for d in as_list(data.get("Devices")) if isinstance(d, dict)]
        out.append(_ev("windows", "device_problems", "Devices reporting a problem code", devs, "",
                       "Win32_PnPEntity.ConfigManagerErrorCode", reliability="reported"))
    return out


_SECURITY_PS = r"""
$o = [ordered]@{}
try { $s = Get-MpComputerStatus -ErrorAction Stop
  $o.Defender = [ordered]@{ Service=[bool]$s.AMServiceEnabled; RealTime=[bool]$s.RealTimeProtectionEnabled;
    SigAge=[int]$s.AntivirusSignatureAge; QuickAge=[int]$s.QuickScanAge; Tamper=[bool]$s.IsTamperProtected;
    Mode=[string]$s.AMRunningMode } } catch { $o.DefenderError = $_.Exception.Message }
try { $o.AV = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop | Select-Object displayName, productState) } catch { $o.AVError = $_.Exception.Message }
try { $o.Firewall = @(Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name, @{n='Enabled';e={[bool]$_.Enabled}}) } catch { $o.FirewallError = $_.Exception.Message }
try { $o.Threats = @(Get-MpThreat -ErrorAction Stop | Select-Object -First 20 ThreatName, SeverityID, IsActive, DidThreatExecute) } catch { $o.ThreatError = $_.Exception.Message }
$o | ConvertTo-Json -Depth 4 -Compress
"""


def av_enabled(product_state: int) -> bool:
    """Security Center productState: bit 12 set = real-time protection on."""
    return bool((int(product_state or 0) >> 12) & 1)


def provide_security(ctx, opts, report):
    if not IS_WINDOWS:
        raise Unavailable("Windows Security status can only be read on Windows")
    report("Reading Windows Security status…", 30)
    data = powershell_json(_SECURITY_PS, timeout=90)
    if not isinstance(data, dict):
        raise RuntimeError("Windows Security did not answer (PowerShell returned nothing)")
    out = []
    avs = [a for a in as_list(data.get("AV")) if isinstance(a, dict)]
    on = [a.get("displayName") or "Unknown" for a in avs if av_enabled(a.get("productState"))]
    out.append(_ev("security", "av_products", "Antivirus registered with Windows Security (on)", on, "",
                   "root/SecurityCenter2 AntiVirusProduct", reliability="reported"))
    if data.get("AVError"):
        out.append(_ev("security", "av_error", "Security Center query", str(data["AVError"])[:160], "",
                       "root/SecurityCenter2", reliability="reported"))
    d = data.get("Defender")
    if isinstance(d, dict):
        out += [
            _ev("security", "defender_service", "Microsoft Defender antivirus service running", bool(d.get("Service")),
                "", "Get-MpComputerStatus", reliability="reported"),
            _ev("security", "defender_realtime", "Defender real-time protection on", bool(d.get("RealTime")), "",
                "Get-MpComputerStatus", reliability="reported"),
            _ev("security", "defender_mode", "Defender running mode", d.get("Mode") or "Unknown", "",
                "Get-MpComputerStatus", reliability="reported"),
            _ev("security", "defender_sig_age", "Defender signature age", int(d.get("SigAge") or 0), "days",
                "Get-MpComputerStatus", reliability="reported"),
            _ev("security", "defender_quick_age", "Days since last Defender quick scan", int(d.get("QuickAge") or 0),
                "days", "Get-MpComputerStatus", reliability="reported"),
        ]
    else:
        out.append(_ev("security", "defender_error", "Microsoft Defender status",
                       f"Not available: {str(data.get('DefenderError') or 'no answer')[:160]}", "",
                       "Get-MpComputerStatus", reliability="reported"))
    fw = [f for f in as_list(data.get("Firewall")) if isinstance(f, dict)]
    if fw:
        out.append(_ev("security", "firewall_off", "Firewall profiles switched off",
                       [f.get("Name") for f in fw if not f.get("Enabled")], "", "Get-NetFirewallProfile",
                       reliability="reported"))
    threats = [t for t in as_list(data.get("Threats")) if isinstance(t, dict)]
    if "ThreatError" not in data:
        out.append(_ev("security", "threats_active", "Threats Defender reports as still active",
                       [t.get("ThreatName") or "Unknown" for t in threats if t.get("IsActive")], "", "Get-MpThreat",
                       reliability="reported"))
        out.append(_ev("security", "threats_history", "Threats in Defender's history",
                       [t.get("ThreatName") or "Unknown" for t in threats], "", "Get-MpThreat",
                       reliability="reported"))
    return out


_DISK_PS = (r"Get-PhysicalDisk | Select-Object FriendlyName, MediaType, @{n='Health';e={[string]$_.HealthStatus}}, "
            r"@{n='Op';e={($_.OperationalStatus -join ',')}} | ConvertTo-Json -Compress")


def provide_hardware(ctx, opts, report):
    out = []
    batt = None
    try:
        batt = psutil.sensors_battery()
    except (psutil.Error, OSError, AttributeError, NotImplementedError):
        pass
    if batt is not None:
        out.append(_ev("hardware", "battery", "Battery charge", round(batt.percent), "%", "psutil.sensors_battery"))
        out.append(_ev("hardware", "on_ac", "Plugged in", bool(batt.power_plugged), "", "psutil.sensors_battery"))
    if not IS_WINDOWS:
        if not out:
            raise Unavailable("Drive health and battery details need Windows")
        return out, "Battery only: drive health needs Windows"
    report("Reading drive health (SMART status reported by Windows)…", 40)
    disks = [d for d in as_list(powershell_json(_DISK_PS, timeout=60)) if isinstance(d, dict)]
    if disks:
        out.append(_ev("hardware", "disks", "Physical drives",
                       [f"{d.get('FriendlyName')}: {d.get('Health')}" for d in disks], "", "Get-PhysicalDisk",
                       reliability="reported"))
        out.append(_ev("hardware", "disks_unhealthy", "Drives Windows reports as not healthy",
                       [f"{d.get('FriendlyName')}: {d.get('Health')} ({d.get('Op')})" for d in disks
                        if str(d.get("Health")).lower() not in ("healthy", "0")], "", "Get-PhysicalDisk",
                       reliability="reported"))
    return out


def _tcp_time(host, port, timeout=3.0):
    t = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout):
        return (time.perf_counter() - t) * 1000


NET_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443)]
DNS_NAME = "www.msftconnecttest.com"


def provide_network(ctx, opts, report):
    out = []
    stats = psutil.net_if_stats()
    up = [n for n, s in stats.items() if s.isup and not n.lower().startswith(("lo", "loopback"))]
    out.append(_ev("network", "adapters_up", "Network adapters connected", up, "", "psutil.net_if_stats"))
    report("Checking name lookup (DNS)…", 20)
    try:
        t = time.perf_counter()
        socket.getaddrinfo(DNS_NAME, 443)
        out.append(_ev("network", "dns_ok", f"DNS lookup of {DNS_NAME} works", True, "", "socket.getaddrinfo"))
        out.append(_ev("network", "dns_ms", "DNS lookup time", round((time.perf_counter() - t) * 1000), "ms",
                       "socket.getaddrinfo"))
    except OSError as exc:
        out.append(_ev("network", "dns_ok", f"DNS lookup of {DNS_NAME} works", False, "",
                       f"socket.getaddrinfo ({exc.__class__.__name__})"))
    times, fails = [], 0
    attempts = int(opts.get("net_attempts", 3))
    for host, port in NET_TARGETS:
        for i in range(attempts):
            _check(ctx)
            report(f"Timing connections to {host} ({i + 1} of {attempts})…", 40 + 25 * NET_TARGETS.index((host, port)))
            try:
                times.append(_tcp_time(host, port))
            except OSError:
                fails += 1
    total = attempts * len(NET_TARGETS)
    out.append(_ev("network", "tcp_fail_pct", "Connection attempts that failed", round(100 * fails / total), "%",
                   "TCP connect to 1.1.1.1:443 and 8.8.8.8:443 (no data sent)", 0, total))
    if times:
        out.append(_ev("network", "tcp_ms", "Average connection time", round(sum(times) / len(times)), "ms",
                       "TCP connect to 1.1.1.1:443 and 8.8.8.8:443", 0, len(times)))
    return out


PROVIDERS = {
    "performance": provide_performance,
    "storage": provide_storage,
    "startup": provide_startup,
    "windows": provide_windows,
    "security": provide_security,
    "hardware": provide_hardware,
    "network": provide_network,
}

MODES = {
    "quick": {"label": "Quick Scan", "providers": list(PROVIDERS), "opts": {"seconds": 8, "tasks": False}},
    "full": {"label": "Full Diagnosis", "providers": list(PROVIDERS),
             "opts": {"seconds": 30, "tasks": True, "net_attempts": 5}},
}


# =========================================================================== rules
def _v(ev: dict, eid: str, default=None):
    e = ev.get(eid)
    return default if e is None else e.value


def _sev(value, high, med, above=True):
    if value is None:
        return None
    if (value >= high) if above else (value <= high):
        return "high"
    if (value >= med) if above else (value <= med):
        return "medium"
    return None


def _conf(ev: dict, eid: str) -> str:
    e = ev.get(eid)
    if e is None:
        return "low"
    return "high" if e.samples >= 20 else "medium"


def rule_memory(ev):
    mem = _v(ev, "performance.mem_avg")
    sev = _sev(mem, THRESHOLDS["mem_high"], THRESHOLDS["mem_med"])
    if not sev:
        return None
    swap = _v(ev, "performance.swap_avg")
    lat = _v(ev, "performance.disk_latency")
    ops = (_v(ev, "performance.disk_ops", 0) or 0) * (ev["performance.mem_avg"].window_s or 1)
    paging = (swap is not None and swap >= THRESHOLDS["swap_med"]) or (
        lat is not None and ops >= THRESHOLDS["disk_min_ops"] and lat >= THRESHOLDS["disk_latency_med"])
    ids = ["performance.mem_avg", "performance.mem_total", "performance.top_mem"]
    ids += [i for i in ("performance.swap_avg", "performance.disk_latency") if i in ev]
    total = _v(ev, "performance.mem_total", 0)
    title = "Memory pressure with heavy paging to disk" if paging else "Memory is nearly full"
    return Finding(
        "memory_pressure", "Performance", title, "high" if paging else sev,
        "confirmed", _conf(ev, "performance.mem_avg"),
        what=f"On average {mem:.0f}% of the {human_size(total)} of memory was in use while we watched. When memory "
             "runs out Windows moves data to the disk (paging), which makes everything feel slow."
             + (" The page file and disk response time show this is happening now." if paging else ""),
        why=f"Average memory use {mem:.0f}% (threshold {THRESHOLDS['mem_med']:.0f}%)"
            + (f"; page file in use {swap:.0f}%" if swap is not None else "")
            + (f"; disk response {lat:.0f} ms per operation" if lat is not None else "") + ".",
        evidence_ids=ids,
        alternatives=["A single app with a memory leak (check the largest processes)",
                      "Browser with many tabs open", f"Too little RAM for this workload ({human_size(total)} installed)"],
        next_steps=["Close or restart the largest processes listed in Evidence", "Check startup apps that stay running",
                    "If it happens daily, more RAM is the real fix"],
        links=[["View processes", "processes", {}], ["Review startup apps", "startup", {}]],
        verify=["performance.mem_avg"])


def rule_disk_latency(ev, merged: set):
    if "memory_pressure" in merged:
        return None   # already explained by memory pressure: one root cause, one card
    lat = _v(ev, "performance.disk_latency")
    if lat is None:
        return None
    ops = (_v(ev, "performance.disk_ops", 0) or 0) * (ev["performance.disk_latency"].window_s or 1)
    if ops < THRESHOLDS["disk_min_ops"]:
        return None
    sev = _sev(lat, THRESHOLDS["disk_latency_high"], THRESHOLDS["disk_latency_med"])
    if not sev:
        return None
    return Finding(
        "disk_latency", "Performance", "Slow disk response", sev, "suspected", "medium",
        what=f"Disk operations took {lat:.0f} ms on average. Healthy SSDs answer in a few ms; spinning drives in "
             "roughly 10–20 ms. Slow responses make apps and boot feel sluggish.",
        why=f"Average {lat:.0f} ms per operation over {ops:.0f} operations (threshold "
            f"{THRESHOLDS['disk_latency_med']:.0f} ms). Marked suspected because a short burst (updates, antivirus "
            "scan) can cause this temporarily.",
        evidence_ids=["performance.disk_latency", "performance.disk_ops"],
        alternatives=["Windows Update or an antivirus scan running in the background", "An old spinning hard drive",
                      "A failing drive (check drive health)", "Drive nearly full"],
        next_steps=["Run Full Diagnosis again when the PC is idle", "Check drive health on Maintenance",
                    "Back up important files before any heavy repair if the drive health is not 'Healthy'"],
        links=[["Check drive health", "maintenance", {"search": "drive health"}]],
        verify=["performance.disk_latency"])


def rule_cpu(ev):
    cpu = _v(ev, "performance.cpu_avg")
    sev = _sev(cpu, THRESHOLDS["cpu_sustained_high"], THRESHOLDS["cpu_sustained_med"])
    if not sev:
        return None
    tops = _v(ev, "performance.top_cpu", []) or []
    lead = tops[0].split(" (PID")[0] if tops else ""
    links = [["View process" + (f" “{lead}”" if lead else "es"), "processes", {"search": lead} if lead else {}]]
    return Finding(
        "cpu_sustained", "Performance", "Processor stayed busy" + (f", mostly {lead}" if lead else ""), sev,
        "confirmed", _conf(ev, "performance.cpu_avg"),
        what=f"The processor averaged {cpu:.0f}% while we watched. Sustained high CPU makes everything respond slowly "
             "and laptops run hot and loud.",
        why=f"Average {cpu:.0f}% over the sampling window (threshold {THRESHOLDS['cpu_sustained_med']:.0f}%). "
            + (f"Busiest: {', '.join(tops[:3])}." if tops else "No single process stood out."),
        evidence_ids=["performance.cpu_avg", "performance.cpu_peak", "performance.cpu_busy_share", "performance.top_cpu"],
        alternatives=["Windows Update, indexing or an antivirus scan (usually temporary)", "A game or app you're using",
                      "Something you don't recognise: investigate before assuming it's malware"],
        next_steps=["Look at the busiest process; if you don't recognise it, open its file location and check the "
                    "publisher", "Run the scan again later to see if it's sustained"],
        links=links, verify=["performance.cpu_avg"])


def rule_space(ev):
    free = _v(ev, "storage.sys_free_pct")
    sev = _sev(free, THRESHOLDS["sys_free_high"], THRESHOLDS["sys_free_med"], above=False)
    if not sev:
        return None
    junk_b = _v(ev, "storage.junk_bytes")
    actions = ["clean_junk"] if junk_b and junk_b >= 100e6 else []
    return Finding(
        "low_space", "Storage", "System drive is running out of space", sev, "confirmed", "high",
        what=f"Only {free:.0f}% ({human_size(_v(ev, 'storage.sys_free_bytes', 0))}) is free on the Windows drive. "
             "Below about 15% Windows updates can fail and the PC slows down.",
        why=f"Free space {free:.0f}% (thresholds: {THRESHOLDS['sys_free_high']:.0f}% high, "
            f"{THRESHOLDS['sys_free_med']:.0f}% medium).",
        evidence_ids=["storage.sys_free_pct", "storage.sys_free_bytes"] + (["storage.junk_bytes"] if junk_b is not None else []),
        alternatives=["Large personal files (videos, downloads)", "Old Windows update files", "Games"],
        next_steps=["Clean safe junk first (no personal files touched)", "Then look for large files you no longer need",
                    "Run Windows Disk Cleanup > Clean up system files for Windows.old"],
        actions=actions, links=[["Find large files", "large", {}], ["Disk analyzer", "disk", {}]],
        verify=["storage.sys_free_bytes"])


def rule_junk(ev):
    b = _v(ev, "storage.junk_bytes")
    if b is None or b < THRESHOLDS["junk_low"]:
        return None
    return Finding(
        "junk", "Storage", f"{human_size(b)} of temporary files and caches can be cleaned",
        "medium" if b >= THRESHOLDS["junk_med"] else "low", "confirmed", "high",
        what="Temp folders, update caches, crash dumps and app caches have built up. They are safe to remove: "
             "apps rebuild caches when needed. Your Recycle Bin is not touched.",
        why=f"Measured {human_size(b)} across Remedy's default safe junk categories (threshold "
            f"{human_size(THRESHOLDS['junk_low'])}). Temp files younger than 24 hours and files in use are skipped.",
        evidence_ids=["storage.junk_bytes"],
        next_steps=["Add 'Clean safe junk' to the repair plan", "Or pick categories yourself in Junk Cleaner"],
        actions=["clean_junk"], links=[["Open Junk Cleaner", "cleaner", {}]],
        verify=["storage.junk_bytes", "storage.sys_free_bytes"])


def rule_other_drives(ev):
    low = _v(ev, "storage.other_low") or []
    if not low:
        return None
    return Finding("other_drives_full", "Storage", "Another drive is almost full", "low", "confirmed", "high",
                   what="These drives have under 5% free: " + "; ".join(low) + ".",
                   why=f"Free space under {THRESHOLDS['other_free_low']:.0f}% on a fixed drive.",
                   evidence_ids=["storage.other_low"], next_steps=["Move or delete files you don't need there"],
                   links=[["Disk analyzer", "disk", {}]])


def rule_startup(ev):
    n = _v(ev, "startup.enabled_count")
    if n is None or n < THRESHOLDS["startup_low"]:
        return None
    names = _v(ev, "startup.enabled_names", []) or []
    return Finding(
        "startup_load", "Performance", f"{n} programs start with Windows",
        "medium" if n >= THRESHOLDS["startup_med"] else "low", "needs_review", "medium",
        what="Each program that starts with Windows adds to boot time and keeps using memory in the background. "
             "Which ones are needed is a judgement call, so Remedy lists them instead of switching any off.",
        why=f"{n} enabled entries (review threshold {THRESHOLDS['startup_low']}). Remedy can't measure each one's "
            "launch time, so this is a count, not a measured delay.",
        evidence_ids=["startup.enabled_count", "startup.enabled_names"],
        alternatives=["Some entries are drivers' helper apps (touchpad, audio) that should stay on"],
        next_steps=["Disable ones you don't need right after sign-in (chat apps, updaters, launchers)",
                    "Keep security software and your PC maker's touchpad/audio helpers"] +
                   ([f"Enabled now: {', '.join(names[:8])}" + ("…" if len(names) > 8 else "")] if names else []),
        links=[["Review startup apps", "startup", {}]], verify=["startup.enabled_count"])


def rule_uptime(ev):
    d = _v(ev, "windows.uptime_days")
    if d is None or d < THRESHOLDS["uptime_days"]:
        return None
    return Finding("uptime", "Windows", f"No restart in {d:.0f} days", "info", "confirmed", "high",
                   what="A restart finishes installed updates and clears memory used by long-running apps.",
                   why=f"Last boot {d:.1f} days ago (threshold {THRESHOLDS['uptime_days']:.0f} days). On Windows with "
                       "Fast Startup, 'Shut down' does not count as a restart.",
                   evidence_ids=["windows.uptime_days"], next_steps=["Restart the PC when convenient (Start > Power > Restart)"],
                   verify=["windows.uptime_days"])


def rule_pending_reboot(ev):
    r = _v(ev, "windows.pending_reboot") or []
    if not r:
        return None
    return Finding("pending_reboot", "Windows", "Windows is waiting for a restart", "low", "confirmed", "high",
                   what="Updates or repairs have been installed and need a restart to finish.",
                   why="Windows has set: " + ", ".join(r) + ".", evidence_ids=["windows.pending_reboot"],
                   next_steps=["Save your work and restart before running repairs"])


def rule_crashes(ev):
    ks = _v(ev, "windows.unexpected_shutdowns") or 0
    bc = _v(ev, "windows.stop_errors") or 0
    if ks + bc < THRESHOLDS["crash_events"]:
        return None
    sev = "high" if bc >= 2 or ks >= 3 else "medium"
    return Finding(
        "instability", "Windows", "Recent crashes or unexpected shutdowns", sev, "confirmed", "high",
        what=f"In the last 14 days Windows logged {bc} stop error(s) (blue screens) and {ks} unexpected shutdown(s).",
        why="Counted from the System event log (Kernel-Power 41 and WER event 1001).",
        evidence_ids=["windows.unexpected_shutdowns", "windows.stop_errors"],
        alternatives=["Power loss or holding the power button (counts as an unexpected shutdown)",
                      "Driver problems (graphics, Wi-Fi, storage)", "Overheating", "Failing RAM or drive"],
        next_steps=["Open Reliability history to see what failed and when", "Check device problems and drive health",
                    "Run the Windows memory test if stop errors repeat"],
        links=[["Open Maintenance (Reliability history, memory test)", "maintenance", {"search": "reliability"}]])


def rule_devices(ev):
    devs = _v(ev, "windows.device_problems") or []
    if not devs:
        return None
    return Finding("device_problems", "Hardware", f"{len(devs)} device(s) report a problem", "medium", "confirmed",
                   "high", what="Windows reports these devices aren't working properly: " + "; ".join(devs[:6]) + ".",
                   why="ConfigManagerErrorCode is not 0 (e.g. 28 = no driver, 22 = disabled, 10 = can't start).",
                   evidence_ids=["windows.device_problems"],
                   alternatives=["A disabled device you don't use (code 22) is harmless"],
                   next_steps=["Check Windows Update > Optional updates for drivers",
                               "Get drivers from the PC maker's support site, not driver-pack tools"],
                   links=[["Open Windows Update", "maintenance", {"search": "windows update"}]])


def rule_disk_health(ev):
    bad = _v(ev, "hardware.disks_unhealthy") or []
    if not bad:
        return None
    return Finding("disk_health", "Hardware", "A drive reports it is not healthy", "high", "confirmed", "high",
                   what="Windows reports a drive problem: " + "; ".join(bad) + ". Failing drives can lose data.",
                   why="Get-PhysicalDisk HealthStatus is not 'Healthy'.", evidence_ids=["hardware.disks_unhealthy"],
                   next_steps=["Back up important files NOW, before any heavy scan or repair",
                               "Then check details with 'Check drive health' and plan a replacement"],
                   links=[["Check drive health", "maintenance", {"search": "drive health"}]])


def rule_av(ev):
    if "security.defender_realtime" not in ev and "security.av_products" not in ev:
        return None
    others = [n for n in (_v(ev, "security.av_products") or []) if "defender" not in n.lower()]
    rt = _v(ev, "security.defender_realtime")
    if rt is False and not others:
        return Finding("av_off", "Security", "No active antivirus protection found", "high", "confirmed", "high",
                       what="Microsoft Defender's real-time protection is off and no other antivirus is registered.",
                       why="Get-MpComputerStatus RealTimeProtectionEnabled = False; Security Center lists no other "
                           "active antivirus.",
                       evidence_ids=[i for i in ("security.defender_realtime", "security.av_products") if i in ev],
                       next_steps=["Open Windows Security and turn real-time protection back on",
                                   "If it turns itself off, it may be managed by an organisation or tampered with"],
                       links=[["Open Security Center", "security", {}]], verify=["security.defender_realtime"])
    return None


def rule_signatures(ev):
    age = _v(ev, "security.defender_sig_age")
    if age is None or _v(ev, "security.defender_realtime") is False or age < THRESHOLDS["sig_age_days"]:
        return None
    return Finding("defender_stale", "Security", f"Virus definitions are {age} days old", "medium", "confirmed", "high",
                   what="Microsoft Defender hasn't updated its virus definitions recently, so it may miss new threats.",
                   why=f"AntivirusSignatureAge = {age} days (threshold {THRESHOLDS['sig_age_days']}).",
                   evidence_ids=["security.defender_sig_age"], next_steps=["Update definitions (repair plan)"],
                   actions=["update_defender"], verify=["security.defender_sig_age"])


def rule_threats(ev):
    active = _v(ev, "security.threats_active") or []
    hist = _v(ev, "security.threats_history") or []
    if active:
        return Finding("threats_active", "Security", "Defender reports an active threat", "high", "confirmed", "high",
                       what="Microsoft Defender itself reports these as still active: " + ", ".join(active[:5]) + ".",
                       why="Reported by the provider (Get-MpThreat IsActive = True). This is Defender's verdict, "
                           "not a Remedy guess.",
                       evidence_ids=["security.threats_active"],
                       next_steps=["Open Windows Security > Protection history and follow Defender's recommended action",
                                   "Run a Defender full or offline scan from Windows Security"],
                       links=[["Open Security Center", "security", {}]])
    if hist:
        return Finding("threats_history", "Security", f"Defender has handled {len(hist)} threat(s) before", "info",
                       "confirmed", "high",
                       what="Defender's history lists: " + ", ".join(hist[:5]) + ". None is reported as still active.",
                       why="Reported by the provider (Get-MpThreat).", evidence_ids=["security.threats_history"],
                       next_steps=["Review Protection history in Windows Security if these are unexpected"],
                       links=[["Open Security Center", "security", {}]])
    return None


def rule_firewall(ev):
    off = _v(ev, "security.firewall_off") or []
    if not off:
        return None
    return Finding("firewall_off", "Security", "Windows Firewall is off for: " + ", ".join(off), "medium", "confirmed",
                   "high", what="With the firewall off, other devices on the network can reach services on this PC.",
                   why="Get-NetFirewallProfile Enabled = False.", evidence_ids=["security.firewall_off"],
                   alternatives=["A third-party firewall may be managing this instead"],
                   next_steps=["Turn it back on in Windows Security > Firewall & network protection, unless another "
                               "firewall product is in use"],
                   links=[["Open Security Center", "security", {}]])


def rule_network(ev):
    if "network.tcp_fail_pct" not in ev:
        return None
    fail = _v(ev, "network.tcp_fail_pct")
    dns = _v(ev, "network.dns_ok")
    ms = _v(ev, "network.tcp_ms")
    if fail >= 100 and not dns:
        return Finding("offline", "Network", "No internet connection", "medium", "confirmed", "high",
                       what="Name lookups and test connections both failed, so this PC can't reach the internet right "
                            "now (or a firewall/proxy blocks it).",
                       why=f"DNS lookup failed; {fail}% of TCP connections to 1.1.1.1 / 8.8.8.8 failed.",
                       evidence_ids=["network.dns_ok", "network.tcp_fail_pct", "network.adapters_up"],
                       alternatives=["Wi-Fi/cable unplugged", "Captive portal (hotel / café sign-in page)",
                                     "Company proxy or firewall"],
                       next_steps=["Check the cable or Wi-Fi", "Flush DNS, then test again"],
                       actions=["flush_dns"], verify=["network.dns_ok", "network.tcp_fail_pct"])
    if dns is False:
        return Finding("dns_failure", "Network", "Name lookups (DNS) are failing", "medium", "confirmed", "high",
                       what="Websites can't be found by name, even though the network connection itself works.",
                       why=f"DNS lookup of {DNS_NAME} failed while {100 - fail}% of direct connections succeeded.",
                       evidence_ids=["network.dns_ok", "network.tcp_fail_pct"],
                       next_steps=["Flush the DNS cache (repair plan)", "Check DNS server settings on the adapter"],
                       actions=["flush_dns"], verify=["network.dns_ok"])
    if fail >= 34 or (ms is not None and ms >= THRESHOLDS["net_latency_ms"]):
        return Finding("network_quality", "Network", "Unreliable or slow connection", "low", "suspected", "medium",
                       what=f"{fail}% of test connections failed"
                            + (f" and connections took {ms} ms on average." if ms is not None else "."),
                       why=f"Thresholds: any failures above a third, or over {THRESHOLDS['net_latency_ms']:.0f} ms. "
                           "Based on a handful of attempts, so it's a lead, not a verdict.",
                       evidence_ids=[i for i in ("network.tcp_fail_pct", "network.tcp_ms") if i in ev],
                       alternatives=["Weak Wi-Fi signal", "Busy network (downloads, streaming)", "VPN"],
                       next_steps=["Run Full Diagnosis again closer to the router", "Use the Wi-Fi report on Maintenance"],
                       links=[["Wi-Fi report", "maintenance", {"search": "wi-fi"}]], verify=["network.tcp_fail_pct"])
    return None


def analyze(evidence: dict) -> list:
    """Apply every rule. evidence: {id: Evidence}. Returns findings sorted by severity."""
    findings = []
    merged = set()
    mem = rule_memory(evidence)
    if mem:
        findings.append(mem)
        if mem.severity == "high" and "paging" in mem.title:
            merged.add("memory_pressure")
    for rule in (rule_cpu, rule_space, rule_junk, rule_other_drives, rule_startup, rule_crashes, rule_devices,
                 rule_disk_health, rule_av, rule_signatures, rule_threats, rule_firewall, rule_network,
                 rule_pending_reboot, rule_uptime):
        try:
            f = rule(evidence)
        except Exception as exc:  # noqa: BLE001 - one bad rule must not hide the others
            log.warning("Diagnostic rule %s failed: %s", rule.__name__, exc)
            continue
        if f:
            findings.append(f)
    f = rule_disk_latency(evidence, merged)
    if f:
        findings.append(f)
    findings.sort(key=lambda x: (SEVERITY_ORDER.get(x.severity, 9), x.category, x.title))
    return findings


# =========================================================================== orchestration
def new_session(mode: str) -> dict:
    info = osinfo.detect()
    return {"schema": SCHEMA_VERSION, "type": "diagnostic", "id": time.strftime("%Y%m%d-%H%M%S") + "-" +
            uuid.uuid4().hex[:6], "mode": mode, "started": time.time(), "finished": 0.0, "status": "running",
            "device": {"os": info.summary(), "form": info.form, "admin": is_admin()},
            "providers": {}, "evidence": {}, "findings": [], "dismissed": {}, "repairs": [], "verification": []}


def run_providers(ctx, names: list, opts: dict, on_status=None, emit=None) -> tuple:
    """Run providers one by one, isolated. Returns ({evidence id: Evidence}, {name: ProviderStatus}).

    on_status(ProviderStatus) is called (from the worker thread) when a provider changes state; emit(text, pct)
    reports overall progress."""
    evidence, statuses = {}, {n: ProviderStatus(n) for n in names}
    emit = emit or (lambda *_: None)
    for i, name in enumerate(names):
        st = statuses[name]
        if ctx is not None and ctx.cancelled():
            st.status, st.message = "cancelled", "Not run: scan cancelled"
            if on_status:
                on_status(st)
            continue
        st.status = "running"
        if on_status:
            on_status(st)
        started = time.monotonic()

        def report(text, pct, _i=i):
            emit(text, int((_i + max(0, min(100, pct)) / 100) * 100 / len(names)))

        try:
            items = PROVIDERS[name](ctx, opts, report)
            note = ""
            if isinstance(items, tuple):          # (readings, why only part of the checks could run)
                items, note = items
            for e in items:
                evidence[e.id] = e
            st.status, st.message = ("partial", note) if note else ("complete", f"{len(items)} readings")
        except TaskCancelled:
            st.status, st.message = "cancelled", "Stopped: scan cancelled (readings so far kept)"
        except Unavailable as exc:
            st.status, st.message = "unavailable", str(exc)
        except Exception as exc:  # noqa: BLE001 - a broken provider is reported, never fatal
            log.warning("Diagnostic provider %s failed: %s", name, exc)
            st.status, st.message = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
        st.duration = time.monotonic() - started
        if on_status:
            on_status(st)
    return evidence, statuses


def finish_session(session: dict, evidence: dict, statuses: dict, cancelled: bool = False) -> dict:
    session["evidence"] = {k: asdict(v) for k, v in evidence.items()}
    session["providers"] = {k: asdict(v) for k, v in statuses.items()}
    session["findings"] = [asdict(f) for f in analyze(evidence)]
    session["finished"] = time.time()
    session["status"] = "cancelled" if cancelled else (
        "partial" if any(s.status != "complete" for s in statuses.values()) else "complete")
    return session


def evidence_from_session(session: dict) -> dict:
    return {k: Evidence(**v) for k, v in (session.get("evidence") or {}).items()}


def not_assessed(session: dict) -> list:
    """[(provider label, reason)] for providers that didn't produce a full result."""
    out = []
    for name, st in (session.get("providers") or {}).items():
        if st.get("status") in ("unavailable", "failed", "cancelled", "partial"):
            out.append((PROVIDER_LABEL.get(name, name), st.get("message") or st.get("status")))
    return out


def summary_counts(session: dict) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    dismissed = session.get("dismissed") or {}
    for f in session.get("findings") or []:
        if f["id"] not in dismissed:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return counts
