"""Thin, safe wrappers around winreg (no-ops on non-Windows so the app can be tested anywhere)."""
from __future__ import annotations

from .utils import IS_WINDOWS

REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_QWORD = 11

if IS_WINDOWS:
    import winreg

    HIVES = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    _VIEW = winreg.KEY_WOW64_64KEY
else:  # pragma: no cover - non-Windows
    winreg = None
    HIVES = {}
    _VIEW = 0


def get_value(hive: str, key: str, name: str):
    """Return (value, type) or None if missing."""
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _VIEW) as k:
            value, typ = winreg.QueryValueEx(k, name)
            return value, typ
    except OSError:
        return None


def set_value(hive: str, key: str, name: str, typ: int, value) -> None:
    if not IS_WINDOWS:
        raise OSError("Registry is only available on Windows")
    with winreg.CreateKeyEx(HIVES[hive], key, 0, winreg.KEY_SET_VALUE | _VIEW) as k:
        winreg.SetValueEx(k, name, 0, typ, value)


def delete_value(hive: str, key: str, name: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_SET_VALUE | _VIEW) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def key_exists(hive: str, key: str) -> bool:
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _VIEW):
            return True
    except OSError:
        return False


def enum_subkeys(hive: str, key: str) -> list:
    if not IS_WINDOWS:
        return []
    names = []
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _VIEW) as k:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return names


def read_values(hive: str, key: str) -> dict:
    """Return {name: (value, type)} for every value in a key."""
    if not IS_WINDOWS:
        return {}
    values = {}
    try:
        with winreg.OpenKey(HIVES[hive], key, 0, winreg.KEY_READ | _VIEW) as k:
            i = 0
            while True:
                try:
                    name, value, typ = winreg.EnumValue(k, i)
                except OSError:
                    break
                values[name] = (value, typ)
                i += 1
    except OSError:
        pass
    return values


def delete_key_tree(hive: str, key: str) -> None:
    if not IS_WINDOWS:
        return
    root = HIVES[hive]
    for sub in enum_subkeys(hive, key):
        delete_key_tree(hive, key + "\\" + sub)
    try:
        winreg.DeleteKeyEx(root, key, _VIEW, 0)
    except FileNotFoundError:
        pass
