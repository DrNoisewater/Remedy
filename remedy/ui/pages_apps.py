"""Apps section: New PC Setup (winget installer), Bloatware Remover, Installed Programs."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QSplitter, QTreeWidgetItem

from .. import theme
from ..core import appstore, programs
from ..core.utils import IS_WINDOWS, human_size, is_admin, log, open_location, run_stream
from .widgets import (OBJ_ROLE, UR, BasePage, Console, ProgressRow, SortItem, attach_empty, button, confirm, error,
                      error_report, label, make_tree, size_widths)


def _s(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# =========================================================================== New PC setup
class SetupPage(BasePage):
    title = "New PC Setup"
    subtitle = "Pick your must-have apps and install them all in one go, silently, from official sources via winget."

    def __init__(self, main):
        super().__init__(main)
        self.installed: set = set()
        self.add_action("Clear", "", lambda: self._check(set()))
        self.btn_install = self.add_action("Install checked", "primary", self.install)

        presets = QHBoxLayout()
        presets.addWidget(label("Quick picks:", "muted"))
        for name, ids in appstore.PRESETS.items():
            presets.addWidget(button(name, "", lambda _=False, i=ids: self._check(set(i), add=True),
                                     f"Check the {name} bundle"))
        presets.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search apps…")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._filter)
        presets.addWidget(self.search)
        self.root.addLayout(presets)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        split = QSplitter(Qt.Vertical)
        self.tree = make_tree(["App", "Status", "What it is"], multi=False, sortable=False, stretch_col=2)
        self.tree.setRootIsDecorated(True)
        size_widths(self.tree, {0: 260, 1: 130})
        self.tree.itemChanged.connect(lambda *_: self._count())
        split.addWidget(self.tree)
        self.console = Console()
        split.addWidget(self.console)
        split.setSizes([520, 160])
        self.root.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        self.root.addLayout(bottom)
        self._build()

    def _build(self):
        self.tree.blockSignals(True)
        groups = {}
        for app in appstore.CATALOG:
            g = groups.get(app.category)
            if g is None:
                g = QTreeWidgetItem([app.category, "", ""])
                g.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[app.category] = g
            it = QTreeWidgetItem([app.name, "", app.description])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            it.setCheckState(0, Qt.Unchecked)
            it.setData(0, OBJ_ROLE, app)
            it.setToolTip(0, app.winget_id)
            g.addChild(it)
        for g in groups.values():
            g.setExpanded(True)
        self.tree.blockSignals(False)
        self._count()

    def _items(self):
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            for ci in range(g.childCount()):
                yield g.child(ci)

    def on_first_show(self):
        if not IS_WINDOWS:
            self.console.add("App installation uses winget and is available on Windows 10/11.")
            return

        def check(_ctx):
            if not appstore.winget_available():
                return None
            return appstore.installed_ids()

        self.run(check, progress=self.progress, text="Checking what's already installed…", cancellable=False,
                 on_done=self._mark_installed)

    def _mark_installed(self, ids):
        if ids is None:
            self.console.add("winget was not found. Install 'App Installer' from the Microsoft Store "
                             "(ms-windows-store://pdp/?productid=9NBLGGH4NNS1), then reopen this page.")
            self.btn_install.setEnabled(False)
            return
        self.installed = ids
        for it in self._items():
            app = it.data(0, OBJ_ROLE)
            if app.winget_id.lower() in ids:
                it.setText(1, "Installed")
                it.setForeground(1, QColor(theme.OK))
        self._count()

    def _check(self, ids: set, add: bool = False):
        self.tree.blockSignals(True)
        for it in self._items():
            app = it.data(0, OBJ_ROLE)
            if app.winget_id in ids and app.winget_id.lower() not in self.installed:
                it.setCheckState(0, Qt.Checked)
            elif not add:
                it.setCheckState(0, Qt.Unchecked)
        self.tree.blockSignals(False)
        self._count()

    def _filter(self, text):
        text = text.lower()
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            any_visible = False
            for ci in range(g.childCount()):
                it = g.child(ci)
                vis = not text or text in it.text(0).lower() or text in it.text(2).lower()
                it.setHidden(not vis)
                any_visible |= vis
            g.setHidden(not any_visible)

    def _count(self):
        n = sum(1 for it in self._items() if it.checkState(0) == Qt.Checked)
        have = sum(1 for a in appstore.CATALOG if a.winget_id.lower() in self.installed)
        self.summary.setText(f"{n} app(s) checked · {len(appstore.CATALOG)} in catalog · {have} already installed")

    def set_busy_ui(self, busy):
        self.btn_install.setEnabled(not busy)

    def install(self):
        rows = [it for it in self._items() if it.checkState(0) == Qt.Checked]
        if not rows:
            self.main.notify("info", "Pick some apps first", "Tick apps in the list, or use a Quick pick bundle.")
            return
        if not IS_WINDOWS:
            return
        if not confirm(self, f"Install {_s(len(rows), 'app')}?",
                       "They download from official sources and install silently, one at a time. "
                       "Some may show a Windows permission prompt.", "Install",
                       bullets=[r.text(0) for r in rows]):
            return
        apps = [r.data(0, OBJ_ROLE) for r in rows]
        by_id = {r.data(0, OBJ_ROLE).winget_id: r for r in rows}
        for r in rows:
            r.setText(1, "Queued")
            r.setForeground(1, QColor(theme.MUTED))

        def work(ctx):
            results = []
            for i, app in enumerate(apps):
                if ctx.cancelled():
                    break
                ctx.progress(f"@{app.winget_id}|Installing ({i + 1} of {len(apps)})…", int(i * 100 / len(apps)),
                             force=True)
                ctx.progress(f"── {app.name} ({app.winget_id})", -1, force=True)
                code = run_stream(appstore.install_args(app.winget_id),
                                  lambda line: ctx.progress(line, -1, force=True), ctx.cancelled)
                ok = appstore.normalize_code(code) in appstore.OK_CODES
                appstore.log_install(app.name, ok, appstore.describe_exit(code))
                ctx.progress(f"@{app.winget_id}|{'Installed' if ok else 'Failed: ' + appstore.describe_exit(code)}",
                             int((i + 1) * 100 / len(apps)), force=True)
                results.append((app.name, ok, appstore.describe_exit(code)))
            return results

        def prog(text, pct):
            if text.startswith("@") and "|" in text:
                wid, status = text[1:].split("|", 1)
                r = by_id.get(wid)
                if r is not None:
                    r.setText(1, status)
                    r.setForeground(1, QColor(theme.OK if status == "Installed" else
                                              theme.DANGER if status.startswith("Failed") else theme.ACCENT))
                self.progress.update_progress(f"{status.split(':')[0].rstrip('…')}: {r.text(0) if r else wid}", pct)
            else:
                # skip winget's spinner / progress-bar noise
                if text.strip() in ("-", "\\", "|", "/") or set(text.strip()) <= set("█▒ ") or "MB /" in text:
                    return
                self.console.add(text)

        def done(results):
            ok = sum(1 for _n, s, _d in results if s)
            fails = [(n, d) for n, s, d in results if not s]
            for r in rows:
                r.setCheckState(0, Qt.Unchecked)
            if ok:
                self.main.notify("success", f"Installed {ok} of {_s(len(results), 'app')}",
                                 "Find them in the Start menu.")
            if fails:
                self.main.notify("warning", f"{_s(len(fails), 'install')} didn't finish",
                                 "Tick them again to retry, or install them from the publisher's site.",
                                 action=("Show details", lambda: error(self, "Some installs failed", "",
                                                                       error_report(fails))))

        self.run(work, progress=self.progress, text="Installing…", on_done=done, on_progress=prog,
                 summary=lambda res: f"Installed {sum(1 for x in res if x[1])} of {len(res)}")


# =========================================================================== Bloatware
class BloatwarePage(BasePage):
    title = "Bloatware Remover"
    subtitle = "Strip preinstalled promo apps, games and trialware. Flagged apps are safe to remove; reinstall any from the Store."

    def __init__(self, main):
        super().__init__(main)
        self.add_action("Select recommended", "", self._recommended)
        self.btn_remove = self.add_action("Remove checked", "danger", self.remove)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter apps…")
        self.search.textChanged.connect(self._filter)
        self.only_known = QCheckBox("Show only known bloat")
        self.only_known.setChecked(True)
        self.only_known.toggled.connect(self._filter)
        self.all_users = QCheckBox("Remove for all users")
        self.all_users.setEnabled(is_admin())
        self.all_users.setToolTip("Requires administrator")
        top.addWidget(self.search, 1)
        top.addWidget(self.only_known)
        top.addWidget(self.all_users)
        self.root.addLayout(top)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["App", "Verdict", "Package", "Version"], multi=False, stretch_col=2)
        size_widths(self.tree, {0: 280, 1: 220, 3: 110})
        self.root.addWidget(self.tree, 1)
        self.empty = attach_empty(self.tree, "Looking for removable apps…")
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(label("Stop Windows adding them back:", "muted"))
        bottom.addWidget(button("Turn off auto-installed apps", "", lambda: self.main.navigate("tweaks"),
                                "Opens Tweaks: 'Disable tips, suggestions & auto-installed apps'"))
        self.root.addLayout(bottom)

    def on_first_show(self):
        if IS_WINDOWS:
            self.refresh()
        else:
            self.root.addWidget(label("Store app removal is available on Windows.", "muted"))

    def refresh(self):
        self.run(lambda ctx: programs.list_appx(), progress=self.progress, text="Listing Store apps…",
                 cancellable=False, on_done=self._populate,
                 summary=lambda apps: f"{sum(1 for a in apps if a['recommended'])} apps flagged as bloat")

    def refresh_action(self):
        self.refresh()

    def _populate(self, apps):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for a in apps:
            verdict = ("Safe to remove" if a["recommended"] else "Optional: keep if you use it") if a["known"] \
                else "Not on our list"
            r = SortItem([a["name"].split(".")[-1] if not a["label"] else a["label"], verdict,
                          a["name"], a["version"]])
            r.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            r.setCheckState(0, Qt.Unchecked)
            r.setData(0, OBJ_ROLE, a)
            r.setData(0, UR, (0 if a["recommended"] else 1 if a["known"] else 2, a["name"].lower()))
            r.setForeground(1, QColor(theme.ACCENT if a["recommended"] else theme.WARN if a["known"] else theme.MUTED))
            self.tree.addTopLevelItem(r)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        rec = sum(1 for a in apps if a["recommended"])
        self.summary.setText(f"{len(apps)} removable Store apps · {rec} flagged as bloat")
        self.empty.setText("No known bloat found. This PC is already clean." if not apps or self.only_known.isChecked()
                           else "No apps match the filter.")
        self._filter()

    def _filter(self, *_):
        text = self.search.text().lower()
        known = self.only_known.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            r = self.tree.topLevelItem(i)
            a = r.data(0, OBJ_ROLE)
            ok = (not known or a["known"]) and (not text or text in r.text(0).lower() or text in a["name"].lower())
            r.setHidden(not ok)

    def _recommended(self):
        n = 0
        for i in range(self.tree.topLevelItemCount()):
            r = self.tree.topLevelItem(i)
            rec = r.data(0, OBJ_ROLE)["recommended"]
            r.setCheckState(0, Qt.Checked if rec else Qt.Unchecked)
            n += rec
        self.main.notify("info" if n else "success", f"Ticked {_s(n, 'app')} flagged as bloat" if n else
                         "No bloat to remove", "Review the list, then press Remove checked." if n else
                         "Nothing on this PC is on our bloat list.")

    def set_busy_ui(self, busy):
        self.btn_remove.setEnabled(not busy)

    def remove(self):
        rows = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if not rows:
            self.main.notify("info", "Nothing ticked", "Tick apps to remove, or use Select recommended.")
            return
        who = "every user on this PC" if self.all_users.isChecked() else "your account"
        if not confirm(self, f"Remove {_s(len(rows), 'app')}?",
                       f"They'll be removed for {who}. You can reinstall any of them from the Microsoft Store.",
                       "Remove", danger=True, bullets=[r.text(0) for r in rows]):
            return
        apps = [r.data(0, OBJ_ROLE) for r in rows]
        all_users = self.all_users.isChecked()

        def work(ctx):
            errs = []
            for i, a in enumerate(apps):
                ctx.progress(f"Removing {a['label'] or a['name']} ({i + 1} of {len(apps)})…",
                             int(i * 100 / len(apps)), force=True)
                try:
                    programs.remove_appx(a["full_name"], all_users)
                except Exception as exc:  # noqa: BLE001
                    errs.append((a["name"], str(exc).strip().splitlines()[0] if str(exc).strip() else "failed"))
            return len(apps) - len(errs), errs

        def done(res):
            ok, errs = res
            if ok:
                self.main.notify("success", f"Removed {_s(ok, 'app')}",
                                 "Reinstall any of them from the Microsoft Store if you miss it.")
            if errs:
                self.main.notify("warning", f"{_s(len(errs), 'app')} couldn't be removed",
                                 "Some system apps refuse removal, or need administrator rights.",
                                 action=("Show details", lambda: error(self, "Some apps could not be removed", "",
                                                                       error_report(errs))))
            self.refresh()

        self.run(work, progress=self.progress, text="Removing apps…", cancellable=False, on_done=done,
                 summary=lambda res: f"Removed {_s(res[0], 'app')}")


# =========================================================================== Programs
class ProgramsPage(BasePage):
    title = "Installed Programs"
    subtitle = "Everything installed on this PC, sortable by size and install date. Launch any uninstaller from here."

    def __init__(self, main):
        super().__init__(main)
        self.add_action("Refresh", "", self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search programs or publishers…")
        self.search.textChanged.connect(self._filter)
        self.root.addWidget(self.search)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Name", "Publisher", "Version", "Size", "Installed"], multi=False, stretch_col=0)
        size_widths(self.tree, {1: 200, 2: 110, 3: 90, 4: 100})
        self.tree.itemDoubleClicked.connect(lambda *_: self.uninstall())
        self.root.addWidget(self.tree, 1)
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(button("Open install folder", "", self._open))
        bottom.addWidget(button("Uninstall", "danger", self.uninstall))
        self.root.addLayout(bottom)

    def on_first_show(self):
        if IS_WINDOWS:
            self.refresh()
        else:
            self.root.addWidget(label("Program management is available on Windows.", "muted"))

    def refresh(self):
        self.run(lambda ctx: programs.list_programs(), progress=self.progress, text="Reading installed programs…",
                 cancellable=False, on_done=self._populate, summary=lambda p: f"{len(p)} programs found")

    def refresh_action(self):
        self.refresh()

    def _populate(self, progs):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for p in progs:
            r = SortItem([p.name, p.publisher, p.version, human_size(p.size) if p.size else "", p.install_date])
            r.setData(0, OBJ_ROLE, p)
            r.setData(3, UR, p.size)
            self.tree.addTopLevelItem(r)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        self.summary.setText(f"{len(progs)} programs · {human_size(sum(p.size for p in progs))} reported")
        self._filter()

    def _filter(self, *_):
        text = self.search.text().lower()
        for i in range(self.tree.topLevelItemCount()):
            r = self.tree.topLevelItem(i)
            r.setHidden(bool(text) and text not in r.text(0).lower() and text not in r.text(1).lower())

    def _current(self):
        sel = self.tree.selectedItems()
        return sel[0].data(0, OBJ_ROLE) if sel else None

    def _open(self):
        p = self._current()
        if p and p.location and os.path.isdir(p.location):
            open_location(p.location)
        elif p:
            self.main.notify("info", "No install folder listed", f"{p.name} doesn't say where it's installed.")

    def uninstall(self):
        p = self._current()
        if not p:
            self.main.notify("info", "Select a program first")
            return
        if confirm(self, f"Uninstall {p.name}?", "Its own uninstaller will open. Follow its steps, "
                                                 "then come back and press Refresh (F5).", "Open uninstaller"):
            try:
                programs.uninstall_program(p)
                self.main.notify("info", f"Uninstaller opened for {p.name}",
                                 "When it finishes, press Refresh to update the list.",
                                 action=("Refresh now", self.refresh))
            except Exception as exc:  # noqa: BLE001
                log.warning("Uninstall failed: %s", exc)
                error(self, "Could not start uninstaller", str(exc))
