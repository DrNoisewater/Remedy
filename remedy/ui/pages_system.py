"""Dashboard, Maintenance, System Info and Settings pages."""
from __future__ import annotations

import os
import time

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QListWidget, QProgressBar, QScrollArea,
                               QSpinBox, QSplitter, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import theme
from ..core import junk, maintenance, startup, sysinfo
from ..core.utils import (APP_DIR, APP_NAME, APP_PUBLISHER, APP_VERSION, IS_WINDOWS, LOG_FILE, fmt_duration,
                          human_size, is_admin, list_drives, open_location, open_target, run_stream, settings,
                          system_drive)
from .widgets import (BasePage, Card, Console, GradientLabel, ProgressRow, ScoreRing, StatTile, button, confirm, label,
                      make_tree, set_bar_level, size_widths)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())


# =========================================================================== Dashboard
def _health(ctx):
    data = {"admin": is_admin()}
    cats = junk.build_categories()
    total = 0
    for i, c in enumerate(cats):
        if ctx.cancelled():
            break
        if c.admin and not data["admin"]:
            continue
        ctx.progress(f"Measuring {c.name}…", int(i * 80 / max(1, len(cats))), force=True)
        junk.scan_category(c, ctx)
        total += c.size
        c.files = []
    data["junk"] = total
    ctx.progress("Checking startup apps…", 85, force=True)
    try:
        data["startup"] = sum(1 for s in startup.list_startup_items(include_tasks=False) if s.enabled)
    except Exception:  # noqa: BLE001
        data["startup"] = 0
    usage = psutil.disk_usage(system_drive())
    data["free_pct"] = 100 - usage.percent
    data["free"] = usage.free
    data["mem"] = psutil.virtual_memory().percent
    data["uptime_days"] = (time.time() - psutil.boot_time()) / 86400
    try:
        data["hiberfil"] = os.path.getsize(os.path.join(system_drive(), "hiberfil.sys")) if IS_WINDOWS else 0
    except OSError:
        data["hiberfil"] = 0
    return data


def score_health(d) -> tuple:
    score, recs = 100, []
    gb = d["junk"] / 1e9
    if gb >= 0.5:
        score -= min(20, int(gb * 3) + 3)
        recs.append(("warn" if gb < 3 else "danger", f"{human_size(d['junk'])} of junk files can be cleaned",
                     "cleaner", "Clean up"))
    if d["free_pct"] < 10:
        score -= 25
        recs.append(("danger", f"System drive is almost full: only {d['free_pct']:.0f}% "
                               f"({human_size(d['free'])}) free", "large", "Find large files"))
    elif d["free_pct"] < 20:
        score -= 10
        recs.append(("warn", f"System drive is getting full: {d['free_pct']:.0f}% free", "disk", "Analyze"))
    if d["startup"] > 10:
        score -= 15
        recs.append(("danger", f"{d['startup']} apps launch at startup, which slows boot noticeably", "startup", "Review"))
    elif d["startup"] > 5:
        score -= 7
        recs.append(("warn", f"{d['startup']} apps launch at startup", "startup", "Review"))
    if d["mem"] > 85:
        score -= 10
        recs.append(("warn", f"Memory usage is high ({d['mem']:.0f}%)", "processes", "See what's using it"))
    if d["uptime_days"] > 7:
        score -= 5
        recs.append(("info", f"No restart in {d['uptime_days']:.0f} days. A reboot clears leaks and finishes updates",
                     "", ""))
    if d["hiberfil"] > 4e9 and d["free_pct"] < 25:
        recs.append(("info", f"hiberfil.sys uses {human_size(d['hiberfil'])}. Turn off hibernation to reclaim it",
                     "tweaks", "Tweaks"))
    if not d["admin"]:
        recs.append(("info", "Running without admin rights, so some cleanup and tweaks are unavailable", "admin",
                     "Restart as admin"))
    if not any(r[0] in ("warn", "danger") for r in recs):
        recs.insert(0, ("ok", "Everything looks great. Nothing urgent to fix.", "", ""))
    return max(0, min(100, score)), recs


