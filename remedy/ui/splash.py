"""Brand artwork (Noisewater Labs 'Nw' tile, Remedy cross + stethoscope icon) and the launch splash."""
from __future__ import annotations


from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

from .. import theme
from ..core.utils import APP_NAME, APP_PUBLISHER, APP_VERSION

NW_TILE = "#17181c"
NW_EDGE = "#2a2c33"
NW_YELLOW = "#f5b800"
NW_GREEN = "#3ddc84"
WHITE = "#ffffff"


def _family(*names) -> str:
    fams = set(QFontDatabase.families())
    return next((n for n in names if n in fams), "")


def _round_pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


# --------------------------------------------------------------------------- Noisewater Labs tile
def draw_nw_tile(p: QPainter, r: QRectF, caption: str = "NOISEWATER LABS") -> None:
    """The Noisewater Labs studio mark: dark tile, beaker top-right, yellow 'Nw', caption."""
    s = r.width()
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(NW_EDGE), max(1.0, s * 0.004)))
    p.setBrush(QColor(NW_TILE))
    p.drawRoundedRect(r, s * 0.035, s * 0.035)

    # beaker
    bx, by, bw, bh = r.left() + s * 0.70, r.top() + s * 0.085, s * 0.19, s * 0.25
    p.setPen(_round_pen(WHITE, s * 0.018))
    p.setBrush(Qt.NoBrush)
    cup = QPainterPath()
    cup.moveTo(bx - bw * 0.08, by)
    cup.lineTo(bx + bw * 0.06, by + bh * 0.08)
    cup.lineTo(bx + bw * 0.06, by + bh * 0.84)
    cup.quadTo(bx + bw * 0.06, by + bh, bx + bw * 0.22, by + bh)
    cup.lineTo(bx + bw * 0.78, by + bh)
    cup.quadTo(bx + bw * 0.94, by + bh, bx + bw * 0.94, by + bh * 0.84)
    cup.lineTo(bx + bw * 0.94, by + bh * 0.08)
    cup.lineTo(bx + bw * 1.06, by)
    p.drawPath(cup)
    for color, fy, x0, x1 in ((NW_YELLOW, 0.30, 0.22, 0.62), (NW_GREEN, 0.46, 0.30, 0.78),
                              (WHITE, 0.62, 0.22, 0.58), (NW_YELLOW, 0.78, 0.22, 0.46)):
        p.setPen(_round_pen(color, s * 0.016))
        y = by + bh * fy
        p.drawLine(QPointF(bx + bw * x0, y), QPointF(bx + bw * x1, y))

    big = QFont(_family("Inter", "Segoe UI Variable Display", "Segoe UI", "Arial") or "sans-serif")
    big.setPixelSize(int(s * 0.36))
    big.setWeight(QFont.Black)
    big.setLetterSpacing(QFont.PercentageSpacing, 94)
    p.setFont(big)
    p.setPen(QColor(NW_YELLOW))
    p.drawText(QRectF(r.left() + s * 0.085, r.top() + s * 0.33, s * 0.9, s * 0.38), Qt.AlignLeft | Qt.AlignVCenter, "Nw")

    if caption:
        cap = QFont(big)
        cap.setPixelSize(int(s * 0.058))
        cap.setWeight(QFont.Bold)
        cap.setLetterSpacing(QFont.AbsoluteSpacing, s * 0.008)
        p.setFont(cap)
        p.setPen(QColor("#f2f2f2"))
        p.drawText(QRectF(r.left() + s * 0.1, r.top() + s * 0.78, s * 0.85, s * 0.1),
                   Qt.AlignLeft | Qt.AlignVCenter, caption)
    p.restore()


# --------------------------------------------------------------------------- Remedy icon
REMEDY_TEAL = theme.ACCENT


def _bezier(p0, p1, p2, p3, n=60):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def icon_geometry() -> dict:
    """Remedy mark in normalized 0..1 tile coordinates: a teal medical cross with a white
    stethoscope draped around it (earpieces top-left, chest piece bottom-right)."""
    ear_l, ear_r, joint, chest = (0.20, 0.18), (0.38, 0.16), (0.28, 0.49), (0.77, 0.69)
    main = _bezier(joint, (0.26, 0.70), (0.36, 0.85), (0.54, 0.84)) + \
        _bezier((0.54, 0.84), (0.65, 0.835), (0.70, 0.79), (chest[0] - 0.06, chest[1] + 0.035))[1:]
    return dict(
        cross=dict(cx=0.585, cy=0.415, size=0.40, thick=0.145, rad=0.03),
        ears=[ear_l, ear_r], ear_r=0.03,
        tubes=[_bezier(ear_l, (0.18, 0.30), (0.22, 0.43), joint),
               _bezier(ear_r, (0.39, 0.30), (0.33, 0.43), joint), main],
        tube_w=0.036, gap=0.022, chest=(chest[0], chest[1], 0.088), chest_ring=0.022, chest_inner=0.045)


