"""Shared helpers: platform detection, settings, logging, commands, sizes and safe deletion."""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
APP_NAME = "Remedy"
APP_PUBLISHER = "Noisewater Labs"
APP_VERSION = "1.1.0"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


class TaskCancelled(Exception):
    """Raised inside a background task when the user presses Cancel."""


# --------------------------------------------------------------------------- paths & logging
def _app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(str(Path.home()), ".local", "share")
    path = Path(base) / "Noisewater Labs" / "Remedy"
    path.mkdir(parents=True, exist_ok=True)
    return path


APP_DIR = _app_dir()
LOG_FILE = APP_DIR / "remedy.log"
SETTINGS_FILE = APP_DIR / "settings.json"
BACKUP_FILE = APP_DIR / "tweak_backup.json"

log = logging.getLogger("remedy")


def setup_logging() -> None:
    if getattr(setup_logging, "_done", False):
        return
    setup_logging._done = True  # type: ignore[attr-defined]
    log.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
    except OSError:
        pass


# --------------------------------------------------------------------------- settings
DEFAULT_SETTINGS = {
    "use_recycle_bin": True,
    "restore_point_before_tweaks": True,
    "skip_system_folders": True,
    "exclusions": [],
    "large_file_min_mb": 250,
    "duplicate_min_mb": 1.0,
    "max_large_results": 5000,
    "window_geometry": "",
}


class Settings:
    def __init__(self) -> None:
        self.data = json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            if SETTINGS_FILE.exists():
                loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update({k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            log.warning("Settings file unreadable; using defaults")

    def get(self, key: str):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        try:
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, SETTINGS_FILE)
        except OSError as exc:
            log.error("Could not save settings: %s", exc)


settings = Settings()


# --------------------------------------------------------------------------- formatting
def human_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_time(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"


# --------------------------------------------------------------------------- privileges
def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def relaunch_as_admin() -> bool:
    """Re-launch this app elevated (UAC prompt). Returns True if the new process started."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pythonw):
            exe = pythonw
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


# --------------------------------------------------------------------------- commands
@dataclass
class CmdResult:
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def _oem_encoding() -> str:
    if IS_WINDOWS:
        try:
            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:
            return "cp437"
    return "utf-8"


def decode_output(data: bytes) -> str:
    if not data:
        return ""
    if data.count(b"\x00") > len(data) // 4:  # UTF-16 output (e.g. sfc.exe)
        data = data.replace(b"\x00", b"")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", _oem_encoding()):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")


def run_command(cmd, timeout: float = 300, shell: bool = False) -> CmdResult:
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            timeout=timeout, shell=shell, creationflags=CREATE_NO_WINDOW)
        return CmdResult(proc.returncode, decode_output(proc.stdout).strip())
    except FileNotFoundError as exc:
        return CmdResult(127, f"Command not found: {exc}")
    except subprocess.TimeoutExpired:
        return CmdResult(124, "Timed out")
    except OSError as exc:
        return CmdResult(1, str(exc))


def run_stream(cmd, on_line, should_cancel=lambda: False, shell: bool = False) -> int:
    """Run a command and pass each output line to on_line. Returns the exit code."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            shell=shell, creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        on_line(f"Could not start command: {exc}")
        return 127
    try:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            text = decode_output(raw).replace("\x00", "")
            for part in text.replace("\r\n", "\n").split("\r"):
                part = part.strip()
                if part:
                    on_line(part)
            if should_cancel():
                proc.terminate()
                on_line("Cancelled.")
                break
        proc.wait()
    finally:
        if proc.stdout:
            proc.stdout.close()
    return proc.returncode


def ps_args(script: str) -> list:
    prefix = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "$ProgressPreference='SilentlyContinue'; ")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", prefix + script]


def powershell(script: str, timeout: float = 300) -> CmdResult:
    return run_command(ps_args(script), timeout=timeout)


def powershell_json(script: str, timeout: float = 120):
    res = powershell(script, timeout=timeout)
    text = res.output.strip()
    if not text:
        return None
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        return None
    try:
        return json.loads(text[min(starts):])
    except ValueError:
        log.warning("Could not parse PowerShell JSON output")
        return None


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


# --------------------------------------------------------------------------- shell integration
def open_location(path: str) -> None:
    """Open Explorer with the file selected (or open the folder)."""
    try:
        path = os.path.normpath(path)
        if IS_WINDOWS:
            if os.path.isdir(path):
                subprocess.Popen(["explorer", path])
            else:
                subprocess.Popen(f'explorer /select,"{path}"')
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except OSError as exc:
        log.error("Could not open location %s: %s", path, exc)


def open_target(target: str) -> None:
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", target])
    except OSError as exc:
        log.error("Could not open %s: %s", target, exc)


def system_drive() -> str:
    return (os.environ.get("SystemDrive", "C:") + "\\") if IS_WINDOWS else "/"


def list_drives() -> list:
    import psutil
    drives = []
    for part in psutil.disk_partitions(all=False):
        if not part.fstype or "cdrom" in part.opts:
            continue
        if not IS_WINDOWS and not (part.mountpoint == "/" or part.mountpoint.startswith(("/home", "/mnt", "/media"))):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        drives.append({"mount": part.mountpoint, "device": part.device, "fstype": part.fstype,
                       "total": usage.total, "used": usage.used, "free": usage.free,
                       "percent": usage.percent})
    return drives


# --------------------------------------------------------------------------- protection & deletion
_SYSTEM_FILE_NAMES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr",
                      "dumpstack.log.tmp", "dumpstack.log", "ntuser.dat", "bootnxt"}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _protected_roots() -> list:
    if IS_WINDOWS:
        env = os.environ
        drive = env.get("SystemDrive", "C:") + "\\"
        roots = [env.get("SystemRoot", r"C:\Windows"),
                 env.get("ProgramFiles", r"C:\Program Files"),
                 env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 env.get("ProgramW6432", ""),
                 os.path.join(env.get("ProgramData", r"C:\ProgramData"), "Microsoft"),
                 os.path.join(drive, "System Volume Information"),
                 os.path.join(drive, "Recovery"),
                 os.path.join(drive, "Boot"),
                 os.path.join(drive, "$Recycle.Bin")]
    else:
        roots = ["/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys",
                 "/usr", "/var/lib"]
    return [_norm(r) for r in roots if r]


