"""Engine checks for the technician workflows: diagnostics rules, repair verification, setup profiles/plan/journal,
sessions, reports and the security heuristics. No Windows needed; nothing on this machine is changed."""
import json
import time

import pytest

from remedy.core import diagnostics as D
from remedy.core import provision, repair, report, security, sessions, tweaks
from remedy.core.osinfo import OSInfo

WIN11 = OSInfo(True, 26100, "24H2", "Professional", laptop=False)
WIN11_LAPTOP = OSInfo(True, 26100, "24H2", "Core", laptop=True)
WIN10 = OSInfo(True, 19045, "22H2", "Professional", laptop=False)


class Ctx:
    def __init__(self, cancel=False):
        self._cancel = cancel

    def progress(self, *a, **k):
        pass

    def cancelled(self):
        return self._cancel


def ev(eid, value, unit="", window=0.0, samples=1):
    provider = eid.split(".")[0]
    return D.Evidence(eid, provider, eid, value, unit, "test", time.time(), window, samples)


def evs(*items):
    return {e.id: e for e in items}


# ---------------------------------------------------------------- diagnostics
def test_memory_pressure_absorbs_disk_latency_into_one_finding():
    e = evs(ev("performance.mem_avg", 93.0, "%", 30, 30), ev("performance.mem_total", 8e9, "bytes"),
            ev("performance.swap_avg", 75.0, "%", 30, 30), ev("performance.disk_latency", 80.0, "ms", 30, 2),
            ev("performance.disk_ops", 50.0, "ops/s", 30, 2), ev("performance.top_mem", ["chrome (PID 1) 2 GB"]))
    found = {f.id: f for f in D.analyze(e)}
    assert "memory_pressure" in found and found["memory_pressure"].severity == "high"
    assert "paging" in found["memory_pressure"].title
    assert "disk_latency" not in found, "one root cause should produce one card"
    assert found["memory_pressure"].confidence == "high"          # 30 samples
    assert set(found["memory_pressure"].evidence_ids) <= set(e)    # every claim points at real evidence


def test_disk_latency_needs_enough_operations():
    few = evs(ev("performance.disk_latency", 200.0, "ms", 8, 2), ev("performance.disk_ops", 1.0, "ops/s", 8, 2))
    assert not D.analyze(few)
    many = evs(ev("performance.disk_latency", 70.0, "ms", 8, 2), ev("performance.disk_ops", 20.0, "ops/s", 8, 2))
    f = D.analyze(many)[0]
    assert f.id == "disk_latency" and f.status == "suspected"


def test_healthy_readings_produce_no_findings():
    e = evs(ev("performance.cpu_avg", 7.0, "%", 8, 8), ev("performance.mem_avg", 40.0, "%", 8, 8),
            ev("storage.sys_free_pct", 60.0, "%"), ev("storage.junk_bytes", 2e8, "bytes"),
            ev("windows.uptime_days", 1.0), ev("network.tcp_fail_pct", 0), ev("network.dns_ok", True))
    assert D.analyze(e) == []


def test_unsigned_slow_pc_is_never_called_malware():
    e = evs(ev("performance.cpu_avg", 97.0, "%", 8, 8), ev("performance.top_cpu", ["weird.exe (PID 9) 90%"]))
    f = D.analyze(e)[0]
    text = (f.title + f.what + f.why + " ".join(f.next_steps)).lower()
    assert "malware" not in f.title.lower() and "virus" not in text
    assert any("don't recognise" in a or "investigate" in a for a in f.alternatives)
    assert f.links and f.links[0][1] == "processes" and f.links[0][2] == {"search": "weird.exe"}


def test_defender_detection_is_confirmed_by_provider():
    e = evs(ev("security.threats_active", ["Trojan:Win32/Test"]), ev("security.threats_history", ["Trojan:Win32/Test"]))
    f = D.analyze(e)[0]
    assert f.id == "threats_active" and f.status == "confirmed" and "Defender" in f.why


