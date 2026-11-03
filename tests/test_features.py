"""Checks for the tailored-to-this-PC logic, catalogues, progress parsing, toasts and One-click optimize."""
import os

import pytest

from remedy.core import maintenance, optimize, osinfo, programs, scanner, tweaks
from remedy.core.osinfo import OSInfo

WIN11_DESKTOP = OSInfo(True, 26100, "24H2", "Professional", laptop=False)
WIN11_LAPTOP = OSInfo(True, 26100, "24H2", "Core", laptop=True)
WIN10 = OSInfo(True, 19045, "22H2", "Professional", laptop=False)
LINUX = OSInfo(False)

# Tweak ids from v1.1.0. They key the undo backups on users' PCs, so they must never change.
V110_IDS = {"power_plan", "animations", "menu_delay", "startup_delay", "transparency", "hibernation",
            "background_apps", "sysmain", "wsearch", "game_mode", "game_dvr", "hags", "mouse_accel", "sticky_keys",
            "net_throttle", "ad_id", "tailored", "suggestions", "web_search", "copilot", "activity", "telemetry",
            "file_ext", "hidden_files", "this_pc", "classic_menu", "end_task"}


# ---------------------------------------------------------------- OS detection
def test_os_names():
    assert WIN11_DESKTOP.is_win11 and not WIN11_DESKTOP.is_win10
    assert WIN10.is_win10 and not WIN10.is_win11
    assert WIN11_DESKTOP.summary() == "Windows 11 Pro 24H2 · build 26100 · Desktop"
    assert WIN11_LAPTOP.edition_name == "Home" and WIN11_LAPTOP.is_home
    assert WIN10.short() == "Windows 10 22H2"


def test_applies():
    assert osinfo.applies("", WIN10) == (True, "")
    assert osinfo.applies("win11", WIN10) == (False, "Windows 11 only")
    assert osinfo.applies("win10", WIN11_DESKTOP) == (False, "Windows 10 only")
    assert osinfo.applies("", LINUX)[0] is False


def test_detect_runs_here():
    info = osinfo.detect(refresh=True)
    assert info.family


# ---------------------------------------------------------------- tweaks catalogue
def test_tweak_catalogue_is_consistent():
    items = tweaks.build_tweaks()
    ids = [t.id for t in items]
    assert len(ids) == len(set(ids)), "duplicate tweak ids"
    assert V110_IDS <= set(ids), f"tweak ids removed: {V110_IDS - set(ids)}"
    for t in items:
        assert t.category in tweaks.CATEGORY_ORDER
        assert t.target in ("", "win10", "win11")
        assert t.risk in ("Safe", "Moderate", "Advanced")
        assert t.name and t.description
        for v in getattr(t, "values", []):
            assert len(v) == 6 and v[0] in ("HKCU", "HKLM")
    orders = [tweaks.CATEGORY_ORDER.index(t.category) for t in items]
    assert orders == sorted(orders)


def test_every_tweak_explains_its_gain():
    items = tweaks.build_tweaks()
    assert set(tweaks.IMPACT) == {t.id for t in items}, "every tweak needs exactly one IMPACT entry"
    for t in items:
        assert t.area in tweaks.AREAS, t.id
        assert t.gain and t.notice and t.tradeoff, t.id
        assert len(t.gain) <= 50, f"{t.id}: gain text too long for the list column"
    assert tweaks.gain_tally([t for t in items if t.id in ("animations", "menu_delay", "ad_id")]) == \
        "2 speed · 1 privacy"


def test_presets_point_at_real_tweaks():
    ids = {t.id for t in tweaks.build_tweaks()}
    for name, members in tweaks.PRESETS.items():
        assert set(members) <= ids, name


def test_tailoring_to_windows_version_and_laptops():
    items = tweaks.build_tweaks()
    by_id = {t.id: t for t in items}
    assert by_id["classic_menu"].availability(WIN10) == (False, "Windows 11 only")
    assert by_id["background_apps"].availability(WIN11_DESKTOP)[0] is False
    assert by_id["background_apps"].availability(WIN10)[0] is True
    win10_suggested = {t.id for t in items if t.suggested(WIN10)}
    assert not any(by_id[i].target == "win11" for i in win10_suggested)
    gaming_laptop = tweaks.preset_ids("Gaming", items, WIN11_LAPTOP)
    gaming_desktop = tweaks.preset_ids("Gaming", items, WIN11_DESKTOP)
    assert "power_plan" in gaming_desktop and "power_plan" not in gaming_laptop
    assert tweaks.preset_ids("Windows 11 clean-up", items, WIN10) == [
        i for i in tweaks.PRESETS["Windows 11 clean-up"] if by_id[i].target != "win11"]