def draw_remedy_icon(p: QPainter, r: QRectF, color: str = REMEDY_TEAL) -> None:
    """Remedy's mark on a Noisewater-style dark tile."""
    g = icon_geometry()
    s = r.width()

    def P(x, y):
        return QPointF(r.left() + x * s, r.top() + y * s)

    def disc(x, y, rad, color_):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color_))
        p.drawEllipse(P(x, y), rad * s, rad * s)

    def tubes(color_, width):
        p.setBrush(Qt.NoBrush)
        p.setPen(_round_pen(color_, width * s))
        for tube in g["tubes"]:
            path = QPainterPath(P(*tube[0]))
            for pt in tube[1:]:
                path.lineTo(P(*pt))
            p.drawPath(path)

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(NW_EDGE), max(1.0, s * 0.006)))
    p.setBrush(QColor(NW_TILE))
    p.drawRoundedRect(r, s * 0.2, s * 0.2)

    c = g["cross"]
    half, th = c["size"] / 2, c["thick"] / 2
    cross = QPainterPath()
    cross.addRoundedRect(QRectF(P(c["cx"] - th, c["cy"] - half), P(c["cx"] + th, c["cy"] + half)),
                         c["rad"] * s, c["rad"] * s)
    cross.addRoundedRect(QRectF(P(c["cx"] - half, c["cy"] - th), P(c["cx"] + half, c["cy"] + th)),
                         c["rad"] * s, c["rad"] * s)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(cross.simplified())

    # stethoscope: dark halo first so it sits cleanly in front of the cross
    cx, cy, rad = g["chest"]
    tubes(NW_TILE, g["tube_w"] + 2 * g["gap"])
    disc(cx, cy, rad + g["gap"], NW_TILE)
    for x, y in g["ears"]:
        disc(x, y, g["ear_r"] + g["gap"] * 0.6, NW_TILE)
    tubes(WHITE, g["tube_w"])
    for x, y in g["ears"]:
        disc(x, y, g["ear_r"], WHITE)
    disc(cx, cy, rad, WHITE)
    disc(cx, cy, rad - g["chest_ring"], NW_TILE)
    disc(cx, cy, g["chest_inner"], NW_GREEN)
    p.restore()


def remedy_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    draw_remedy_icon(p, QRectF(0.5, 0.5, size - 1, size - 1))
    p.end()
    return pm


class TileWidget(QWidget):
    """Static Noisewater Labs tile (About card)."""

    def __init__(self, size: int = 120, caption: str = "NOISEWATER", parent=None):
        super().__init__(parent)
        self.caption = caption
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        draw_nw_tile(p, QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), self.caption)
        p.end()


# --------------------------------------------------------------------------- splash
class Splash(QWidget):
    W, H = 640, 330

    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.message = "Starting…"
        self.pct = 0.0
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.W // 2, geo.center().y() - self.H // 2)

    def status(self, message: str, pct: float) -> None:
        self.message = message
        self.pct = max(self.pct, min(1.0, pct))
        self.repaint()
        QApplication.processEvents()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(theme.BG))
        p.drawRoundedRect(card, 16, 16)

        tile = QRectF(30, 30, 270, 270)
        draw_nw_tile(p, tile, "NOISEWATER LABS")

        x = tile.right() + 34
        w = card.right() - x - 30
        draw_remedy_icon(p, QRectF(x, 66, 58, 58))

        title = QFont(_family("Segoe UI Variable Display", "Segoe UI", "Inter") or "sans-serif")
        title.setPixelSize(44)
        title.setWeight(QFont.Bold)
        p.setFont(title)
        grad = QLinearGradient(x + 72, 0, x + 260, 0)
        grad.setColorAt(0, QColor(theme.ACCENT))
        grad.setColorAt(1, QColor(theme.ACCENT2))
        p.setPen(QPen(QBrush(grad), 1))
        p.drawText(QRectF(x + 72, 64, w - 72, 60), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)

        body = QFont(title)
        body.setPixelSize(15)
        body.setWeight(QFont.Normal)
        p.setFont(body)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(x, 136, w, 24), Qt.AlignLeft | Qt.AlignVCenter, "PC optimization suite")
        body.setPixelSize(12)
        p.setFont(body)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(x, 160, w, 20), Qt.AlignLeft | Qt.AlignVCenter, f"Version {APP_VERSION}")

        bar = QRectF(x, 236, w, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE2))
        p.drawRoundedRect(bar, 2, 2)
        fill = QLinearGradient(bar.left(), 0, bar.right(), 0)
        fill.setColorAt(0, QColor(theme.ACCENT))
        fill.setColorAt(1, QColor(theme.ACCENT2))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(4.0, bar.width() * self.pct), bar.height()), 2, 2)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(x, 208, w, 22), Qt.AlignLeft | Qt.AlignVCenter, self.message)
        body.setPixelSize(11)
        p.setFont(body)
        p.drawText(QRectF(x, 262, w, 20), Qt.AlignLeft | Qt.AlignVCenter, f"© 2026 {APP_PUBLISHER}")
        p.end()

    def finish(self, window) -> None:
        """Fade out, then hand over to the main window."""
        self.status("Ready", 1.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(260)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InQuad)
        anim.finished.connect(self.close)
        anim.finished.connect(window.raise_)
        anim.finished.connect(window.activateWindow)
        self._anim = anim
        anim.start()
