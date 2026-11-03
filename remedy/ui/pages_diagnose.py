"""Diagnose & Repair: read-only scans -> evidence-backed findings -> an approved repair plan -> verified results."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLineEdit,
                               QMenu, QScrollArea, QSplitter, QStackedWidget, QTabWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import theme
from ..core import diagnostics as D
from ..core import repair, report, sessions
from ..core.utils import fmt_duration, log, settings
from .widgets import BasePage, Card, ProgressRow, attach_empty, button, confirm, label, make_tree, pill, size_widths

SEV_COLOR = {k: v[0] for k, v in theme.SEVERITY.items()}
PROV_STATE = {"queued": ("Queued", theme.FAINT), "running": ("Scanning…", theme.ACCENT),
              "complete": ("Complete", theme.OK), "partial": ("Partial", theme.WARN),
              "unavailable": ("Not assessed", theme.FAINT), "failed": ("Failed", theme.DANGER),
              "cancelled": ("Cancelled", theme.WARN)}


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


def _when(ts) -> str:
    if not ts:
        return ""
    return time.strftime("%d %b %Y, %H:%M", time.localtime(ts))


class ProviderChip(QFrame):
    """One diagnostic area with its live state (text + colour, never colour alone)."""

    def __init__(self, name: str):
        super().__init__()
        self.setObjectName("row")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        self.title = label(D.PROVIDER_LABEL.get(name, name), "cardtitle")
        self.state = label("Not run", "faint")
        lay.addWidget(self.title)
        lay.addWidget(self.state)

    def set_state(self, status: str, message: str = ""):
        text, color = PROV_STATE.get(status, ("Not run", theme.FAINT))
        self.state.setText(f"● {text}")
        self.state.setStyleSheet(f"color: {color}; font-size: 9pt; font-weight: 600;")
        self.setToolTip(message or text)


class DiagnosePage(BasePage):
    title = "Diagnose & Repair"
    subtitle = ("Find out why this PC is slow, unstable or unsafe. Scans only read; nothing changes until you approve "
                "a repair.")

    def __init__(self, main):
        super().__init__(main)
        self.session = None
        self.plan_ids: list = []
        self._opening = False
        self.btn_export = self.add_action("Export report…", "", self.export_report,
                                          "Save this diagnosis as an HTML report or JSON record")
        self.btn_custom = self.add_action("Custom ▾", "", None, "Pick which areas to check")
        self.btn_full = self.add_action("Full Diagnosis", "", lambda: self.start("full"),
                                        "About 30 seconds of sampling plus every check, including logon tasks")
        self.btn_quick = self.add_action("Quick Scan", "primary", lambda: self.start("quick"),
                                         "About 8 seconds of sampling plus the main checks")
        self._custom_menu()

        self.meta = label("", "muted")
        self.root.addWidget(self.meta)
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.chips = {}
        for name in D.PROVIDERS:
            chip = ProviderChip(name)
            self.chips[name] = chip
            strip.addWidget(chip, 1)
        self.root.addLayout(strip)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        self.views = QStackedWidget()
        self.views.addWidget(self._empty_view())
        self.views.addWidget(self._results_view())
        self.views.addWidget(self._plan_view())
        self.root.addWidget(self.views, 1)
        self._load_last()

    # ------------------------------------------------------------------ building
    def _custom_menu(self):
        m = QMenu(self.btn_custom)
        self.custom_actions = {}
        for name in D.PROVIDERS:
            a = m.addAction(D.PROVIDER_LABEL[name])
            a.setCheckable(True)
            a.setChecked(True)
            self.custom_actions[name] = a
        m.addSeparator()
        m.addAction("Run custom scan", lambda: self.start("custom"))
        self.btn_custom.setMenu(m)

    def _empty_view(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 0)
        hero = QFrame()
        hero.setObjectName("hero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(28, 24, 28, 24)
        hl.setSpacing(10)
        hl.addWidget(label("Start here", "kicker"))
        t = label("What's wrong with this PC?", "h1")
        hl.addWidget(t)
        hl.addWidget(label("Remedy watches the PC for a few seconds, reads what Windows reports, and lists only what "
                           "matters, each with the evidence behind it and how sure we are. Slowness is not treated as "
                           "malware, and nothing is changed by scanning.", "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        modes = [("Quick Scan", "≈ 8 s of sampling + the main checks. Time also depends on how much junk there is "
                                "to measure.", "quick", "primary"),
                 ("Full Diagnosis", "≈ 30 s of sampling, more connection tests, and logon tasks in the startup count.",
                  "full", "")]
        for i, (name, text, mode, kind) in enumerate(modes):
            c = Card()
            c.setMinimumHeight(150)
            c.body.addWidget(label(name, "h2"))
            c.body.addWidget(label(text, "muted", wrap=True))
            c.body.addWidget(button(f"Start {name}", kind, lambda _=False, m=mode: self.start(m)), 0, Qt.AlignLeft)
            grid.addWidget(c, 0, i)
        hl.addLayout(grid)
        hl.addWidget(label("Checks: performance (CPU, memory, disk response), storage and safe junk, startup load, "
                           "Windows (restarts, crashes, devices), security (Defender, firewall, Defender's own "
                           "detections), drive health and network. The network check opens a connection to 1.1.1.1 "
                           "and 8.8.8.8 to time it; no data is sent.", "faint", wrap=True))
        lay.addWidget(hero)
        lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sc.setWidget(w)
        return sc

    def _results_view(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        filt = QHBoxLayout()
        filt.addWidget(label("Findings", "cardtitle"))
        filt.addStretch(1)
        self.sev_filter = QComboBox()
        self.sev_filter.addItems(["All severities", "High", "Medium and above", "Hide info"])
        self.sev_filter.setAccessibleName("Filter findings by severity")
        self.sev_filter.currentIndexChanged.connect(self._filter)
        filt.addWidget(self.sev_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search findings…")
        self.search.setFixedWidth(180)
        self.search.textChanged.connect(self._filter)
        filt.addWidget(self.search)
        ll.addLayout(filt)
        self.tree = make_tree(["Severity", "Finding", "Status"], multi=False, sortable=False, stretch_col=1)
        size_widths(self.tree, {0: 96, 2: 170})
        self.tree.setRootIsDecorated(False)
        self.tree.currentItemChanged.connect(self._picked)
        self.tree.itemDoubleClicked.connect(lambda *_: self._first_link())
        self.empty = attach_empty(self.tree, "No findings yet.")
        ll.addWidget(self.tree, 1)
        self.not_assessed = label("", "faint", wrap=True)
        ll.addWidget(self.not_assessed)
        split.addWidget(left)
        split.addWidget(self._inspector())
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 6)
        split.setSizes([520, 620])
        lay.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.counts = label("", "muted")
        bottom.addWidget(self.counts, 1)
        self.btn_plan = button("Review repair plan (0)", "primary", lambda: self.main.navigate("diagnose", {"view": "plan"}),
                               "See exactly what would change before anything runs")
        bottom.addWidget(self.btn_plan)
        lay.addLayout(bottom)
        return w

    def _inspector(self):
        card = Card()
        card.setMinimumWidth(360)
        self.i_title = label("Pick a finding", "h2")
        self.i_title.setWordWrap(True)
        card.body.addWidget(self.i_title)
        self.i_chips = QHBoxLayout()
        self.i_chips.setSpacing(6)
        card.body.addLayout(self.i_chips)
        self.tabs = QTabWidget()
        self.t_what = label("", "", wrap=True)
        self.t_why = label("", "", wrap=True)
        self.t_do = label("", "", wrap=True)
        for text_label, name in ((self.t_what, "What's happening"), (self.t_why, "Why we think so"),
                                 (self.t_do, "What to do")):
            text_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(4, 10, 8, 4)
            hl.addWidget(text_label)
            hl.addStretch(1)
            sc.setWidget(holder)
            self.tabs.addTab(sc, name)
        self.ev_tree = make_tree(["Reading", "Value", "Source", "Window"], multi=False, sortable=False, stretch_col=0)
        size_widths(self.ev_tree, {1: 150, 2: 170, 3: 110})
        self.tabs.addTab(self.ev_tree, "Evidence")
        card.body.addWidget(self.tabs, 1)
        self.i_actions = QHBoxLayout()
        self.i_actions.setSpacing(8)
        card.body.addLayout(self.i_actions)
        return card

    def _plan_view(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(button("← Back to findings", "ghost", lambda: self.main.go_back()
                              if self.main.history.can_back() else self.main.navigate("diagnose")))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(label("Repair plan", "h2"))
        lay.addWidget(label("Review every action before anything changes. Only ticked actions run, one at a time. "
                            "Afterwards Remedy measures again and shows whether each one really helped.", "muted",
                            wrap=True))
        self.plan_tree = make_tree(["Action", "Risk", "Can it be undone?", "Restart"], multi=False, sortable=False,
                                   stretch_col=2)
        size_widths(self.plan_tree, {0: 280, 1: 80, 3: 80})
        self.plan_tree.itemChanged.connect(lambda *_: self._plan_changed())
        self.plan_tree.currentItemChanged.connect(self._plan_picked)
        self.plan_empty = attach_empty(self.plan_tree, "Nothing in the plan yet. Open a finding and press "
                                                       "'Add to repair plan'.")
        lay.addWidget(self.plan_tree, 2)
        self.plan_detail = label("", "muted", wrap=True)
        lay.addWidget(self.plan_detail)
        self.preflight_box = QHBoxLayout()
        self.preflight_box.setSpacing(16)
        lay.addLayout(self.preflight_box)
        row = QHBoxLayout()
        row.addWidget(button("Preview every change", "", self.preview))
        row.addStretch(1)
        self.btn_apply = button("Apply 0 approved changes", "primary", self.apply)
        row.addWidget(self.btn_apply)
        lay.addLayout(row)
        self.verify_card = Card("Before and after")
        self.verify_tree = make_tree(["Repair", "Measurement", "Before", "After", "Verified?"], multi=False,
                                     sortable=False, stretch_col=1)
        size_widths(self.verify_tree, {0: 200, 2: 110, 3: 110, 4: 170})
        self.verify_card.body.addWidget(self.verify_tree)
        vr = QHBoxLayout()
        self.undo_note = label("", "faint", wrap=True)
        vr.addWidget(self.undo_note, 1)
        vr.addWidget(button("Re-run affected checks", "", self.rerun_checks))
        vr.addWidget(button("Finish report…", "", self.export_report))
        self.verify_card.body.addLayout(vr)
        self.verify_card.hide()
        lay.addWidget(self.verify_card, 2)
        return w

    # ------------------------------------------------------------------ routing
    def open_route(self, params: dict) -> None:
        super().open_route(params)
        if params.get("view") == "plan":
            self._refresh_plan()
            self.views.setCurrentIndex(2)
            return
        self.views.setCurrentIndex(1 if self.session else 0)
        fid = params.get("finding")
        if fid and self.session:
            self._opening = True
            try:
                for i in range(self.tree.topLevelItemCount()):
                    it = self.tree.topLevelItem(i)
                    if it.data(0, Qt.UserRole) == fid:
                        self.tree.setCurrentItem(it)
                        self.tree.scrollToItem(it)
                        break
            finally:
                self._opening = False

    def route_label(self, params: dict) -> str:
        if params.get("view") == "plan":
            return "Repair Plan"
        f = self._finding(params.get("finding"))
        return f["title"][:48] + ("…" if len(f["title"]) > 48 else "") if f else ""

    def refresh_action(self):
        self.start("quick")

    # ------------------------------------------------------------------ scanning
    def set_busy_ui(self, busy):
        for b in (self.btn_quick, self.btn_full, self.btn_custom, self.btn_apply):
            b.setEnabled(not busy)

    def start(self, mode: str):
        if self.busy():
            return
        cfg = D.MODES.get(mode, D.MODES["quick"])
        names = list(cfg["providers"])
        opts = dict(cfg["opts"])
        if mode == "custom":
            names = [n for n, a in self.custom_actions.items() if a.isChecked()]
            if not names:
                self.main.notify("info", "Pick at least one area", "Use Custom ▾ to choose what to check.")
                return
        for n, chip in self.chips.items():
            chip.set_state("queued" if n in names else "", "" if n in names else "Not part of this scan")
        label_ = cfg["label"] if mode != "custom" else "Custom scan"

        def work(ctx):
            session = D.new_session(mode)
            ev, st = D.run_providers(
                ctx, names, opts,
                on_status=lambda s: ctx.progress(f"@prov|{s.name}|{s.status}|{s.message}", -1, force=True),
                emit=lambda text, pct: ctx.progress(text, pct))
            return D.finish_session(session, ev, st, cancelled=ctx.cancelled())

        def prog(text, pct):
            if text.startswith("@prov|"):
                _t, name, status, msg = text.split("|", 3)
                if name in self.chips:
                    self.chips[name].set_state(status, msg)
                if status == "running":
                    self.progress.update_progress(f"Checking {D.PROVIDER_LABEL.get(name, name).lower()}…", pct)
                return
            self.progress.update_progress(text, pct)

        self.run(work, progress=self.progress, text=f"{label_}: starting…", on_done=self._scanned, on_progress=prog,
                 summary=lambda s: f"{label_} finished: {len(s['findings'])} finding(s)")

    def _scanned(self, session):
        prev = self.session
        self.session = session
        if prev and prev.get("dismissed"):
            session["dismissed"] = {k: v for k, v in prev["dismissed"].items()
                                    if any(f["id"] == k for f in session["findings"])}
        self.plan_ids = [a for a in self.plan_ids if any(a in f.get("actions", []) for f in session["findings"])]
        self._save()
        self._populate()
        self.main.navigate("diagnose", {}, replace=True)
        c = D.summary_counts(session)
        serious = c["high"] + c["medium"]
        na = len(D.not_assessed(session))
        kind = "error" if c["high"] else "warning" if c["medium"] else "success"
        self.main.notify(kind, f"{serious} issue(s) worth attention" if serious else "No serious problems found",
                         (f"{c['high']} high, {c['medium']} medium, {c['low'] + c['info']} minor. " if serious else
                          "The checks that ran look fine. ")
                         + (f"{na} area(s) couldn't be fully checked here." if na else ""))
        dash = self.main.pages.get("dashboard")
        if dash is not None and hasattr(dash, "refresh_summary"):
            dash.refresh_summary()

    def _save(self):
        if self.session:
            try:
                sessions.save(self.session)
            except OSError as exc:
                log.warning("Could not save diagnostic session: %s", exc)

    def _load_last(self):
        try:
            last = sessions.latest("diagnostic")
        except Exception:  # noqa: BLE001
            last = None
        if last and last.get("findings") is not None:
            self.session = last
            self._populate()

    # ------------------------------------------------------------------ results
    def _finding(self, fid):
        if not self.session or not fid:
            return None
        return next((f for f in self.session["findings"] if f["id"] == fid), None)

    def _populate(self):
        s = self.session
        took = (s.get("finished") or 0) - (s.get("started") or 0)
        mode = {"quick": "Quick Scan", "full": "Full Diagnosis"}.get(s.get("mode"), "Custom scan")
        self.meta.setText(f"Last {mode}: {_when(s.get('started'))} · took {fmt_duration(took) if took >= 60 else f'{took:.0f} s'}"
                          f" · result: {s.get('status')}. Values are what was measured then; scan again to refresh.")
        for name, chip in self.chips.items():
            st = (s.get("providers") or {}).get(name)
            chip.set_state(st["status"], st.get("message", "")) if st else chip.set_state("", "Not part of this scan")
        self.tree.blockSignals(True)
        self.tree.clear()
        dismissed = s.get("dismissed") or {}
        for f in s["findings"]:
            color, sev = theme.SEVERITY.get(f["severity"], (theme.MUTED, f["severity"]))
            status = f"{D.STATUS_LABEL.get(f['status'], f['status'])} · {f['confidence']} confidence"
            it = QTreeWidgetItem([f"● {sev}", f["title"], status])
            it.setData(0, Qt.UserRole, f["id"])
            it.setForeground(0, QColor(color))
            it.setForeground(2, QColor(theme.MUTED))
            it.setToolTip(1, f["what"])
            if f["id"] in dismissed:
                it.setText(1, f"{f['title']}  (dismissed)")
                for col in range(3):
                    it.setForeground(col, QColor(theme.FAINT))
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        na = D.not_assessed(s)
        self.not_assessed.setText(("Not fully assessed: " + "; ".join(f"{p}: {why}" for p, why in na)) if na else
                                  "Every area was checked.")
        self.empty.setText("No problems found by the checks that ran." if s["findings"] else "No findings yet.")
        c = D.summary_counts(s)
        self.counts.setText(f"{c['high']} high · {c['medium']} medium · {c['low']} low · {c['info']} info"
                            + (f" · {len(dismissed)} dismissed" if dismissed else ""))
        self._filter()
        self._update_plan_button()
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self._show_finding(None)
        if self.views.currentIndex() == 0:
            self.views.setCurrentIndex(1)

    def _filter(self, *_):
        mode = self.sev_filter.currentIndex()
        allowed = [None, {"high"}, {"high", "medium"}, {"high", "medium", "low"}][mode]
        q = self.search.text().lower()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            f = self._finding(it.data(0, Qt.UserRole))
            ok = f is not None and (allowed is None or f["severity"] in allowed) and (
                not q or q in f["title"].lower() or q in f["what"].lower())
            it.setHidden(not ok)

    def _picked(self, item, _prev):
        fid = item.data(0, Qt.UserRole) if item else None
        self._show_finding(self._finding(fid))
        if fid and not self._opening and self.isVisible():
            cur = self.main.history.current
            replace = cur is not None and cur.key == "diagnose" and "finding" in cur.args
            self.main.navigate("diagnose", {"finding": fid}, replace=replace)

    def _show_finding(self, f):
        _clear(self.i_chips)
        _clear(self.i_actions)
        self.ev_tree.clear()
        if f is None:
            self.i_title.setText("Pick a finding" if self.session and self.session["findings"] else
                                 "Nothing to inspect")
            for t in (self.t_what, self.t_why, self.t_do):
                t.setText("")
            return
        self.i_title.setText(f["title"])
        color, sev = theme.SEVERITY.get(f["severity"], (theme.MUTED, f["severity"]))
        self.i_chips.addWidget(pill(f"{sev} severity", color))
        self.i_chips.addWidget(pill(D.STATUS_LABEL.get(f["status"], f["status"]), theme.INFO))
        self.i_chips.addWidget(pill(f"{f['confidence'].capitalize()} confidence", theme.MUTED))
        self.i_chips.addWidget(pill(f["category"], theme.ACCENT2))
        self.i_chips.addStretch(1)
        self.t_what.setText(f["what"])
        why = f["why"]
        if f.get("alternatives"):
            why += "\n\nOther possible causes:\n" + "\n".join(f"•  {a}" for a in f["alternatives"])
        self.t_why.setText(why)
        steps = "\n".join(f"{i}.  {s}" for i, s in enumerate(f.get("next_steps") or [], 1)) or "No action needed."
        if f.get("actions"):
            steps += "\n\nRemedy can do this for you after you review it: " + ", ".join(
                repair.ACTIONS[a].title for a in f["actions"] if a in repair.ACTIONS) + "."
        self.t_do.setText(steps)
        evs = self.session.get("evidence") or {}
        for eid in f.get("evidence_ids") or []:
            e = evs.get(eid)
            if not e:
                continue
            ev = D.Evidence(**e)
            win = "single reading" if not ev.window_s else (f"last {ev.window_s / 86400:.0f} days" if ev.window_s >= 86400
                                                            else f"{ev.window_s:.0f} s · {ev.samples} samples")
            it = QTreeWidgetItem([ev.label, ev.display(), ev.source, win])
            it.setToolTip(0, ev.label)
            it.setToolTip(1, ev.display())
            it.setToolTip(2, f"{ev.source} ({ev.reliability}) at {_when(ev.collected_at)}")
            self.ev_tree.addTopLevelItem(it)
        dismissed = (self.session.get("dismissed") or {}).get(f["id"])
        for aid in f.get("actions") or []:
            if aid not in repair.ACTIONS:
                continue
            inplan = aid in self.plan_ids
            b = button("In repair plan ✓" if inplan else "Add to repair plan", "" if inplan else "primary",
                       lambda _=False, a=aid: self._add_to_plan(a))
            b.setEnabled(not inplan)
            self.i_actions.addWidget(b)
        for text, key, params in f.get("links") or []:
            self.i_actions.addWidget(button(text, "", lambda _=False, k=key, p=params: self.main.navigate(k, p)))
        self.i_actions.addStretch(1)
        self.i_actions.addWidget(button("Restore" if dismissed else "Dismiss…", "ghost",
                                        lambda _=False, fid=f["id"]: self._dismiss(fid)))

    def _first_link(self):
        it = self.tree.currentItem()
        f = self._finding(it.data(0, Qt.UserRole)) if it else None
        if f and f.get("links"):
            _t, key, params = f["links"][0]
            self.main.navigate(key, params)

    def _dismiss(self, fid):
        d = self.session.setdefault("dismissed", {})
        if fid in d:
            del d[fid]
        else:
            reason, ok = QInputDialog.getText(self, "Dismiss finding", "Why? (kept in the session and the report)",
                                              text="Expected on this PC")
            if not ok:
                return
            d[fid] = reason.strip()[:200] or "Dismissed"
        self._save()
        cur = fid
        self._populate()
        self.open_route({"finding": cur})

    # ------------------------------------------------------------------ repair plan
    def _add_to_plan(self, aid):
        if aid not in self.plan_ids:
            self.plan_ids.append(aid)
        self._update_plan_button()
        it = self.tree.currentItem()
        self._show_finding(self._finding(it.data(0, Qt.UserRole)) if it else None)
        self.main.notify("info", f"Added: {repair.ACTIONS[aid].title}", "Nothing runs until you review the plan "
                         "and press Apply.", action=("Review plan", lambda: self.main.navigate("diagnose",
                                                                                               {"view": "plan"})))

    def _update_plan_button(self):
        self.btn_plan.setText(f"Review repair plan ({len(self.plan_ids)})")

    def _refresh_plan(self):
        self.plan_tree.blockSignals(True)
        self.plan_tree.clear()
        for aid in self.plan_ids:
            a = repair.ACTIONS[aid]
            usable, why = repair.available(a)
            it = QTreeWidgetItem([a.title, a.risk, a.reversible, "Yes" if a.reboot else "No"])
            it.setData(0, Qt.UserRole, aid)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if usable else Qt.Unchecked)
            if not usable:
                it.setText(0, f"{a.title}  ({why})")
                it.setForeground(0, QColor(theme.FAINT))
                it.setFlags(Qt.ItemIsSelectable)
            it.setToolTip(2, a.reversible)
            self.plan_tree.addTopLevelItem(it)
        self.plan_tree.blockSignals(False)
        if self.plan_tree.topLevelItemCount():
            self.plan_tree.setCurrentItem(self.plan_tree.topLevelItem(0))
        else:
            self.plan_detail.setText("")
        self._plan_changed()
        self._show_verification()

    def _checked_plan(self) -> list:
        out = []
        for i in range(self.plan_tree.topLevelItemCount()):
            it = self.plan_tree.topLevelItem(i)
            if it.checkState(0) == Qt.Checked:
                out.append(it.data(0, Qt.UserRole))
        return out

    def _plan_changed(self):
        ids = self._checked_plan()
        self.btn_apply.setText(f"Apply {len(ids)} approved change{'s' if len(ids) != 1 else ''}")
        self.btn_apply.setEnabled(bool(ids) and (self.worker is None or not self.worker.isRunning()))
        _clear(self.preflight_box)
        self.preflight_box.addWidget(label("Preflight", "cardtitle"), 0, Qt.AlignTop)
        parts = [f"<span style='color:{theme.OK if ok else theme.WARN}'>{'✓' if ok else '!'} <b>{name}</b>: "
                 f"{detail}</span>" for name, ok, detail in repair.preflight(ids)]
        lb = label("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts), "", wrap=True)
        lb.setTextFormat(Qt.RichText)
        self.preflight_box.addWidget(lb, 1)

    def _plan_picked(self, item, _prev):
        if item is None:
            return
        a = repair.ACTIONS.get(item.data(0, Qt.UserRole))
        if a:
            self.plan_detail.setText(f"What changes: {a.change}\nWhy: {a.why}\nUndo: {a.reversible}\n"
                                     f"Time: {a.duration or 'varies'} · Administrator: {'needed' if a.admin else 'not needed'}")

    def preview(self):
        from .dialogs import info
        ids = self._checked_plan()
        if not ids:
            self.main.notify("info", "Nothing ticked", "Tick at least one action in the plan.")
            return
        info(self, "Exactly what will change", "Only these run, in this order. Each is measured again afterwards.",
             bullets=[f"{repair.ACTIONS[a].title}: {repair.ACTIONS[a].change}" for a in ids])

    def apply(self):
        ids = self._checked_plan()
        if not ids or not self.session:
            return
        if self.busy():
            return
        if not confirm(self, f"Apply {len(ids)} change{'s' if len(ids) != 1 else ''}?",
                       "These run one at a time. You can cancel between steps; finished steps stay done.",
                       "Apply now", bullets=[f"{repair.ACTIONS[a].title} ({repair.ACTIONS[a].risk} risk)" for a in ids]):
            return
        before = D.evidence_from_session(self.session)
        provs = repair.providers_for(ids)

        def work(ctx):
            receipts = repair.run_plan(ctx, ids, emit=lambda t, p: ctx.progress(t, int(p * 0.7)))
            ctx.progress("Measuring again to verify…", 72, force=True)
            after, _st = D.run_providers(None, provs, {"seconds": 3, "net_attempts": 2},
                                         emit=lambda t, p: ctx.progress(f"Verifying: {t}", 72 + int(p * 0.28)))
            return receipts, after

        def done(res):
            receipts, after = res
            rows = repair.verify(before, after, receipts)
            self.session.setdefault("repairs", []).extend(receipts)
            self.session["verification"] = rows
            merged = dict(before)
            merged.update(after)
            self.session["evidence"] = {k: D.asdict(v) for k, v in merged.items()}
            self.session["findings"] = [D.asdict(f) for f in D.analyze(merged)]
            self.plan_ids = [a for a in self.plan_ids if a not in [r["id"] for r in receipts if r["status"] == "done"]]
            self._save()
            self._populate()
            self._refresh_plan()
            ok = sum(1 for r in receipts if r["status"] == "done")
            ver = sum(1 for r in rows if r["verdict"] == "Verified")
            fails = [r for r in receipts if r["status"] != "done"]
            self.main.notify("success" if not fails else "warning", f"{ok} of {len(receipts)} repair(s) done",
                             f"{ver} result(s) verified by measuring again." + (
                                 " " + "; ".join(f"{r['title']}: {r['summary']}" for r in fails) if fails else ""))

        self.run(work, progress=self.progress, text="Applying approved repairs…", on_done=done,
                 summary=lambda r: f"{sum(1 for x in r[0] if x['status'] == 'done')} of {len(r[0])} repairs done")

    def _show_verification(self):
        rows = (self.session or {}).get("verification") or []
        self.verify_card.setVisible(bool(rows))
        self.verify_tree.clear()
        colors = {"Verified": theme.OK, "No change": theme.WARN, "Worse": theme.DANGER, "Not verified": theme.MUTED}
        for r in rows:
            it = QTreeWidgetItem([r["action"], r["metric"], r["before"], r["after"],
                                  r["verdict"] + (f" ({r['note']})" if r.get("note") else "")])
            it.setForeground(4, QColor(colors.get(r["verdict"], theme.MUTED)))
            it.setToolTip(4, r.get("note") or r["verdict"])
            self.verify_tree.addTopLevelItem(it)
        done = [r for r in (self.session or {}).get("repairs", []) if r.get("status") == "done"]
        self.undo_note.setText("Undo: " + " ".join(f"{repair.ACTIONS[r['id']].title}: {repair.ACTIONS[r['id']].reversible}"
                                                    for r in done if r["id"] in repair.ACTIONS) if done else "")

    def rerun_checks(self):
        if not self.session or self.busy():
            return
        ids = [r["id"] for r in self.session.get("repairs", []) if r.get("status") == "done"]
        provs = repair.providers_for(ids) or ["storage"]
        before = D.evidence_from_session(self.session)

        def work(ctx):
            ev, _st = D.run_providers(ctx, provs, {"seconds": 3, "net_attempts": 2},
                                      emit=lambda t, p: ctx.progress(t, p))
            return ev

        def done(after):
            merged = dict(before)
            merged.update(after)
            self.session["evidence"] = {k: D.asdict(v) for k, v in merged.items()}
            self.session["findings"] = [D.asdict(f) for f in D.analyze(merged)]
            self._save()
            self._populate()
            self._refresh_plan()

        self.run(work, progress=self.progress, text="Re-running affected checks (read-only)…", on_done=done,
                 summary=lambda _e: "Checks updated")

    # ------------------------------------------------------------------ export
    def export_report(self):
        if not self.session:
            self.main.notify("info", "Nothing to export yet", "Run a Quick Scan first.")
            return
        default = os.path.join(os.path.expanduser("~"), "Documents" if os.path.isdir(
            os.path.join(os.path.expanduser("~"), "Documents")) else "",
            f"Remedy-diagnosis-{time.strftime('%Y%m%d-%H%M')}.html")
        path, flt = QFileDialog.getSaveFileName(self, "Save diagnostic report", default,
                                                "HTML report (*.html);;JSON record (*.json)")
        if not path:
            return
        privacy = bool(settings.get("privacy_mode"))
        try:
            if path.lower().endswith(".json") or "JSON" in flt:
                text = report.to_json(self.session, privacy)
            else:
                text = report.diagnostic_html(self.session, privacy)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            self.main.notify("error", "Couldn't save the report", str(exc))
            return
        from ..core.utils import open_location
        self.main.notify("success", "Report saved", os.path.basename(path) + (" (names redacted)" if privacy else ""),
                         action=("Show file", lambda: open_location(path)))
