"""Junk categories: temp files, caches, logs, crash dumps, Recycle Bin."""
from __future__ import annotations

import fnmatch
import glob
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

from .scanner import _is_link, walk_entries
from .utils import IS_WINDOWS, empty_recycle_bin, human_size, log, recycle_bin_info


@dataclass
class JunkTarget:
    folder: str
    pattern: str = "*"
    recursive: bool = True
    min_age_hours: float = 0.0


@dataclass
class JunkCategory:
    key: str
    name: str
    description: str
    targets: list = field(default_factory=list)
    group: str = "System"
    default: bool = True
    admin: bool = False
    special: str = ""
    warning: str = ""
    size: int = 0
    count: int = 0
    scanned: bool = False
    files: list = field(default_factory=list, repr=False)


def _dirs(*patterns) -> list:
    out = []
    for pat in patterns:
        out.extend(p for p in glob.glob(pat) if os.path.isdir(p))
    return out


def _chromium_targets(root: str) -> list:
    targets = []
    esc = glob.escape(root)
    for sub in ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache"):
        targets += [JunkTarget(d) for d in _dirs(os.path.join(esc, "*", sub))]
    for sub in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            targets.append(JunkTarget(d))
    return targets


def build_categories() -> list:
    if not IS_WINDOWS:
        return _posix_categories()
    env = os.environ
    local = env.get("LOCALAPPDATA", "")
    roaming = env.get("APPDATA", "")
    windir = env.get("SystemRoot", r"C:\Windows")
    progdata = env.get("ProgramData", r"C:\ProgramData")
    temp = env.get("TEMP", os.path.join(local, "Temp"))
    cats: list = []

    def add(key, name, desc, targets, **kw):
        cats.append(JunkCategory(key, name, desc, targets, **kw))

    add("user_temp", "User temporary files",
        "Leftovers from apps and installers older than 24 hours. Files in use are skipped.",
        [JunkTarget(temp, min_age_hours=24)])
    add("win_temp", "Windows temporary files", "System temp folder (items older than 24 hours).",
        [JunkTarget(os.path.join(windir, "Temp"), min_age_hours=24)], admin=True)
    add("wu_cache", "Windows Update download cache",
        "Update packages that are already installed. Windows re-downloads anything it still needs.",
        [JunkTarget(os.path.join(windir, "SoftwareDistribution", "Download"), min_age_hours=24)], admin=True)
    add("delivery_opt", "Delivery Optimization cache", "Update chunks cached to share with other PCs.",
        [JunkTarget(os.path.join(windir, r"ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows"
                                         r"\DeliveryOptimization\Cache"))], admin=True)
    add("error_reports", "Windows error reports", "Queued and archived crash reports.",
        [JunkTarget(os.path.join(progdata, r"Microsoft\Windows\WER\ReportArchive")),
         JunkTarget(os.path.join(progdata, r"Microsoft\Windows\WER\ReportQueue")),
         JunkTarget(os.path.join(local, r"Microsoft\Windows\WER"))], admin=True)
    add("crash_dumps", "Crash dumps", "Memory dumps from crashed apps and blue screens (only useful for debugging).",
        [JunkTarget(os.path.join(local, "CrashDumps")), JunkTarget(os.path.join(windir, "Minidump")),
         JunkTarget(windir, "MEMORY.DMP", recursive=False),
         JunkTarget(os.path.join(windir, "LiveKernelReports"), "*.dmp")], admin=True)
    add("thumbnails", "Thumbnail & icon cache", "Explorer rebuilds these automatically. Locked files are skipped.",
        [JunkTarget(os.path.join(local, r"Microsoft\Windows\Explorer"), "thumbcache_*.db", recursive=False),
         JunkTarget(os.path.join(local, r"Microsoft\Windows\Explorer"), "iconcache_*.db", recursive=False)])
    add("win_logs", "Old Windows log files", "Setup and servicing logs older than 7 days.",
        [JunkTarget(os.path.join(windir, "Logs"), "*.log", min_age_hours=168),
         JunkTarget(os.path.join(windir, "Panther"), "*.log", min_age_hours=168)], admin=True, default=False)
    add("recycle_bin", "Recycle Bin", "Everything currently in the Recycle Bin on all drives.",
        [], special="recycle")

    browsers = {
        "Google Chrome": os.path.join(local, r"Google\Chrome\User Data"),
        "Microsoft Edge": os.path.join(local, r"Microsoft\Edge\User Data"),
        "Brave": os.path.join(local, r"BraveSoftware\Brave-Browser\User Data"),
        "Vivaldi": os.path.join(local, r"Vivaldi\User Data"),
    }
    for label, root in browsers.items():
        if os.path.isdir(root):
            add("browser_" + label.lower().replace(" ", "_"), f"{label} cache",
                "Cached web content only. Passwords, cookies, history and tabs are untouched.",
                _chromium_targets(root), group="Browsers", warning="Close the browser first for best results")
    opera = [JunkTarget(d) for d in _dirs(os.path.join(glob.escape(local), "Opera Software", "*", "Cache"))]
    if opera:
        add("browser_opera", "Opera cache", "Cached web content only.", opera, group="Browsers",
            warning="Close the browser first for best results")
    firefox = [JunkTarget(d) for d in _dirs(os.path.join(glob.escape(local), "Mozilla", "Firefox", "Profiles", "*", "cache2"))]
    if firefox:
        add("browser_firefox", "Firefox cache", "Cached web content only. Logins and history are untouched.",
            firefox, group="Browsers", warning="Close the browser first for best results")

    app_dirs = []
    for base, subs in [(os.path.join(roaming, "discord"), ("Cache", "Code Cache", "GPUCache")),
                       (os.path.join(roaming, "Slack"), ("Cache", "Code Cache", "GPUCache")),
                       (os.path.join(roaming, r"Microsoft\Teams"), ("Cache", "Code Cache", "GPUCache", "tmp")),
                       (os.path.join(roaming, "Code"), ("Cache", "CachedData", "Code Cache", "GPUCache")),
                       (os.path.join(roaming, "Spotify"), ("Browser",)),
                       (os.path.join(local, "Spotify"), ("Data", "Browser")),
                       (os.path.join(local, "Steam"), ("htmlcache",))]:
        app_dirs += [os.path.join(base, s) for s in subs if os.path.isdir(os.path.join(base, s))]
    add("app_caches", "App caches", "Discord, Slack, Teams, VS Code, Spotify and Steam web caches.",
        [JunkTarget(d) for d in app_dirs], group="Apps", warning="Close those apps first")
    add("shader_cache", "DirectX & GPU shader caches",
        "Rebuilt automatically; the first launch of a game may stutter briefly.",
        [JunkTarget(d) for d in [os.path.join(local, "D3DSCache"), os.path.join(local, r"NVIDIA\DXCache"),
                                 os.path.join(local, r"NVIDIA\GLCache"), os.path.join(local, r"AMD\DxCache"),
                                 os.path.join(local, r"AMD\DxcCache"), os.path.join(local, r"AMD\VkCache"),
                                 os.path.join(local, r"Intel\ShaderCache")]],
        group="Apps", default=False)
    add("dev_caches", "Developer package caches", "pip, npm, Yarn, NuGet and Gradle download caches.",
        [JunkTarget(d) for d in [os.path.join(local, r"pip\cache"), os.path.join(local, "npm-cache"),
                                 os.path.join(local, r"Yarn\Cache"), os.path.join(local, r"NuGet\v3-cache"),
                                 os.path.join(str(Path.home()), r".gradle\caches")]],
        group="Developer", default=False)
    return cats


