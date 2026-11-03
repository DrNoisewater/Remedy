"""New PC Setup: pick a profile -> inspect the PC -> configure -> review the plan -> deploy -> verify -> hand over.

Each wizard step is a route ({"step": n}), so the mouse Back button moves between steps. Going back never undoes
or repeats work: the deploy journal remembers what is done and re-running skips it. Drafts save automatically.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import theme
from ..core import appstore, maintenance, osinfo, provision, report, sessions
from ..core.utils import IS_WINDOWS, log, open_location, settings
from .widgets import (UR, BasePage, Card, Console, ProgressRow, SortItem, attach_empty, button, confirm, label,
                      make_tree, pill, size_widths)

STAGES = [("Inspect", 1), ("Configure", 2), ("Deploy", 3), ("Verify", 5), ("Handoff", 6)]
STEP_TITLES = {0: "", 1: "Inspect", 2: "Configure", 3: "Review plan", 4: "Deploy", 5: "Verify", 6: "Handoff"}
STATE_TEXT = {"todo": "Will run", "satisfied": "Already in place", "blocked": "Blocked", "review": "Needs review",
              "manual": "Manual check"}
STATE_COLOR = {"todo": theme.ACCENT, "satisfied": theme.OK, "blocked": theme.FAINT, "review": theme.WARN,
               "manual": theme.INFO}
JOURNAL_COLOR = {"verified": theme.OK, "satisfied": theme.OK, "done": theme.ACCENT, "failed": theme.DANGER,
                 "interrupted": theme.WARN, "running": theme.ACCENT, "pending": theme.MUTED, "skipped": theme.FAINT,
                 "blocked": theme.FAINT, "manual_done": theme.OK, "manual_open": theme.WARN}
UNFINISHED = ("draft", "deploying", "interrupted", "paused", "deployed", "verified")


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


def _scroll(widget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sc.setWidget(widget)
    return sc


class ProfileCard(QFrame):
    """Selectable profile tile with wrapped text. Click, Space or Enter selects it; Tab moves between tiles."""

    def __init__(self, profile, on_pick):
        super().__init__()
        self.profile, self.on_pick = profile, on_pick
        self.setObjectName("row")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"Profile {profile.name}")
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        top = QHBoxLayout()
        name = label(profile.name)
        name.setStyleSheet("font-weight: 650; font-size: 11pt;")
        top.addWidget(name, 1)
        top.addWidget(label("Built-in" if profile.builtin else f"Yours · v{profile.version}", "faint"))
        lay.addLayout(top)
        desc = label(profile.description, "muted", wrap=True)
        desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(desc, 1)
        lay.addWidget(label(f"{len(profile.apps)} apps · {len(profile.tweaks)} settings · "
                            f"{len(profile.checklist)} checks", "faint"))
        self.setToolTip(profile.description + (f"\n\nNotes: {profile.notes}" if profile.notes else ""))

    def set_selected(self, on: bool):
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        self.setFocus()
        self.on_pick(self.profile.id)

    def mouseDoubleClickEvent(self, e):
        self.on_pick(self.profile.id, go=True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.on_pick(self.profile.id, go=e.key() != Qt.Key_Space)
        else:
            super().keyPressEvent(e)


class NewPCPage(BasePage):
    title = "New PC Setup"
    subtitle = ("Prepare a client's computer with a repeatable, verified setup: inspect, configure, install, verify "
                "and hand over a report. Nothing changes until you review the plan.")

    def __init__(self, main):
        super().__init__(main)
        self.profiles = provision.load_profiles()
        self.profile = None              # working copy for the current session
        self.session = None
        self.inspection = None
        self.tasks: list = []
        self.step = 0
        self._pause = False
        self._auto_review = False
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(600)
        self.save_timer.timeout.connect(self._save)

        self.stepper = QHBoxLayout()
        self.stepper.setSpacing(4)
        self.stage_btns = []
        for i, (name, step) in enumerate(STAGES):
            if i:
                self.stepper.addWidget(label("──", "faint"))
            b = QPushButton(f"{i + 1}  {name}")
            b.setProperty("kind", "step")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, s=step: self.goto(s))
            self.stage_btns.append((b, step))
            self.stepper.addWidget(b)
        self.stepper.addStretch(1)
        self.session_label = label("", "faint")
        self.stepper.addWidget(self.session_label)
        self.stepper_wrap = QWidget()
        self.stepper_wrap.setLayout(self.stepper)
        self.root.addWidget(self.stepper_wrap)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        self.views = QStackedWidget()
        for build in (self._v_start, self._v_inspect, self._v_configure, self._v_review, self._v_deploy,
                      self._v_verify, self._v_handoff):
            self.views.addWidget(build())
        self.root.addWidget(self.views, 1)
        self._render_profiles()
        self._sync_stepper()

    # ================================================================== views
    def _v_start(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(14)
        self.resume_banner = QFrame()
        self.resume_banner.setObjectName("banner")
        self.resume_banner.setProperty("tone", "warn")
        rb = QHBoxLayout(self.resume_banner)
        rb.setContentsMargins(14, 10, 10, 10)
        self.resume_text = label("", "", wrap=True)
        rb.addWidget(self.resume_text, 1)
        rb.addWidget(button("Discard", "ghost", self._discard_resume))
        rb.addWidget(button("Review before resume", "", lambda: self._resume(review=True)))
        rb.addWidget(button("Resume setup", "primary", lambda: self._resume(review=False)))
        self.resume_banner.hide()
        lay.addWidget(self.resume_banner)

        top = Card()
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(10)
        g.addWidget(label("Client / device", "cardtitle"), 0, 0)
        self.client = QLineEdit()
        self.client.setPlaceholderText("Optional label, e.g. “Front desk PC” or “Laptop 12”")
        self.client.setMaxLength(60)
        self.client.setAccessibleName("Client or device label")
        self.client.setToolTip("Kept in this PC's session only. Left out of exported reports unless you include it.")
        g.addWidget(self.client, 0, 1)
        info = osinfo.detect()
        g.addWidget(label("Detected", "cardtitle"), 2, 0)
        det = label(info.summary(), "muted", wrap=True)
        det.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        g.addWidget(det, 2, 1, 1, 2)
        g.addWidget(label("Setup type", "cardtitle"), 1, 0)
        types = QHBoxLayout()
        self.type_group = QButtonGroup(self)
        for i, (key, (name, desc)) in enumerate(provision.SETUP_TYPES.items()):
            b = QPushButton(name)
            b.setProperty("kind", "choice")
            b.setCheckable(True)
            b.setToolTip(desc)
            b.setProperty("setup_type", key)
            b.setChecked(i == 0)
            self.type_group.addButton(b)
            types.addWidget(b)
        types.addStretch(1)
        g.addLayout(types, 1, 1, 1, 2)
        g.setColumnStretch(1, 1)
        top.body.addLayout(g)
        lay.addWidget(top)

        head = QHBoxLayout()
        head.addWidget(label("Select a profile", "h2"))
        head.addStretch(1)
        head.addWidget(button("Import…", "", self._import, "Load a .remedyprofile file (checked before use)"))
        head.addWidget(button("Export…", "", self._export, "Save the selected profile to share or carry on USB"))
        head.addWidget(button("Duplicate", "", self._duplicate, "Copy the selected profile to make it your own"))
        self.btn_delete = button("Delete", "ghost", self._delete_profile)
        head.addWidget(self.btn_delete)
        lay.addLayout(head)
        self.profile_grid = QGridLayout()
        self.profile_grid.setSpacing(12)
        for c in range(3):
            self.profile_grid.setColumnStretch(c, 1)
        self.profile_cards = []
        self._selected_id = ""
        lay.addLayout(self.profile_grid)
        where = "on this USB stick (portable mode)" if provision.portable_mode() else "on this PC"
        lay.addWidget(label(f"Your profiles are saved {where}: {provision.profile_dir()}. Profiles hold choices only, "
                            "never passwords, keys or personal files.", "faint", wrap=True))
        lay.addStretch(1)
        nav = QHBoxLayout()
        nav.addWidget(button("My Standard Setup", "", self.my_standard_setup,
                             "Use your last-approved profile, inspect this PC and go straight to the review"))
        nav.addStretch(1)
        nav.addWidget(button("Inspect PC →", "primary", self._begin))
        lay.addLayout(nav)
        return _scroll(w)

    def _v_inspect(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Inspect this PC", "h2"))
        lay.addWidget(label("Read-only. Each line says how Remedy knows it: Known, Not available, Requires admin or "
                            "Requires restart. Nothing is guessed.", "muted", wrap=True))
        self.facts = make_tree(["Item", "Value", "Status", "Source"], multi=False, sortable=False, stretch_col=1)
        size_widths(self.facts, {0: 220, 2: 130, 3: 190})
        self.facts_empty = attach_empty(self.facts, "Inspection starts automatically.")
        lay.addWidget(self.facts, 1)
        nav = QHBoxLayout()
        nav.addWidget(button("← Back", "", self.main.go_back))
        nav.addWidget(button("Inspect again", "", self.inspect))
        nav.addStretch(1)
        self.btn_to_config = button("Configure →", "primary", lambda: self.goto(2))
        nav.addWidget(self.btn_to_config)
        lay.addLayout(nav)
        return w

    def _v_configure(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.cfg_title = label("Configure", "h2")
        head.addWidget(self.cfg_title, 1)
        self.cfg_modified = pill("Changed from profile", theme.WARN)
        self.cfg_modified.hide()
        head.addWidget(self.cfg_modified)
        head.addWidget(button("Save as my profile…", "", self._save_as_profile,
                              "Keep these choices as a reusable profile"))
        lay.addLayout(head)
        split = QSplitter(Qt.Horizontal)
        self.cats = QListWidget()
        self.cats.setMaximumWidth(300)
        self.cats.setWordWrap(True)
        self.cats.setAccessibleName("Setup categories")
        self.cats.currentRowChanged.connect(lambda _r: self._render_category())
        split.addWidget(self.cats)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.cat_blurb = label("", "muted", wrap=True)
        rl.addWidget(self.cat_blurb)
        self.cfg_tree = make_tree(["Item", "Status", "Why / details"], multi=False, sortable=False, stretch_col=2)
        size_widths(self.cfg_tree, {0: 280, 1: 140})
        self.cfg_tree.itemChanged.connect(self._cfg_changed)
        rl.addWidget(self.cfg_tree, 1)
        self.add_row = QHBoxLayout()
        self.add_box = QLineEdit()
        self.add_box.setPlaceholderText("Add an app from Remedy's catalog… (type a name)")
        self.add_box.textChanged.connect(self._suggest_apps)
        self.add_row.addWidget(self.add_box, 1)
        rl.addLayout(self.add_row)
        self.suggest = QListWidget()
        self.suggest.setMaximumHeight(130)
        self.suggest.itemActivated.connect(self._add_app)
        self.suggest.itemClicked.connect(self._add_app)
        self.suggest.hide()
        rl.addWidget(self.suggest)
        split.addWidget(right)
        split.setSizes([280, 900])
        lay.addWidget(split, 1)
        self.cfg_counts = label("", "muted")
        lay.addWidget(self.cfg_counts)
        nav = QHBoxLayout()
        nav.addWidget(button("← Back", "", self.main.go_back))
        nav.addWidget(button("Save draft", "", lambda: (self._save(), self.main.notify(
            "success", "Draft saved", "Reopen it from New PC Setup or Sessions & Reports."))))
        nav.addStretch(1)
        nav.addWidget(button("Review complete plan →", "primary", lambda: self.goto(3)))
        lay.addLayout(nav)
        return w

    def _v_review(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Review the plan", "h2"))
        lay.addWidget(label("Everything that will happen, in one list. Only rows marked “Will run” and ticked are "
                            "carried out. Already-done items are skipped, so running again never repeats work.",
                            "muted", wrap=True))
        self.review_tree = make_tree(["#", "Action", "Why", "Selected", "Status", "Risk", "Undo", "Restart"],
                                     multi=False, sortable=True, stretch_col=2)
        size_widths(self.review_tree, {0: 44, 1: 250, 3: 70, 4: 130, 5: 70, 6: 200, 7: 110})
        lay.addWidget(self.review_tree, 1)
        self.preflight_row = QHBoxLayout()
        self.preflight_row.setSpacing(16)
        lay.addLayout(self.preflight_row)
        nav = QHBoxLayout()
        nav.addWidget(button("← Back", "", self.main.go_back))
        nav.addWidget(button("Dry run", "", self._dry_run, "List the exact changes, skips and checks. Changes nothing."))
        nav.addStretch(1)
        self.btn_start = button("Start setup", "primary", self._start_deploy)
        nav.addWidget(self.btn_start)
        lay.addLayout(nav)
        return w

    def _v_deploy(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.dep_title = label("Preparing this PC", "h2")
        head.addWidget(self.dep_title, 1)
        self.dep_count = label("", "muted")
        head.addWidget(self.dep_count)
        lay.addLayout(head)
        self.dep_bar = QProgressBar()
        self.dep_bar.setRange(0, 1)
        lay.addWidget(self.dep_bar)
        lay.addWidget(label("Progress counts finished steps, not a guess at time. You can switch pages; setup keeps "
                            "running and shows in the Task Center.", "faint", wrap=True))
        split = QSplitter(Qt.Vertical)
        self.dep_tree = make_tree(["Step", "Status", "Details"], multi=False, sortable=False, stretch_col=2)
        size_widths(self.dep_tree, {0: 300, 1: 150})
        split.addWidget(self.dep_tree)
        self.dep_console = Console()
        self.dep_console.add("Installer output appears here.")
        split.addWidget(self.dep_console)
        split.setSizes([420, 150])
        lay.addWidget(split, 1)
        nav = QHBoxLayout()
        self.btn_pause = button("Pause after current step", "", self._toggle_pause,
                                "Stops safely between steps. Resume later from Review.")
        nav.addWidget(self.btn_pause)
        nav.addWidget(button("Show in Task Center", "ghost", self.main.open_tasks))
        nav.addStretch(1)
        self.btn_to_verify = button("Verify →", "primary", lambda: self.goto(5))
        nav.addWidget(self.btn_to_verify)
        lay.addLayout(nav)
        return w

    def _v_verify(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Verify", "h2"))
        lay.addWidget(label("Remedy re-reads the PC instead of trusting exit codes: apps must show up in winget's list "
                            "and settings must read back as applied. Tick the manual checks you've done.", "muted",
                            wrap=True))
        split = QSplitter(Qt.Horizontal)
        self.ver_tree = make_tree(["Item", "Result", "How we checked"], multi=False, sortable=False, stretch_col=2)
        size_widths(self.ver_tree, {0: 260, 1: 170})
        split.addWidget(self.ver_tree)
        box = Card("Manual checklist")
        self.manual_tree = make_tree(["Check", "What to look for"], multi=False, sortable=False, stretch_col=1)
        size_widths(self.manual_tree, {0: 200})
        self.manual_tree.itemChanged.connect(self._manual_changed)
        box.body.addWidget(self.manual_tree)
        split.addWidget(box)
        split.setSizes([640, 460])
        lay.addWidget(split, 1)
        nav = QHBoxLayout()
        nav.addWidget(button("← Back", "", self.main.go_back))
        nav.addWidget(button("Check again", "", self.verify))
        nav.addStretch(1)
        nav.addWidget(button("Handoff →", "primary", lambda: self.goto(6)))
        lay.addLayout(nav)
        return w

    def _v_handoff(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)
        lay.addWidget(label("Client handoff", "h2"))
        self.hand_summary = QHBoxLayout()
        self.hand_summary.setSpacing(8)
        lay.addLayout(self.hand_summary)
        self.hand_detail = label("", "muted", wrap=True)
        lay.addWidget(self.hand_detail)
        lay.addWidget(label("Technician note (included in the report)", "cardtitle"))
        self.note = QPlainTextEdit()
        self.note.setObjectName("note")
        self.note.setPlaceholderText("e.g. Printer driver from HP's site still needed; client will sign in to OneDrive.")
        self.note.setMaximumHeight(120)
        self.note.textChanged.connect(self._note_changed)
        lay.addWidget(self.note)
        self.include_label = QCheckBox("Include the client label in exported reports")
        lay.addWidget(self.include_label)
        lay.addWidget(label("Reports never include passwords, keys or personal files. With privacy mode on "
                            "(Settings), the computer and user names are redacted too.", "faint", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button("Save client report…", "primary", lambda: self._export_report("html")))
        row.addWidget(button("Save JSON record…", "", lambda: self._export_report("json")))
        row.addWidget(button("Save profile changes…", "", self._save_as_profile))
        row.addStretch(1)
        row.addWidget(button("Finish session", "", self._finish))
        lay.addLayout(row)
        lay.addStretch(1)
        return _scroll(w)

    # ================================================================== stepper / routing
    def _sync_stepper(self):
        done_upto = {0: 0, 1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5}.get(self.step, 0)
        cur_stage = {1: 0, 2: 1, 3: 2, 4: 2, 5: 3, 6: 4}.get(self.step, -1)
        for i, (b, _s) in enumerate(self.stage_btns):
            state = "current" if i == cur_stage else "done" if i < done_upto else ""
            b.setText(f"{'✓' if state == 'done' else i + 1}  {STAGES[i][0]}")
            b.setProperty("state", state)
            b.setEnabled(self.session is not None)
            b.style().unpolish(b)
            b.style().polish(b)
        self.stepper_wrap.setVisible(self.step > 0)
        if self.session:
            who = self.session.get("client_label") or "Unlabelled PC"
            self.session_label.setText(f"{who} · {self.profile.name if self.profile else ''}")

    def route_label(self, params: dict) -> str:
        step = int(params.get("step", 0) or 0)
        return STEP_TITLES.get(step, "")

    def open_route(self, params: dict) -> None:
        step = int(params.get("step", 0) or 0)
        if step and self.session is None:
            step = 0
        self._show_step(step)

    def goto(self, step: int):
        """Move to a wizard step (recorded in history so mouse Back returns here)."""
        if step and self.session is None:
            self.main.notify("info", "Pick a profile first", "Then press Inspect PC.")
            return
        if step >= 2 and self.inspection is None:
            self._after_inspect = step      # inspect first, then continue to where you were going
            step = 1
        self.main.navigate("newpc", {"step": step} if step else {})

    def _show_step(self, step: int):
        self.step = step
        self.views.setCurrentIndex(step)
        if step == 1 and self.inspection is None and (self.worker is None or not self.worker.isRunning()):
            QTimer.singleShot(50, self.inspect)
        elif step == 2:
            self._build_plan()
            self._render_categories()
        elif step == 3:
            self._build_plan()
            self._render_review()
        elif step == 4:
            self._render_deploy()
        elif step == 5:
            self._render_verify()
            if self.session and self.session.get("status") == "deployed" and not self._busy_quiet():
                QTimer.singleShot(50, self.verify)
        elif step == 6:
            self._render_handoff()
        if self.session and step:
            self.session["step"] = step
            self._queue_save()
        self._sync_stepper()

    def _busy_quiet(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    # ================================================================== profiles
    def _render_profiles(self):
        _clear(self.profile_grid)
        self.profile_cards = []
        ids = [p.id for p in self.profiles]
        want = self._selected_id or settings.get("last_profile") or "everyday"
        self._selected_id = want if want in ids else (ids[0] if ids else "")
        for i, p in enumerate(self.profiles):
            card = ProfileCard(p, self._pick_profile)
            self.profile_cards.append(card)
            self.profile_grid.addWidget(card, i // 3, i % 3)
        self._sync_profile_buttons()

    def _pick_profile(self, pid: str, go: bool = False):
        self._selected_id = pid
        self._sync_profile_buttons()
        if go:
            self._begin()

    def _sync_profile_buttons(self):
        for c in self.profile_cards:
            c.set_selected(c.profile.id == self._selected_id)
        p = self._selected_profile()
        self.btn_delete.setEnabled(bool(p and not p.builtin))

    def _selected_profile(self):
        return next((p for p in self.profiles if p.id == self._selected_id), None)

    def _reload_profiles(self, select: str = ""):
        self.profiles = provision.load_profiles()
        if select:
            settings.set("last_profile", select)
            self._selected_id = select
        self._render_profiles()

    def _import(self):
        path, _f = QFileDialog.getOpenFileName(self, "Import a setup profile", os.path.expanduser("~"),
                                               f"Remedy profiles (*{provision.PROFILE_EXT});;JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                p, warnings = provision.parse_profile(f.read(), source="import")
        except (OSError, ValueError) as exc:
            from .dialogs import error
            error(self, "Couldn't import that profile", str(exc))
            return
        bullets = [f"{len(p.apps)} apps: " + ", ".join(p.apps[:8]) + ("…" if len(p.apps) > 8 else ""),
                   f"{len(p.tweaks)} settings: " + ", ".join(p.tweaks[:8]) + ("…" if len(p.tweaks) > 8 else ""),
                   f"{len(p.checklist)} checklist items"] + warnings[:6]
        if not confirm(self, f"Import “{p.name}”?", "Here's everything it would ask for. Nothing runs now; it's "
                                                    "just saved as one of your profiles.", "Import", bullets=bullets):
            return
        provision.save_profile(p)
        self._reload_profiles(p.id)
        self.main.notify("success", f"Imported {p.name}", f"{len(warnings)} item(s) skipped." if warnings else "")

    def _export(self):
        p = self._selected_profile()
        if not p:
            return
        path, _f = QFileDialog.getSaveFileName(self, "Export profile", os.path.join(
            os.path.expanduser("~"), f"{provision.slugify(p.name)}{provision.PROFILE_EXT}"),
            f"Remedy profiles (*{provision.PROFILE_EXT})")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(provision.export_text(p))
        except OSError as exc:
            self.main.notify("error", "Couldn't save the profile", str(exc))
            return
        self.main.notify("success", f"Exported {p.name}", os.path.basename(path),
                         action=("Show file", lambda: open_location(path)))

    def _duplicate(self):
        p = self._selected_profile()
        if not p:
            return
        name, ok = QInputDialog.getText(self, "Duplicate profile", "Name for your copy:", text=f"{p.name} (mine)")
        if not ok or not name.strip():
            return
        copy = provision.duplicate(p, name.strip()[:60])
        provision.save_profile(copy)
        self._reload_profiles(copy.id)
        self.main.notify("success", f"Created {copy.name}", "Select it and press Inspect PC, or edit its contents on "
                                                            "the Configure step and save.")

    def _delete_profile(self):
        p = self._selected_profile()
        if not p or p.builtin:
            return
        if confirm(self, f"Delete profile “{p.name}”?", "Only the profile file is removed. Past sessions keep their "
                                                        "own copy.", "Delete", danger=True):
            provision.delete_profile(p)
            self._reload_profiles()

    def _save_as_profile(self):
        if not self.profile:
            return
        default = self.profile.name if not self.profile.builtin else f"{self.profile.name} (mine)"
        name, ok = QInputDialog.getText(self, "Save as profile", "Profile name:", text=default)
        if not ok or not name.strip():
            return
        sel = (self.session or {}).get("selections") or {}
        p = provision.duplicate(self.profile, name.strip()[:60])
        # keep only what's ticked, so the profile matches what you approved
        p.apps = [a for a in p.apps if sel.get(f"app:{a}", True)]
        p.tweaks = [t for t in p.tweaks if sel.get(f"tweak:{t}", True)]
        if not self.profile.builtin and name.strip() == self.profile.name:
            p.id, p.version = self.profile.id, self.profile.version + 1
        provision.save_profile(p)
        self._reload_profiles(p.id)
        self.main.notify("success", f"Saved profile {p.name}", f"Version {p.version}. It's now your standard setup.")

    # ================================================================== start / resume
    def _begin(self):
        p = self._selected_profile()
        if p is None:
            self.main.notify("info", "Pick a profile first")
            return
        b = self.type_group.checkedButton()
        stype = b.property("setup_type") if b else "new_oem"
        self.profile = provision.parse_profile(p.to_dict(), source="local")[0]
        self.profile.builtin = p.builtin
        self.session = provision.new_session(self.profile, stype, self.client.text().strip())
        self.inspection = None
        self.tasks = []
        settings.set("last_profile", p.id)
        self.cfg_modified.hide()
        self._save()
        self.goto(1)

    def my_standard_setup(self):
        """Last approved profile -> fast inspection -> land on the review. Never runs anything by itself."""
        last = settings.get("last_profile") or "everyday"
        if any(p.id == last for p in self.profiles):
            self._selected_id = last
            self._sync_profile_buttons()
        self._auto_review = True
        self._begin()

    def offer_resume(self):
        """Called after start-up: show a banner (and a toast) for an unfinished setup."""
        cand = None
        for s in sessions.list_sessions("setup"):
            if s.get("status") in UNFINISHED and time.time() - (s.get("updated") or 0) < 14 * 86400:
                cand = s
                break
        if cand is None:
            self.resume_banner.hide()
            return
        if provision.mark_interrupted(cand):
            sessions.save(cand)
        self._resume_candidate = cand
        when = time.strftime("%d %b %H:%M", time.localtime(cand.get("updated") or 0))
        counts = provision.outcome_counts(cand)
        done = counts.get("done", 0) + counts.get("verified", 0) + counts.get("satisfied", 0)
        self.resume_text.setText(f"Unfinished setup: {cand.get('client_label') or 'Unlabelled PC'} · "
                                 f"{cand.get('profile', {}).get('name')} · last saved {when} · status "
                                 f"{cand.get('status')} · {done} step(s) done. Resuming re-checks this PC first, so "
                                 "nothing finished is repeated.")
        self.resume_banner.show()
        if cand.get("status") in ("interrupted", "paused", "deploying"):
            self.main.notify("warning", "A New PC Setup was interrupted",
                             "Resume it when you're ready; Remedy re-checks the PC first.",
                             action=("Review", lambda: self.main.navigate("newpc")))

    def _discard_resume(self):
        cand = getattr(self, "_resume_candidate", None)
        if cand and confirm(self, "Discard this unfinished setup?", "Changes already made to the PC stay as they are. "
                                                                  "Only the saved session is marked finished.",
                            "Discard"):
            cand["status"] = "finished"
            sessions.save(cand)
            self.resume_banner.hide()

    def load_session(self, s: dict, step: int | None = None):
        """Open a saved setup session (from the banner or Sessions & Reports)."""
        self.session = s
        self.profile = provision.parse_profile(s.get("profile") or {}, source="local")[0]
        self.client.setText(s.get("client_label") or "")
        self.note.blockSignals(True)
        self.note.setPlainText(s.get("note") or "")
        self.note.blockSignals(False)
        self.inspection = None
        self.tasks = []
        self.resume_banner.hide()
        self.goto(step if step is not None else max(1, min(6, int(s.get("step") or 1))))

    def _resume(self, review: bool):
        cand = getattr(self, "_resume_candidate", None)
        if not cand:
            return
        self._auto_review = True
        self._resume_run = not review
        self.load_session(cand, step=1)

    # ================================================================== inspect
    def inspect(self):
        if self.session is None or self.busy():
            return

        def work(ctx):
            return provision.inspect(ctx, emit=lambda t, p: ctx.progress(t, p, force=True))

        self.run(work, progress=self.progress, text="Inspecting this PC (read-only)…", on_done=self._inspected,
                 summary=lambda r: f"{len(r['facts'])} facts recorded")

    def _inspected(self, data):
        self.inspection = data
        self.session["inspection"] = data["facts"]
        self.facts.clear()
        colors = {"Known": theme.OK, "Not available": theme.FAINT, "Requires admin": theme.WARN,
                  "Requires restart": theme.WARN}
        for f in data["facts"]:
            it = QTreeWidgetItem([f["label"], str(f["value"]), f["state"], f.get("source", "")])
            it.setForeground(2, QColor(colors.get(f["state"], theme.MUTED)))
            it.setToolTip(1, str(f["value"]))
            self.facts.addTopLevelItem(it)
        self.tweak_status = provision.tweak_statuses(self.profile.tweaks) if IS_WINDOWS else {}
        self._build_plan()
        self._save()
        target, self._after_inspect = getattr(self, "_after_inspect", 0), 0
        if self._auto_review:
            self._auto_review = False
            self.goto(3)
            if getattr(self, "_resume_run", False):
                self._resume_run = False
                QTimer.singleShot(200, self._start_deploy)
        elif target and self.step == 1:
            self.goto(target)

    # ================================================================== plan / configure
    def _build_plan(self):
        if self.session is None or self.inspection is None:
            return
        self.tasks = provision.build_plan(self.profile, self.inspection, tweak_status=getattr(self, "tweak_status", {}),
                                          selections=self.session.get("selections"))
        j = self.session.get("tasks") or {}
        for t in self.tasks:          # results from an earlier run win over the fresh plan
            e = j.get(t.id)
            if e and e.get("status") in ("done", "verified", "satisfied") and t.kind != "check":
                t.state, t.selected, t.detail = "satisfied", False, "Done in this session"

    def _categories(self):
        groups = {}
        for t in self.tasks:
            name = {"app": "Apps", "tweak": "Windows settings", "check": "Checklist"}[t.kind]
            if t.kind == "tweak":
                name = f"Windows settings · {t.category}"
            groups.setdefault(name, []).append(t)
        return groups

    def _render_categories(self):
        groups = self._categories()
        cur = self.cats.currentItem().data(Qt.UserRole) if self.cats.currentItem() else None
        self.cats.blockSignals(True)
        self.cats.clear()
        for name in sorted(groups, key=lambda n: (n != "Apps", n == "Checklist", n)):
            ts = groups[name]
            n_sel = sum(1 for t in ts if t.runnable)
            n_ok = sum(1 for t in ts if t.state == "satisfied")
            it = QListWidgetItem(f"{name}\n{n_sel} to do · {n_ok} in place")
            it.setData(Qt.UserRole, name)
            self.cats.addItem(it)
            if name == cur:
                self.cats.setCurrentItem(it)
        if self.cats.currentRow() < 0 and self.cats.count():
            self.cats.setCurrentRow(0)
        self.cats.blockSignals(False)
        self.cfg_title.setText(f"Configure · {self.profile.name}")
        self._render_category()
        self._render_counts()

    def _render_category(self):
        it = self.cats.currentItem()
        name = it.data(Qt.UserRole) if it else ""
        ts = self._categories().get(name, [])
        blurbs = {"Apps": "Installed silently from the official winget source, one at a time. Apps already on the PC "
                          "are detected and skipped.",
                  "Checklist": "Things only a person can confirm. You tick them on the Verify step."}
        self.cat_blurb.setText(blurbs.get(name, "Each setting is backed up before it changes and can be undone on the "
                                                "Tweaks page. Settings for another Windows version are blocked."))
        is_apps = name == "Apps"
        self.add_box.setVisible(is_apps)
        self.suggest.setVisible(is_apps and bool(self.add_box.text()))
        self.cfg_tree.blockSignals(True)
        self.cfg_tree.clear()
        for t in ts:
            row = QTreeWidgetItem([t.title, STATE_TEXT.get(t.state, t.state),
                                   (t.detail + " · " if t.detail and t.state != "todo" else "") + t.why
                                   + (f" · Takes effect after: {t.reboot}" if t.reboot else "")])
            row.setData(0, Qt.UserRole, t.id)
            row.setForeground(1, QColor(STATE_COLOR.get(t.state, theme.MUTED)))
            row.setToolTip(2, row.text(2))
            if t.state in ("todo", "review"):
                row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                row.setCheckState(0, Qt.Checked if t.selected else Qt.Unchecked)
            else:
                row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.cfg_tree.addTopLevelItem(row)
        self.cfg_tree.blockSignals(False)

    def _render_counts(self):
        c = provision.plan_counts(self.tasks)
        self.cfg_counts.setText(f"{c['selected']} selected actions · {c['satisfied']} already in place · "
                                f"{c['review']} need review · {c['blocked']} blocked · {c['reboot']} need a restart or "
                                f"sign-out · {c['manual']} manual checks")

    def _cfg_changed(self, item, _col):
        tid = item.data(0, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked
        self.session.setdefault("selections", {})[tid] = checked
        for t in self.tasks:
            if t.id == tid:
                t.selected = checked
        self._render_counts()
        self._queue_save()

    def _suggest_apps(self, text):
        self.suggest.clear()
        q = text.lower().strip()
        if not q:
            self.suggest.hide()
            return
        have = set(self.profile.apps)
        for a in appstore.CATALOG:
            if a.winget_id not in have and (q in a.name.lower() or q in a.category.lower()):
                it = QListWidgetItem(f"+ {a.name}  ·  {a.category} · {a.description}")
                it.setData(Qt.UserRole, a.winget_id)
                self.suggest.addItem(it)
        self.suggest.setVisible(self.suggest.count() > 0)

    def _add_app(self, item):
        wid = item.data(Qt.UserRole)
        if wid and wid not in self.profile.apps:
            self.profile.apps.append(wid)
            self.session["profile"] = self.profile.to_dict()
            self.session.setdefault("selections", {})[f"app:{wid}"] = True
            self.cfg_modified.show()
            self.add_box.clear()
            self._build_plan()
            self._render_categories()
            self._queue_save()

    # ================================================================== review / deploy
    def _render_review(self):
        """Run order first (settings, then apps, then manual checks); click a header to sort differently."""
        self.review_tree.setSortingEnabled(False)
        self.review_tree.clear()
        order = {"tweak": 0, "app": 1, "check": 2}
        ranked = sorted(enumerate(self.tasks), key=lambda it: (order[it[1].kind], not it[1].runnable, it[0]))
        for n, (_i, t) in enumerate(ranked, 1):
            status = STATE_TEXT.get(t.state, t.state) if not (t.state in ("todo", "review") and not t.selected) \
                else "Not ticked"
            row = SortItem([str(n), t.title, t.why, "Yes" if t.runnable else "No", status, t.risk, t.reversible,
                            t.reboot or "No"])
            row.setData(0, UR, n)
            row.setForeground(4, QColor(STATE_COLOR.get(t.state, theme.MUTED) if t.runnable or t.state != "todo"
                                        else theme.FAINT))
            row.setToolTip(1, t.title)
            row.setToolTip(2, t.why + (f"\n{t.detail}" if t.detail else ""))
            row.setToolTip(6, t.reversible)
            self.review_tree.addTopLevelItem(row)
        self.review_tree.setSortingEnabled(True)
        self.review_tree.sortItems(0, Qt.AscendingOrder)
        _clear(self.preflight_row)
        self.preflight_row.addWidget(label("Preflight", "cardtitle"), 0, Qt.AlignTop)
        parts = [f"<span style='color:{theme.OK if ok else theme.WARN}'>{'✓' if ok else '!'} <b>{name}</b>: "
                 f"{detail}</span>" for name, ok, detail in provision.preflight(self.tasks, self.inspection or {})]
        lb = label("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts) or "Nothing to check", "", wrap=True)
        lb.setTextFormat(Qt.RichText)
        self.preflight_row.addWidget(lb, 1)
        n = sum(1 for t in self.tasks if t.runnable and t.kind != "check")
        self.btn_start.setText(f"Start setup ({n} action{'s' if n != 1 else ''})" if n else "Nothing to run · Verify →")
        self.btn_start.setEnabled(not self._busy_quiet())

    def _dry_run(self):
        from .dialogs import info
        if self.inspection is None:
            return
        info(self, "Dry run: what would happen", "Nothing was changed.",
             detail="\n".join(provision.dry_run(self.tasks, self.inspection)))

    def _start_deploy(self):
        if self.session is None or self.busy():
            return
        run = [t for t in self.tasks if t.runnable and t.kind != "check"]
        if not run:
            self.goto(5)
            return
        blocking = [n for n, ok, _d in provision.preflight(self.tasks, self.inspection or {}) if not ok]
        apps = sum(1 for t in run if t.kind == "app")
        text = (f"{len(run) - apps} Windows setting(s), then {apps} app install(s), one at a time. "
                "Already-done steps are skipped. You can pause between steps.")
        if blocking:
            text += f"\n\nPreflight warnings: {', '.join(blocking)}."
        if not confirm(self, f"Start setup: {len(run)} action{'s' if len(run) != 1 else ''}?", text, "Start setup",
                       bullets=[t.title for t in run]):
            return
        self._deploy(skip_restore=False)

    def _deploy(self, skip_restore: bool):
        self._pause = False
        self.btn_pause.setText("Pause after current step")
        provision.journal_tasks(self.session, self.tasks)
        session, tasks = self.session, list(self.tasks)
        use_rp = (not skip_restore and IS_WINDOWS and bool(settings.get("restore_point_before_tweaks"))
                  and bool((self.inspection or {}).get("admin")))
        rp = maintenance.create_restore_point if use_rp else None
        if skip_restore:
            session["restore_point_skipped"] = True
        self.goto(4)

        def work(ctx):
            try:
                provision.deploy(ctx, session, tasks, save=sessions.save,
                                 emit=lambda t, p: ctx.progress(t, p, force=t.startswith("@")),
                                 should_pause=lambda: self._pause, restore_point=rp)
            except provision.RestorePointFailed as exc:
                return {"restore_failed": str(exc)}
            return {"ok": True}

        def prog(text, pct):
            if text.startswith("@"):
                body = text[1:]
                tid, _sep, rest = body.partition("|")
                if rest.startswith("="):
                    status, _s, detail = rest[1:].partition("|")
                    self._set_dep_row(tid, status, detail)
                    self.dep_console.add(f"{'✔' if status in ('done', 'satisfied') else '✖'} {tid}: {status} {detail}")
                else:
                    self._set_dep_row(tid, "running", "")
                    self.dep_console.add(f"\n▶ {rest}")
                    self.progress.update_progress(rest, pct)
                self._update_dep_count()
            elif text.strip() and text.strip() not in ("-", "\\", "|", "/") and not set(text.strip()) <= set("█▒ ") \
                    and "MB /" not in text:
                self.dep_console.add(text)

        def done(res):
            if res.get("restore_failed"):
                self._render_deploy()
                if confirm(self, "Windows couldn't create a restore point",
                           f"{res['restore_failed']}\n\nNothing has been changed yet. Continue without a restore point? "
                           "Each setting is still backed up individually and can be undone on the Tweaks page.",
                           "Continue without", danger=True):
                    self._deploy(skip_restore=True)
                else:
                    self.main.notify("info", "Setup paused", "Nothing was changed. Resume from Review when ready.")
                return
            self._render_deploy()
            st = self.session.get("status")
            counts = provision.outcome_counts(self.session)
            if st == "paused":
                self.main.notify("info", "Setup paused", "Finished steps are saved. Press Start on Review to continue.")
            else:
                fails = counts.get("failed", 0) + counts.get("interrupted", 0)
                self.main.notify("warning" if fails else "success", "Setup finished" if not fails else
                                 f"Setup finished with {fails} problem(s)", "Next: verify the results.",
                                 action=("Verify now", lambda: self.goto(5)))
                if self.step == 4:
                    self.goto(5)

        self._render_deploy()
        self.run(work, progress=self.progress, text=f"Setting up {self.session.get('client_label') or 'this PC'}…",
                 on_done=done, on_progress=prog,
                 summary=lambda r: "Setup paused for a decision" if r.get("restore_failed") else "Setup steps finished")

    def _toggle_pause(self):
        self._pause = not self._pause
        self.btn_pause.setText("Will pause after this step (click to continue)" if self._pause
                               else "Pause after current step")

    def set_busy_ui(self, busy):
        self.btn_start.setEnabled(not busy)
        self.btn_pause.setEnabled(busy)
        self.btn_to_verify.setEnabled(not busy)

    def _render_deploy(self):
        self.dep_tree.clear()
        self.dep_rows = {}
        j = (self.session or {}).get("tasks") or {}
        order = [t for t in self.tasks if t.kind == "tweak"] + [t for t in self.tasks if t.kind == "app"]
        for t in order:
            e = j.get(t.id)
            if e is None or e.get("status") in ("skipped", "blocked"):
                continue
            row = QTreeWidgetItem([e["title"], "", ""])
            self.dep_tree.addTopLevelItem(row)
            self.dep_rows[t.id] = row
            self._set_dep_row(t.id, e.get("status", "pending"), e.get("detail", ""))
        self._update_dep_count()

    def _set_dep_row(self, tid, status, detail):
        row = getattr(self, "dep_rows", {}).get(tid)
        if row is None:
            return
        text = {"pending": "• Queued", "running": "⟳ Working…", "done": "✓ Done", "verified": "✓ Verified",
                "satisfied": "✓ Already in place", "failed": "✖ Failed", "interrupted": "■ Interrupted"}.get(status, status)
        row.setText(1, text)
        row.setText(2, detail or "")
        row.setData(0, Qt.UserRole, status)
        row.setForeground(1, QColor(JOURNAL_COLOR.get(status, theme.MUTED)))

    def _update_dep_count(self):
        rows = list(getattr(self, "dep_rows", {}).values())
        total = len(rows)
        fin = sum(1 for r in rows if r.data(0, Qt.UserRole) in ("done", "verified", "satisfied", "failed"))
        self.dep_count.setText(f"{fin} / {total} actions completed")
        self.dep_bar.setRange(0, max(1, total))
        self.dep_bar.setValue(fin)
        self.dep_title.setText(f"Preparing {(self.session or {}).get('client_label') or 'this PC'}")

    # ================================================================== verify / handoff
    def verify(self):
        if self.session is None or self.busy():
            return
        refs = [e["ref"] for e in self.session.get("tasks", {}).values() if e["kind"] == "tweak"]

        def work(ctx):
            ctx.progress("Asking winget what's installed…", 20, force=True)
            from ..core import appstore as ap
            installed = ap.installed_ids() if ap.winget_available() else None
            ctx.progress("Reading settings back…", 70, force=True)
            st = provision.tweak_statuses(refs) if IS_WINDOWS else {}
            return installed, st

        def done(res):
            installed, st = res
            provision.verify(self.session, installed, st)
            self._save()
            self._render_verify()

        self.run(work, progress=self.progress, text="Verifying results (read-only)…", on_done=done,
                 summary=lambda _r: "Verification finished")

    def _render_verify(self):
        self.ver_tree.clear()
        j = (self.session or {}).get("tasks") or {}
        for tid, e in j.items():
            if e["kind"] == "check" or e.get("status") in ("skipped", "blocked"):
                continue
            it = QTreeWidgetItem([e["title"], report.SETUP_STATUS_TEXT.get(e["status"], e["status"]),
                                  e.get("verify") or e.get("detail") or ""])
            it.setForeground(1, QColor(JOURNAL_COLOR.get(e["status"], theme.MUTED)))
            self.ver_tree.addTopLevelItem(it)
        self.manual_tree.blockSignals(True)
        self.manual_tree.clear()
        manual = (self.session or {}).get("manual") or {}
        for key in (self.profile.checklist if self.profile else []):
            title, detail = provision.CHECKLIST[key]
            it = QTreeWidgetItem([title, detail])
            it.setData(0, Qt.UserRole, key)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if manual.get(key) else Qt.Unchecked)
            it.setToolTip(1, detail)
            self.manual_tree.addTopLevelItem(it)
        self.manual_tree.blockSignals(False)

    def _manual_changed(self, item, _col):
        key = item.data(0, Qt.UserRole)
        self.session.setdefault("manual", {})[key] = item.checkState(0) == Qt.Checked
        e = self.session.get("tasks", {}).get(f"check:{key}")
        if e is not None:
            e["status"] = "manual_done" if item.checkState(0) == Qt.Checked else "manual_open"
        self._queue_save()

    def _render_handoff(self):
        _clear(self.hand_summary)
        counts = provision.outcome_counts(self.session or {})
        groups = [("Verified", ("verified",), theme.OK), ("Already in place", ("satisfied",), theme.OK),
                  ("Done, not verified", ("done",), theme.ACCENT), ("Failed", ("failed", "interrupted"), theme.DANGER),
                  ("Skipped", ("skipped", "blocked"), theme.FAINT), ("Checks done", ("manual_done",), theme.OK),
                  ("Checks open", ("manual_open", "pending"), theme.WARN)]
        for name, keys, color in groups:
            n = sum(counts.get(k, 0) for k in keys)
            if n:
                self.hand_summary.addWidget(pill(f"{n} {name.lower()}", color))
        self.hand_summary.addStretch(1)
        j = (self.session or {}).get("tasks") or {}
        reboot = sorted({e["reboot"] for e in j.values() if e.get("reboot") and e.get("status") in ("done", "verified")})
        fails = [e["title"] for e in j.values() if e.get("status") in ("failed", "interrupted")]
        lines = []
        if reboot:
            lines.append("Restart needed: some changes finish after " + " / ".join(r.lower() for r in reboot) + ".")
        if fails:
            lines.append("Needs attention: " + ", ".join(fails) + ".")
        if not lines:
            lines.append("Everything that ran has been recorded. Save the client report, then finish the session.")
        self.hand_detail.setText(" ".join(lines))

    def _note_changed(self):
        if self.session is not None:
            self.session["note"] = self.note.toPlainText()[:4000]
            self._queue_save()

    def _export_report(self, kind):
        if self.session is None:
            return
        self._save()
        ext = "html" if kind == "html" else "json"
        default = os.path.join(os.path.expanduser("~"), f"Remedy-setup-{time.strftime('%Y%m%d')}.{ext}")
        path, _f = QFileDialog.getSaveFileName(self, "Save report", default,
                                               "HTML report (*.html)" if kind == "html" else "JSON record (*.json)")
        if not path:
            return
        privacy = bool(settings.get("privacy_mode"))
        incl = self.include_label.isChecked()
        try:
            text = report.setup_html(self.session, privacy, incl) if kind == "html" else \
                report.to_json(self.session, privacy, incl)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            self.main.notify("error", "Couldn't save the report", str(exc))
            return
        self.main.notify("success", "Report saved", os.path.basename(path), action=("Show file",
                                                                                     lambda: open_location(path)))

    def _finish(self):
        if self.session is None:
            return
        open_checks = [e["title"] for e in self.session.get("tasks", {}).values() if e.get("status") == "manual_open"]
        if open_checks and not confirm(self, "Finish with open checks?", "These manual checks aren't ticked yet. They "
                                                                         "stay listed as open in the report.",
                                       "Finish anyway", bullets=open_checks):
            return
        self.session["status"] = "finished"
        self._save()
        self.main.notify("success", "Session finished", "Find it any time in Sessions & Reports.",
                         action=("Open Sessions", lambda: self.main.navigate("sessions")))
        self.session = None
        self.inspection = None
        self.tasks = []
        self.main.navigate("newpc", {})

    # ================================================================== persistence
    def _queue_save(self):
        if not self._busy_quiet():
            self.save_timer.start()

    def _save(self):
        if self.session is None:
            return
        try:
            sessions.save(self.session)
        except OSError as exc:
            log.warning("Could not save setup session: %s", exc)