class DashboardPage(BasePage):
    title = "Dashboard"
    subtitle = "Live vitals and a one-click health check."

    def __init__(self, main):
        super().__init__(main)
        self.btn_check = self.add_action("Run health check", "primary", self.run_check)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.cpu = StatTile("CPU", spark_color=theme.ACCENT)
        self.mem = StatTile("Memory", spark_color=theme.ACCENT2)
        self.disk = StatTile("System drive", bar=True)
        self.up = StatTile("Uptime")
        for i, t in enumerate((self.cpu, self.mem, self.disk, self.up)):
            grid.addWidget(t, 0, i)
        self.root.addLayout(grid)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(14)
        health = Card("System health")
        hl = QHBoxLayout()
        hl.setSpacing(24)
        hl.setContentsMargins(4, 4, 4, 4)
        self.ring = ScoreRing()
        hl.addWidget(self.ring, 0, Qt.AlignVCenter)
        self.recs = QVBoxLayout()
        self.recs.setSpacing(8)
        self.recs.addWidget(label("Run a health check to get a score and a personalised fix list.", "muted", wrap=True))
        self.recs.addStretch(1)
        hl.addLayout(self.recs, 1)
        health.body.addLayout(hl)
        row.addWidget(health, 3)
        drives = Card("Drives")
        self.drives = QVBoxLayout()
        self.drives.setSpacing(10)
        drives.body.addLayout(self.drives)
        drives.body.addStretch(1)
        row.addWidget(drives, 2)
        self.root.addLayout(row, 1)

        quick = QHBoxLayout()
        for text, key in (("Clean junk", "cleaner"), ("Find large files", "large"), ("Startup apps", "startup"),
                          ("Tweaks", "tweaks"), ("New PC setup", "setup"), ("Remove bloat", "bloat")):
            quick.addWidget(button(text, "", lambda _=False, k=key: self.main.navigate(k)))
        quick.addStretch(1)
        self.root.addLayout(quick)

        psutil.cpu_percent(None)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.tick)
        self.drive_timer = QTimer(self)
        self.drive_timer.setInterval(30000)
        self.drive_timer.timeout.connect(self.refresh_drives)

    def showEvent(self, e):
        super().showEvent(e)
        self.tick()
        self.refresh_drives()
        self.timer.start()
        self.drive_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()
        self.drive_timer.stop()

    def tick(self):
        cpu = psutil.cpu_percent(None)
        freq = psutil.cpu_freq()
        self.cpu.set(f"{cpu:.0f}%", f"{psutil.cpu_count()} threads" + (f" · {freq.current / 1000:.2f} GHz" if freq else ""))
        self.cpu.spark.push(cpu)
        vm = psutil.virtual_memory()
        self.mem.set(f"{vm.percent:.0f}%", f"{human_size(vm.used)} of {human_size(vm.total)}")
        self.mem.spark.push(vm.percent)
        self.up.set(fmt_duration(time.time() - psutil.boot_time()), f"{len(psutil.pids())} processes running")

    def refresh_drives(self):
        _clear(self.drives)
        sd = os.path.normcase(system_drive())
        for d in list_drives():
            top = QHBoxLayout()
            top.addWidget(label(f"{d['mount']}  ·  {d['fstype']}"))
            top.addStretch(1)
            top.addWidget(label(f"{human_size(d['free'])} free of {human_size(d['total'])}", "muted"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            set_bar_level(bar, d["percent"])
            self.drives.addLayout(top)
            self.drives.addWidget(bar)
            if os.path.normcase(d["mount"]) == sd:
                self.disk.set(f"{d['percent']:.0f}%", f"{human_size(d['free'])} free on {d['mount']}")
                set_bar_level(self.disk.bar, d["percent"])

    def set_busy_ui(self, busy):
        self.btn_check.setEnabled(not busy)

    def run_check(self):
        self.run(_health, progress=self.progress, text="Running health check…", on_done=self._show)

    def _show(self, data):
        score, recs = score_health(data)
        self.ring.set_score(score)
        _clear(self.recs)
        colors = {"ok": theme.OK, "warn": theme.WARN, "danger": theme.DANGER, "info": theme.INFO}
        for level, text, target, action in recs:
            row = QHBoxLayout()
            dot = label("●")
            dot.setStyleSheet(f"color: {colors[level]}; font-size: 12pt;")
            row.addWidget(dot)
            row.addWidget(label(text, wrap=True), 1)
            if target == "admin":
                row.addWidget(button(action, "", self.main.relaunch_admin))
            elif target:
                row.addWidget(button(action, "", lambda _=False, t=target: self.main.navigate(t)))
            self.recs.addLayout(row)
        self.recs.addStretch(1)
        self.main.toast(f"Health score: {score}/100")


# =========================================================================== Maintenance
class MaintenancePage(BasePage):
    title = "Maintenance & Repair"
    subtitle = "Repair Windows, optimize drives, update every app and diagnose hardware, one click each."

    def __init__(self, main):
        super().__init__(main)
        self.actions_list = maintenance.build_actions()
        self.buttons = []
        split = QSplitter(Qt.Vertical)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 6, 0)
        admin = is_admin()
        for i, a in enumerate(self.actions_list):
            card = Card(a.group)
            heading = label(a.title)
            heading.setStyleSheet("font-weight: 600; font-size: 11pt;")
            card.body.addWidget(heading)
            card.body.addWidget(label(a.description, "muted", wrap=True))
            card.body.addStretch(1)
            b = button("Open" if a.launch else "Run", "primary" if not a.launch else "", lambda _=False, x=a: self.start(x))
            if a.admin and not admin:
                b.setEnabled(False)
                b.setToolTip("Needs administrator. Use Restart as admin.")
                b.setText("Needs admin")
            if not IS_WINDOWS:
                b.setEnabled(False)
            card.body.addWidget(b, 0, Qt.AlignLeft)
            self.buttons.append((b, a))
            grid.addWidget(card, i // 3, i % 3)
        scroll.setWidget(holder)
        split.addWidget(scroll)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 8, 0, 0)
        self.progress = ProgressRow()
        bl.addWidget(self.progress)
        self.console = Console()
        self.console.add("Output from maintenance tasks appears here.")
        bl.addWidget(self.console)
        split.addWidget(bottom)
        split.setSizes([480, 220])
        self.root.addWidget(split, 1)

    def set_busy_ui(self, busy):
        for b, a in self.buttons:
            if not a.launch and b.text() != "Needs admin":
                b.setEnabled(not busy)

    def start(self, a):
        if a.launch:
            if " " in a.launch and not a.launch.startswith("ms-"):
                import subprocess
                subprocess.Popen(a.launch, shell=True)
            else:
                open_target(a.launch)
            self.console.add(f"Opened {a.title}.")
            return
        if a.key in ("netreset", "winget_all") and not confirm(self, a.title, f"{a.description}\n\nContinue?", "Run"):
            return
        self.console.add(f"\n▶ {a.title}")

        def work(ctx):
            return run_stream(a.cmd, lambda line: ctx.progress(line, -1, force=True), ctx.cancelled)

        def done(code):
            self.console.add(f"✔ Finished (exit code {code})" if code == 0 else f"✖ Finished with exit code {code}")
            if a.note and os.path.exists(a.note):
                open_target(a.note)
            self.main.toast(f"{a.title}: {'done' if code == 0 else 'finished with warnings'}")

        def on_line(text, _pct):
            self.console.add(text)
            self.progress.update_progress(text[:120], -1)

        self.run(work, progress=self.progress, text=f"{a.title}…", on_done=done, on_progress=on_line)


# =========================================================================== System info
class SysInfoPage(BasePage):
    title = "System Info"
    subtitle = "Your hardware and Windows details at a glance. Handy for support and upgrades."

    def __init__(self, main):
        super().__init__(main)
        self.text = ""
        self.add_action("Copy report", "", self._copy)
        self.add_action("Refresh", "", self.refresh)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Item", "Details"], multi=False, sortable=False, stretch_col=1)
        self.tree.setRootIsDecorated(True)
        size_widths(self.tree, {0: 260})
        self.root.addWidget(self.tree, 1)

    def on_first_show(self):
        self.refresh()

    def refresh(self):
        self.run(lambda ctx: sysinfo.get_system_info(), progress=self.progress, text="Collecting hardware details…",
                 cancellable=False, on_done=self._populate)

    def _populate(self, sections):
        self.text = sysinfo.as_text(sections)
        self.tree.clear()
        for title, rows in sections:
            g = QTreeWidgetItem([title, ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            for k, v in rows:
                c = QTreeWidgetItem([k, str(v)])
                c.setToolTip(1, str(v))
                g.addChild(c)
            self.tree.addTopLevelItem(g)
            g.setExpanded(True)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.text)
        self.main.toast("System report copied to clipboard")


# =========================================================================== Settings & log
class SettingsPage(BasePage):
    title = "Settings & Activity"
    subtitle = f"{APP_NAME} {APP_VERSION} by {APP_PUBLISHER}"

    def __init__(self, main):
        super().__init__(main)
        row = QHBoxLayout()
        row.setSpacing(14)

        prefs = Card("Safety")
        for key, text in (("use_recycle_bin", "Send deleted files to the Recycle Bin (recommended)"),
                          ("restore_point_before_tweaks", "Create a restore point before applying tweaks"),
                          ("skip_system_folders", "Skip the Windows folder when searching for large files")):
            cb = QCheckBox(text)
            cb.setChecked(bool(settings.get(key)))
            cb.toggled.connect(lambda v, k=key: settings.set(k, v))
            prefs.body.addWidget(cb)
        lim = QHBoxLayout()
        lim.addWidget(label("Max results in Large Files", "muted"))
        spin = QSpinBox()
        spin.setRange(100, 100000)
        spin.setValue(int(settings.get("max_large_results")))
        spin.valueChanged.connect(lambda v: settings.set("max_large_results", v))
        lim.addWidget(spin)
        lim.addStretch(1)
        prefs.body.addLayout(lim)
        status = Card("Privileges")
        admin = is_admin()
        st = label("● Administrator: full access" if admin else "● Standard user: some features limited")
        st.setStyleSheet(f"color: {theme.OK if admin else theme.WARN}; font-weight: 600;")
        status.body.addWidget(st)
        if not admin and IS_WINDOWS:
            status.body.addWidget(button("Restart as administrator", "primary", self.main.relaunch_admin), 0, Qt.AlignLeft)
        status.body.addWidget(button("Open data folder", "", lambda: open_location(str(APP_DIR))), 0, Qt.AlignLeft)
        prefs.body.addStretch(1)
        row.addWidget(prefs, 3)

        excl = Card("Excluded folders (never scanned)")
        self.excl = QListWidget()
        self.excl.addItems(settings.get("exclusions") or [])
        excl.body.addWidget(self.excl)
        eb = QHBoxLayout()
        eb.addWidget(button("Add folder…", "", self._add_excl))
        eb.addWidget(button("Remove", "", self._rm_excl))
        eb.addStretch(1)
        excl.body.addLayout(eb)
        about = Card("About")
        ab = QHBoxLayout()
        ab.setSpacing(16)
        from .splash import TileWidget
        ab.addWidget(TileWidget(128, "NOISEWATER LABS"), 0, Qt.AlignTop)
        at = QVBoxLayout()
        at.setSpacing(3)
        at.addWidget(GradientLabel(f"{APP_NAME} {APP_VERSION}", 22, QFont.Bold))
        at.addSpacing(4)
        at.addWidget(label("PC optimization suite"))
        at.addWidget(label("For Windows 10 and 11", "muted"))
        at.addSpacing(6)
        at.addWidget(label(f"Made by {APP_PUBLISHER}", "muted"))
        at.addStretch(1)
        ab.addLayout(at, 1)
        about.body.addLayout(ab)
        side = QVBoxLayout()
        side.addWidget(about)
        side.addWidget(status)
        side.addWidget(excl, 1)
        row.addLayout(side, 2)
        self.root.addLayout(row)

        logc = Card("Activity log")
        self.console = Console()
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[-200:]:
                    self.console.add(line.rstrip())
        except OSError:
            pass
        logc.body.addWidget(self.console)
        logc.body.addWidget(button("Open full log", "", lambda: open_target(str(LOG_FILE))), 0, Qt.AlignLeft)
        self.root.addWidget(logc, 1)
        main.log_bus.message.connect(self.console.add)

    def _add_excl(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Exclude a folder from scans")
        if path:
            items = list(settings.get("exclusions") or [])
            path = os.path.normpath(path)
            if path not in items:
                items.append(path)
                settings.set("exclusions", items)
                self.excl.addItem(path)

    def _rm_excl(self):
        row = self.excl.currentRow()
        if row >= 0:
            items = list(settings.get("exclusions") or [])
            text = self.excl.takeItem(row).text()
            if text in items:
                items.remove(text)
            settings.set("exclusions", items)
