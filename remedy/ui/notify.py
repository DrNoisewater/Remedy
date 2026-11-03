"""Toast notifications: typed (success / info / warning / error), stacked bottom-right, with an optional action.

Usage from any page:  self.main.notify("success", "Freed 2.4 GB", "1,204 files removed")
                      self.main.notify("error", "Couldn't apply 2 tweaks", "Needs administrator", action=("Details", fn))
"""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import theme

KINDS = {
    # kind: (colour, glyph drawn in the badge)
    "success": (theme.OK, "check"),
    "info": (theme.INFO, "i"),
    "warning": (theme.WARN, "!"),
    "error": (theme.DANGER, "x"),
}
MAX_VISIBLE = 4
MARGIN = 24
GAP = 10


def duration_for(kind: str, title: str, body: str = "") -> int:
    """How long a toast stays up: long enough to read, longer for problems."""
    words = len((title + " " + body).split())
    ms = 2600 + words * 260
    if kind in ("warning", "error"):
        ms += 2500
    return max(3500, min(ms, 11000))


class _Badge(QWidget):
    """Round coloured badge with a tick, i, ! or x."""

    def __init__(self, color: str, glyph: str, parent=None):
        super().__init__(parent)
        self.color, self.glyph = QColor(color), glyph
        self.setFixedSize(26, 26)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fill = QColor(self.color)
        fill.setAlpha(46)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QRectF(0, 0, 26, 26))
        pen = QPen(self.color, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        if self.glyph == "check":
            p.drawPolyline([QPointF(8, 13.5), QPointF(11.5, 17), QPointF(18, 9.5)])
        elif self.glyph == "x":
            p.drawLine(9, 9, 17, 17)
            p.drawLine(17, 9, 9, 17)
        else:
            f = QFont(self.font())
            f.setPixelSize(15)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 26, 26), Qt.AlignCenter, self.glyph)
        p.end()


class _Countdown(QWidget):
    """Thin line along the bottom that shrinks as the toast's time runs out."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.frac = 1.0
        self.setFixedHeight(2)

    def set_frac(self, value):
        self.frac = float(value)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        c = QColor(self.color)
        c.setAlpha(150)
        p.fillRect(0, 0, int(self.width() * self.frac), self.height(), c)
        p.end()


class ToastCard(QFrame):
    closed = Signal(object)

    def __init__(self, parent, kind: str, title: str, body: str = "", action=None, ms: int = 0):
        super().__init__(parent)
        kind = kind if kind in KINDS else "info"
        color, glyph = KINDS[kind]
        self.kind, self.title_text, self.body_text = kind, title, body
        self.setObjectName("toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QFrame#toast {{ background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                           f"border-left: 3px solid {color}; border-radius: 10px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(14, 12, 10, 11)
        row.setSpacing(12)
        row.addWidget(_Badge(color, glyph), 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"color: {theme.TEXT}; font-weight: 700; font-size: 10.5pt;")
        texts.addWidget(self.title)
        self.body = QLabel(body)
        self.body.setWordWrap(True)
        self.body.setStyleSheet(f"color: {theme.MUTED}; font-size: 9.5pt;")
        self.body.setVisible(bool(body))
        texts.addWidget(self.body)
        if action:
            text, fn = action
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {color}; "
                              f"font-weight: 700; padding: 4px 0 0 0; text-align: left; }}"
                              f"QPushButton:hover {{ text-decoration: underline; }}")
            btn.clicked.connect(lambda: (fn(), self.dismiss()))
            texts.addWidget(btn, 0, Qt.AlignLeft)
        row.addLayout(texts, 1)
        close = QPushButton("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setToolTip("Dismiss")
        close.setFixedSize(22, 22)
        close.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {theme.MUTED}; "
                            f"font-size: 9pt; padding: 0; }} QPushButton:hover {{ color: {theme.TEXT}; }}")
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignTop)
        outer.addLayout(row)
        self.countdown = _Countdown(color)
        outer.addWidget(self.countdown)

        self.ms = ms or duration_for(kind, title, body)
        self.life = QVariantAnimation(self)
        self.life.setStartValue(1.0)
        self.life.setEndValue(0.0)
        self.life.setDuration(self.ms)
        self.life.valueChanged.connect(self.countdown.set_frac)
        self.life.finished.connect(self.dismiss)
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        # one animation per property, reused, so moving a toast never cancels its fade
        self.mover = QPropertyAnimation(self, b"pos", self)
        self.mover.setDuration(240)
        self.mover.setEasingCurve(QEasingCurve.OutCubic)
        self.fade_in = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade_in.setDuration(200)
        self.fade_in.setEndValue(1.0)
        self._leaving = False

    # ---- life cycle
    def start(self):
        self.life.start()

    def restart(self):
        self.life.stop()
        self.life.start()

    def enterEvent(self, e):          # hovering keeps the toast up so it can be read
        if self.life.state() == QVariantAnimation.Running:
            self.life.pause()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.life.state() == QVariantAnimation.Paused:
            self.life.resume()
        super().leaveEvent(e)

    def dismiss(self):
        if self._leaving:
            return
        self._leaving = True
        self.life.stop()
        self.fade_in.stop()
        self.fade_in.setDuration(220)
        self.fade_in.setEndValue(0.0)
        self.fade_in.finished.connect(lambda: self.closed.emit(self))
        self.fade_in.start()


class ToastManager:
    """Owns the stack of toasts for one window."""

    def __init__(self, window: QWidget, width: int = 380):
        self.window = window
        self.width = width
        self.cards: list = []

    def show(self, kind: str, title: str, body: str = "", action=None, ms: int = 0) -> ToastCard:
        for card in self.cards:   # same message again: just keep it up longer
            if not card._leaving and card.title_text == title and card.body_text == body and card.kind == kind:
                card.restart()
                return card
        card = ToastCard(self.window, kind, title, body, action, ms)
        card.setFixedWidth(min(self.width, max(280, self.window.width() - 2 * MARGIN)))
        card.layout().activate()
        card.setFixedHeight(card.layout().totalHeightForWidth(card.width()))
        card.closed.connect(self._remove)
        self.cards.append(card)
        live = [c for c in self.cards if not c._leaving]
        for old in live[:-MAX_VISIBLE]:
            old.dismiss()
        card.show()
        card.raise_()
        self._layout(new=card)
        card.start()
        return card

    def _remove(self, card):
        if card in self.cards:
            self.cards.remove(card)
        card.hide()
        card.deleteLater()
        self._layout()

    def _layout(self, new=None):
        """Stack newest at the bottom; existing toasts glide up to make room."""
        w = self.window
        y = w.height() - MARGIN
        for card in reversed(self.cards):
            y -= card.height()
            target = QPoint(w.width() - card.width() - MARGIN, y)
            if card is new:
                card.move(target + QPoint(40, 0))
                card.fade_in.start()
            card.mover.stop()
            card.mover.setEndValue(target)
            card.mover.start()
            y -= GAP

    def reposition(self):
        for card in self.cards:
            card.raise_()
            card.mover.stop()
        w = self.window
        y = w.height() - MARGIN
        for card in reversed(self.cards):
            y -= card.height()
            card.move(w.width() - card.width() - MARGIN, y)
            y -= GAP

    def visible(self) -> list:
        return [c for c in self.cards if not c._leaving]
