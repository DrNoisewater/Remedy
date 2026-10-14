"""Apps section: New PC Setup (winget installer), Bloatware Remover, Installed Programs."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QSplitter, QTreeWidgetItem

from .. import theme
from ..core import appstore, programs
from ..core.utils import IS_WINDOWS, human_size, is_admin, log, open_location, run_stream
from .widgets import (OBJ_ROLE, UR, BasePage, Console, ProgressRow, SortItem, button, confirm, error,
                      error_report, label, make_tree, size_widths)


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
            self.main.toast("Check some apps or pick a Quick pick bundle first.")
            return
        if not IS_WINDOWS:
            return
        if not confirm(self, "Install apps", f"Install {len(rows)} app(s) silently with winget?\n\n"
                                             "Installers run one at a time. Some may show a UAC prompt.", "Install"):
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
                ctx.progress(f"@{app.winget_id}|Installing…", int(i * 100 / len(apps)), force=True)
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
                self.progress.update_progress(f"{status.split(':')[0]} {r.text(0) if r else wid}", pct)
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
            self.main.toast(f"Installed {ok} of {len(results)} app(s)")
            if fails:
                error(self, "Some installs failed", "You can retry them or install manually.", error_report(fails))

        self.run(work, progress=self.progress, text="Installing…", on_done=done, on_progress=prog)


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
        self.tree = make_tree(["App", "What it is", "Package", "Version"], multi=False, stretch_col=2)
        size_widths(self.tree, {0: 260, 1: 240, 3: 110})
        self.root.addWidget(self.tree, 1)
        self.summary = label("", "muted")
        self.root.addWidget(self.summary)

    def on_first_show(self):
        if IS_WINDOWS:
            self.refresh()
        else:
            self.root.addWidget(label("Store app removal is available on Windows.", "muted"))

    def refresh(self):
        self.run(lambda ctx: programs.list_appx(), progress=self.progress, text="Listing Store apps…",
                 cancellable=False, on_done=self._populate)

    def _populate(self, apps):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for a in apps:
            r = SortItem([a["name"].split(".")[-1] if not a["label"] else a["label"],
                          ("Recommended to remove" if a["recommended"] else "Optional") if a["known"] else "",
                          a["name"], a["version"]])
            r.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            r.setCheckState(0, Qt.Unchecked)
            r.setData(0, OBJ_ROLE, a)
            r.setData(0, UR, (0 if a["recommended"] else 1 if a["known"] else 2, a["name"].lower()))
            if a["recommended"]:
                r.setForeground(1, QColor(theme.ACCENT))
            self.tree.addTopLevelItem(r)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        rec = sum(1 for a in apps if a["recommended"])
        self.summary.setText(f"{len(apps)} removable Store apps · {rec} flagged as bloat")
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
        for i in range(self.tree.topLevelItemCount()):
            r = self.tree.topLevelItem(i)
            r.setCheckState(0, Qt.Checked if r.data(0, OBJ_ROLE)["recommended"] else Qt.Unchecked)

    def set_busy_ui(self, busy):
        self.btn_remove.setEnabled(not busy)

    def remove(self):
        rows = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())
                if self.tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if not rows:
            self.main.toast("Check apps to remove, or use Select recommended.")
            return
        names = ", ".join(r.text(0) for r in rows[:8]) + ("…" if len(rows) > 8 else "")
        if not confirm(self, "Remove apps", f"Remove {len(rows)} app(s)?\n\n{names}\n\n"
                                            "You can reinstall any of them from the Microsoft Store.", "Remove"):
            return
        apps = [r.data(0, OBJ_ROLE) for r in rows]
        all_users = self.all_users.isChecked()

        def work(ctx):
            errs = []
            for i, a in enumerate(apps):
                ctx.progress(f"Removing {a['label'] or a['name']}…", int(i * 100 / len(apps)), force=True)
                try:
                    programs.remove_appx(a["full_name"], all_users)
                except Exception as exc:  # noqa: BLE001
                    errs.append((a["name"], str(exc).strip().splitlines()[0] if str(exc).strip() else "failed"))
            return len(apps) - len(errs), errs

        def done(res):
            ok, errs = res
            self.main.toast(f"Removed {ok} app(s)")
            if errs:
                error(self, "Some apps could not be removed", "", error_report(errs))
            self.refresh()

        self.run(work, progress=self.progress, text="Removing…", cancellable=False, on_done=done)


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
                 cancellable=False, on_done=self._populate)

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
            self.main.toast("This program doesn't report an install folder.")

    def uninstall(self):
        p = self._current()
        if not p:
            return
        if confirm(self, "Uninstall", f"Launch the uninstaller for {p.name}?", "Uninstall"):
            try:
                programs.uninstall_program(p)
                self.main.toast(f"Uninstaller launched for {p.name}. Press Refresh when it finishes.")
            except Exception as exc:  # noqa: BLE001
                log.warning("Uninstall failed: %s", exc)
                error(self, "Could not start uninstaller", str(exc))
