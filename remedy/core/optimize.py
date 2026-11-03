"""One-click optimize: a short, safe checklist run in order (restore point, junk, tweaks, bloat, DNS)."""
from __future__ import annotations

from dataclasses import dataclass

from . import junk, maintenance, osinfo, programs, tweaks
from .utils import TaskCancelled, human_size, is_admin, log, run_command


@dataclass
class Step:
    key: str
    title: str
    description: str
    default: bool = True
    available: bool = True
    reason: str = ""          # why it's unavailable


def plan(info=None, admin=None) -> list:
    """The steps offered on this PC, already tailored to its Windows version and rights."""
    info = info or osinfo.detect()
    admin = is_admin() if admin is None else admin
    win = info.is_windows
    steps = [
        Step("restore", "Create a restore point",
             "A safety snapshot first, so every change below can be rolled back from System Restore.",
             default=True, available=win and admin,
             reason="Windows only" if not win else "Needs administrator"),
        Step("junk", "Clean junk files",
             "Temp files, caches, crash dumps and the Recycle Bin. Only the safe, default categories."),
        Step("tweaks", f"Apply recommended tweaks for {info.short()}",
             "Speed, privacy and quality-of-life settings picked for this PC"
             + (" (battery-hungry ones skipped on laptops)." if info.laptop else ".")
             + " Each one can be undone from the Tweaks page.",
             available=win, reason="Windows only"),
        Step("bloat", "Remove flagged bloatware",
             "Uninstalls preinstalled promo apps and games marked 'Safe to remove'. Reinstall any from the Store.",
             default=False, available=win, reason="Windows only"),
        Step("dns", "Refresh the network cache",
             "Flushes the DNS cache, which fixes sites that won't load after network changes.",
             available=win, reason="Windows only"),
    ]
    return steps


def _check(ctx):
    if ctx is not None and ctx.cancelled():
        raise TaskCancelled()


def step_restore(ctx, report) -> str:
    report("Creating a restore point (can take a minute)…", -1)
    ok, msg = maintenance.create_restore_point()
    if not ok:
        raise RuntimeError((msg or "Restore point failed").strip().splitlines()[-1])
    return "Restore point created"


def step_junk(ctx, report, admin=None) -> str:
    admin = is_admin() if admin is None else admin
    cats = [c for c in junk.build_categories() if c.default and (admin or not c.admin)]
    found = 0
    for i, c in enumerate(cats):
        _check(ctx)
        report(f"Measuring {c.name}…", int(i * 50 / max(1, len(cats))))
        junk.scan_category(c, ctx)
        found += c.size
    freed = deleted = skipped = 0
    for i, c in enumerate(cats):
        _check(ctx)
        report(f"Cleaning {c.name}… {human_size(freed)} freed", 50 + int(i * 50 / max(1, len(cats))))
        f, d, x = junk.clean_category(c, ctx)
        freed, deleted, skipped = freed + f, deleted + d, skipped + x
    tail = f", {skipped:,} in use" if skipped else ""
    return f"Freed {human_size(freed)} ({deleted:,} files{tail})"


def pending_tweaks(info=None, admin=None) -> list:
    info = info or osinfo.detect()
    admin = is_admin() if admin is None else admin
    out = []
    for t in tweaks.build_tweaks():
        if not t.suggested(info) or (t.admin and not admin):
            continue
        try:
            if t.status():
                continue
        except Exception:  # noqa: BLE001 - unknown status: let apply decide
            pass
        out.append(t)
    return out


def step_tweaks(ctx, report) -> str:
    todo = pending_tweaks()
    if not todo:
        return "Already optimized: nothing to change"
    ok, failed, restarts = 0, 0, set()
    for i, t in enumerate(todo):
        _check(ctx)
        report(f"{t.name}…", int(i * 100 / len(todo)))
        try:
            t.apply()
            ok += 1
            if t.restart:
                restarts.add(t.restart.lower())
            log.info("One-click: applied %s", t.name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("One-click: %s failed (%s)", t.name, exc)
    text = f"Applied {ok} tweak{'s' if ok != 1 else ''}"
    if failed:
        text += f", {failed} failed"
    if restarts:
        text += " · finish with: " + " / ".join(sorted(restarts))
    return text


def step_bloat(ctx, report) -> str:
    report("Listing installed Store apps…", -1)
    apps = [a for a in programs.list_appx() if a["recommended"]]
    if not apps:
        return "No bloatware found"
    removed = 0
    for i, a in enumerate(apps):
        _check(ctx)
        report(f"Removing {a['label'] or a['name']}…", int(i * 100 / len(apps)))
        try:
            programs.remove_appx(a["full_name"])
            removed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("One-click: could not remove %s (%s)", a["name"], exc)
    return f"Removed {removed} of {len(apps)} apps"


def step_dns(ctx, report) -> str:
    report("Flushing DNS…", -1)
    res = run_command(["ipconfig", "/flushdns"], timeout=60)
    if not res.ok:
        raise RuntimeError(res.output or "ipconfig failed")
    return "DNS cache refreshed"


RUNNERS = {"restore": step_restore, "junk": step_junk, "tweaks": step_tweaks, "bloat": step_bloat, "dns": step_dns}


def run(ctx, keys: list) -> list:
    """Run the chosen steps in order. Progress lines: "@key|run|text" / "@key|ok|summary" / "@key|fail|why".
    Returns [(key, ok, summary)]. A failed step doesn't stop the others; Cancel does."""
    results = []
    n = max(1, len(keys))
    for i, key in enumerate(keys):
        _check(ctx)

        def report(text, pct, _i=i, _key=key):
            overall = int((_i + (max(0, pct) / 100 if pct >= 0 else 0)) * 100 / n)
            ctx.progress(f"@{_key}|run|{text}|{pct}", overall, force=True)

        report("Starting…", 0)
        try:
            summary = RUNNERS[key](ctx, report)
            results.append((key, True, summary))
            ctx.progress(f"@{key}|ok|{summary}|100", int((i + 1) * 100 / n), force=True)
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - keep going with the other steps
            why = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
            results.append((key, False, why))
            ctx.progress(f"@{key}|fail|{why}|-1", int((i + 1) * 100 / n), force=True)
    return results