PROTECTED_ROOTS = _protected_roots()


def protection_reason(path: str):
    """Return why a path must not be deleted by the file tools, or None if it is fine."""
    p = _norm(path)
    if os.path.basename(p).lower() in _SYSTEM_FILE_NAMES:
        return "Windows-managed system file"
    if os.path.dirname(p) == p:
        return "drive root"
    for root in PROTECTED_ROOTS:
        if p == root or p.startswith(root.rstrip(os.sep) + os.sep):
            return f"inside protected system location ({root})"
    return None


def path_size(path: str) -> int:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            total = 0
            for dirpath, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(dirpath, name)).st_size
                    except OSError:
                        pass
            return total
        return os.lstat(path).st_size
    except OSError:
        return 0


def _force_remove(func, path, _exc=None):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_permanently(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_force_remove)
        else:
            shutil.rmtree(path, onerror=_force_remove)
    else:
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


def delete_paths(paths, use_recycle_bin=None, ctx=None) -> dict:
    """Delete files/folders safely. Returns {'deleted': [...], 'freed': int, 'errors': [(path, msg)]}."""
    if use_recycle_bin is None:
        use_recycle_bin = bool(settings.get("use_recycle_bin"))
    result = {"deleted": [], "freed": 0, "errors": [], "recycled": use_recycle_bin}
    send2trash = None
    if use_recycle_bin:
        try:
            from send2trash import send2trash  # type: ignore
        except ImportError:
            result["errors"] = [(p, "send2trash is not installed (pip install send2trash)") for p in paths]
            return result
    total = max(1, len(paths))
    for i, path in enumerate(paths):
        if ctx is not None:
            if ctx.cancelled():
                break
            ctx.progress(f"Removing {os.path.basename(path)}", int(i * 100 / total))
        reason = protection_reason(path)
        if reason:
            result["errors"].append((path, f"Skipped: {reason}"))
            continue
        if not os.path.lexists(path):
            result["errors"].append((path, "No longer exists"))
            continue
        try:
            size = path_size(path)
            if use_recycle_bin:
                send2trash(path)
            else:
                remove_permanently(path)
            if os.path.lexists(path):
                raise OSError("could not remove everything (files in use?)")
            result["deleted"].append(path)
            result["freed"] += size
            log.info("%s %s (%s)", "Recycled" if use_recycle_bin else "Deleted", path, human_size(size))
        except Exception as exc:  # noqa: BLE001 - report every failure to the user
            result["errors"].append((path, str(exc)))
            log.warning("Could not remove %s: %s", path, exc)
    return result


# --------------------------------------------------------------------------- recycle bin
if IS_WINDOWS:
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        class _SHQUERYRBINFO(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong),
                        ("i64NumItems", ctypes.c_longlong)]
    else:
        class _SHQUERYRBINFO(ctypes.Structure):  # type: ignore[no-redef]
            _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong),
                        ("i64NumItems", ctypes.c_longlong)]


def recycle_bin_info() -> tuple:
    """Return (bytes, item_count) for all Recycle Bins."""
    if IS_WINDOWS:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        try:
            if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
                return int(info.i64Size), int(info.i64NumItems)
        except Exception:
            pass
        return 0, 0
    trash = os.path.join(str(Path.home()), ".local", "share", "Trash", "files")
    if not os.path.isdir(trash):
        return 0, 0
    return path_size(trash), len(os.listdir(trash))


def empty_recycle_bin() -> bool:
    if IS_WINDOWS:
        flags = 0x1 | 0x2 | 0x4  # no confirmation, no progress UI, no sound
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        return hr in (0, -2147418113)  # S_OK or E_UNEXPECTED (already empty)
    trash = os.path.join(str(Path.home()), ".local", "share", "Trash")
    for sub in ("files", "info"):
        folder = os.path.join(trash, sub)
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                try:
                    remove_permanently(os.path.join(folder, name))
                except OSError:
                    pass
    return True
