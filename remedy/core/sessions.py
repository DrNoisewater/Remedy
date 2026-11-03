"""Technician sessions: diagnostic runs and New PC Setup jobs, stored locally as versioned JSON.

Location: %LOCALAPPDATA%\\Noisewater Labs\\Remedy\\sessions (never on a USB stick unless you export a report).
What is stored: Windows version, form factor, measurements, findings, actions and results, an optional client
label you type, and your technician note. Not stored: computer name, user names, passwords, product keys, browser
data or personal files. Old sessions are pruned after the retention period in Settings.
"""
from __future__ import annotations

import json
import os
import re
import time

from .utils import APP_DIR, log, settings

SESSIONS_DIR = APP_DIR / "sessions"
SCHEMA_VERSION = 1
_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def _path(session_id: str):
    if not _ID.match(session_id or ""):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return SESSIONS_DIR / f"{session_id}.json"


def migrate(data: dict) -> dict:
    """Bring an older session file up to the current schema (v1 is the first; future versions add steps here)."""
    data.setdefault("schema", SCHEMA_VERSION)
    data.setdefault("type", "diagnostic")
    data.setdefault("status", "complete")
    return data


def save(session: dict) -> str:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session["updated"] = time.time()
    path = _path(session["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def load(session_id: str) -> dict:
    return migrate(json.loads(_path(session_id).read_text(encoding="utf-8")))


def delete(session_id: str) -> None:
    try:
        _path(session_id).unlink()
    except FileNotFoundError:
        pass


def title_of(s: dict) -> str:
    if s.get("type") == "setup":
        label = s.get("client_label") or "Unlabelled PC"
        return f"{label} · {s.get('profile', {}).get('name', 'Setup')}"
    return f"{'Full Diagnosis' if s.get('mode') == 'full' else 'Quick Scan'} · {s.get('device', {}).get('os', '')}"


def list_sessions(kind: str = "") -> list:
    """Newest first. Unreadable files are skipped (and logged), never fatal."""
    out = []
    if not SESSIONS_DIR.is_dir():
        return out
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            s = migrate(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable session %s: %s", p.name, exc)
            continue
        if kind and s.get("type") != kind:
            continue
        out.append(s)
    out.sort(key=lambda s: -(s.get("updated") or s.get("started") or 0))
    return out


def latest(kind: str, status_in=()) -> dict | None:
    for s in list_sessions(kind):
        if not status_in or s.get("status") in status_in:
            return s
    return None


def prune(days: int | None = None) -> int:
    days = int(settings.get("session_retention_days") if days is None else days)
    if days <= 0 or not SESSIONS_DIR.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    n = 0
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n