def _posix_categories() -> list:
    home = str(Path.home())
    return [
        JunkCategory("user_cache", "User cache (~/.cache)", "Application caches; rebuilt automatically.",
                     [JunkTarget(os.path.join(home, ".cache"), min_age_hours=24)], default=False),
        JunkCategory("tmp", "Temporary files", "Your files in /tmp older than 24 hours.",
                     [JunkTarget("/tmp", min_age_hours=24)]),
        JunkCategory("recycle_bin", "Trash", "Everything currently in the Trash.", [], special="recycle"),
    ]


def _flat(folder: str):
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        yield e.path, st.st_size, st.st_mtime
                except OSError:
                    continue
    except OSError:
        return


def scan_category(cat: JunkCategory, ctx=None) -> JunkCategory:
    cat.size = cat.count = 0
    cat.files = []
    cat.scanned = True
    if cat.special == "recycle":
        cat.size, cat.count = recycle_bin_info()
        return cat
    now = time.time()
    seen: set = set()
    for t in cat.targets:
        if not os.path.isdir(t.folder):
            continue
        cutoff = now - t.min_age_hours * 3600 if t.min_age_hours else None
        source = walk_entries([t.folder], ctx, skip_system=False, exclusions=[]) if t.recursive else _flat(t.folder)
        pattern = t.pattern.lower()
        for path, size, mtime in source:
            if pattern != "*" and not fnmatch.fnmatch(os.path.basename(path).lower(), pattern):
                continue
            if cutoff and mtime > cutoff:
                continue
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            cat.files.append((path, size))
            cat.size += size
            cat.count += 1
    return cat


def _prune_empty_dirs(root: str) -> None:
    """Remove empty sub-folders of root (never root itself, never through links)."""
    order = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                        if e.is_dir(follow_symlinks=False) and not _is_link(e, st):
                            stack.append(e.path)
                            order.append(e.path)
                    except OSError:
                        continue
        except OSError:
            continue
    for d in reversed(order):
        try:
            os.rmdir(d)
        except OSError:
            pass


def clean_category(cat: JunkCategory, ctx=None) -> tuple:
    """Delete a scanned category's files. Returns (bytes_freed, files_deleted, files_failed)."""
    if cat.special == "recycle":
        size, count = recycle_bin_info()
        ok = empty_recycle_bin()
        log.info("Emptied Recycle Bin (%s)", human_size(size))
        return (size, count, 0) if ok else (0, 0, count)
    freed = deleted = failed = 0
    for i, (path, size) in enumerate(cat.files):
        if ctx is not None and ctx.cancelled():
            break
        try:
            try:
                os.remove(path)
            except PermissionError:
                os.chmod(path, stat.S_IWRITE)
                os.remove(path)
            freed += size
            deleted += 1
        except OSError:
            failed += 1
        if ctx is not None and i % 250 == 0:
            ctx.progress(f"Cleaning {cat.name}… {deleted:,} files removed")
    for t in cat.targets:
        if t.recursive and os.path.isdir(t.folder):
            _prune_empty_dirs(t.folder)
    log.info("Cleaned %s: %s freed, %d files removed, %d skipped (in use)",
             cat.name, human_size(freed), deleted, failed)
    cat.files = []
    return freed, deleted, failed
