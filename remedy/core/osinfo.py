"""Which Windows is this? Version, release, edition and form factor, so Remedy can tailor what it offers."""
from __future__ import annotations

import platform
import sys
from dataclasses import dataclass

from . import registry
from .utils import IS_WINDOWS

NT_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
WIN11_BUILD = 22000


@dataclass(frozen=True)
class OSInfo:
    is_windows: bool
    build: int = 0
    release: str = ""        # e.g. "24H2"
    edition: str = ""        # e.g. "Professional", "Core" (Home)
    laptop: bool = False
    arch: str = ""

    @property
    def is_win11(self) -> bool:
        return self.is_windows and self.build >= WIN11_BUILD

    @property
    def is_win10(self) -> bool:
        return self.is_windows and 0 < self.build < WIN11_BUILD

    @property
    def family(self) -> str:
        if not self.is_windows:
            return platform.system() or "Unknown"
        return "Windows 11" if self.is_win11 else "Windows 10"

    @property
    def is_home(self) -> bool:
        return self.edition.lower().startswith("core")

    @property
    def edition_name(self) -> str:
        e = self.edition.lower()
        if e.startswith("core"):
            return "Home"
        for key, name in (("professional", "Pro"), ("enterprise", "Enterprise"), ("education", "Education"),
                          ("server", "Server")):
            if key in e:
                return name
        return self.edition

    @property
    def form(self) -> str:
        return "Laptop" if self.laptop else "Desktop"

    def summary(self) -> str:
        """e.g. "Windows 11 Pro 24H2 · build 26100 · Laptop"."""
        if not self.is_windows:
            return f"{self.family} (preview mode: Windows features are switched off)"
        name = " ".join(x for x in (self.family, self.edition_name, self.release) if x)
        return f"{name} · build {self.build} · {self.form}"

    def short(self) -> str:
        if not self.is_windows:
            return self.family
        return " ".join(x for x in (self.family, self.release) if x)


def _laptop() -> bool:
    try:
        import psutil
        return psutil.sensors_battery() is not None
    except Exception:  # noqa: BLE001 - no battery info means desktop
        return False


def _read(name: str):
    value = registry.get_value("HKLM", NT_KEY, name)
    return value[0] if value else None


_CACHE = None


def detect(refresh: bool = False) -> OSInfo:
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    if not IS_WINDOWS:
        _CACHE = OSInfo(False, arch=platform.machine(), laptop=_laptop())
        return _CACHE
    build = 0
    try:
        build = int(_read("CurrentBuildNumber") or 0)
    except (TypeError, ValueError):
        pass
    if not build:
        build = getattr(sys.getwindowsversion(), "build", 0)
    release = str(_read("DisplayVersion") or _read("ReleaseId") or "")
    edition = str(_read("EditionID") or "")
    _CACHE = OSInfo(True, build, release, edition, _laptop(), platform.machine())
    return _CACHE


def applies(target: str, info: OSInfo = None) -> tuple:
    """Is a feature meant for this Windows? target: "" (any), "win11" or "win10". Returns (ok, reason)."""
    info = info or detect()
    if not info.is_windows:
        return False, "Windows only"
    if target == "win11" and not info.is_win11:
        return False, "Windows 11 only"
    if target == "win10" and not info.is_win10:
        return False, "Windows 10 only"
    return True, ""