def test_third_party_antivirus_is_not_reported_as_unprotected():
    e = evs(ev("security.defender_realtime", False), ev("security.av_products", ["Contoso AV"]))
    assert not [f for f in D.analyze(e) if f.id == "av_off"]
    e2 = evs(ev("security.defender_realtime", False), ev("security.av_products", []))
    assert [f for f in D.analyze(e2) if f.id == "av_off"][0].severity == "high"
    assert D.av_enabled(0x61100) and not D.av_enabled(0x60100)


def test_providers_are_isolated_and_honest(monkeypatch):
    def boom(ctx, opts, report):
        raise RuntimeError("WMI exploded")

    def unavailable(ctx, opts, report):
        raise D.Unavailable("Windows only")

    def ok(ctx, opts, report):
        return [ev("storage.sys_free_pct", 5.0, "%"), ev("storage.sys_free_bytes", 1e9, "bytes")]

    monkeypatch.setattr(D, "PROVIDERS", {"performance": boom, "security": unavailable, "storage": ok})
    evidence, st = D.run_providers(Ctx(), ["performance", "security", "storage"], {})
    assert st["performance"].status == "failed" and "WMI exploded" in st["performance"].message
    assert st["security"].status == "unavailable"
    assert st["storage"].status == "complete"
    session = D.finish_session(D.new_session("quick"), evidence, st)
    assert session["status"] == "partial", "a failed provider must not look like a healthy, complete scan"
    assert {p for p, _w in D.not_assessed(session)} == {"Performance", "Security"}
    assert session["findings"][0]["id"] == "low_space"


def test_cancel_keeps_partial_results(monkeypatch):
    ctx = Ctx()

    def first(c, opts, report):
        ctx._cancel = True
        return [ev("windows.uptime_days", 30.0)]

    monkeypatch.setattr(D, "PROVIDERS", {"windows": first, "storage": lambda *a: []})
    evidence, st = D.run_providers(ctx, ["windows", "storage"], {})
    assert st["windows"].status == "complete" and st["storage"].status == "cancelled"
    session = D.finish_session(D.new_session("quick"), evidence, st, cancelled=True)
    assert session["status"] == "cancelled" and session["findings"][0]["id"] == "uptime"


def test_real_providers_run_here_without_crashing():
    evidence, st = D.run_providers(Ctx(), ["performance", "windows"], {"seconds": 1})
    assert st["performance"].status == "complete"
    assert "performance.cpu_avg" in evidence and evidence["performance.cpu_avg"].window_s == 1


# ---------------------------------------------------------------- repair plan
def test_repair_verification_compares_like_for_like():
    before = evs(ev("storage.junk_bytes", 3e9, "bytes"), ev("storage.sys_free_bytes", 10e9, "bytes"))
    after = evs(ev("storage.junk_bytes", 1e8, "bytes"), ev("storage.sys_free_bytes", 12e9, "bytes"))
    rows = repair.verify(before, after, [{"id": "clean_junk", "status": "done"}])
    assert [r["verdict"] for r in rows] == ["Verified", "Verified"]
    rows = repair.verify(before, {}, [{"id": "clean_junk", "status": "done"}])
    assert all(r["verdict"] == "Not verified" for r in rows), "no reading after = not verified, never a fake pass"
    rows = repair.verify(before, after, [{"id": "clean_junk", "status": "failed"}])
    assert rows[0]["verdict"] == "Not verified"
    same = repair.verify(before, before, [{"id": "clean_junk", "status": "done"}])
    assert same[0]["verdict"] == "No change"


