"""Repair plan: approved, previewed actions linked to findings, with receipts and real before/after verification.

Nothing here runs without the user ticking it and confirming. Each action says exactly what it changes, how risky
it is, whether it can be undone, and which measurement proves it worked. A command's exit code alone is never
treated as proof: verification re-runs the affected diagnostic providers and compares like-for-like readings.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import diagnostics, junk, osinfo
from .utils import TaskCancelled, human_size, is_admin, log, powershell, run_command


@dataclass
class RepairAction:
    id: str
    title: str
    change: str                 # exactly what changes on the PC
    why: str
    risk: str                   # Low | Medium | High
    reversible: str             # honest undo story
    admin: bool = False
    windows_only: bool = True
    reboot: bool = False
    duration: str = ""          # honest, rough
    verify: list = field(default_factory=list)       # [(evidence id, "lower"|"higher"|"true")]
    providers: list = field(default_factory=list)    # providers to re-run for verification


ACTIONS = {
    "clean_junk": RepairAction(
        "clean_junk", "Clean safe junk files",
        "Deletes files in Remedy's default junk categories: user and Windows temp folders (files older than 24 h), "
        "update and delivery caches, crash dumps, error reports, thumbnail cache and browser/app caches. Files in use "
        "are skipped. The Recycle Bin and your documents are never touched.",
        "Frees disk space taken by temporary files and caches.", "Low",
        "Not undoable, but nothing personal is removed: these are caches and temp files apps recreate when needed.",
        admin=False, windows_only=False, duration="Usually under a minute",
        verify=[("storage.junk_bytes", "lower"), ("storage.sys_free_bytes", "higher")], providers=["storage"]),
    "flush_dns": RepairAction(
        "flush_dns", "Flush the DNS cache",
        "Runs 'ipconfig /flushdns', which empties Windows' list of remembered website addresses.",
        "Fixes sites that won't load after a network or DNS change.", "Low",
        "Nothing to undo: the cache refills by itself as you browse.", duration="A few seconds",
        verify=[("network.dns_ok", "true")], providers=["network"]),
    "update_defender": RepairAction(
        "update_defender", "Update Microsoft Defender definitions",
        "Runs 'Update-MpSignature' to download the latest virus definitions from Microsoft.",
        "Old definitions can miss new threats.", "Low",
        "Not undoable (and no need): Defender keeps its previous definitions until the new ones install.",
        admin=True, duration="Usually under 2 minutes",
        verify=[("security.defender_sig_age", "lower")], providers=["security"]),
}


def available(action: RepairAction, info=None, admin=None) -> tuple:
    """(usable, reason)."""
    info = info or osinfo.detect()
    admin = is_admin() if admin is None else admin
    if action.windows_only and not info.is_windows:
        return False, "Windows only"
    if action.admin and not admin:
        return False, "Needs administrator (use Restart as admin)"
    return True, ""


def preflight(action_ids: list, info=None, admin=None) -> list:
    """[(check, ok, detail)] shown before anything runs."""
    info = info or osinfo.detect()
    admin = is_admin() if admin is None else admin
    acts = [ACTIONS[a] for a in action_ids if a in ACTIONS]
    checks = []
    needs_admin = any(a.admin for a in acts)
    checks.append(("Administrator", admin or not needs_admin,
                   "Running as administrator" if admin else
                   ("Some selected repairs need administrator" if needs_admin else "Not needed for these repairs")))
    blocked = [a.title for a in acts if not available(a, info, admin)[0]]
    checks.append(("Supported here", not blocked, "All selected repairs can run on this PC" if not blocked
                   else "Can't run here: " + ", ".join(blocked)))
    checks.append(("Restart needed", True, "Yes, for some repairs" if any(a.reboot for a in acts)
                   else "No restart needed"))
    checks.append(("Restore point", True, "Not needed: these repairs don't change system settings"
                   if not any(a.risk != "Low" for a in acts) else "A restore point is created first"))
    return checks


def _run_clean_junk(ctx, report):
    cats = diagnostics.junk_categories()
    for i, c in enumerate(cats):
        if ctx is not None and ctx.cancelled():
            raise TaskCancelled()
        report(f"Measuring {c.name}…", int(i * 40 / max(1, len(cats))))
        junk.scan_category(c, ctx)
    freed = deleted = skipped = 0
    for i, c in enumerate(cats):
        if ctx is not None and ctx.cancelled():
            raise TaskCancelled()
        report(f"Cleaning {c.name}… {human_size(freed)} so far", 40 + int(i * 60 / max(1, len(cats))))
        f, d, x = junk.clean_category(c, ctx)
        freed, deleted, skipped = freed + f, deleted + d, skipped + x
    return f"Removed {deleted:,} files ({human_size(freed)}); {skipped:,} in use were skipped", {"freed": freed}


def _run_flush_dns(ctx, report):
    report("Flushing DNS…", 50)
    res = run_command(["ipconfig", "/flushdns"], timeout=60)
    if not res.ok:
        raise RuntimeError(res.output or "ipconfig failed")
    return "DNS cache flushed", {}


def _run_update_defender(ctx, report):
    report("Downloading Defender definitions…", 30)
    res = powershell("Update-MpSignature -ErrorAction Stop; 'ok'", timeout=900)
    if not res.ok:
        raise RuntimeError((res.output or "Update-MpSignature failed").strip().splitlines()[-1])
    return "Definitions update finished", {}


RUNNERS = {"clean_junk": _run_clean_junk, "flush_dns": _run_flush_dns, "update_defender": _run_update_defender}


def run_plan(ctx, action_ids: list, emit=None) -> list:
    """Run approved actions in order. A failure doesn't stop the rest; Cancel stops before the next one.
    Returns receipts: {id, title, ok, summary, started, finished, data}."""
    emit = emit or (lambda *_: None)
    receipts = []
    for i, aid in enumerate(action_ids):
        action = ACTIONS[aid]
        if ctx is not None and ctx.cancelled():
            receipts.append({"id": aid, "title": action.title, "ok": False, "status": "skipped",
                             "summary": "Not run: cancelled before it started", "started": 0, "finished": 0})
            continue
        ok, why = available(action)
        if not ok:
            receipts.append({"id": aid, "title": action.title, "ok": False, "status": "blocked", "summary": why,
                             "started": 0, "finished": 0})
            continue
        started = time.time()

        def report(text, pct, _i=i):
            emit(f"{action.title}: {text}", int((_i + max(0, pct) / 100) * 100 / len(action_ids)))

        try:
            summary, data = RUNNERS[aid](ctx, report)
            receipts.append({"id": aid, "title": action.title, "ok": True, "status": "done", "summary": summary,
                             "started": started, "finished": time.time(), "data": data})
            log.info("Repair %s: %s", aid, summary)
        except TaskCancelled:
            receipts.append({"id": aid, "title": action.title, "ok": False, "status": "cancelled",
                             "summary": "Stopped part-way: cancelled", "started": started, "finished": time.time()})
        except Exception as exc:  # noqa: BLE001 - partial failure is a first-class result
            msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            receipts.append({"id": aid, "title": action.title, "ok": False, "status": "failed", "summary": msg,
                             "started": started, "finished": time.time()})
            log.warning("Repair %s failed: %s", aid, msg)
    return receipts


def providers_for(action_ids: list) -> list:
    out = []
    for aid in action_ids:
        for p in ACTIONS[aid].providers:
            if p not in out:
                out.append(p)
    return out


def verify(before: dict, after: dict, receipts: list) -> list:
    """Compare like-for-like readings. before/after: {evidence id: Evidence}.
    Returns rows {action, metric, before, after, verdict, note}; verdict in Verified | No change | Worse |
    Not verified."""
    rows = []
    for r in receipts:
        action = ACTIONS.get(r["id"])
        if action is None:
            continue
        if r.get("status") != "done":
            rows.append({"action": action.title, "metric": "", "before": "", "after": "",
                         "verdict": "Not verified", "note": f"Repair did not complete ({r.get('status')})"})
            continue
        for eid, direction in action.verify:
            b, a = before.get(eid), after.get(eid)
            if b is None or a is None:
                rows.append({"action": action.title, "metric": (a or b).label if (a or b) else eid,
                             "before": b.display() if b else "not measured", "after": a.display() if a else "not measured",
                             "verdict": "Not verified", "note": "No comparable reading before and after"})
                continue
            if direction == "true":
                verdict = "Verified" if a.value is True else ("No change" if a.value == b.value else "Worse")
            else:
                try:
                    delta = float(a.value) - float(b.value)
                except (TypeError, ValueError):
                    verdict = "Not verified"
                else:
                    good = delta < 0 if direction == "lower" else delta > 0
                    verdict = "Verified" if good else ("No change" if delta == 0 else "Worse")
            rows.append({"action": action.title, "metric": a.label, "before": b.display(), "after": a.display(),
                         "verdict": verdict, "note": ""})
    return rows
