"""One-click optimize window: tick the steps, press Start, watch each one tick off."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QProgressBar, QPushButton, QSizePolicy,
                               QVBoxLayout)

from .. import theme
from ..core import optimize, osinfo
from .dialogs import fit_height
from .widgets import GradientLabel, fmt_eta, label, start_task


def parse_line(text: str):
    """"@key|state|text|pct" -> (key, state, text, pct) or None for plain detail lines."""
    if not text.startswith("@") or text.count("|") < 3:
        return None
    key, state, rest = text[1:].split("|", 2)
    body, pct = rest.rsplit("|", 1)
    try:
        pct = int(pct)
    except ValueError:
        pct = -1
    return key, state, body, pct


class StepRow(QFrame):
    def __init__(self, step, parent=None):
        super().__init__(parent)
        self.step = step
        self.setObjectName("recommend")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        self.check = QCheckBox()
        self.check.setChecked(step.default and step.available)
        self.check.setEnabled(step.available)
        lay.addWidget(self.check, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title = label(step.title)
        self.title.setStyleSheet("font-weight: 700; font-size: 10.5pt;")
        texts.addWidget(self.title)
        desc = step.description if step.available else f"Not available here: {step.reason}."
        self.desc = label(desc, "muted", wrap=True)
        texts.addWidget(self.desc)
        self.bar = QProgressBar()
        self.bar.setObjectName("mini")
        self.bar.setRange(0, 100)
        self.bar.hide()
        texts.addWidget(self.bar)
        lay.addLayout(texts, 1)
        self.status = label("", "muted")
        self.status.setMinimumWidth(190)
        self.status.setMaximumWidth(260)
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.status)
        self.title.mousePressEvent = lambda _e: self.check.isEnabled() and self.check.toggle()
        if not step.available:
            self.title.setStyleSheet(f"font-weight: 700; font-size: 10.5pt; color: {theme.MUTED};")

    def chosen(self) -> bool:
        return self.check.isChecked() and self.step.available

    def set_state(self, state: str, text: str = "", pct: int = -1):
        colors = {"wait": theme.MUTED, "run": theme.ACCENT, "ok": theme.OK, "fail": theme.DANGER,
                  "skip": theme.MUTED}
        marks = {"wait": "Waiting", "run": "", "ok": "✓ ", "fail": "✕ ", "skip": "Skipped"}
        self.status.setStyleSheet(f"color: {colors[state]}; font-weight: {'700' if state != 'wait' else '400'};")
        if state == "run":
            self.bar.show()
            if pct >= 0:
                self.bar.setRange(0, 100)
                self.bar.setValue(pct)
            else:
                self.bar.setRange(0, 0)
            self.status.setText(f"{pct}%" if pct >= 0 else "Working…")
            if text:
                self.desc.setText(text)
        else:
            self.bar.hide()
            self.status.setText(marks[state] + (text if state in ("ok", "fail") else ""))
            if state in ("ok", "fail"):
                self.desc.setText(self.step.description)


class OptimizeWizard(QDialog):
    def __init__(self, main, preselect_bloat: bool = False):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("One-click optimize")
        self.setMinimumWidth(760)
        self.worker = None
        self.results = []
        self.info = osinfo.detect()

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(12)
        root.addWidget(GradientLabel("One-click optimize", 24))
        root.addWidget(label(f"Tuned for this PC: {self.info.summary()}", "muted", wrap=True))
        root.addWidget(label("Untick anything you'd rather skip, then press Start. Steps run in order and you can "
                             "cancel between them.", "muted", wrap=True))

        self.rows = {}
        for step in optimize.plan(self.info):
            row = StepRow(step)
            if step.key == "bloat" and preselect_bloat and step.available:
                row.check.setChecked(True)
            self.rows[step.key] = row
            root.addWidget(row)

        overall = QHBoxLayout()
        self.overall_text = label("Ready when you are.", "")
        self.overall_text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.overall_pct = label("", "")
        self.overall_pct.setStyleSheet("font-weight: 700;")
        overall.addWidget(self.overall_text, 1)
        overall.addWidget(self.overall_pct)
        root.addLayout(overall)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        root.addWidget(self.bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_close = QPushButton("Cancel")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.clicked.connect(self._close_or_cancel)
        self.btn_start = QPushButton("Start")
        self.btn_start.setProperty("kind", "primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.start)
        buttons.addWidget(self.btn_close)
        buttons.addWidget(self.btn_start)
        root.addLayout(buttons)
        for row in self.rows.values():
            row.check.toggled.connect(self._count)
        self._count()
        fit_height(self, 780)

    def _keys(self):
        return [k for k, r in self.rows.items() if r.chosen()]

    def _count(self, *_):
        if self.worker is None and not self.results:
            n = len(self._keys())
            self.btn_start.setEnabled(n > 0)
            self.btn_start.setText(f"Start ({n} step{'s' if n != 1 else ''})" if n else "Pick a step")

    def start(self):
        if self.results:          # finished: the button now says Close
            self.accept()
            return
        keys = self._keys()
        if not keys:
            return
        for k, row in self.rows.items():
            row.check.setEnabled(False)
            row.set_state("wait" if k in keys else "skip")
        self.btn_start.setEnabled(False)
        self.btn_start.setText("Optimizing…")
        self.btn_close.setText("Cancel")
        self.current = None
        import time
        self._t0 = time.monotonic()
        self._token = self.main.activity.start("dashboard", "One-click optimize", "Starting…") \
            if hasattr(self.main, "activity") else None
        self.worker = start_task(optimize.run, keys, on_done=self._done, on_error=self._failed,
                                 on_progress=self._progress)

    def _progress(self, text, pct):
        parsed = parse_line(text)
        if parsed:
            key, state, body, step_pct = parsed
            row = self.rows.get(key)
            if row is not None:
                row.set_state(state, body, step_pct)
            if state == "run":
                self.current = key
                self.overall_text.setText(f"{row.step.title}: {body}" if row else body)
        elif self.current in self.rows and text:
            self.rows[self.current].desc.setText(text)
        if pct >= 0:
            self.bar.setValue(pct)
            self.overall_pct.setText(f"{pct}%")
        if self._token is not None:
            self.main.activity.update(self._token, self.overall_text.text(), pct)

    def _finish_ui(self):
        self.worker = None
        self.btn_start.setEnabled(True)
        self.btn_start.setText("Close")
        self.btn_close.hide()

    def _done(self, results):
        import time
        self.results = results or [("none", True, "")]
        self._finish_ui()
        self.bar.setValue(100)
        self.overall_pct.setText("100%")
        ok = sum(1 for _k, good, _s in results if good)
        fails = len(results) - ok
        took = fmt_eta(time.monotonic() - self._t0)
        self.overall_text.setText(f"Finished {ok} of {len(results)} steps in {took}."
                                  + (" Check the red step for details." if fails else ""))
        if self._token is not None:
            self.main.activity.finish(self._token, not fails, f"{ok} of {len(results)} steps done")
        freed = next((s for k, good, s in results if k == "junk" and good), "")
        body = " · ".join(s for _k, good, s in results if good)
        self.main.notify("success" if not fails else "warning",
                         "Your PC is optimized" if not fails else f"Optimized, with {fails} step(s) to check",
                         body[:220] or freed)

    def _failed(self, msg):
        self.results = [("cancelled", False, msg)]
        self._finish_ui()
        cancelled = msg == "Cancelled"
        self.overall_text.setText("Stopped. Steps already finished stay done." if cancelled else f"Error: {msg}")
        if self.current in self.rows:
            self.rows[self.current].set_state("fail", "Cancelled" if cancelled else "Error")
        if self._token is not None:
            self.main.activity.finish(self._token, False, "Cancelled" if cancelled else "Failed")
        self.main.notify("warning", "One-click optimize stopped", "Steps already finished stay done.")

    def _close_or_cancel(self):
        if self.worker is not None:
            self.worker.cancel()
            self.btn_close.setEnabled(False)
            self.btn_close.setText("Stopping…")
            self.overall_text.setText("Stopping after the current item…")
        else:
            self.reject()

    def reject(self):
        if self.worker is not None:      # Esc / window X while running = cancel, keep the window open
            self._close_or_cancel()
            return
        super().reject()
