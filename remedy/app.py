"""Remedy main window: sidebar navigation, page stack, toasts and startup sequence."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .core.utils import (APP_NAME, APP_PUBLISHER, APP_VERSION, IS_WINDOWS, is_admin, log, relaunch_as_admin,
                         settings, setup_logging)
from .ui import widgets as W

# (key, label, MDL2 glyph, section)
NAV = [
    ("dashboard", "Dashboard", "", "OVERVIEW"),
    ("cleaner", "Junk Cleaner", "", "CLEAN UP"),
    ("large", "Large Files", "", "CLEAN UP"),
    ("dupes", "Duplicates", "", "CLEAN UP"),
    ("disk", "Disk Analyzer", "", "CLEAN UP"),
    ("startup", "Startup Apps", "", "OPTIMIZE"),
    ("tweaks", "Tweaks", "", "OPTIMIZE"),
    ("services", "Services", "", "OPTIMIZE"),
    ("processes", "Processes", "", "OPTIMIZE"),
    ("setup", "New PC Setup", "", "APPS"),
    ("bloat", "Bloatware Remover", "", "APPS"),
    ("programs", "Installed Programs", "", "APPS"),
    ("maintenance", "Maintenance", "", "SYSTEM"),
    ("sysinfo", "System Info", "", "SYSTEM"),
    ("settings", "Settings & Log", "", "SYSTEM"),
]


def _page_classes() -> dict:
    from .ui import pages_apps, pages_clean, pages_optimize, pages_system
    return {
        "dashboard": pages_system.DashboardPage, "cleaner": pages_clean.CleanerPage,
        "large": pages_clean.LargeFilesPage, "dupes": pages_clean.DuplicatesPage, "disk": pages_clean.DiskPage,
        "startup": pages_optimize.StartupPage, "tweaks": pages_optimize.TweaksPage,
        "services": pages_optimize.ServicesPage, "processes": pages_optimize.ProcessesPage,
        "setup": pages_apps.SetupPage, "bloat": pages_apps.BloatwarePage, "programs": pages_apps.ProgramsPage,
        "maintenance": pages_system.MaintenancePage, "sysinfo": pages_system.SysInfoPage,
        "settings": pages_system.SettingsPage,
    }


class Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 11)
        self.text = QLabel()
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self._faded)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade)
        self.hide()

    def show_message(self, text: str, ms: int = 4500):
        self.text.setText(text)
        self.setFixedWidth(min(520, max(280, self.parent().width() // 2)))
        self.adjustSize()
        self.reposition()
        self.anim.stop()
        self.effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self.timer.start(ms)

    def reposition(self):
        p = self.parent()
        self.move(p.width() - self.width() - 24, p.height() - self.height() - 24)

    def _fade(self):
        self.anim.setDuration(400)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _faded(self):
        if self.effect.opacity() < 0.05:
            self.hide()


class MainWindow(QMainWindow):
    def __init__(self, splash=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(W.app_icon())
        self.setMinimumSize(1100, 700)
        self._restore_geometry()
        self.log_bus = W.LogBus()
        handler = W.QtLogHandler(self.log_bus)
        log.addHandler(handler)

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages: dict = {}
        classes = _page_classes()
        for i, (key, text, _glyph, _sec) in enumerate(NAV):
            if splash:
                splash.status(f"Loading {text}…", 0.15 + 0.8 * i / len(NAV))
            page = classes[key](self)
            self.pages[key] = page
            self.stack.addWidget(page)
        self.toast_widget = Toast(self)
        self.navigate("dashboard")

    # ---------------------------------------------------------------- window size & position
    def _restore_geometry(self):
        """Reopen at the last size/position; first run gets a roomy window centred on screen."""
        saved = settings.get("window_geometry")
        if saved:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii"))):
                    return
            except (ValueError, UnicodeError):
                pass
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        w, h = 1520, 960
        if avail is not None:
            w, h = min(w, int(avail.width() * 0.92)), min(h, int(avail.height() * 0.92))
            self.resize(w, h)
            self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)
        else:
            self.resize(w, h)

    def _save_geometry(self):
        settings.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))

    # ---------------------------------------------------------------- sidebar
    def _sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(250)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 22, 0, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 16, 10)
        logo = QLabel()
        logo.setPixmap(W.app_icon().pixmap(38, 38))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(W.GradientLabel(APP_NAME, 17, weight=QFont.ExtraBold))
        names.addWidget(W.label(f"BY {APP_PUBLISHER.upper()}", "brandsub"))
        brand.addLayout(names, 1)
        lay.addLayout(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.nav_rows: dict = {}
        section = None
        for key, text, glyph, sec in NAV:
            if sec != section:
                section = sec
                head = QListWidgetItem(sec)
                head.setFlags(Qt.NoItemFlags)
                f = QFont()
                f.setPointSize(8)
                f.setBold(True)
                f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
                head.setFont(f)
                self.nav.addItem(head)
            item = QListWidgetItem(W.glyph_icon(glyph), text)
            item.setData(Qt.UserRole, key)
            self.nav.addItem(item)
            self.nav_rows[key] = self.nav.row(item)
        self.nav.currentItemChanged.connect(self._nav_changed)
        lay.addWidget(self.nav, 1)

        admin = is_admin()
        status = W.label("● Administrator" if admin else "● Limited mode")
        status.setStyleSheet(f"color: {theme.OK if admin else theme.WARN}; font-weight: 600; padding-left: 24px;")
        lay.addWidget(status)
        if not admin and IS_WINDOWS:
            b = W.button("Restart as admin", "", self.relaunch_admin, "Unlock system-wide cleanup, tweaks and repairs")
            wrap = QHBoxLayout()
            wrap.setContentsMargins(20, 4, 20, 0)
            wrap.addWidget(b)
            lay.addLayout(wrap)
        ver = W.label(f"v{APP_VERSION}", "muted")
        ver.setStyleSheet("padding-left: 24px; font-size: 8pt;")
        lay.addWidget(ver)
        return side

    def _nav_changed(self, item, _prev):
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if key and key in self.pages:
            self.stack.setCurrentWidget(self.pages[key])

    # ---------------------------------------------------------------- public API for pages
    def navigate(self, key: str, folder: str = ""):
        if key == "admin":
            self.relaunch_admin()
            return
        if key not in self.nav_rows:
            return
        self.nav.setCurrentRow(self.nav_rows[key])
        page = self.pages.get(key)
        if folder and hasattr(page, "scan_folder"):
            QTimer.singleShot(80, lambda: page.scan_folder(folder))

    def toast(self, text: str):
        self.toast_widget.show_message(text)

    def relaunch_admin(self):
        single = getattr(self, "single", None)
        if single is not None:
            single.close()  # free the single-instance slot so the elevated copy can take over
        if relaunch_as_admin():
            log.info("Relaunching as administrator")
            QApplication.quit()
        else:
            if single is not None:
                single.listen()
            self.toast("Elevation was cancelled.")

    def bring_to_front(self):
        """Called when Remedy is launched again while already running."""
        if not self.isVisible():
            return  # still on the splash; the window will appear on its own
        state = self.windowState() & ~Qt.WindowMinimized
        self.setWindowState(state | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        self.toast("Remedy is already running.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toast_widget") and self.toast_widget.isVisible():
            self.toast_widget.reposition()

    def closeEvent(self, e):
        self._save_geometry()
        for w in list(W._ACTIVE):
            w.cancel()
            w.wait(1500)
        super().closeEvent(e)


def main() -> int:
    setup_logging()
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoisewaterLabs.Remedy")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_PUBLISHER)
    app.setStyle("Fusion")
    theme.apply_palette(app)
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(W.app_icon())

    from .single_instance import SingleInstance
    single = SingleInstance()
    if single.notify_running():
        log.info("Remedy is already running; brought the existing window to the front")
        return 0
    single.listen()

    from .ui.splash import Splash
    splash = Splash()
    splash.show()
    started = time.monotonic()
    splash.status("Starting engine…", 0.05)
    log.info("%s %s starting (admin=%s)", APP_NAME, APP_VERSION, is_admin())

    window = MainWindow(splash)
    window.single = single
    single.activated.connect(window.bring_to_front)
    splash.status("Ready", 1.0)
    remaining = max(0, int((1.4 - (time.monotonic() - started)) * 1000))  # keep the splash visible briefly

    def reveal():
        window.show()
        splash.finish(window)

    QTimer.singleShot(remaining, reveal)
    return app.exec()
