"""UI checks: one Back/Forward history (mouse side buttons included), state kept across navigation, background tasks
survive page switches, command palette, Task Center, and the New PC Setup wizard never repeating finished work."""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from remedy.core import diagnostics as D
from remedy.core import provision
from remedy.ui.navigation import NavHistory, Route


@pytest.fixture(scope="module")
def window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from remedy.app import MainWindow
    w = MainWindow()
    w.resize(1280, 820)
    w.show()
    app.processEvents()
    yield w
    w.tasks.records.clear()
    w.close()
    app.processEvents()


def _pump(ms=50):
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def _wait(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _pump(20)
        if cond():
            return True
    return False


def _fake_session():
    s = D.new_session("quick")
    e = {x.id: x for x in [
        D.Evidence("performance.cpu_avg", "performance", "CPU", 95.0, "%", "t", time.time(), 8, 8),
        D.Evidence("performance.top_cpu", "performance", "Top", ["busyapp.exe (PID 7) 80%"], "", "t", time.time()),
        D.Evidence("windows.uptime_days", "windows", "Up", 12.0, "days", "t", time.time())]}
    return D.finish_session(s, e, {"performance": D.ProviderStatus("performance", "complete")})


def test_history_semantics():
    h = NavHistory()
    for k in ("a", "b", "c"):
        h.push(Route.make(k))
    assert not h.push(Route.make("c")), "same view twice is one entry"
    assert h.back().key == "b" and h.back().key == "a" and h.back() is None
    assert h.forward().key == "b"
    h.push(Route.make("d"))
    assert not h.can_forward(), "a new page drops the forward trail, like a browser"
    h.replace(Route.make("d", {"x": 1}))
    assert h.current.args == {"x": 1} and h.back().key == "b"


def test_mouse_back_forward_restores_views(window):
    w = window
    diag = w.pages["diagnose"]
    diag.session = _fake_session()
    diag._populate()
    w.navigate("dashboard")
    w.navigate("diagnose")
    fid = diag.session["findings"][1]["id"]       # pick a finding that isn't the default first one
    w.navigate("diagnose", {"finding": fid})
    assert diag.tree.currentItem().data(0, Qt.UserRole) == fid
    w.navigate("processes", {"search": "busyapp.exe"})
    assert w.pages["processes"].search.text() == "busyapp.exe"

    target = w.pages["processes"].tree.viewport()
    QTest.mouseClick(target, Qt.BackButton)             # mouse side button, delivered through the app
    _pump(200)
    assert w.history.current == Route.make("diagnose", {"finding": fid})
    assert w.current_page() is diag and diag.tree.currentItem().data(0, Qt.UserRole) == fid
    QTest.mouseClick(diag.tree.viewport(), Qt.BackButton)
    _pump(200)
    assert w.history.current.key == "diagnose" and not w.history.current.args
    QTest.mouseClick(diag.tree.viewport(), Qt.ForwardButton)
    _pump(200)
    assert w.history.current.args == {"finding": fid}
    assert diag.tree.currentItem().data(0, Qt.UserRole) == fid, "selection restored"
    w.go_forward()
    assert w.current_page() is w.pages["processes"]
    assert w.pages["processes"].search.text() == "busyapp.exe", "filter kept"
    assert w.btn_back.isEnabled() and not w.btn_fwd.isEnabled()
    assert w.nav.currentItem().data(Qt.UserRole) == "processes", "sidebar follows history"


def test_navigation_never_cancels_a_running_task(window):
    w = window
    page = w.pages["sysinfo"]
    release = {"go": False}

    def slow(ctx):
        while not release["go"] and not ctx.cancelled():
            time.sleep(0.02)
        return "finished"

    w.navigate("sysinfo")
    worker = page.run(slow, text="Slow test task")
    for key in ("dashboard", "diagnose", "newpc", "settings"):
        w.navigate(key)
        _pump(30)
    w.go_back()
    w.go_back()
    assert worker.isRunning(), "switching pages must not stop background work"
    assert [r.title for r in w.tasks.running()] == [page.title]
    release["go"] = True
    assert _wait(lambda: not w.tasks.running())
    rec = w.tasks.ordered()[0]
    assert rec.status == "done" and rec.title == page.title


def test_palette_finds_pages_tools_and_never_runs_changes(window):
    from remedy.ui.palette import search
    entries = window.palette_entries()
    assert search(entries, "startup")[0].title == "Startup Apps"
    assert search(entries, "new pc")[0].title == "New PC Setup"
    assert any(e.kind == "Tweak" for e in search(entries, "file extensions"))
    risky = [e for e in entries if e.kind == "Action"]
    assert all(("Read-only" in e.hint or "Nothing runs" in e.hint) for e in risky if e.title != "Run health check")


def test_collapsing_a_group_keeps_the_current_page_visible(window):
    w = window
    w.navigate("startup")
    w._toggle_group("PERFORMANCE")
    assert not w.nav.item(w.nav_rows["startup"]).isHidden()
    w._toggle_group("STORAGE & CLEANUP")
    assert w.nav.item(w.nav_rows["cleaner"]).isHidden()
    w.navigate("cleaner")
    assert not w.nav.item(w.nav_rows["cleaner"]).isHidden(), "navigating into a folded group unfolds it"


def test_wizard_back_never_repeats_finished_work(window, monkeypatch):
    from remedy.ui import pages_newpc
    w = window
    page = w.pages["newpc"]
    monkeypatch.setattr(pages_newpc, "confirm", lambda *a, **k: True)
    ran = []
    monkeypatch.setattr(provision, "EXECUTORS", {
        "app": lambda c, tid, ref, emit: (ran.append(tid), ("done", "ok"))[1],
        "tweak": lambda c, tid, ref, emit: (ran.append(tid), ("done", "ok"))[1]})
    monkeypatch.setattr(pages_newpc, "IS_WINDOWS", False)       # no restore point / tweak reads in the container
    from remedy.core import osinfo
    monkeypatch.setattr(osinfo, "detect", lambda refresh=False: osinfo.OSInfo(True, 26100, "24H2", "Professional"))
    monkeypatch.setattr(page, "inspect", lambda: None)          # don't run the live inspection in the container
    w.navigate("newpc")
    page._selected_id = "everyday"
    page._begin()
    assert w.history.current.args == {"step": 1}
    page.inspection = {"facts": [], "installed": set(), "winget": True, "admin": True, "internet": True,
                       "laptop": False, "on_ac": True, "free": 60e9}
    page.profile.apps = ["7zip.7zip", "VideoLAN.VLC"]
    page.profile.tweaks = []
    page.tweak_status = {}
    page.goto(2)
    page.goto(3)
    assert w.history.current.args == {"step": 3}
    assert "2 actions" in page.btn_start.text()
    page._start_deploy()
    assert _wait(lambda: page.session.get("status") in ("deployed", "verified") and page.worker is None)
    assert ran == ["app:7zip.7zip", "app:VideoLAN.VLC"]
    _pump(100)
    w.go_back()                                    # mouse Back from Verify/Deploy into the plan
    while w.history.current.args.get("step") != 3 and w.history.can_back():
        w.go_back()
    assert w.history.current.args == {"step": 3}
    assert page.btn_start.text().startswith("Nothing to run"), "finished steps are not offered again"
    page._start_deploy()                           # pressing it goes to Verify instead of reinstalling
    _pump(100)
    assert ran == ["app:7zip.7zip", "app:VideoLAN.VLC"]
    from remedy.core import sessions
    stored = sessions.load(page.session["id"])
    assert stored["tasks"]["app:7zip.7zip"]["status"] == "done", "the journal on disk matches what happened"
