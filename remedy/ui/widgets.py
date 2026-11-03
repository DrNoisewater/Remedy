"""Reusable UI building blocks: background tasks, cards, charts, trees and the base page."""
from __future__ import annotations

import logging
import os
import time
from collections import deque

from PySide6.QtCore import QEasingCurve, QObject, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView,
                               QLabel, QPlainTextEdit, QProgressBar, QPushButton,
                               QSizePolicy, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import theme
from ..core.utils import TaskCancelled, human_size, list_drives, log

UR = Qt.UserRole          # sort key
PATH_ROLE = Qt.UserRole + 1
OBJ_ROLE = Qt.UserRole + 2


# --------------------------------------------------------------------------- background work
class Worker(QThread):
    progressed = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self._cancel = False
        self._last = 0.0

    def cancel(self):
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def progress(self, text: str, pct: int = -1, force: bool = False):
        now = time.monotonic()
        if force or now - self._last > 0.1:
            self._last = now
            self.progressed.emit(text, int(pct))

    def run(self):
        try:
            result = self.fn(self, *self.args, **self.kwargs)
            self.succeeded.emit(result)
        except TaskCancelled:
            self.failed.emit("Cancelled")
        except Exception as exc:  # noqa: BLE001 - surface every failure in the UI
            log.exception("Background task failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _Relay(QObject):
    """Lives in the GUI thread so callbacks always run there (queued connections).

    Holds the worker alive until BOTH the thread has finished and the result callback
    has been delivered. Dropping it earlier could lose the callback or destroy a
    still-running QThread.
    """

    def __init__(self, worker, on_done, on_error, on_progress):
        super().__init__()
        self.worker = worker
        self.on_done, self.on_error, self.on_progress = on_done, on_error, on_progress
        self._delivered = False
        self._finished = False

    def _maybe_release(self):
        if self._delivered and self._finished:
            _ACTIVE.discard(self.worker)

    @Slot(object)
    def done(self, result):
        try:
            if self.on_done:
                self.on_done(result)
        finally:
            self._delivered = True
            self._maybe_release()

    @Slot(str)
    def fail(self, msg):
        try:
            if self.on_error:
                self.on_error(msg)
        finally:
            self._delivered = True
            self._maybe_release()

    @Slot(str, int)
    def prog(self, text, pct):
        if self.on_progress:
            self.on_progress(text, pct)

    @Slot()
    def thread_finished(self):
        self._finished = True
        self._maybe_release()


_ACTIVE: set = set()


def start_task(fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs) -> Worker:
    """Run fn(worker, *args, **kwargs) on a background thread. fn uses worker.progress()/cancelled()."""
    w = Worker(fn, args, kwargs)
    relay = _Relay(w, on_done, on_error, on_progress)
    w._relay = relay
    w.succeeded.connect(relay.done)
    w.failed.connect(relay.fail)
    w.progressed.connect(relay.prog)
    w.finished.connect(relay.thread_finished)
    _ACTIVE.add(w)
    w.start()
    return w


# --------------------------------------------------------------------------- icons
_ICON_FAMILY = None


def icon_family() -> str:
    global _ICON_FAMILY
    if _ICON_FAMILY is None:
        fams = set(QFontDatabase.families())
        _ICON_FAMILY = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "")
    return _ICON_FAMILY


def glyph_icon(code: str, size: int = 18) -> QIcon:
    fam = icon_family()
    icon = QIcon()
    if not fam:
        return icon
    for mode, color in ((QIcon.Normal, theme.MUTED), (QIcon.Selected, theme.ACCENT), (QIcon.Active, theme.TEXT)):
        pm = QPixmap(size * 2, size * 2)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.TextAntialiasing)
        f = QFont(fam)
        f.setPixelSize(int(size * 1.35))
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(pm.rect(), Qt.AlignCenter, code)
        p.end()
        icon.addPixmap(pm, mode)
    return icon


