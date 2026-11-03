"""Apply and undo every registry tweak on a real Windows machine, and check nothing is left changed.

This CHANGES WINDOWS SETTINGS, so it only runs when REMEDY_TWEAK_ROUNDTRIP=1 is set. The cloud checks set it on
their throwaway Windows machine; it never runs on a normal PC by accident.
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("REMEDY_TWEAK_ROUNDTRIP") != "1",
    reason="changes Windows settings: only runs on the CI Windows machine (REMEDY_TWEAK_ROUNDTRIP=1)")


def _registry_tweaks():
    from remedy.core import tweaks
    out = []
    for t in tweaks.build_tweaks():
        if isinstance(t, tweaks.ClassicMenuTweak) or type(t) is tweaks.RegTweak:
            out.append(t)
        elif isinstance(t, tweaks.CompositeTweak):
            out += [p for p in t.parts if type(p) is tweaks.RegTweak]
    return out


def _snapshot(t):
    from remedy.core import registry, tweaks
    if isinstance(t, tweaks.ClassicMenuTweak):
        return registry.key_exists("HKCU", t.CLSID + r"\InprocServer32")
    return [registry.get_value(h, k, n) for h, k, n, *_ in t.values]


@pytest.mark.parametrize("tweak", _registry_tweaks(), ids=lambda t: t.id)
def test_apply_then_undo_restores_everything(tweak):
    before = _snapshot(tweak)
    try:
        tweak.apply()
        assert tweak.status() is True, f"{tweak.id}: not reported as applied after apply()"
    finally:
        tweak.revert()
    assert _snapshot(tweak) == before, f"{tweak.id}: undo did not restore the original values"


def test_undo_restores_a_custom_value():
    """If the user had their own value before, undo must bring back THEIR value, not Windows' default."""
    from remedy.core import registry, tweaks
    from remedy.core.registry import REG_SZ
    t = next(x for x in tweaks.build_tweaks() if x.id == "menu_delay")
    hive, key, name = t.values[0][:3]
    original = registry.get_value(hive, key, name)
    try:
        registry.set_value(hive, key, name, REG_SZ, "123")
        t.apply()
        assert registry.get_value(hive, key, name)[0] == "0"
        t.revert()
        assert registry.get_value(hive, key, name)[0] == "123"
    finally:
        if original is None:
            registry.delete_value(hive, key, name)
        else:
            registry.set_value(hive, key, name, original[1], original[0])


def test_essential_services_are_refused():
    from remedy.core import services
    with pytest.raises(PermissionError):
        services.set_start_type("Dhcp", "disabled")
    with pytest.raises(PermissionError):
        services.control("AudioSrv", "stop")


def test_protected_store_apps_are_refused():
    from remedy.core import programs
    with pytest.raises(PermissionError):
        programs.remove_appx("Microsoft.WindowsStore_22401.1400.6.0_x64__8wekyb3d8bbwe")
