"""Task Center: one place that lists every background job (running, finished, failed, cancelled) from any page.

BasePage.run() registers each job here automatically. Jobs belong to their page, not to what is on screen, so
switching pages never restarts or cancels them. Cancelling from here always asks first.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .. import theme

STATUS_TEXT = {"running": "Running", "done": "Done", "failed": "Failed", "cancelled": "Cancelled"}
STATUS_COLOR = {"running": theme.ACCENT, "done": theme.OK, "failed": theme.DANGER, "cancelled": theme.WARN}


@dataclass
class TaskRecord:
    id: int
    title: str
    page_key: str
    text: str = ""
    pct: int = -1
    status: str = "running"
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    summary: str = ""
    cancel: object = None                      # callable, or None when the job can't be stopped safely
    log: deque = field(default_factory=lambda: deque(maxlen=300))

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


class TaskCenter(QObject):
    changed = Signal()

    def __init__(self, keep: int = 40):
        super().__init__()
        self.records: dict = {}
        self.keep = keep
        self._next = 0

    def start(self, title: str, page_key: str, text: str = "", cancel=None) -> int:
        self._next += 1
        rec = TaskRecord(self._next, title, page_key, text, cancel=cancel)
        rec.log.append(f"{time.strftime('%H:%M:%S')}  Started: {text or title}")
        self.records[rec.id] = rec
        self._trim()
        self.changed.emit()
        return rec.id

    def update(self, task_id: int, text: str = "", pct: int = -1) -> None:
        rec = self.records.get(task_id)
        if rec is None or rec.status != "running":
            return
        if text and text != rec.text:
            rec.text = text
            if not text.startswith("@"):
                rec.log.append(f"{time.strftime('%H:%M:%S')}  {text}")
        if pct >= 0:
            rec.pct = min(100, int(pct))
        self.changed.emit()

    def finish(self, task_id: int, status: str, summary: str = "") -> None:
        rec = self.records.get(task_id)
        if rec is None:
            return
        rec.status = status if status in STATUS_TEXT else "done"
        rec.finished = time.time()
        rec.summary = summary
        rec.cancel = None
        rec.log.append(f"{time.strftime('%H:%M:%S')}  {STATUS_TEXT[rec.status]}{': ' + summary if summary else ''}")
        self.changed.emit()

    def running(self) -> list:
        return [r for r in self.records.values() if r.status == "running"]

    def ordered(self) -> list:
        """Running first (oldest first), then the most recently finished."""
        run = sorted(self.running(), key=lambda r: r.started)
        done = sorted((r for r in self.records.values() if r.status != "running"), key=lambda r: -r.finished)
        return run + done

    def clear_finished(self) -> None:
        for k in [k for k, r in self.records.items() if r.status != "running"]:
            del self.records[k]
        self.changed.emit()

    def _trim(self):
        done = sorted((r for r in self.records.values() if r.status != "running"), key=lambda r: r.finished)
        while len(self.records) > self.keep and done:
            del self.records[done.pop(0).id]


def _fmt(seconds: float) -> str:
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


class TaskCenterPanel(QFrame):
    """Popover listing the jobs. Closes on Esc or a click outside (it is a Qt popup)."""

    def __init__(self, main, center: TaskCenter):
        super().__init__(main, Qt.Popup)
        self.main, self.center = main, center
        self.setObjectName("popover")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Task Center")
        t.setStyleSheet("font-size: 12pt; font-weight: 650;")
        head.addWidget(t, 1)
        from .widgets import button
        self.clear_btn = button("Clear finished", "ghost", self.center.clear_finished)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)
        sub = QLabel("Jobs keep running when you switch pages. Nothing is cancelled without asking.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.holder = QWidget()
        self.list = QVBoxLayout(self.holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(8)
        self.scroll.setWidget(self.holder)
        lay.addWidget(self.scroll, 1)
        self.center.changed.connect(self._schedule)
        self._pending = False
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.rebuild)

    def _schedule(self):
        if self.isVisible() and not self._pending:
            self._pending = True
            QTimer.singleShot(150, self.rebuild)

    def open_below(self, anchor):
        self.rebuild()
        pos = anchor.mapToGlobal(anchor.rect().bottomRight())
        h = min(560, max(220, self.main.height() - 120))
        self.setFixedHeight(h)
        self.move(pos.x() - self.width(), pos.y() + 6)
        self.show()
        self.clock.start()

    def hideEvent(self, e):
        self.clock.stop()
        super().hideEvent(e)

    def rebuild(self):
        from .widgets import button
        self._pending = False
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        records = self.center.ordered()
        self.clear_btn.setEnabled(any(r.status != "running" for r in records))
        if not records:
            empty = QLabel("No tasks yet this session.\nScans, cleanups, installs and repairs will appear here.")
            empty.setObjectName("empty")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(120)
            self.list.addWidget(empty)
        for r in records:
            box = QFrame()
            box.setObjectName("row")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 10, 12, 10)
            bl.setSpacing(4)
            top = QHBoxLayout()
            name = QLabel(r.title)
            name.setStyleSheet("font-weight: 600;")
            top.addWidget(name, 1)
            pct = f" {r.pct}%" if r.status == "running" and r.pct >= 0 else ""
            st = QLabel(f"● {STATUS_TEXT[r.status]}{pct}")
            st.setStyleSheet(f"color: {STATUS_COLOR[r.status]}; font-weight: 600;")
            top.addWidget(st)
            bl.addLayout(top)
            detail = QLabel((r.summary or r.text or "").lstrip("@"))
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            bl.addWidget(detail)
            if r.status == "running":
                bar = QProgressBar()
                bar.setObjectName("mini")
                if r.pct >= 0:
                    bar.setRange(0, 100)
                    bar.setValue(r.pct)
                else:
                    bar.setRange(0, 0)
                bl.addWidget(bar)
            row = QHBoxLayout()
            when = QLabel(("Elapsed " if r.status == "running" else "Took ") + _fmt(r.elapsed))
            when.setObjectName("faint")
            row.addWidget(when, 1)
            row.addWidget(button("Go to page", "link", lambda _=False, k=r.page_key: self._go(k)))
            row.addWidget(button("Log", "link", lambda _=False, rec=r: self._log(rec)))
            if r.status == "running" and r.cancel is not None:
                row.addWidget(button("Cancel…", "link", lambda _=False, rec=r: self._cancel(rec)))
            bl.addLayout(row)
            self.list.addWidget(box)
        self.list.addStretch(1)

    def _go(self, key):
        self.hide()
        if key:
            self.main.navigate(key)

    def _log(self, rec):
        from .dialogs import info
        self.hide()
        info(self.main, f"{rec.title}: log", f"{STATUS_TEXT[rec.status]} · {_fmt(rec.elapsed)}", "\n".join(rec.log))

    def _cancel(self, rec):
        from .dialogs import confirm
        self.hide()
        if rec.status == "running" and rec.cancel is not None and confirm(
                self.main, f"Stop “{rec.title}”?", "It stops at the next safe point. Work already finished is kept "
                                                    "and nothing is undone.", "Stop task"):
            rec.cancel()
