"""Command palette (Ctrl+K): type to jump to any page, tool, tweak, app or setting.

Entries only navigate, or start read-only checks. Anything that changes the PC still goes through its page's
review/confirm step, so nothing risky can run straight from search.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout


@dataclass
class Entry:
    title: str
    kind: str              # Page | Action | Tool | Tweak | App | Setting
    hint: str = ""
    run: object = None     # callable
    keywords: str = ""


def score(query: str, text: str) -> int:
    """Higher is better; -1 = no match. Every word of the query must appear (as a substring or, failing that,
    as letters in order); matches at the start of words score highest."""
    q = query.lower().strip()
    if not q:
        return 0
    t = text.lower()
    total = 0
    for word in q.split():
        i = t.find(word)
        if i >= 0:
            total += 30 if i == 0 else 20 if not t[i - 1].isalnum() else 8
            continue
        pos = -1
        for ch in word:
            pos = t.find(ch, pos + 1)
            if pos < 0:
                return -1
        total += 2
    return total


def search(entries: list, query: str, limit: int = 40) -> list:
    if not query.strip():
        return entries[:limit]
    scored = []
    for i, e in enumerate(entries):
        s = score(query, f"{e.title} {e.keywords}")
        if s < 0:
            s2 = score(query, e.hint)
            if s2 < 0:
                continue
            s = s2 // 2
        scored.append((-s, i, e))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [e for _s, _i, e in scored[:limit]]


class CommandPalette(QDialog):
    def __init__(self, main, entries: list):
        super().__init__(main, Qt.FramelessWindowHint | Qt.Dialog)
        self.setModal(True)
        self.entries = entries
        self.setObjectName("palettedlg")
        self.setStyleSheet("QDialog#palettedlg { border: 1px solid #333B3F; border-radius: 12px; }")
        self.setFixedWidth(min(640, max(420, main.width() - 200)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)
        self.box = QLineEdit()
        self.box.setObjectName("palette")
        self.box.setPlaceholderText("Search pages, tools, tweaks, apps and settings…")
        self.box.setAccessibleName("Command search")
        self.box.textChanged.connect(self._refresh)
        self.box.installEventFilter(self)
        lay.addWidget(self.box)
        self.list = QListWidget()
        self.list.setObjectName("palettelist")
        self.list.setMinimumHeight(340)
        self.list.itemActivated.connect(self._activate)
        self.list.itemClicked.connect(self._activate)
        lay.addWidget(self.list, 1)
        foot = QLabel("↑ ↓ to move · Enter to open · Esc to close. Search only opens things; changes are always "
                      "reviewed on their page.")
        foot.setObjectName("faint")
        foot.setWordWrap(True)
        lay.addWidget(foot)
        self._refresh("")
        geo = main.geometry()
        self.adjustSize()
        self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 70)

    def _refresh(self, text):
        self.list.clear()
        for e in search(self.entries, text):
            it = QListWidgetItem(f"{e.title}\n{e.kind}{' · ' + e.hint if e.hint else ''}")
            it.setData(Qt.UserRole, e)
            it.setToolTip(e.hint)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            it = QListWidgetItem("No matches. Try a page name like “startup”, or a word like “disk”.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)

    def eventFilter(self, obj, ev):
        if obj is self.box and ev.type() == ev.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                it = self.list.currentItem()
                if it is not None:
                    self._activate(it)
                return True
        return super().eventFilter(obj, ev)

    def _activate(self, item):
        e = item.data(Qt.UserRole)
        if not isinstance(e, Entry):
            return
        self.accept()
        if callable(e.run):
            e.run()