# ---------------------------------------------------------------- other catalogues
def test_maintenance_actions():
    actions = maintenance.build_actions()
    keys = [a.key for a in actions]
    assert len(keys) == len(set(keys))
    assert all(a.group in maintenance.GROUP_ORDER for a in actions)
    assert all(bool(a.cmd) != bool(a.launch) for a in actions)


@pytest.mark.parametrize("line,expected", [
    ("Verification 45% complete.", 45),
    ("[=========================100.0%==========================]", 100),
    ("  62,3 %", 62),
    ("Beginning system scan.", None),
    ("Rate 250% boost", None),
])
def test_percent_in(line, expected):
    assert maintenance.percent_in(line) == expected


def test_bloat_list_shape():
    for name, (label, recommended) in programs.KNOWN_BLOAT.items():
        assert name and label and isinstance(recommended, bool)


def test_estimated_scan_percent():
    assert scanner.estimate_pct(50, 100) == 49
    assert scanner.estimate_pct(500, 100) == 99
    assert scanner.estimate_pct(10, 0) == -1
    assert scanner.estimate_pct(50, 100, 0, 30) == 15


# ---------------------------------------------------------------- One-click optimize
def test_plan_is_tailored():
    linux = {s.key: s for s in optimize.plan(LINUX, admin=False)}
    assert linux["junk"].available and not linux["tweaks"].available
    win = {s.key: s for s in optimize.plan(WIN11_LAPTOP, admin=False)}
    assert not win["restore"].available and win["restore"].reason == "Needs administrator"
    assert "Windows 11 24H2" in win["tweaks"].title and "laptops" in win["tweaks"].description
    assert win["bloat"].default is False


class _Ctx:
    def __init__(self, cancel_after=None):
        self.lines, self.cancel_after = [], cancel_after

    def progress(self, text, pct=-1, force=False):
        self.lines.append((text, pct))

    def cancelled(self):
        return self.cancel_after is not None and len(self.lines) >= self.cancel_after


def test_run_keeps_going_after_a_failed_step(monkeypatch):
    def good(ctx, report):
        report("half", 50)
        return "fine"

    def bad(ctx, report):
        raise RuntimeError("Needs administrator\nmore detail")
    monkeypatch.setitem(optimize.RUNNERS, "junk", good)
    monkeypatch.setitem(optimize.RUNNERS, "dns", bad)
    ctx = _Ctx()
    results = optimize.run(ctx, ["junk", "dns"])
    assert results == [("junk", True, "fine"), ("dns", False, "Needs administrator")]
    from remedy.ui.optimize_wizard import parse_line
    parsed = [parse_line(t) for t, _ in ctx.lines]
    assert ("junk", "run", "half", 50) in parsed
    assert ("dns", "fail", "Needs administrator", -1) in parsed
    assert ctx.lines[-1][1] == 100


def test_run_can_be_cancelled(monkeypatch):
    from remedy.core.utils import TaskCancelled
    monkeypatch.setitem(optimize.RUNNERS, "junk", lambda ctx, report: "x")
    with pytest.raises(TaskCancelled):
        optimize.run(_Ctx(cancel_after=0), ["junk"])


# ---------------------------------------------------------------- health score
def test_health_score_uses_tailored_checks():
    from remedy.ui.pages_system import score_health
    base = {"admin": True, "junk": 0, "free_pct": 50, "free": 100e9, "startup": 2, "mem": 30, "uptime_days": 1,
            "hiberfil": 0}
    assert score_health(base)[0] == 100
    score, recs = score_health({**base, "tweaks_pending": 9, "bloat": 5, "laptop": True, "os": "Windows 11 24H2"})
    assert score < 100
    texts = " ".join(r[1] for r in recs)
    assert "Windows 11 24H2" in texts and "bloatware" in texts and "battery" in texts