def app_icon() -> QIcon:
    """Remedy's icon: teal medical cross with a stethoscope on a Noisewater-style tile."""
    from .splash import remedy_pixmap
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(remedy_pixmap(size))
    return icon


# --------------------------------------------------------------------------- dialogs
def confirm(parent, title: str, text: str, ok_text: str = "Continue", danger: bool = False, bullets=None) -> bool:
    from .dialogs import confirm as _confirm
    return _confirm(parent, title, text, ok_text, danger, bullets)


def info(parent, title: str, text: str, detail: str = "", bullets=None, kind: str = "info") -> None:
    from .dialogs import info as _info
    _info(parent, title, text, detail, bullets, kind)


def error(parent, title: str, text: str, detail: str = "") -> None:
    from .dialogs import error as _error
    _error(parent, title, text, detail)


def error_report(errors, limit: int = 200) -> str:
    return "\n".join(f"{p}\n    → {m}" for p, m in errors[:limit])


# --------------------------------------------------------------------------- building blocks
def button(text: str, kind: str = "", slot=None, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(slot)
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def pill(text: str, color: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("pill")
    c = QColor(color)
    lb.setStyleSheet(f"background: transparent; border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.35); "
                     f"color: {color};")
    return lb


class GradientLabel(QWidget):
    """Display text in one solid colour (kept as a class so older callers still work; the gradient was removed)."""

    def __init__(self, text: str, pixel_size: int = 18, weight=QFont.Bold, parent=None):
        super().__init__(parent)
        self.text = text
        self.font_ = QFont(self.font())
        self.font_.setPixelSize(pixel_size)
        self.font_.setWeight(min(weight, QFont.DemiBold))
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font_)
        self.setFixedSize(fm.horizontalAdvance(text) + 4, fm.height() + 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self.font_)
        p.setPen(QColor(theme.TEXT))
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        p.end()


class Card(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 16, 20, 16)
        self.body.setSpacing(12)
        if title:
            self.body.addWidget(label(title, "cardtitle"))


class Sparkline(QWidget):
    def __init__(self, color: str, maxlen: int = 60, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.values = deque([0.0] * maxlen, maxlen=maxlen)
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, v: float):
        self.values.append(max(0.0, min(100.0, float(v))))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -1)
        n = len(self.values)
        step = r.width() / max(1, n - 1)
        path = QPainterPath()
        for i, v in enumerate(self.values):
            x, y = r.left() + i * step, r.bottom() - v / 100 * r.height()
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(self.color, 1.5))
        p.drawPath(path)
        p.end()


class StatTile(Card):
    def __init__(self, title: str, spark_color: str = "", bar: bool = False, parent=None):
        super().__init__(title, parent)
        self.value = label("", "bignum")
        self.sub = label("", "sub")
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self.spark = Sparkline(spark_color) if spark_color else None
        self.bar = QProgressBar() if bar else None
        if self.spark:
            self.body.addWidget(self.spark)
        if self.bar:
            self.bar.setRange(0, 100)
            self.body.addWidget(self.bar)
        self.body.addStretch(1)

    def set(self, value: str, sub: str = ""):
        self.value.setText(value)
        self.sub.setText(sub)


def set_bar_level(bar: QProgressBar, pct: float) -> None:
    level = "danger" if pct >= 90 else "warn" if pct >= 80 else ""
    if bar.property("level") != level:
        bar.setProperty("level", level)
        bar.style().unpolish(bar)
        bar.style().polish(bar)
    bar.setValue(int(pct))


