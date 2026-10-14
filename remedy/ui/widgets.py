"""Reusable UI building blocks: background tasks, cards, charts, trees and the base page."""
from __future__ import annotations

import logging
import os
import time
from collections import deque

from PySide6.QtCore import QEasingCurve, QObject, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
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
def confirm(parent, title: str, text: str, ok_text: str = "Continue") -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning)
    yes = box.addButton(ok_text, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def info(parent, title: str, text: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setDetailedText(detail)
    box.setIcon(QMessageBox.Information)
    box.exec()


def error(parent, title: str, text: str, detail: str = "") -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setDetailedText(detail)
    box.setIcon(QMessageBox.Critical)
    box.exec()


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
    lb.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},0.16); color: {color};")
    return lb


class GradientLabel(QWidget):
    """Text painted with the Remedy teal to violet gradient (matches the splash)."""

    def __init__(self, text: str, pixel_size: int = 18, weight=QFont.Bold, parent=None):
        super().__init__(parent)
        self.text = text
        self.font_ = QFont(self.font())
        self.font_.setPixelSize(pixel_size)
        self.font_.setWeight(weight)
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font_)
        self.setFixedSize(fm.horizontalAdvance(text) + 4, fm.height() + 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        g = QLinearGradient(0, 0, max(1, self.width()), 0)
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor(theme.ACCENT2))
        p.setFont(self.font_)
        p.setPen(QPen(QBrush(g), 1))
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        p.end()


class Card(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            self.body.addWidget(label(title.upper(), "cardtitle"))


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
        fill = QPainterPath(path)
        fill.lineTo(r.right(), r.bottom())
        fill.lineTo(r.left(), r.bottom())
        fill.closeSubpath()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        c1 = QColor(self.color)
        c1.setAlpha(90)
        c2 = QColor(self.color)
        c2.setAlpha(0)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.fillPath(fill, QBrush(g))
        p.setPen(QPen(self.color, 2))
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
            return "EXCELLENT", theme.OK
        if score >= 75:
            return "GOOD", theme.OK
        if score >= 55:
            return "FAIR", theme.WARN
        return "NEEDS WORK", theme.DANGER

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
        glow = QColor(color)
        glow.setAlpha(40)
        span = -int(360 * 16 * self._shown / 100)
        p.setPen(QPen(glow, self.STROKE + 8, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, span)
        p.setPen(QPen(QColor(color), self.STROKE, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, span)

        cx, cy = r.center().x(), r.center().y()
        num = QFont(base)
        num.setPixelSize(50)
        num.setWeight(QFont.Bold)
        p.setFont(num)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(cx - 70, cy - 46, 140, 56), Qt.AlignHCenter | Qt.AlignBottom, str(int(round(self._shown))))
        small = QFont(base)
        small.setPixelSize(12)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(cx - 70, cy + 10, 140, 16), Qt.AlignCenter, "out of 100")
        tag = QFont(base)
        tag.setPixelSize(11)
        tag.setWeight(QFont.Bold)
        tag.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
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
        g = QLinearGradient(bar.left(), 0, bar.right(), 0)
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor(theme.ACCENT2))
        painter.setBrush(QBrush(g))
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


def size_widths(tree: QTreeWidget, widths: dict) -> None:
    for col, w in widths.items():
        tree.setColumnWidth(col, w)


class ProgressRow(QWidget):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.text = label("", "muted")
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        self.cancel = button("Cancel", "ghost", self.cancel_requested.emit)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel)
        self.hide()

    def start(self, text: str, cancellable: bool = True):
        self.text.setText(text)
        self.bar.setRange(0, 0)
        self.cancel.setVisible(cancellable)
        self.show()

    def update_progress(self, text: str, pct: int = -1):
        if text:
            self.text.setText(text)
        if pct >= 0:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 100)
            self.bar.setValue(min(100, pct))

    def stop(self):
        self.hide()


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
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(self.title, "h1"))
        titles.addWidget(label(self.subtitle, "muted"))
        head.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.root.addLayout(head)

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

    def busy(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            self.main.toast("Please wait for the current task to finish.")
            return True
        return False

    def run(self, fn, *args, progress: ProgressRow = None, text: str = "Working…", on_done=None,
            cancellable: bool = True, on_progress=None, **kwargs):
        if self.busy():
            return None
        if progress is not None:
            progress.start(text, cancellable)
            try:
                progress.cancel_requested.disconnect()
            except (RuntimeError, TypeError):
                pass
            progress.cancel_requested.connect(self.cancel_task)

        def done(result):
            self.worker = None
            if progress is not None:
                progress.stop()
            self.set_busy_ui(False)
            if on_done:
                on_done(result)

        def fail(msg):
            self.worker = None
            if progress is not None:
                progress.stop()
            self.set_busy_ui(False)
            if msg == "Cancelled":
                self.main.toast("Cancelled")
            else:
                error(self, "Something went wrong", msg)

        self.set_busy_ui(True)
        if on_progress is None and progress is not None:
            on_progress = progress.update_progress
        self.worker = start_task(fn, *args, on_done=done, on_error=fail, on_progress=on_progress, **kwargs)
        return self.worker

    def cancel_task(self):
        if self.worker is not None:
            self.worker.cancel()

    def set_busy_ui(self, busy: bool):
        """Override to disable buttons while working."""
