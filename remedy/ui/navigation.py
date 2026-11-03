"""Central navigation: one Back/Forward history for the whole app, plus mouse side buttons and Alt+arrows.

Every page change goes through MainWindow.navigate(), which records a Route here. Pages stay alive in the page
stack, so their scroll position, selection, filters and running tasks survive navigation; a Route's params restore
sub-views (a selected finding, a wizard step). Going Back only changes what is shown: it never re-runs or undoes work.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Route:
    key: str
    params: tuple = field(default_factory=tuple)   # sorted (name, value) pairs, so routes compare by value

    @classmethod
    def make(cls, key: str, params: dict | None = None) -> "Route":
        return cls(key, tuple(sorted((params or {}).items())))

    @property
    def args(self) -> dict:
        return dict(self.params)


class NavHistory:
    """Browser-style history. push() drops the forward trail; replace() swaps the current entry in place."""

    def __init__(self, limit: int = 100):
        self.limit = limit
        self._back: list = []
        self._forward: list = []
        self.current: Route | None = None

    def push(self, route: Route) -> bool:
        if route == self.current:
            return False
        if self.current is not None:
            self._back.append(self.current)
            del self._back[:-self.limit]
        self.current = route
        self._forward.clear()
        return True

    def replace(self, route: Route) -> None:
        self.current = route

    def can_back(self) -> bool:
        return bool(self._back)

    def can_forward(self) -> bool:
        return bool(self._forward)

    def back(self) -> Route | None:
        if not self._back:
            return None
        if self.current is not None:
            self._forward.append(self.current)
        self.current = self._back.pop()
        return self.current

    def forward(self) -> Route | None:
        if not self._forward:
            return None
        if self.current is not None:
            self._back.append(self.current)
        self.current = self._forward.pop()
        return self.current

    def trail(self) -> list:
        return list(self._back) + ([self.current] if self.current else []) + list(reversed(self._forward))


class MouseNavFilter(QObject):
    """Application event filter: mouse side buttons (XButton1/XButton2) and keyboard Back/Forward keys.

    Acts on button release (like browsers), consumes both press and release so the event is handled exactly once,
    ignores clicks while a dialog is open, and leaves widgets alone that set the property ownsHistoryButtons=True.
    """

    def __init__(self, window, on_back, on_forward):
        super().__init__(window)
        self.window = window
        self.on_back, self.on_forward = on_back, on_forward
        self._last = 0.0

    def _ours(self, obj) -> bool:
        if not obj.isWidgetType():
            return False          # the QWindow copy of the event: let it reach the widget, handle it there
        if obj.window() is not self.window or QApplication.activeModalWidget() is not None:
            return False
        w = obj
        while w is not None:
            if w.property("ownsHistoryButtons"):
                return False
            w = w.parentWidget()
        return True

    def _fire(self, back: bool):
        now = time.monotonic()
        if now - self._last < 0.15:      # a keyboard Back key and a mouse button can arrive together
            return
        self._last = now
        (self.on_back if back else self.on_forward)()

    def eventFilter(self, obj, event):
        et = event.type()
        if et in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            button = event.button()
            if button in (Qt.BackButton, Qt.ForwardButton) and self._ours(obj):
                if et == QEvent.MouseButtonRelease:
                    self._fire(button == Qt.BackButton)
                return True
        elif et == QEvent.KeyPress and event.key() in (Qt.Key_Back, Qt.Key_Forward) and self._ours(obj):
            self._fire(event.key() == Qt.Key_Back)
            return True
        return False
