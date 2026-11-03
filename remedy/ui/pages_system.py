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
from ..core import junk, maintenance, optimize, osinfo, programs, startup, sysinfo
from ..core.utils import (APP_DIR, APP_NAME, APP_PUBLISHER, APP_VERSION, IS_WINDOWS, LOG_FILE, fmt_duration,
                          human_size, is_admin, list_drives, open_location, open_target, run_stream, settings,
                          system_drive)
from .widgets import (BasePage, Card, Console, GradientLabel, ProgressRow, ResponsiveGrid, ScoreRing, StatTile, button,
                      confirm, label,
                      make_tree, pill, set_bar_level, size_widths)


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
    info = osinfo.detect()
    data["laptop"] = info.laptop
    data["os"] = info.short()
    if IS_WINDOWS:
        ctx.progress("Checking recommended tweaks…", 90, force=True)
        try:
            data["tweaks_pending"] = len(optimize.pending_tweaks(info, data["admin"]))
        except Exception:  # noqa: BLE001
            data["tweaks_pending"] = 0
        ctx.progress("Looking for bloatware…", 95, force=True)
        try:
            data["bloat"] = sum(1 for a in programs.list_appx() if a["recommended"])
        except Exception:  # noqa: BLE001
            data["bloat"] = 0
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
    pending = d.get("tweaks_pending", 0)
    if pending >= 5:
        score -= 6
        recs.append(("warn", f"{pending} recommended tweaks for {d.get('os', 'Windows')} aren't on yet",
                     "tweaks", "Review tweaks"))
    elif pending:
        recs.append(("info", f"{pending} recommended tweak{'s' if pending != 1 else ''} not on yet",
                     "tweaks", "Review tweaks"))
    bloat = d.get("bloat", 0)
    if bloat >= 3:
        score -= 6
        recs.append(("warn", f"{bloat} preinstalled bloatware apps are taking up space", "bloat", "Remove bloat"))
    elif bloat:
        recs.append(("info", f"{bloat} bloatware app{'s' if bloat != 1 else ''} installed", "bloat", "Remove bloat"))
    if d.get("laptop"):
        recs.append(("info", "This is a laptop: check how much your battery has worn", "maintenance",
                     "Battery report"))
    if d["hiberfil"] > 4e9 and d["free_pct"] < 25 and not d.get("laptop"):
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
        self.btn_check = self.add_action("Run health check", "", self.run_check, "Score this PC and list what to fix")
        self.add_action("One-click optimize", "primary", self.optimize,
                        "Clean junk, apply recommended tweaks and more, in one go")
        info = osinfo.detect()
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addWidget(pill(info.short() if info.is_windows else f"{info.family} (preview)", theme.ACCENT))
        if info.is_windows:
            if info.edition_name:
                chips.addWidget(pill(info.edition_name, theme.INFO))
            chips.addWidget(pill(f"Build {info.build}", theme.MUTED))
        chips.addWidget(pill(info.form, theme.ACCENT2))
        admin = is_admin()
        chips.addWidget(pill("Administrator" if admin else "Limited mode", theme.OK if admin else theme.WARN))
        chips.addStretch(1)
        self.root.addLayout(chips)
        self.root.addWidget(self._hero())
        self.cpu = StatTile("CPU", spark_color=theme.ACCENT)
        self.mem = StatTile("Memory", spark_color=theme.MUTED)
        self.disk = StatTile("System drive", bar=True)
        self.up = StatTile("Uptime")
        self.root.addWidget(ResponsiveGrid((self.cpu, self.mem, self.disk, self.up), 170))
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        health = Card("System health")
        hl = QHBoxLayout()
        hl.setSpacing(24)
        hl.setContentsMargins(4, 4, 4, 4)
        self.ring = ScoreRing()
        hl.addWidget(self.ring, 0, Qt.AlignTop)
        self.recs = QVBoxLayout()
        self.recs.setSpacing(8)
        self._intro()
        hl.addLayout(self.recs, 1)
        health.body.addLayout(hl)
        drives = Card("Drives")
        self.drives = QVBoxLayout()
        self.drives.setSpacing(10)
        drives.body.addLayout(self.drives)
        drives.body.addStretch(1)
        self.root.addWidget(ResponsiveGrid((health, drives), 360), 1)

        quick = [button(text, "", lambda _=False, k=key: self.main.navigate(k))
                 for text, key in (("Clean junk", "cleaner"), ("Find large files", "large"), ("Startup apps", "startup"),
                                   ("Tweaks", "tweaks"), ("Remove bloat", "bloat"), ("Repair Windows", "maintenance"))]
        self.root.addWidget(ResponsiveGrid(quick, 128, spacing=8))
        for card in (health, drives):
            card.setMinimumHeight(250)
        self.make_scrollable()

        psutil.cpu_percent(None)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.tick)
        self.drive_timer = QTimer(self)
        self.drive_timer.setInterval(30000)
        self.drive_timer.timeout.connect(self.refresh_drives)

    # ---------------------------------------------------------------- start-here row
    def _hero(self):
        diag = Card("Last diagnosis")
        self.h_diag_title = label("Not diagnosed yet", "h2")
        self.h_diag_body = label("Run a Quick Scan to find out what's wrong, why, and what to do next. Scanning "
                                 "changes nothing.", "muted", wrap=True)
        diag.body.addWidget(self.h_diag_title)
        diag.body.addWidget(self.h_diag_body, 1)
        b = QHBoxLayout()
        self.h_view = button("View findings", "", lambda: self.main.navigate("diagnose"))
        b.addWidget(self.h_view)
        b.addWidget(button("Quick Scan", "", lambda: (self.main.navigate("diagnose"),
                                                      self.main.pages["diagnose"].start("quick"))))
        b.addStretch(1)
        diag.body.addLayout(b)
        sec = Card("Security")
        self.h_sec_title = label("Not assessed", "h2")
        self.h_sec_body = label("Checked as part of a diagnosis.", "muted", wrap=True)
        sec.body.addWidget(self.h_sec_title)
        sec.body.addWidget(self.h_sec_body, 1)
        sec.body.addWidget(button("Security Center", "", lambda: self.main.navigate("security")), 0, Qt.AlignLeft)
        hero = QFrame()
        hero.setObjectName("hero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(20, 16, 20, 16)
        hl.setSpacing(8)
        hl.addWidget(label("Technician workflows", "kicker"))
        hl.addWidget(label("Fix a problem PC, or prepare a new one with a reusable profile.", "muted", wrap=True))
        hl.addStretch(1)
        hl.addWidget(button("Diagnose && Repair", "", lambda: self.main.navigate("diagnose")))
        hl.addWidget(button("Set Up a New PC", "", lambda: self.main.navigate("newpc")))
        for card in (diag, sec, hero):
            card.setMinimumHeight(192)      # wrapped text doesn't report its height to grid layouts
        return ResponsiveGrid((diag, sec, hero), 260)

    def refresh_summary(self):
        from ..core import diagnostics as D
        from ..core import sessions as S
        page = self.main.pages.get("diagnose") if hasattr(self.main, "pages") else None
        s = getattr(page, "session", None) or S.latest("diagnostic")
        if not s:
            return
        c = D.summary_counts(s)
        serious = c["high"] + c["medium"]
        when = time.strftime("%d %b, %H:%M", time.localtime(s.get("started") or 0))
        self.h_diag_title.setText(f"{serious} item{'s' if serious != 1 else ''} need attention" if serious
                                  else "No serious problems found")
        self.h_diag_title.setStyleSheet(f"color: {theme.DANGER if c['high'] else theme.WARN if serious else theme.OK};")
        tops = [f for f in s.get("findings") or [] if f["id"] not in (s.get("dismissed") or {})][:3]
        lines = [f"{theme.SEVERITY.get(f['severity'], ('', f['severity']))[1]}: {f['title']}" for f in tops]
        na = D.not_assessed(s)
        self.h_diag_body.setText(f"Scanned {when}. " + (" · ".join(lines) if lines else "Nothing flagged.")
                                 + (f" ({len(na)} area(s) not fully assessed.)" if na else ""))
        ev = s.get("evidence") or {}
        prov = (s.get("providers") or {}).get("security", {})
        if prov.get("status") == "complete":
            rt = (ev.get("security.defender_realtime") or {}).get("value")
            others = [n for n in (ev.get("security.av_products") or {}).get("value") or [] if "defender" not in n.lower()]
            sec_f = [f for f in s.get("findings") or [] if f["category"] == "Security" and f["severity"] in ("high", "medium")]
            if rt or others:
                self.h_sec_title.setText("Protected" if not sec_f else "Protected · review")
                self.h_sec_title.setStyleSheet(f"color: {theme.OK if not sec_f else theme.WARN};")
            else:
                self.h_sec_title.setText("Not protected")
                self.h_sec_title.setStyleSheet(f"color: {theme.DANGER};")
            self.h_sec_body.setText(("Microsoft Defender is on" if rt else f"{', '.join(others)} is on" if others else
                                     "No active antivirus found") + (f" · {len(sec_f)} item(s) to review" if sec_f else
                                                                     "") + f" · as of {when}")
        else:
            self.h_sec_title.setText("Not assessed")
            self.h_sec_title.setStyleSheet(f"color: {theme.FAINT};")
            self.h_sec_body.setText(prov.get("message") or "Run a diagnosis to check protection status.")

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_summary()
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

    def _intro(self):
        intro = label("How healthy is this PC?", "")
        intro.setStyleSheet("font-size: 13pt; font-weight: 700;")
        self.recs.addWidget(intro)
        self.recs.addWidget(label("Run a health check for a score out of 100 and a fix list tailored to this PC. "
                                  "Or press One-click optimize to clean up and tune everything safely in one go.",
                                  "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button("Run health check", "", self.run_check))
        row.addWidget(button("One-click optimize", "", self.optimize))
        row.addStretch(1)
        self.recs.addLayout(row)
        self.recs.addStretch(1)

    def refresh_action(self):
        self.run_check()

    def optimize(self, preselect_bloat=False):
        from .optimize_wizard import OptimizeWizard
        OptimizeWizard(self.main, preselect_bloat=preselect_bloat).exec()
        self.tick()
        self.refresh_drives()

    def run_check(self):
        self.run(_health, progress=self.progress, text="Running health check…", on_done=self._show,
                 summary=lambda d: f"Score {score_health(d)[0]} / 100")

    def _show(self, data):
        score, recs = score_health(data)
        self.ring.set_score(score)
        _clear(self.recs)
        colors = {"ok": theme.OK, "warn": theme.WARN, "danger": theme.DANGER, "info": theme.INFO}
        problems = [r for r in recs if r[0] in ("warn", "danger")]
        head = QHBoxLayout()
        title = label(f"{len(problems)} thing{'s' if len(problems) != 1 else ''} worth fixing" if problems
                      else "Looking good")
        title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        head.addWidget(title, 1)
        if problems:
            head.addWidget(button("Fix it all", "primary", lambda: self.optimize(data.get("bloat", 0) >= 3),
                                  "Opens One-click optimize with the right steps ticked"))
        self.recs.addLayout(head)
        for level, text, target, action in recs:
            box = QFrame()
            box.setObjectName("recommend")
            box.setStyleSheet(f"QFrame#recommend {{ border-left: 3px solid {colors[level]}; }}")
            row = QHBoxLayout(box)
            row.setContentsMargins(12, 8, 8, 8)
            row.addWidget(label(text, wrap=True), 1)
            if target == "admin":
                row.addWidget(button(action, "", self.main.relaunch_admin))
            elif target:
                row.addWidget(button(action, "", lambda _=False, t=target: self.main.navigate(t)))
            self.recs.addWidget(box)
        self.recs.addStretch(1)
        kind = "success" if score >= 75 else "warning" if score >= 55 else "error"
        grade = ScoreRing.grade(score)[0].capitalize()
        self.main.notify(kind, f"Health score: {score} / 100 ({grade})",
                         f"{len(problems)} thing{'s' if len(problems) != 1 else ''} worth fixing. "
                         "See the list next to the score." if problems else "Nothing urgent. Nice work.")


# =========================================================================== Maintenance
class MaintenancePage(BasePage):
    title = "Maintenance & Repair"
    subtitle = "Repair Windows, optimize drives, update every app and diagnose hardware, one click each."

    def __init__(self, main):
        super().__init__(main)
        self.actions_list = maintenance.build_actions()
        self.buttons = []
        self.cards = {}
        split = QSplitter(Qt.Vertical)
        scroll = self.scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 6, 0)
        outer.setSpacing(8)
        admin = is_admin()
        group_blurbs = {
            "Safety": "Make a safety net before big changes.",
            "Repair": "Fix corrupted system files, drive errors and a stuck taskbar or Store.",
            "Cleanup": "Free space Windows itself is holding on to.",
            "Security": "Virus scans and protection, using the antivirus built into Windows.",
            "Updates": "Keep Windows and every app up to date.",
            "Diagnostics": "Find out what's wrong: drives, battery, memory, Wi-Fi and crashes.",
            "Network": "Fix connection problems and check your speed.",
        }
        grid = None
        current = None
        i = 0
        for a in self.actions_list:
            if a.group != current:
                current = a.group
                head = QHBoxLayout()
                head.addWidget(label(a.group, "section"))
                head.addWidget(label(group_blurbs.get(a.group, ""), "muted"), 1)
                outer.addLayout(head)
                grid = QGridLayout()
                grid.setSpacing(12)
                for col in range(3):
                    grid.setColumnStretch(col, 1)
                outer.addLayout(grid)
                i = 0
            card = Card()
            heading = label(a.title)
            heading.setStyleSheet("font-weight: 600; font-size: 11pt;")
            card.body.addWidget(heading)
            card.body.addWidget(label(a.description, "muted", wrap=True))
            card.body.addStretch(1)
            b = button("Open" if a.launch else "Run", "primary" if not a.launch else "", lambda _=False, x=a: self.start(x))
            usable, why = osinfo.applies(a.target)
            if not usable:
                b.setEnabled(False)
                b.setText(why)
            elif a.admin and not admin:
                b.setEnabled(False)
                b.setToolTip("Needs administrator. Use Restart as admin in the sidebar.")
                b.setText("Needs admin")
            card.body.addWidget(b, 0, Qt.AlignLeft)
            self.buttons.append((b, a))
            self.cards[a.title.lower()] = card
            grid.addWidget(card, i // 3, i % 3)
            i += 1
        outer.addStretch(1)
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

    def open_route(self, params: dict) -> None:
        """{"search": text}: scroll to the first action whose title contains text and highlight it briefly."""
        q = str(params.get("search") or "").lower()
        if not q:
            return
        for title, card in self.cards.items():
            if q in title:
                QTimer.singleShot(60, lambda c=card: self.scroll.ensureWidgetVisible(c, 0, 40))
                card.setProperty("tone", "accent")
                card.style().unpolish(card)
                card.style().polish(card)

                def unflash(c=card):
                    c.setProperty("tone", "")
                    c.style().unpolish(c)
                    c.style().polish(c)
                QTimer.singleShot(2500, unflash)
                break

    def set_busy_ui(self, busy):
        for b, a in self.buttons:
            if not a.launch and b.text() in ("Run", "Running…"):
                b.setEnabled(not busy)

    def start(self, a):
        if a.launch:
            if " " in a.launch and not a.launch.startswith("ms-"):
                import subprocess
                subprocess.Popen(a.launch, shell=True)
            else:
                open_target(a.launch)
            self.console.add(f"Opened {a.title}.")
            self.main.notify("info", f"Opened {a.title}")
            return
        if a.confirm and not confirm(self, f"{a.title}?", a.confirm, "Run"):
            return
        self.console.add(f"\n▶ {a.title}")

        def work(ctx):
            def line(text):
                pct = maintenance.percent_in(text) if a.progress else None
                ctx.progress(text, -1 if pct is None else pct, force=True)
            return run_stream(a.cmd, line, ctx.cancelled)

        def done(code):
            self.console.add("✔ Finished" if code == 0 else f"✖ Finished with exit code {code}")
            if a.note and os.path.exists(a.note):
                open_target(a.note)
            if code == 0:
                self.main.notify("success", f"{a.title}: done", "Details are in the output panel below."
                                 + (" The report has opened in your browser." if a.note else ""))
            else:
                self.main.notify("warning", f"{a.title} finished with warnings",
                                 f"Exit code {code}. Scroll the output panel for what it reported.")

        def on_line(text, pct):
            if text.strip():
                self.console.add(text)
                self.progress.update_progress(text.strip()[:140], pct)
            elif pct >= 0:
                self.progress.update_progress("", pct)

        self.run(work, progress=self.progress, text=f"{a.title}…", on_done=done, on_progress=on_line,
                 summary=lambda code: f"{a.title}: finished" if code == 0 else f"{a.title}: exit code {code}")


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
        self.main.notify("success", "System report copied", "Paste it into an email or support chat with Ctrl+V.")


# =========================================================================== Settings & log
class SettingsPage(BasePage):
    title = "Settings & Activity"
    subtitle = f"{APP_NAME} {APP_VERSION} by {APP_PUBLISHER}"

    def __init__(self, main):
        super().__init__(main)
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
        prefs.body.addWidget(label("Privacy & technician sessions", "cardtitle"))
        priv = QCheckBox("Privacy mode: redact computer and user names in exported reports (recommended)")
        priv.setChecked(bool(settings.get("privacy_mode")))
        priv.toggled.connect(lambda v: settings.set("privacy_mode", v))
        prefs.body.addWidget(priv)
        ret = QHBoxLayout()
        ret.addWidget(label("Delete sessions older than (days, 0 = keep)", "muted"))
        rspin = QSpinBox()
        rspin.setRange(0, 3650)
        rspin.setValue(int(settings.get("session_retention_days")))
        rspin.valueChanged.connect(lambda v: settings.set("session_retention_days", v))
        ret.addWidget(rspin)
        ret.addStretch(1)
        prefs.body.addLayout(ret)
        prefs.body.addWidget(label(f"Sessions: {APP_DIR / 'sessions'} · Profiles: {APP_DIR / 'profiles'} (or a "
                                   "RemedyData folder next to Remedy.exe for portable USB use).", "faint", wrap=True))
        status = Card("Privileges")
        admin = is_admin()
        st = label("● Administrator: full access" if admin else "● Standard user: some features limited")
        st.setStyleSheet(f"color: {theme.OK if admin else theme.WARN}; font-weight: 600;")
        status.body.addWidget(st)
        if not admin and IS_WINDOWS:
            status.body.addWidget(button("Restart as administrator", "primary", self.main.relaunch_admin), 0, Qt.AlignLeft)
        status.body.addWidget(button("Open data folder", "", lambda: open_location(str(APP_DIR))), 0, Qt.AlignLeft)
        prefs.body.addStretch(1)

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
        at.addSpacing(6)
        at.addWidget(label(f"This PC: {osinfo.detect().summary()}", "muted", wrap=True))
        at.addWidget(label("Shortcuts: Ctrl+K search everything · Alt+Left/Right or mouse side buttons go back/"
                           "forward · F5 refresh or scan · Ctrl+F search this page · Esc asks to stop a task · "
                           "Ctrl+1–9 jump to a page", "muted", wrap=True))
        at.addStretch(1)
        ab.addLayout(at, 1)
        about.body.addLayout(ab)
        side_w = QWidget()
        side = QVBoxLayout(side_w)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(about)
        side.addWidget(status)
        side.addWidget(excl, 1)
        self.root.addWidget(ResponsiveGrid((prefs, side_w), 420))

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
        self.console.setMinimumHeight(160)
        self.excl.setMinimumHeight(90)
        self.root.addWidget(logc, 1)
        self.make_scrollable()
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