def test_repair_plan_runs_only_approved_and_reports_partial_failure(monkeypatch):
    calls = []
    monkeypatch.setitem(repair.RUNNERS, "clean_junk", lambda ctx, rep: (calls.append("junk"), ("ok", {}))[1])
    monkeypatch.setitem(repair.ACTIONS, "clean_junk", repair.ACTIONS["clean_junk"])
    receipts = repair.run_plan(Ctx(), ["clean_junk", "flush_dns"])
    assert calls == ["junk"]
    by = {r["id"]: r for r in receipts}
    assert by["clean_junk"]["status"] == "done"
    assert by["flush_dns"]["status"] in ("blocked", "failed", "done")   # Windows only: blocked on Linux


def test_every_repair_action_explains_itself():
    for a in repair.ACTIONS.values():
        assert a.change and a.why and a.reversible and a.risk in ("Low", "Medium", "High")
        assert a.verify and a.providers, f"{a.id} must say how it is verified"
        assert a.id in repair.RUNNERS
    for f_id in ("clean_junk", "flush_dns", "update_defender"):
        assert f_id in repair.ACTIONS


# ---------------------------------------------------------------- setup profiles
def test_builtin_profiles_are_conservative_and_valid():
    tw = {t.id: t for t in tweaks.build_tweaks()}
    from remedy.core.appstore import CATALOG
    cat = {a.winget_id for a in CATALOG}
    names = {p.id for p in provision.BUILTIN_PROFILES}
    assert {"everyday", "gaming", "business", "laptop", "creator", "technician"} <= names
    for p in provision.BUILTIN_PROFILES:
        assert set(p.apps) <= cat, p.id
        for t in p.tweaks:
            assert tw[t].risk == "Safe", f"{p.id}: built-in profiles only use Safe tweaks ({t})"
        assert set(p.checklist) <= set(provision.CHECKLIST)


def test_imported_profile_cannot_smuggle_commands():
    data = {"name": "Sneaky", "apps": ["7zip.7zip", "bad id; rm -rf", "https://evil/x.exe"],
            "tweaks": ["file_ext", "made_up"], "checklist": ["printer", "nope"], "script": "format c:",
            "id": "everyday"}
    p, warnings = provision.parse_profile(json.dumps(data))
    assert p.apps == ["7zip.7zip"] and p.tweaks == ["file_ext"] and p.checklist == ["printer"]
    assert p.id != "everyday", "imports can't overwrite a built-in profile"
    assert any("script" in w for w in warnings) and len(warnings) == 4
    with pytest.raises(ValueError):
        provision.parse_profile("{not json")
    with pytest.raises(ValueError):
        provision.parse_profile({"name": "x", "schema": 99})
    with pytest.raises(ValueError):
        provision.parse_profile({"apps": []})


def test_profile_save_load_roundtrip():
    p = provision.duplicate(provision.BUILTIN_PROFILES[0], "Shop standard")
    provision.save_profile(p)
    loaded = {x.id: x for x in provision.load_profiles()}
    assert loaded[p.id].name == "Shop standard" and loaded[p.id].apps == p.apps
    with pytest.raises(ValueError):
        provision.save_profile(provision.BUILTIN_PROFILES[0])
    provision.delete_profile(p)
    assert p.id not in {x.id for x in provision.load_profiles()}


def _inspection(installed=(), winget=True, admin=True, laptop=False):
    return {"installed": set(installed), "winget": winget, "admin": admin, "laptop": laptop, "internet": True,
            "free": 50e9, "on_ac": True}


