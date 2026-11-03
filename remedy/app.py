"""Remedy main window: grouped sidebar, top bar (Back/Forward, breadcrumbs, search, Task Center), page stack,
toasts and the startup sequence. All page changes go through navigate(), which keeps one app-wide history."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QStackedWidget, QVBoxLayout, QWidget)

from . import theme
from .core.utils import (APP_NAME, APP_PUBLISHER, APP_VERSION, IS_WINDOWS, is_admin, log, relaunch_as_admin,
                         settings, setup_logging)
from .ui import widgets as W
from .ui.navigation import MouseNavFilter, NavHistory, Route
from .ui.notify import ToastManager
from .ui.taskcenter import TaskCenter, TaskCenterPanel

# (key, label, MDL2 glyph, section). Section "" = top-level (no heading). Keys are stable: other code and saved
# settings refer to them, so never rename one.
NAV = [
    ("dashboard", "Dashboard", "", ""),
    ("diagnose", "Diagnose & Repair", "", ""),
    ("newpc", "New PC Setup", "", ""),
    ("startup", "Startup Apps", "", "PERFORMANCE"),
    ("processes", "Processes", "", "PERFORMANCE"),
    ("tweaks", "Optimize & Tweaks", "", "PERFORMANCE"),
    ("services", "Services", "", "PERFORMANCE"),
    ("security", "Security Center", "", "SECURITY"),
    ("cleaner", "Junk Cleaner", "", "STORAGE & CLEANUP"),
    ("large", "Large Files", "", "STORAGE & CLEANUP"),
    ("dupes", "Duplicates", "", "STORAGE & CLEANUP"),
    ("disk", "Disk Analyzer", "", "STORAGE & CLEANUP"),
    ("setup", "App Installer", "", "SOFTWARE"),
    ("bloat", "Bloatware Remover", "", "SOFTWARE"),
    ("programs", "Installed Programs", "", "SOFTWARE"),
    ("maintenance", "Windows & Repair", "", "SYSTEM"),
    ("sysinfo", "Hardware & System", "", "SYSTEM"),
    ("sessions", "Sessions & Reports", "", "TECHNICIAN"),
    ("settings", "Settings & Log", "", "~"),
]
NAV_LABEL = {k: label for k, label, _g, _s in NAV}
NAV_SECTION = {k: s for k, _l, _g, s in NAV}


def _page_classes() -> dict:
    from .ui import pages_apps, pages_clean, pages_diagnose, pages_newpc, pages_optimize, pages_security, \
        pages_sessions, pages_system
    return {
        "dashboard": pages_system.DashboardPage, "diagnose": pages_diagnose.DiagnosePage,
        "newpc": pages_newpc.NewPCPage, "security": pages_security.SecurityPage,
        "cleaner": pages_clean.CleanerPage, "large": pages_clean.LargeFilesPage, "dupes": pages_clean.DuplicatesPage,
        "disk": pages_clean.DiskPage, "startup": pages_optimize.StartupPage, "tweaks": pages_optimize.TweaksPage,
        "services": pages_optimize.ServicesPage, "processes": pages_optimize.ProcessesPage,
        "setup": pages_apps.SetupPage, "bloat": pages_apps.BloatwarePage, "programs": pages_apps.ProgramsPage,
        "maintenance": pages_system.MaintenancePage, "sysinfo": pages_system.SysInfoPage,
        "sessions": pages_sessions.SessionsPage, "settings": pages_system.SettingsPage,
    }


class MainWindow(QMainWindow):
    def __init__(self, splash=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(W.app_icon())
        self.setMinimumSize(980, 640)
        self._restore_geometry()
        self.log_bus = W.LogBus()
        handler = W.QtLogHandler(self.log_bus)
        log.addHandler(handler)
        self.tasks = TaskCenter()
        self.history = NavHistory()

        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._topbar())
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)
        self.setCentralWidget(central)

        self.pages: dict = {}
        classes = _page_classes()
        for i, (key, text, _glyph, _sec) in enumerate(NAV):
            if splash:
                splash.status(f"Loading {text}…", 0.15 + 0.8 * i / len(NAV))
            page = classes[key](self)
            page.key = key
            self.pages[key] = page
            self.stack.addWidget(page)
        self.toasts = ToastManager(self)
        self.task_panel = TaskCenterPanel(self, self.tasks)
        self.tasks.changed.connect(self._update_tasks_button)
        self._shortcuts()
        self._mouse_filter = MouseNavFilter(self, self.go_back, self.go_forward)
        QApplication.instance().installEventFilter(self._mouse_filter)
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
        w, h = 1480, 940
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
        side.setFixedWidth(240)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 20, 0, 16)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(20, 0, 16, 12)
        logo = QLabel()
        logo.setPixmap(W.app_icon().pixmap(34, 34))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(W.GradientLabel(APP_NAME, 17, weight=QFont.DemiBold))
        names.addWidget(W.label(f"by {APP_PUBLISHER}", "brandsub"))
        brand.addLayout(names, 1)
        lay.addLayout(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setAccessibleName("Main navigation")
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.setIconSize(QSize(16, 16))
        self.nav_rows: dict = {}
        self.nav_groups: dict = {}       # section -> (header item, [rows])
        collapsed = set(settings.get("nav_collapsed") or [])
        section = ""
        for key, text, glyph, sec in NAV:
            if sec != section:
                section = sec
                if sec == "~":
                    spacer = QListWidgetItem("")
                    spacer.setFlags(Qt.NoItemFlags)
                    spacer.setSizeHint(QSize(10, 10))
                    self.nav.addItem(spacer)
                else:
                    head = QListWidgetItem(("▸  " if sec in collapsed else "▾  ") + sec.capitalize())
                    head.setFlags(Qt.ItemIsEnabled)
                    head.setData(Qt.UserRole + 1, sec)
                    head.setToolTip("Click to fold or unfold this group")
                    f = QFont()
                    f.setPointSize(8)
                    f.setPointSize(9)
                    f.setWeight(QFont.Medium)
                    head.setFont(f)
                    head.setForeground(Qt.gray)
                    self.nav.addItem(head)
                    self.nav_groups[sec] = (head, [])
            item = QListWidgetItem(W.glyph_icon(glyph), text)
            item.setData(Qt.UserRole, key)
            item.setToolTip(text)
            self.nav.addItem(item)
            self.nav_rows[key] = self.nav.row(item)
            if sec in self.nav_groups:
                self.nav_groups[sec][1].append(item)
                item.setHidden(sec in collapsed)
        self.nav.currentItemChanged.connect(self._nav_changed)
        self.nav.itemClicked.connect(self._nav_clicked)
        lay.addWidget(self.nav, 1)

        self.activity = W.ActivityPanel(self.navigate)
        act_wrap = QHBoxLayout()
        act_wrap.setContentsMargins(12, 0, 12, 8)
        act_wrap.addWidget(self.activity)
        lay.addLayout(act_wrap)

        admin = is_admin()
        status = W.label("● Administrator" if admin else "● Limited mode")
        status.setStyleSheet(f"color: {theme.OK if admin else theme.WARN}; font-weight: 600; padding-left: 22px;")
        status.setToolTip("Full access to system cleanup, tweaks and repairs" if admin
                          else "Some cleanup, tweaks and repairs need administrator rights")
        lay.addWidget(status)
        if not admin and IS_WINDOWS:
            b = W.button("Restart as admin", "", self.relaunch_admin, "Unlock system-wide cleanup, tweaks and repairs")
            wrap = QHBoxLayout()
            wrap.setContentsMargins(18, 4, 18, 0)
            wrap.addWidget(b)
            lay.addLayout(wrap)
        from .core.osinfo import detect
        ver = W.label(f"v{APP_VERSION}  ·  {detect().short()}", "faint")
        ver.setStyleSheet("padding-left: 22px;")
        lay.addWidget(ver)
        return side

    def _toggle_group(self, sec: str, expand=None):
        head, items = self.nav_groups[sec]
        collapsed = set(settings.get("nav_collapsed") or [])
        fold = (sec not in collapsed) if expand is None else not expand
        if fold:
            current = self.nav.currentItem()
            if current in items:
                return            # never hide the page you're on
            collapsed.add(sec)
        else:
            collapsed.discard(sec)
        for it in items:
            it.setHidden(fold)
        head.setText(("▸  " if fold else "▾  ") + sec.capitalize())
        settings.set("nav_collapsed", sorted(collapsed))

    def _nav_clicked(self, item):
        sec = item.data(Qt.UserRole + 1)
        if sec:
            self._toggle_group(sec)

    def _nav_changed(self, item, _prev):
        if item is None:
            return
        key = item.data(Qt.UserRole)
        if key and key in self.pages and (self.history.current is None or self.history.current.key != key):
            self.navigate(key)

    # ---------------------------------------------------------------- top bar
    def _topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(6)
        self.btn_back = W.button("←", "nav", self.go_back, "Back (Alt+Left or the mouse Back button)")
        self.btn_back.setAccessibleName("Back")
        self.btn_fwd = W.button("→", "nav", self.go_forward, "Forward (Alt+Right or the mouse Forward button)")
        self.btn_fwd.setAccessibleName("Forward")
        lay.addWidget(self.btn_back)
        lay.addWidget(self.btn_fwd)
        lay.addSpacing(8)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        lay.addLayout(self.crumbs)
        lay.addStretch(1)
        self.btn_search = W.button("Search                         Ctrl+K", "search", self.open_palette,
                                   "Jump to any page, tool, tweak, app or setting")
        self.btn_search.setMinimumWidth(250)
        self.btn_search.setAccessibleName("Search")
        lay.addWidget(self.btn_search)
        self.btn_tasks = W.button("Tasks", "", self.open_tasks, "Task Center: everything running or finished")
        self.btn_tasks.setAccessibleName("Task Center")
        lay.addWidget(self.btn_tasks)
        return bar

    def _update_tasks_button(self):
        n = len(self.tasks.running())
        failed = sum(1 for r in self.tasks.records.values() if r.status == "failed")
        text = f"Tasks · {n} running" if n else "Tasks"
        if failed and not n:
            text = f"Tasks · {failed} failed"
        self.btn_tasks.setText(text)
        self.btn_tasks.setProperty("kind", "primary" if n else "")
        self.btn_tasks.style().unpolish(self.btn_tasks)
        self.btn_tasks.style().polish(self.btn_tasks)

    def _update_chrome(self):
        self.btn_back.setEnabled(self.history.can_back())
        self.btn_fwd.setEnabled(self.history.can_forward())
        while self.crumbs.count():
            it = self.crumbs.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        route = self.history.current
        if route is None:
            return
        parts = []           # (text, target route or None)
        if route.key != "dashboard":
            parts.append(("Remedy", Route.make("dashboard")))
        sec = NAV_SECTION.get(route.key, "")
        if sec and sec != "~":
            parts.append((sec.title().replace("&", "&&"), None))
        page = self.pages[route.key]
        sub = page.route_label(route.args) if hasattr(page, "route_label") else ""
        parts.append((NAV_LABEL.get(route.key, route.key).replace("&", "&&"), Route.make(route.key) if sub else None))
        if sub:
            parts.append((sub.replace("&", "&&"), None))
        for i, (text, target) in enumerate(parts):
            if i:
                sep = W.label("/", "faint")
                self.crumbs.addWidget(sep)
            b = W.button(text, "crumb")
            if target is None:
                b.setEnabled(False)       # current location / group label
            else:
                b.clicked.connect(lambda _=False, r=target: self.navigate(r.key, r.args))
                b.setToolTip(f"Go to {text.replace('&&', '&')}")
            self.crumbs.addWidget(b)

    # ---------------------------------------------------------------- navigation
    def navigate(self, key: str, params: dict | None = None, folder: str = "", replace: bool = False,
                 _record: bool = True):
        """Show a page. params restore a sub-view (e.g. {"finding": id}). History records each distinct view;
        replace=True swaps the current entry (used for picking another item inside the same sub-view)."""
        if key == "admin":
            self.relaunch_admin()
            return
        page = self.pages.get(key)
        if page is None:
            return
        route = Route.make(key, params)
        if _record:
            if replace and self.history.current is not None and self.history.current.key == key:
                self.history.replace(route)
            else:
                self.history.push(route)
        self._show(route)
        if folder and hasattr(page, "scan_folder"):
            QTimer.singleShot(80, lambda: page.scan_folder(folder))

    def _show(self, route: Route):
        page = self.pages[route.key]
        sec = NAV_SECTION.get(route.key, "")
        if sec in self.nav_groups and self.nav_groups[sec][1][0].isHidden():
            self._toggle_group(sec, expand=True)
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(self.nav_rows[route.key])
        self.nav.blockSignals(False)
        if self.stack.currentWidget() is not page:
            self.stack.setCurrentWidget(page)
        try:
            page.open_route(route.args)
        except Exception as exc:  # noqa: BLE001 - a bad sub-route must never break navigation
            log.warning("Could not open %s with %s: %s", route.key, route.args, exc)
        self._update_chrome()

    def go_back(self):
        route = self.history.back()
        if route is not None:
            self._show(route)

    def go_forward(self):
        route = self.history.forward()
        if route is not None:
            self._show(route)

    # ---------------------------------------------------------------- public API for pages
    def notify(self, kind: str, title: str, body: str = "", action=None, ms: int = 0):
        """Show a toast. kind: success | info | warning | error. action: (button text, callback)."""
        return self.toasts.show(kind, title, body, action, ms)

    def toast(self, text: str, kind: str = "info"):
        self.notify(kind, text)

    def current_page(self):
        return self.stack.currentWidget()

    def open_tasks(self):
        self.task_panel.open_below(self.btn_tasks)

    def palette_entries(self) -> list:
        from .core import appstore, maintenance, tweaks
        from .ui.palette import Entry
        out = []
        for key, label, _g, sec in NAV:
            page = self.pages[key]
            out.append(Entry(label, "Page", page.subtitle, lambda k=key: self.navigate(k),
                             f"{page.title} {sec.lower() if sec != '~' else ''}"))
        diag = self.pages["diagnose"]
        out += [
            Entry("Start a Quick Scan", "Action", "Read-only: measures this PC and lists problems. Changes nothing.",
                  lambda: (self.navigate("diagnose"), diag.start("quick")), "diagnose slow check health"),
            Entry("Start a Full Diagnosis", "Action", "Read-only, about a minute of sampling. Changes nothing.",
                  lambda: (self.navigate("diagnose"), diag.start("full")), "diagnose slow check deep"),
            Entry("My Standard Setup", "Action", "Inspect this PC with your last-used setup profile, then review the "
                  "plan. Nothing runs until you confirm.", lambda: (self.navigate("newpc"),
                                                                    self.pages["newpc"].my_standard_setup()),
                  "new pc profile provision"),
            Entry("Run health check", "Action", "Read-only score out of 100 with a fix list.",
                  lambda: (self.navigate("dashboard"), self.pages["dashboard"].run_check()), "score"),
            Entry("Open Task Center", "Tool", "Everything running or finished this session", self.open_tasks, "jobs"),
            Entry("Privacy mode for reports", "Setting", "Redact computer and user names in exported reports",
                  lambda: self.navigate("settings"), "redact client"),
            Entry("Recycle Bin for deleted files", "Setting", "Send files deleted by Remedy to the Recycle Bin",
                  lambda: self.navigate("settings"), "safety delete"),
            Entry("Restore point before tweaks", "Setting", "Create a restore point before changing settings",
                  lambda: self.navigate("settings"), "safety"),
        ]
        for a in maintenance.build_actions():
            out.append(Entry(a.title, "Tool", a.description,
                             lambda t=a.title: self.navigate("maintenance", {"search": t}), "maintenance repair"))
        for t in tweaks.build_tweaks():
            out.append(Entry(t.name, "Tweak", t.description, lambda i=t.id: self.navigate("tweaks", {"tweak": i}),
                             t.category))
        for app in appstore.CATALOG:
            out.append(Entry(app.name, "App", app.description,
                             lambda n=app.name: self.navigate("setup", {"search": n}), "install " + app.category))
        return out

    def open_palette(self):
        from .ui.palette import CommandPalette
        CommandPalette(self, self.palette_entries()).exec()

    def _shortcuts(self):
        """F5 refresh/scan, Ctrl+F search on page, Ctrl+K command search, Alt+Left/Right back/forward,
        Esc (asks before stopping a running task), Ctrl+1…9 jump between the first pages."""
        def refresh():
            page = self.current_page()
            fn = getattr(page, "refresh_action", None)
            if fn:
                fn()

        def search():
            box = self.current_page().findChild(QLineEdit)
            if box is not None and box.isVisible():
                box.setFocus()
                box.selectAll()

        def escape():
            page = self.current_page()
            worker = getattr(page, "worker", None)
            if worker is not None and worker.isRunning():
                from .ui.dialogs import confirm
                if confirm(self, f"Stop “{page.title}” task?",
                           "It stops at the next safe point. Finished work is kept and nothing is undone.",
                           "Stop task"):
                    page.cancel_task()

        QShortcut(QKeySequence("F5"), self, refresh)
        QShortcut(QKeySequence.Find, self, search)
        QShortcut(QKeySequence("Ctrl+K"), self, self.open_palette)
        QShortcut(QKeySequence("Alt+Left"), self, self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, self.go_forward)
        QShortcut(QKeySequence("Esc"), self, escape)
        for i, (key, *_rest) in enumerate(NAV[:9]):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, lambda k=key: self.navigate(k))

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
            self.notify("warning", "Still in limited mode", "The administrator prompt was cancelled.")

    def bring_to_front(self):
        """Called when Remedy is launched again while already running."""
        if not self.isVisible():
            return  # still on the splash; the window will appear on its own
        state = self.windowState() & ~Qt.WindowMinimized
        self.setWindowState(state | Qt.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()
        self.notify("info", "Remedy is already open", "Here it is.")

    def after_show(self):
        """Things to check once the window is visible: an unfinished New PC Setup to resume."""
        try:
            self.pages["newpc"].offer_resume()
        except Exception as exc:  # noqa: BLE001
            log.warning("Resume check failed: %s", exc)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toasts"):
            self.toasts.reposition()

    def closeEvent(self, e):
        running = self.tasks.running()
        if running and self.isVisible():
            from .ui.dialogs import confirm
            if not confirm(self, "Tasks are still running", "Closing Remedy stops: " +
                           ", ".join(r.title for r in running) + ". New PC Setup can be resumed next time.",
                           "Close anyway", danger=True):
                e.ignore()
                return
        self._save_geometry()
        QApplication.instance().removeEventFilter(self._mouse_filter)
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

    try:
        from .core import sessions
        sessions.prune()
    except Exception as exc:  # noqa: BLE001
        log.warning("Session cleanup failed: %s", exc)

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
        QTimer.singleShot(900, window.after_show)

    QTimer.singleShot(remaining, reveal)
    return app.exec()