class ScoreRing(QWidget):
    """Circular health gauge: animated arc, big score, 'out of 100' and a grade word."""

    SIZE = 184
    STROKE = 11

    def __init__(self, parent=None):
        super().__init__(parent)
        self.score = None
        self._shown = 0.0
        self.setFixedSize(self.SIZE, self.SIZE)
        from PySide6.QtCore import QVariantAnimation
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(900)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, value):
        self._shown = float(value)
        self.update()

    def set_score(self, score):
        self.score = score
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(float(score))
        self.anim.start()

    @staticmethod
    def grade(score):
        if score >= 90:
            return "Excellent", theme.OK
        if score >= 75:
            return "Good", theme.OK
        if score >= 55:
            return "Fair", theme.WARN
        return "Needs work", theme.DANGER

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.STROKE / 2 + 6
        r = QRectF(self.rect()).adjusted(m, m, -m, -m)
        p.setPen(QPen(QColor(theme.SURFACE2), self.STROKE, Qt.SolidLine, Qt.FlatCap))
        p.drawEllipse(r)
        base = QFont(self.font())
        if self.score is None:
            base.setPixelSize(13)
            p.setFont(base)
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, "Not checked yet")
            p.end()
            return
        label_text, color = self.grade(self.score)
        span = -int(360 * 16 * self._shown / 100)
        p.setPen(QPen(QColor(color), self.STROKE, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, span)

        cx, cy = r.center().x(), r.center().y()
        num = QFont(base)
        num.setPixelSize(50)
        num.setWeight(QFont.Medium)
        p.setFont(num)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(cx - 70, cy - 46, 140, 56), Qt.AlignHCenter | Qt.AlignBottom, str(int(round(self._shown))))
        small = QFont(base)
        small.setPixelSize(12)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(cx - 70, cy + 10, 140, 16), Qt.AlignCenter, "out of 100")
        tag = QFont(base)
        tag.setPixelSize(12)
        tag.setWeight(QFont.Medium)
        p.setFont(tag)
        p.setPen(QColor(color))
        p.drawText(QRectF(cx - 70, cy + 28, 140, 16), Qt.AlignCenter, label_text)
        p.end()


class BarList(QWidget):
    """Horizontal bars: [(label, value_bytes, color)]."""

    ROW = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list = []

    def set_data(self, rows):
        self.rows = rows
        self.setMinimumHeight(max(1, len(rows)) * self.ROW + 4)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Run a scan to see the breakdown")
            return
        top = max(v for _, v, _ in self.rows) or 1
        w = self.width()
        label_w, value_w = 140, 80
        for i, (text, value, color) in enumerate(self.rows):
            y = i * self.ROW + 2
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(0, y, label_w - 8, self.ROW - 6), Qt.AlignVCenter | Qt.AlignLeft, text)
            bar_w = max(0.0, (w - label_w - value_w) * value / top)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE2))
            p.drawRoundedRect(QRectF(label_w, y + 8, w - label_w - value_w, 10), 5, 5)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(label_w, y + 8, max(4.0, bar_w), 10), 5, 5)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(w - value_w + 6, y, value_w - 6, self.ROW - 6), Qt.AlignVCenter | Qt.AlignRight,
                       human_size(value))
        p.end()