def test_plan_is_tailored_to_this_pc():
    prof = provision.SetupProfile("t", "T", apps=["7zip.7zip", "Not.In.Catalog"],
                                  tweaks=["classic_menu", "power_plan", "file_ext", "hibernation"], checklist=["printer"])
    plan = {t.id: t for t in provision.build_plan(prof, _inspection(installed={"7zip.7zip"}), WIN10,
                                                  tweak_status={"file_ext": True})}
    assert plan["app:7zip.7zip"].state == "satisfied" and not plan["app:7zip.7zip"].runnable
    assert plan["app:Not.In.Catalog"].state == "review" and not plan["app:Not.In.Catalog"].selected
    assert plan["tweak:classic_menu"].state == "blocked" and "Windows 11" in plan["tweak:classic_menu"].detail
    assert plan["tweak:power_plan"].state == "review"            # Moderate risk: never pre-ticked
    assert plan["tweak:file_ext"].state == "satisfied"
    assert plan["check:printer"].state == "manual"
    laptop = {t.id: t for t in provision.build_plan(prof, _inspection(laptop=True), WIN11_LAPTOP)}
    assert laptop["tweak:hibernation"].state == "review" and "Laptop" in laptop["tweak:hibernation"].detail
    no_admin = {t.id: t for t in provision.build_plan(prof, _inspection(admin=False), WIN11)}
    assert no_admin["tweak:power_plan"].state == "blocked"
    no_winget = {t.id: t for t in provision.build_plan(prof, _inspection(winget=False), WIN11)}
    assert no_winget["app:7zip.7zip"].state == "blocked"
    kept = provision.build_plan(prof, _inspection(), WIN11, selections={"tweak:file_ext": False})
    assert not {t.id: t for t in kept}["tweak:file_ext"].selected
    lines = provision.dry_run(kept, _inspection())
    assert not any("Not.In.Catalog via winget" in ln for ln in lines), "an unticked review item isn't run"
    assert any("Install Not.In.Catalog: not ticked" in ln for ln in lines), "...but it is listed as skipped"


def _plan_and_session(apps=("A.One", "B.Two"), tw=("file_ext",)):
    prof = provision.SetupProfile("t", "T", apps=list(apps), tweaks=list(tw))
    tasks = provision.build_plan(prof, _inspection(), WIN11)
    for t in tasks:
        if t.state == "review":
            t.selected = True
    return prof, tasks, provision.new_session(prof, "new_oem", "Desk 1")


def test_deploy_is_idempotent_and_journals_every_step():
    _p, tasks, s = _plan_and_session()
    ran, saves = [], []
    ex = {"app": lambda c, tid, ref, emit: (ran.append(tid), ("done", "ok"))[1],
          "tweak": lambda c, tid, ref, emit: (ran.append(tid), ("done", "ok"))[1]}
    provision.deploy(Ctx(), s, tasks, save=lambda x: saves.append(json.dumps(x)), executors=ex)
    assert ran == ["tweak:file_ext", "app:A.One", "app:B.Two"], "settings first, then apps one at a time"
    assert s["status"] == "deployed" and len(saves) >= 7
    ran.clear()
    provision.deploy(Ctx(), s, tasks, executors=ex)
    assert ran == [], "running again must not repeat finished work"


def test_resume_after_crash_reruns_only_unfinished():
    _p, tasks, s = _plan_and_session()
    provision.journal_tasks(s, tasks)
    s["status"] = "deploying"
    s["tasks"]["tweak:file_ext"]["status"] = "done"
    s["tasks"]["app:A.One"]["status"] = "running"        # Remedy was killed mid-install
    assert provision.mark_interrupted(s)
    assert s["status"] == "interrupted" and s["tasks"]["app:A.One"]["status"] == "interrupted"
    ran = []
    ex = {"app": lambda c, tid, ref, emit: (ran.append(tid), ("satisfied", "Already installed"))[1],
          "tweak": lambda c, tid, ref, emit: (ran.append(tid), ("done", ""))[1]}
    provision.deploy(Ctx(), s, tasks, executors=ex)
    assert ran == ["app:A.One", "app:B.Two"]
    assert s["tasks"]["app:A.One"]["status"] == "satisfied"


def test_failed_restore_point_stops_before_any_change():
    _p, tasks, s = _plan_and_session()
    ran = []
    ex = {"app": lambda *a: ran.append(1), "tweak": lambda *a: ran.append(1)}
    with pytest.raises(provision.RestorePointFailed):
        provision.deploy(Ctx(), s, tasks, executors=ex, restore_point=lambda: (False, "Restore is off"))
    assert ran == [] and s["status"] == "paused"


