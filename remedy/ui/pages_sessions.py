"""Sessions & Reports: every diagnostic run and New PC Setup kept on this PC, with reports and anonymized exports."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QSplitter

from .. import theme
from ..core import diagnostics as D
from ..core import provision, report, sessions
from ..core.utils import open_location, settings
from .widgets import BasePage, Card, SortItem, UR, attach_empty, button, confirm, label, make_tree, size_widths

STATUS_COLOR = {"complete": theme.OK, "finished": theme.OK, "verified": theme.OK, "partial": theme.WARN,
                "interrupted": theme.WARN, "paused": theme.WARN, "deploying": theme.ACCENT, "draft": theme.INFO,
                "deployed": theme.ACCENT, "cancelled": theme.FAINT}


def _next_step(s: dict) -> str:
    st = s.get("status")
    if s.get("type") == "setup":
        return {"draft": "Continue setup", "interrupted": "Resume", "paused": "Resume", "deploying": "Resume",
                "deployed": "Verify", "verified": "Hand over"}.get(st, "Report")
    c = D.summary_counts(s)
    open_ = c["high"] + c["medium"]
    return f"{open_} issue(s) to review" if open_ else "Report"


class SessionsPage(BasePage):
    title = "Sessions & Reports"
    subtitle = ("Every diagnosis and setup is kept on this PC so you can resume, compare and hand over reports. "
                "Nothing is uploaded.")

    def __init__(self, main):
        super().__init__(main)
        self.add_action("Refresh", "", self.refresh)
        self.add_action("New diagnosis", "", lambda: self.main.navigate("diagnose"))
        self.add_action("New setup", "primary", lambda: self.main.navigate("newpc"))
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search client label, profile or Windows version…")
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        self.kind = QComboBox()
        self.kind.addItems(["All types", "Diagnostics", "New PC Setup"])
        self.kind.currentIndexChanged.connect(self._filter)
        top.addWidget(self.kind)
        self.root.addLayout(top)
        split = QSplitter(Qt.Horizontal)
        self.tree = make_tree(["Device / session", "Type", "Status", "Date", "Next step"], multi=False, stretch_col=0)
        size_widths(self.tree, {1: 105, 2: 85, 3: 125, 4: 150})
        self.tree.currentItemChanged.connect(self._picked)
        self.tree.itemDoubleClicked.connect(lambda *_: self._open())
        self.empty = attach_empty(self.tree, "No sessions yet. Run a Quick Scan or start a New PC Setup; they're "
                                             "saved here automatically.")
        split.addWidget(self.tree)
        self.detail = Card("Session details")
        self.d_title = label("Select a session", "h2")
        self.d_title.setWordWrap(True)
        self.d_body = label("", "muted", wrap=True)
        self.d_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.body.addWidget(self.d_title)
        self.detail.body.addWidget(self.d_body, 1)
        row = QHBoxLayout()
        self.b_open = button("Open", "primary", self._open)
        self.b_report = button("Save report…", "", self._report)
        self.b_json = button("Export anonymized JSON…", "", self._json,
                             "Computer and user names are always redacted in this export")
        self.b_del = button("Delete", "ghost", self._delete)
        for b in (self.b_open, self.b_report, self.b_json, self.b_del):
            row.addWidget(b)
        row.addStretch(1)
        self.detail.body.addLayout(row)
        split.addWidget(self.detail)
        split.setSizes([760, 400])
        self.root.addWidget(split, 1)
        self.root.addWidget(label(f"Stored in {sessions.SESSIONS_DIR}. Sessions older than "
                                  f"{settings.get('session_retention_days')} days are removed automatically (change it in "
                                  "Settings). Stored: Windows version, measurements, actions and your notes. Not stored: "
                                  "computer name, passwords, keys, personal files.", "faint", wrap=True))
        self._set_buttons(None)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh_action(self):
        self.refresh()

    def refresh(self):
        cur = self.tree.currentItem().data(0, Qt.UserRole + 5) if self.tree.currentItem() else None
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for s in sessions.list_sessions():
            kind = "New PC Setup" if s.get("type") == "setup" else "Diagnostics"
            when = s.get("updated") or s.get("started") or 0
            it = SortItem([sessions.title_of(s), kind, s.get("status", ""), time.strftime("%Y-%m-%d %H:%M",
                                                                                     time.localtime(when)),
                           _next_step(s)])
            it.setData(0, Qt.UserRole + 5, s["id"])
            it.setData(3, UR, when)
            it.setForeground(2, QColor(STATUS_COLOR.get(s.get("status"), theme.MUTED)))
            self.tree.addTopLevelItem(it)
            if s["id"] == cur:
                self.tree.setCurrentItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(3, Qt.DescendingOrder)
        self._filter()

    def _filter(self, *_):
        q = self.search.text().lower()
        k = self.kind.currentIndex()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            ok = (k == 0 or (k == 1 and it.text(1) == "Diagnostics") or (k == 2 and it.text(1) == "New PC Setup"))
            ok = ok and (not q or q in it.text(0).lower())
            it.setHidden(not ok)

    def _current(self):
        it = self.tree.currentItem()
        if not it:
            return None
        try:
            return sessions.load(it.data(0, Qt.UserRole + 5))
        except (OSError, ValueError):
            return None

    def _set_buttons(self, s):
        for b in (self.b_open, self.b_report, self.b_json, self.b_del):
            b.setEnabled(s is not None)

    def _picked(self, item, _prev):
        s = self._current()
        self._set_buttons(s)
        if s is None:
            self.d_title.setText("Select a session")
            self.d_body.setText("")
            return
        self.d_title.setText(sessions.title_of(s))
        if s.get("type") == "setup":
            c = provision.outcome_counts(s)
            parts = [f"{n} {report.SETUP_STATUS_TEXT.get(k, k).lower()}" for k, n in sorted(c.items()) if k]
            self.d_body.setText(f"Profile: {s.get('profile', {}).get('name')} v{s.get('profile', {}).get('version')}\n"
                                f"Setup type: {provision.SETUP_TYPES.get(s.get('setup_type'), ('',))[0]}\n"
                                f"Device: {s.get('device', {}).get('os', '')}\nStatus: {s.get('status')}\n\n"
                                + (" · ".join(parts) or "Nothing run yet")
                                + (f"\n\nNote: {s['note']}" if s.get("note") else ""))
            self.b_open.setText("Resume" if s.get("status") in ("interrupted", "paused", "deploying") else "Open")
        else:
            c = D.summary_counts(s)
            na = D.not_assessed(s)
            reps = s.get("repairs") or []
            ver = s.get("verification") or []
            self.d_body.setText(f"Device: {s.get('device', {}).get('os', '')}\nResult: {s.get('status')}\n\n"
                                f"Findings: {c['high']} high · {c['medium']} medium · {c['low']} low · {c['info']} info\n"
                                + "".join(f"  • {f['title']}\n" for f in (s.get('findings') or [])[:8])
                                + (f"\nRepairs: {len(reps)} ({sum(1 for r in reps if r.get('status') == 'done')} done), "
                                   f"{sum(1 for v in ver if v['verdict'] == 'Verified')} verified" if reps else "")
                                + (f"\nNot fully assessed: {', '.join(p for p, _w in na)}" if na else ""))
            self.b_open.setText("Open in Diagnose")

    def _open(self):
        s = self._current()
        if s is None:
            return
        if s.get("type") == "setup":
            page = self.main.pages["newpc"]
            if page.busy():
                return
            self.main.navigate("newpc")
            page.load_session(s)
        else:
            page = self.main.pages["diagnose"]
            page.session = s
            page._populate()
            self.main.navigate("diagnose")

    def _report(self):
        s = self._current()
        if s is None:
            return
        default = os.path.join(os.path.expanduser("~"), f"Remedy-{s.get('type')}-{s['id'][:8]}.html")
        path, _f = QFileDialog.getSaveFileName(self, "Save report", default, "HTML report (*.html)")
        if not path:
            return
        privacy = bool(settings.get("privacy_mode"))
        html = report.setup_html(s, privacy) if s.get("type") == "setup" else report.diagnostic_html(s, privacy)
        self._write(path, html)

    def _json(self):
        s = self._current()
        if s is None:
            return
        default = os.path.join(os.path.expanduser("~"), f"Remedy-{s.get('type')}-{s['id'][:8]}-anonymized.json")
        path, _f = QFileDialog.getSaveFileName(self, "Export anonymized record", default, "JSON (*.json)")
        if path:
            self._write(path, report.to_json(s, privacy=True, include_label=False))

    def _write(self, path, text):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            self.main.notify("error", "Couldn't save", str(exc))
            return
        self.main.notify("success", "Saved", os.path.basename(path), action=("Show file", lambda: open_location(path)))

    def _delete(self):
        s = self._current()
        if s and confirm(self, "Delete this session?", "Only Remedy's record is removed; nothing on the PC changes.",
                         "Delete", danger=True):
            sessions.delete(s["id"])
            self.refresh()
