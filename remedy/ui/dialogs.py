"""Themed dialogs: confirm / info / error, with an icon badge, plain-language text and optional details."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout)

from .. import theme
from .notify import _Badge

KIND_STYLE = {
    "confirm": (theme.WARN, "!"),
    "danger": (theme.DANGER, "!"),
    "info": (theme.INFO, "i"),
    "success": (theme.OK, "check"),
    "error": (theme.DANGER, "x"),
}


def fit_height(dialog: QDialog, width: int) -> None:
    """Size a dialog so wrapped text gets all the lines it needs (Qt otherwise squashes it)."""
    lay = dialog.layout()
    lay.activate()
    height = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.sizeHint().height()
    dialog.setMinimumSize(width, height)
    dialog.resize(width, height)


class Dialog(QDialog):
    def __init__(self, parent, kind: str, title: str, text: str, buttons, bullets=None, detail: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMaximumWidth(640)
        self.setObjectName("dialog")
        color, glyph = KIND_STYLE.get(kind, KIND_STYLE["info"])
        self.choice = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(_Badge(color, glyph), 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet("font-size: 13pt; font-weight: 700;")
        texts.addWidget(t)
        if text:
            body = QLabel(text)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setStyleSheet(f"color: {theme.MUTED};")
            texts.addWidget(body)
        for line in (bullets or [])[:12]:
            b = QLabel(f"•  {line}")
            b.setWordWrap(True)
            texts.addWidget(b)
        if bullets and len(bullets) > 12:
            texts.addWidget(QLabel(f"…and {len(bullets) - 12} more"))
        head.addLayout(texts, 1)
        root.addLayout(head)

        if detail:
            self.detail = QPlainTextEdit(detail)
            self.detail.setObjectName("console")
            self.detail.setReadOnly(True)
            self.detail.setMinimumHeight(160)
            self.detail.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.detail.hide()
            toggle = QPushButton("Show details")
            toggle.setProperty("kind", "ghost")
            toggle.setCursor(Qt.PointingHandCursor)

            def flip():
                self.detail.setVisible(not self.detail.isVisible())
                toggle.setText("Hide details" if self.detail.isVisible() else "Show details")
                self.adjustSize()
            toggle.clicked.connect(flip)
            root.addWidget(toggle, 0, Qt.AlignLeft)
            root.addWidget(self.detail, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        for text_, kind_, value in buttons:
            btn = QPushButton(text_)
            btn.setCursor(Qt.PointingHandCursor)
            if kind_:
                btn.setProperty("kind", kind_)
            btn.clicked.connect(lambda _=False, v=value: self._pick(v))
            if kind_ in ("primary", "danger"):
                btn.setDefault(True)
            row.addWidget(btn)
        root.addLayout(row)
        fit_height(self, 520)

    def _pick(self, value):
        self.choice = value
        self.accept()


def confirm(parent, title: str, text: str, ok_text: str = "Continue", danger: bool = False, bullets=None) -> bool:
    d = Dialog(parent, "danger" if danger else "confirm", title, text,
               [("Cancel", "", False), (ok_text, "danger" if danger else "primary", True)], bullets)
    d.exec()
    return bool(d.choice)


def info(parent, title: str, text: str, detail: str = "", bullets=None, kind: str = "info") -> None:
    Dialog(parent, kind, title, text, [("OK", "primary", True)], bullets, detail).exec()


def error(parent, title: str, text: str, detail: str = "") -> None:
    Dialog(parent, "error", title, text, [("OK", "primary", True)], None, detail).exec()