class ShareDelegate(QStyledItemDelegate):
    """Paints a percentage bar from the item's UserRole float (0..1)."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        frac = index.data(UR)
        if frac is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 0, -48, 0)
        bar = QRectF(r.left(), r.center().y() - 4, r.width(), 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.SURFACE2))
        painter.drawRoundedRect(bar, 4, 4)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(3.0, bar.width() * float(frac)), 8), 4, 4)
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(QRectF(option.rect.right() - 46, option.rect.top(), 44, option.rect.height()),
                         Qt.AlignVCenter | Qt.AlignRight, f"{float(frac) * 100:.1f}%")
        painter.restore()


class SortItem(QTreeWidgetItem):
    """Tree item that sorts by UserRole data when present (sizes, dates, numbers)."""

    def __lt__(self, other):
        tree = self.treeWidget()
        col = tree.sortColumn() if tree else 0
        a, b = self.data(col, UR), other.data(col, UR)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text(col).lower() < other.text(col).lower()


def make_tree(headers, multi: bool = True, sortable: bool = True, stretch_col: int = 0) -> QTreeWidget:
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setAlternatingRowColors(True)
    t.setRootIsDecorated(False)
    t.setUniformRowHeights(True)
    t.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
    t.setSortingEnabled(sortable)
    t.setAllColumnsShowFocus(True)
    hdr = t.header()
    hdr.setStretchLastSection(False)
    hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for i in range(len(headers)):
        hdr.setSectionResizeMode(i, QHeaderView.Stretch if i == stretch_col else QHeaderView.Interactive)
    t.setContextMenuPolicy(Qt.CustomContextMenu)
    return t


def attach_empty(tree: QTreeWidget, text: str) -> QLabel:
    """Friendly message in the middle of an empty list ("Press Scan to…"). Returns the label so text can change."""
    msg = QLabel(text, tree.viewport())
    msg.setObjectName("empty")
    msg.setAlignment(Qt.AlignCenter)
    msg.setWordWrap(True)
    msg.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sync(*_):
        try:
            vp = tree.viewport()
            msg.setGeometry(40, 0, max(100, vp.width() - 80), max(60, vp.height()))
            msg.setVisible(tree.topLevelItemCount() == 0)
        except RuntimeError:  # the list was closed before this ran
            pass

    model = tree.model()
    for sig in (model.rowsInserted, model.rowsRemoved, model.modelReset, model.layoutChanged):
        sig.connect(sync)
    old_resize = tree.viewport().resizeEvent

    def on_resize(e):
        old_resize(e)
        sync()
    tree.viewport().resizeEvent = on_resize
    QTimer.singleShot(0, msg, sync)
    return msg


def size_widths(tree: QTreeWidget, widths: dict) -> None:
    for col, w in widths.items():
        tree.setColumnWidth(col, w)


def fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


class ProgressRow(QFrame):
    """Task progress strip: what's happening, percent, elapsed / time left, a bar and Cancel.

    Shows a short green "Done in 12s" confirmation when the task finishes.
    """

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("progressrow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(12)
        self.dot = label("●")
        self.dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 9pt;")
        self.text = label("", "")
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.pct = label("", "")
        self.pct.setStyleSheet("font-weight: 700;")
        self.pct.setMinimumWidth(40)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time = label("", "muted")
        self.time.setMinimumWidth(120)
        self.time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(240)
        self.cancel = button("Cancel", "ghost", self.cancel_requested.emit, "Stop this task (Esc)")
        lay.addWidget(self.dot)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.pct)
        lay.addWidget(self.time)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel)
        self._started = 0.0
        self._pct = -1
        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._tick)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self.hide()

    def _set_state(self, state: str):
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)

    def start(self, text: str, cancellable: bool = True):
        self.hide_timer.stop()
        self._set_state("running")
        self.dot.setText("●")
        self.dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 9pt;")
        self.text.setText(text)
        self.pct.setText("")
        self.time.setText("")
        self.bar.setRange(0, 0)
        self.bar.show()
        self.cancel.setVisible(cancellable)
        self._started = time.monotonic()
        self._pct = -1
        self.clock.start()
        self.show()

    def update_progress(self, text: str, pct: int = -1):
        if text:
            self.text.setText(text)
            self.text.setToolTip(text)
        if pct >= 0:
            pct = min(100, int(pct))
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 100)
            self.bar.setValue(pct)
            self._pct = pct
            self.pct.setText(f"{pct}%")
        self._tick()

    def _tick(self):
        if not self._started:
            return
        elapsed = time.monotonic() - self._started
        parts = [fmt_eta(elapsed)]
        if 3 <= self._pct < 100 and elapsed > 3:
            parts.append(f"~{fmt_eta(elapsed * (100 - self._pct) / self._pct)} left")
        self.time.setText(" · ".join(parts))

    def stop(self, ok: bool = True, summary: str = ""):
        """Finish: flash a short result, then tuck away."""
        self.clock.stop()
        elapsed = time.monotonic() - self._started if self._started else getattr(self, "_elapsed", 0)
        self._elapsed = elapsed
        self._started = 0.0
        self.bar.hide()
        self.cancel.hide()
        self.pct.setText("")
        color = theme.OK if ok else theme.WARN
        self._set_state("done" if ok else "stopped")
        self.dot.setText("✓" if ok else "■")
        self.dot.setStyleSheet(f"color: {color}; font-size: 10pt; font-weight: 700;")
        self.text.setText(summary or ("Done" if ok else "Stopped"))
        self.time.setText(f"in {fmt_eta(elapsed)}" if ok and elapsed >= 1 else "")
        self.hide_timer.start(2600 if ok else 3500)


class ActivityPanel(QFrame):
    """Sidebar list of everything currently running, so progress stays visible from any page."""

    def __init__(self, navigate, parent=None):
        super().__init__(parent)
        self.setObjectName("activity")
        self.navigate = navigate
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 10, 12, 10)
        self.lay.setSpacing(8)
        head = label("Running", "cardtitle")
        self.lay.addWidget(head)
        self.entries: dict = {}
        self._next = 0
        self.hide()

    def start(self, key: str, title: str, text: str) -> int:
        self._next += 1
        token = self._next
        box = QFrame()
        box.setObjectName("activityitem")
        box.setCursor(Qt.PointingHandCursor)
        box.setToolTip(f"Go to {title}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(3)
        top = QHBoxLayout()
        name = label(title)
        name.setStyleSheet("font-weight: 600; font-size: 9pt;")
        pct = label("", "muted")
        pct.setStyleSheet("font-size: 8.5pt;")
        top.addWidget(name, 1)
        top.addWidget(pct)
        bl.addLayout(top)
        status = label(text, "muted")
        status.setStyleSheet("font-size: 8.5pt;")
        status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bl.addWidget(status)
        bar = QProgressBar()
        bar.setObjectName("mini")
        bar.setRange(0, 0)
        bl.addWidget(bar)
        box.mousePressEvent = lambda _e, k=key: self.navigate(k)
        self.lay.addWidget(box)
        self.entries[token] = (box, status, pct, bar)
        self.show()
        return token

    def update(self, token: int, text: str = "", pct: int = -1):
        entry = self.entries.get(token)
        if not entry:
            return
        _box, status, pct_label, bar = entry
        if text:
            status.setText(text)
        if pct >= 0:
            if bar.maximum() == 0:
                bar.setRange(0, 100)
            bar.setValue(min(100, int(pct)))
            pct_label.setText(f"{min(100, int(pct))}%")

    def finish(self, token: int, ok: bool = True, text: str = ""):
        entry = self.entries.get(token)
        if not entry:
            return
        box, status, pct_label, bar = entry
        bar.setRange(0, 100)
        bar.setValue(100 if ok else bar.value())
        pct_label.setText("✓" if ok else "■")
        pct_label.setStyleSheet(f"color: {theme.OK if ok else theme.WARN}; font-weight: 700;")
        status.setText(text or ("Done" if ok else "Stopped"))
        QTimer.singleShot(2500, lambda: self._drop(token))

    def _drop(self, token: int):
        entry = self.entries.pop(token, None)
        if entry:
            entry[0].deleteLater()
        if not self.entries:
            self.hide()

    def running(self) -> int:
        return len(self.entries)


class Console(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("console")
        self.setReadOnly(True)
        self.setMaximumBlockCount(8000)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def add(self, text: str):
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


class LogBus(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bus: LogBus):
        super().__init__()
        self.bus = bus
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.bus.message.emit(self.format(record))
        except RuntimeError:
            pass


class LocationPicker(QWidget):
    """Drive/folder chooser. roots() returns the list of paths to scan."""

    def __init__(self, allow_all: bool = True, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(320)
        lay.addWidget(self.combo, 1)
        lay.addWidget(button("Browse…", slot=self.browse))
        drives = list_drives()
        if allow_all and len(drives) > 1:
            self.combo.addItem("All drives", [d["mount"] for d in drives])
        for d in drives:
            self.combo.addItem(f"{d['mount']}   {human_size(d['free'])} free of {human_size(d['total'])}", [d["mount"]])
        home = os.path.expanduser("~")
        self.combo.addItem(f"Your user folder   {home}", [home])

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to scan")
        if path:
            self.set_folder(path)

    def set_folder(self, path: str):
        path = os.path.normpath(path)
        self.combo.insertItem(0, path, [path])
        self.combo.setCurrentIndex(0)

    def roots(self) -> list:
        return list(self.combo.currentData() or [])


class ResponsiveGrid(QWidget):
    """Lays its widgets out in as many equal columns as fit (each at least min_col px wide), dropping to fewer
    columns in a narrow window or at high DPI scaling instead of squashing or clipping them."""

    def __init__(self, widgets, min_col: int = 260, spacing: int = 14, max_cols: int = 0, parent=None):
        super().__init__(parent)
        self.items = list(widgets)
        self.min_col, self.max_cols = min_col, max_cols or len(self.items)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(spacing)
        self.cols = 0
        self._place(self.max_cols)

    def _place(self, cols: int):
        cols = max(1, min(self.max_cols, cols))
        if cols == self.cols:
            return
        self.cols = cols
        for w in self.items:
            self.grid.removeWidget(w)
        for c in range(self.max_cols):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        for i, w in enumerate(self.items):
            self.grid.addWidget(w, i // cols, i % cols)
        # We usually get here from inside the parent's layout pass, where a size change is ignored; tell the
        # parent right after that pass instead, so it makes room for the extra rows.
        QTimer.singleShot(0, self._announce)

    def _announce(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QScrollArea
        self.grid.invalidate()
        self.updateGeometry()
        w = self.parentWidget()
        while w is not None:
            if w.layout() is not None:
                w.layout().invalidate()
            if isinstance(w, QScrollArea):           # make the scroll area re-measure its (now taller) content
                QCoreApplication.postEvent(w, QEvent(QEvent.LayoutRequest))
                break
            w = w.parentWidget()

    def minimumSizeHint(self):
        # Only ask for one column's width, so a scroll area can hand us the real (narrow) width and we re-flow;
        # the height is whatever the current arrangement needs.
        from PySide6.QtCore import QSize
        return QSize(self.min_col, self.grid.minimumSize().height())

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(self.min_col * self.cols, max(self.grid.sizeHint().height(), self.grid.minimumSize().height()))

    def hasHeightForWidth(self):
        return False        # our minimum height already matches the current arrangement

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place(max(1, (e.size().width() + self.grid.spacing()) // (self.min_col + self.grid.spacing())))


# --------------------------------------------------------------------------- page base
class BasePage(QWidget):
    title = ""
    subtitle = ""

    def __init__(self, main):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self._shown = False
        self.worker = None
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(30, 26, 30, 22)
        self.root.setSpacing(14)
        head = self._head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(self.title, "h1"))
        sub = label(self.subtitle, "muted", wrap=True)
        sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.root.addLayout(head)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Narrow window / big text: move the page's action buttons under the title instead of squashing them.
        from PySide6.QtWidgets import QBoxLayout
        need = self.actions.sizeHint().width() + 260 + 60
        want = QBoxLayout.TopToBottom if e.size().width() < need else QBoxLayout.LeftToRight
        if self._head.direction() != want:
            self._head.setDirection(want)
            self._head.setAlignment(self.actions, Qt.AlignLeft if want == QBoxLayout.TopToBottom else Qt.Alignment())

    def make_scrollable(self) -> None:
        """Move everything below the page header into a vertical scroll area, so small windows and high DPI
        scale settings scroll instead of squashing or overlapping content."""
        from PySide6.QtWidgets import QScrollArea
        body = QWidget()
        body.setObjectName("page")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(self.root.spacing())
        while self.root.count() > 1:
            stretch = self.root.stretch(1)
            item = self.root.takeAt(1)
            if item.widget():
                lay.addWidget(item.widget(), stretch)
            elif item.layout():
                lay.addLayout(item.layout(), stretch)
            elif item.spacerItem():
                lay.addItem(item.spacerItem())
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # last resort: scroll rather than clip
        sc.setWidget(body)
        self.root.addWidget(sc, 1)
        self.body_layout = lay

    def add_action(self, text: str, kind: str = "", slot=None, tooltip: str = "") -> QPushButton:
        b = button(text, kind, slot, tooltip)
        self.actions.addWidget(b)
        return b

    def showEvent(self, e):
        super().showEvent(e)
        if not self._shown:
            self._shown = True
            QTimer.singleShot(50, self.on_first_show)

    def on_first_show(self):
        pass

    def open_route(self, params: dict) -> None:
        """Called on every navigation to this page (including Back/Forward). params restore a sub-view.
        Default: {"search": text} fills the page's search box if it has one."""
        box = getattr(self, "search", None)
        if "search" in params and box is not None and hasattr(box, "setText"):
            box.setText(str(params["search"]))

    def route_label(self, params: dict) -> str:
        """Breadcrumb text for a sub-view ("" = the page itself)."""
        return ""

    def busy(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            self.main.notify("warning", "Still working on the last task",
                             "Wait for it to finish, or press Cancel (Esc) to stop it.")
            return True
        return False

    def run(self, fn, *args, progress: ProgressRow = None, text: str = "Working…", on_done=None,
            cancellable: bool = True, on_progress=None, summary=None, **kwargs):
        """Run fn in the background with a progress strip, a sidebar activity entry and error toasts.

        summary(result) -> str is shown as the green "Done" line when the task finishes.
        """
        if self.busy():
            return None
        if progress is not None:
            progress.start(text, cancellable)
            import warnings
            with warnings.catch_warnings():          # nothing connected yet is fine
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    progress.cancel_requested.disconnect()
                except (RuntimeError, TypeError):
                    pass
            progress.cancel_requested.connect(self.cancel_task)
        activity = getattr(self.main, "activity", None)
        token = activity.start(getattr(self, "key", ""), self.title, text) if activity is not None else None
        center = getattr(self.main, "tasks", None)
        task_id = center.start(self.title, getattr(self, "key", ""), text,
                               cancel=self.cancel_task if cancellable else None) if center is not None else None

        def done(result):
            self.worker = None
            line = ""
            if summary is not None:
                try:
                    line = summary(result) or ""
                except Exception:  # noqa: BLE001 - a summary must never break the result
                    line = ""
            if progress is not None:
                progress.stop(True, line)
            if token is not None:
                activity.finish(token, True, line)
            if task_id is not None:
                center.finish(task_id, "done", line)
            self.set_busy_ui(False)
            if on_done:
                on_done(result)

        def fail(msg):
            self.worker = None
            cancelled = msg == "Cancelled"
            if progress is not None:
                progress.stop(False, "Cancelled" if cancelled else "Stopped by an error")
            if token is not None:
                activity.finish(token, False, "Cancelled" if cancelled else "Failed")
            if task_id is not None:
                center.finish(task_id, "cancelled" if cancelled else "failed",
                              "Cancelled" if cancelled else (msg.splitlines()[0] if msg else "Failed"))
            self.set_busy_ui(False)
            if cancelled:
                self.main.notify("warning", f"{self.title}: cancelled", "Nothing further was changed.")
            else:
                first = msg.splitlines()[0] if msg else "Unknown error"
                self.main.notify("error", f"{self.title}: something went wrong", first[:160],
                                 action=("Show details", lambda: error(self, "Something went wrong", first, msg)))

        def prog(text_, pct):
            if token is not None:
                activity.update(token, text_, pct)
            if task_id is not None:
                center.update(task_id, text_, pct)
            if on_progress is not None:
                on_progress(text_, pct)
            elif progress is not None:
                progress.update_progress(text_, pct)

        self.set_busy_ui(True)
        self.worker = start_task(fn, *args, on_done=done, on_error=fail, on_progress=prog, **kwargs)
        return self.worker

    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel()

    def set_busy_ui(self, busy: bool):
        """Override to disable buttons while working."""