def test_failure_and_pause_are_first_class():
    _p, tasks, s = _plan_and_session()

    def app(c, tid, ref, emit):
        if ref == "A.One":
            raise RuntimeError("installer failed")
        return "done", "ok"

    ex = {"app": app, "tweak": lambda *a: ("done", "")}
    provision.deploy(Ctx(), s, tasks, executors=ex, should_pause=lambda: True)
    assert s["status"] == "paused" and s["tasks"]["app:A.One"]["status"] == "pending"
    provision.deploy(Ctx(), s, tasks, executors=ex)
    assert s["tasks"]["app:A.One"]["status"] == "failed" and "installer failed" in s["tasks"]["app:A.One"]["detail"]
    assert s["tasks"]["app:B.Two"]["status"] == "done", "one failure doesn't stop the rest"


def test_verify_trusts_reality_not_exit_codes():
    _p, tasks, s = _plan_and_session()
    provision.deploy(Ctx(), s, tasks, executors={"app": lambda *a: ("done", ""), "tweak": lambda *a: ("done", "")})
    s["manual"] = {"printer": True}
    provision.verify(s, installed={"a.one"}, tweak_status={"file_ext": False})
    t = s["tasks"]
    assert t["app:A.One"]["status"] == "verified"
    assert t["app:B.Two"]["status"] == "done" and "doesn't list" in t["app:B.Two"]["verify"]
    assert t["tweak:file_ext"]["status"] == "failed"


# ---------------------------------------------------------------- sessions & reports
def test_sessions_roundtrip_and_reject_bad_ids():
    s = D.new_session("quick")
    sessions.save(s)
    assert sessions.load(s["id"])["mode"] == "quick"
    assert any(x["id"] == s["id"] for x in sessions.list_sessions("diagnostic"))
    with pytest.raises(ValueError):
        sessions.load("../../etc/passwd")
    sessions.delete(s["id"])
    assert not any(x["id"] == s["id"] for x in sessions.list_sessions())


def test_reports_redact_and_escape(monkeypatch):
    monkeypatch.setattr(report, "PRIVATE_TOKENS_EXTRA", ["WORKSTATION-07", "jdoe"])
    s = D.new_session("quick")
    s["findings"] = [{"id": "x", "category": "Performance", "title": "<script>alert(1)</script>",
                      "severity": "high", "status": "confirmed", "confidence": "high",
                      "what": r"C:\Users\jdoe\AppData\bad.exe on WORKSTATION-07", "why": "w", "next_steps": []}]
    html = report.diagnostic_html(s, privacy=True)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "jdoe" not in html and "WORKSTATION-07" not in html and "[redacted]" in html
    setup = provision.new_session(provision.BUILTIN_PROFILES[0], "new_oem", "Mrs Smith's PC")
    assert "Mrs Smith" not in report.setup_html(setup) and "Mrs Smith" not in report.to_json(setup)
    assert "Mrs Smith" in report.setup_html(setup, include_label=True)


# ---------------------------------------------------------------- security heuristics
@pytest.mark.parametrize("path,status,level", [
    (r"C:\Users\x\AppData\Roaming\upd\svc.exe", "NotSigned", "review"),
    (r"C:\Program Files\Tool\tool.exe", "NotSigned", "none"),
    (r"C:\Program Files\Tool\tool.exe", "Valid", "none"),
    (r"C:\Users\x\AppData\Local\App\app.exe", "Valid", "none"),
    (r"C:\Gone\old.exe", "Missing", "broken"),
    (r"C:\Program Files\X\x.exe", "HashMismatch", "review"),
])
def test_startup_heuristics_are_leads_not_verdicts(path, status, level):
    got, why = security.assess_entry(path, status, "CN=Contoso")
    assert got == level
    assert got != "provider", "Remedy never issues a provider-level verdict itself"
    assert "malware" not in why.lower() or "not a verdict" in why.lower() or "not mean" in why.lower()