# ---------------------------------------------------------------- toasts
@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_toasts_stack_dedupe_and_cap(qapp):
    from PySide6.QtWidgets import QWidget
    from remedy.ui.notify import MAX_VISIBLE, ToastManager, duration_for
    win = QWidget()
    win.resize(1000, 800)
    m = ToastManager(win)
    first = m.show("success", "Freed 1 GB", "12 files")
    assert m.show("success", "Freed 1 GB", "12 files") is first          # same message: not shown twice
    for i in range(6):
        m.show("info", f"Message {i}")
    assert len(m.visible()) == MAX_VISIBLE
    assert duration_for("error", "x") > duration_for("success", "x")
    assert 3500 <= duration_for("info", "word " * 200) <= 11000
    win.close()


def test_toast_kinds_fall_back_to_info(qapp):
    from PySide6.QtWidgets import QWidget
    from remedy.ui.notify import ToastManager
    win = QWidget()
    card = ToastManager(win).show("nonsense", "Hello")
    assert card.kind == "info"
    win.close()


# ---------------------------------------------------------------- safety guards
def test_essential_services_are_protected():
    from remedy.core import services
    assert services.protection("Dhcp", "disabled")
    assert services.protection("audiosrv", "stop")                      # case doesn't matter
    assert services.protection("Dnscache", "manual", "automatic")
    assert not services.protection("Dhcp", "start") and not services.protection("Dhcp", "restart")
    assert not services.protection("Dhcp", "automatic")
    assert not services.protection("Fax", "disabled")
    for t in tweaks.build_tweaks():                                      # our own tweaks never touch them
        for name in getattr(t, "service_names", []):
            assert name not in services.ESSENTIAL, t.id


def test_protected_store_apps():
    assert programs.appx_protection("Microsoft.WindowsStore")
    assert programs.appx_protection("Microsoft.DesktopAppInstaller")
    assert programs.appx_protection("windows.immersivecontrolpanel")
    assert programs.appx_protection("RealtekSemiconductorCorp.RealtekAudioControl")
    assert not programs.appx_protection("king.com.CandyCrushSaga")
    assert not programs.appx_protection("Microsoft.Windows.DevHome")      # known bloat is never protected
    for name in programs.KNOWN_BLOAT:
        assert not programs.appx_protection(name), name
    with pytest.raises(PermissionError):
        programs.remove_appx("Microsoft.WindowsStore_22401.1400.6.0_x64__8wekyb3d8bbwe")


def test_recycle_bin_is_never_emptied_by_default():
    from remedy.core import junk
    rb = next(c for c in junk.build_categories() if c.special == "recycle")
    assert rb.default is False


def test_one_click_never_empties_the_recycle_bin(monkeypatch):
    from remedy.core import junk
    from remedy.core.junk import JunkCategory
    cats = [JunkCategory("tmp", "Temp", "", default=True),
            JunkCategory("recycle_bin", "Recycle Bin", "", special="recycle", default=True)]
    monkeypatch.setattr(junk, "build_categories", lambda: cats)
    cleaned = []
    monkeypatch.setattr(junk, "scan_category", lambda c, ctx=None: c)
    monkeypatch.setattr(junk, "clean_category", lambda c, ctx=None: (cleaned.append(c.key), (0, 0, 0))[1])
    optimize.step_junk(_Ctx(), lambda *_: None, admin=True)
    assert cleaned == ["tmp"]


def test_duplicates_skip_app_folders(tmp_path):
    for folder in ("Photos", "Photos copy", "AppData/Local/SomeApp", "Program Files/Game", "node_modules/x"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "same.bin").write_bytes(b"x" * 5000)
    res = scanner.find_duplicates(None, [str(tmp_path)], 1)
    paths = {os.path.relpath(f["path"], tmp_path).split(os.sep)[0] for g in res["groups"] for f in g["files"]}
    assert paths == {"Photos", "Photos copy"}
    res_all = scanner.find_duplicates(None, [str(tmp_path)], 1, include_app_folders=True)
    assert sum(len(g["files"]) for g in res_all["groups"]) == 5


def test_restore_point_runs_as_its_own_batch(monkeypatch):
    monkeypatch.setitem(optimize.RUNNERS, "junk", lambda ctx, report: "ok")
    ctx = _Ctx()
    optimize.run(ctx, ["junk"], offset=1, total=2)
    assert ctx.lines[-1][1] == 100 and ctx.lines[0][1] == 50


def test_restore_script_puts_the_frequency_setting_back():
    script = maintenance.RESTORE_POINT_SCRIPT
    assert "finally" in script and "Remove-ItemProperty" in script and "$old" in script
