"""Clean section: Junk Cleaner, Large Files, Duplicates, Disk Analyzer."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QLineEdit, QMenu, QSpinBox, QSplitter,
                               QStyle, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import junk, scanner
from ..core.utils import TaskCancelled, delete_paths, fmt_time, human_size, is_admin, open_location, settings
from .widgets import (OBJ_ROLE, PATH_ROLE, UR, BarList, BasePage, Card, ProgressRow, ShareDelegate, SortItem,
                      button, confirm, error, error_report, info, label, make_tree, size_widths)
from . import widgets as W


def _delete_summary(page, res: dict, what: str = "file") -> None:
    n, freed = len(res["deleted"]), res["freed"]
    if n:
        verb = "Moved to Recycle Bin" if res.get("recycled") else "Deleted"
        tail = ". Empty the Recycle Bin in Junk Cleaner to reclaim the space." if res.get("recycled") else ""
        page.main.toast(f"{verb}: {n} {what}{'s' if n != 1 else ''} ({human_size(freed)}){tail}")
    if res["errors"]:
        error(page, "Some items were skipped",
              f"{len(res['errors'])} item(s) could not be removed. They may be in use, protected, "
              f"or need administrator rights.", error_report(res["errors"]))


def _copy(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


# =========================================================================== Junk cleaner
def _scan_junk(ctx):
    cats = junk.build_categories()
    admin = is_admin()
    for i, c in enumerate(cats):
        if ctx.cancelled():
            raise TaskCancelled()
        ctx.progress(f"Scanning {c.name}…", int(i * 100 / max(1, len(cats))), force=True)
        if c.admin and not admin:
            continue
        junk.scan_category(c, ctx)
    return cats


def _clean_junk(ctx, cats):
    freed = deleted = failed = 0
    for i, c in enumerate(cats):
        if ctx.cancelled():
            break
        ctx.progress(f"Cleaning {c.name}…", int(i * 100 / max(1, len(cats))), force=True)
        f, d, x = junk.clean_category(c, ctx)
        freed, deleted, failed = freed + f, deleted + d, failed + x
    return freed, deleted, failed


class CleanerPage(BasePage):
    title = "Junk Cleaner"
    subtitle = "Reclaim space from temp files, caches, crash dumps, logs and the Recycle Bin."

    def __init__(self, main):
        super().__init__(main)
        self.cats: dict = {}
        self.checked: dict = {}
        self.btn_scan = self.add_action("Scan", "", self.scan)
        self.btn_clean = self.add_action("Clean selected", "primary", self.clean)

        hero = Card()
        row = QHBoxLayout()
        col = QVBoxLayout()
        self.big = label("0 B", "bignum")
        self.big_sub = label("Scanning for junk…", "muted", wrap=True)
        col.addWidget(label("RECLAIMABLE SPACE", "cardtitle"))
        col.addWidget(self.big)
        col.addWidget(self.big_sub)
        row.addLayout(col, 1)
        hero.body.addLayout(row)
        self.root.addWidget(hero)

        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Category", "Size", "Files", "Notes"], multi=False, sortable=False)
        self.tree.setRootIsDecorated(True)
        size_widths(self.tree, {1: 110, 2: 90, 3: 300})
        self.tree.itemChanged.connect(self._changed)
        self.tree.currentItemChanged.connect(self._show_desc)
        self.root.addWidget(self.tree, 1)
        self.desc = label("", "muted", wrap=True)
        self.root.addWidget(self.desc)
        if not is_admin():
            self.root.addWidget(label("Some system categories need administrator rights. Use “Restart as admin” "
                                      "in the sidebar to include them.", "muted", wrap=True))

    def on_first_show(self):
        self.scan()

    def set_busy_ui(self, busy):
        self.btn_scan.setEnabled(not busy)
        self.btn_clean.setEnabled(not busy)

    def _remember(self):
        for key, item in self._items():
            self.checked[key] = item.checkState(0) == Qt.Checked

    def _items(self):
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            for ci in range(g.childCount()):
                it = g.child(ci)
                yield it.data(0, PATH_ROLE), it

    def scan(self):
        self._remember()
        self.run(_scan_junk, progress=self.progress, text="Scanning…", on_done=self._populate)

    def _populate(self, cats):
        self.cats = {c.key: c for c in cats}
        admin = is_admin()
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict = {}
        for c in cats:
            g = groups.get(c.group)
            if g is None:
                g = QTreeWidgetItem([c.group, "", "", ""])
                g.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate | Qt.ItemIsSelectable)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[c.group] = g
            locked = c.admin and not admin
            note = "Needs administrator" if locked else c.warning
            it = QTreeWidgetItem([c.name, human_size(c.size) if c.scanned else "",
                                  f"{c.count:,}" if c.scanned else "", note])
            it.setData(0, PATH_ROLE, c.key)
            it.setToolTip(0, c.description)
            flags = Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            if not locked:
                flags |= Qt.ItemIsEnabled
            it.setFlags(flags)
            checked = self.checked.get(c.key, c.default) and not locked
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            g.addChild(it)
        for g in groups.values():
            total = sum(self.cats[g.child(i).data(0, PATH_ROLE)].size for i in range(g.childCount()))
            g.setText(1, human_size(total))
            g.setExpanded(True)
        self.tree.blockSignals(False)
        self._update_total()

    def _changed(self, *_):
        self._update_total()

    def _show_desc(self, item, _prev):
        if item is not None and item.data(0, PATH_ROLE) in self.cats:
            self.desc.setText(self.cats[item.data(0, PATH_ROLE)].description)

    def _selected(self):
        return [self.cats[k] for k, it in self._items() if it.checkState(0) == Qt.Checked and k in self.cats]

    def _update_total(self):
        sel = self._selected()
        total = sum(c.size for c in sel)
        everything = sum(c.size for c in self.cats.values())
        self.big.setText(human_size(total))
        self.big_sub.setText(f"Selected from {len(sel)} categories · {human_size(everything)} found in total. "
                             f"Only temporary and cache data is touched; files in use are skipped.")

    def clean(self):
        cats = [c for c in self._selected() if c.size > 0]
        if not cats:
            self.main.toast("Nothing selected to clean. Run a scan first.")
            return
        total = sum(c.size for c in cats)
        if not confirm(self, "Clean junk", f"Permanently remove {human_size(total)} of junk from "
                                           f"{len(cats)} categories?", "Clean now"):
            return
        self._remember()
        self.run(_clean_junk, cats, progress=self.progress, text="Cleaning…", on_done=self._cleaned)

    def _cleaned(self, result):
        freed, deleted, failed = result
        msg = f"Freed {human_size(freed)}: {deleted:,} files removed"
        if failed:
            msg += f", {failed:,} in use and skipped"
        self.main.toast(msg)
        self.scan()


# =========================================================================== Large files
AGE_OPTIONS = [("Any age", 0), ("Not modified in 3 months", 90), ("Not modified in 6 months", 180),
               ("Not modified in 1 year", 365), ("Not modified in 2 years", 730)]


class LargeFilesPage(BasePage):
    title = "Large Files"
    subtitle = "Find the biggest files eating your storage: videos, ISOs, installers and old backups."

    def __init__(self, main):
        super().__init__(main)
        self.btn_scan = self.add_action("Scan", "primary", self.scan)

        controls = QHBoxLayout()
        self.location = W.LocationPicker()
        self.min_size = QSpinBox()
        self.min_size.setRange(1, 1_000_000)
        self.min_size.setSuffix(" MB")
        self.min_size.setValue(int(settings.get("large_file_min_mb")))
        self.age = QComboBox()
        for text, days in AGE_OPTIONS:
            self.age.addItem(text, days)
        controls.addWidget(self.location, 3)
        controls.addWidget(label("Min size", "muted"))
        controls.addWidget(self.min_size)
        controls.addWidget(self.age)
        self.root.addLayout(controls)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name or folder…")
        self.search.textChanged.connect(self._filter)
        self.category = QComboBox()
        self.category.addItem("All types")
        for cat in list(scanner.CATEGORY_EXTS) + ["Other"]:
            self.category.addItem(cat)
        self.category.currentIndexChanged.connect(self._filter)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category)
        self.root.addLayout(filters)

        self.tree = make_tree(["Name", "Size", "Type", "Modified", "Folder"], stretch_col=4)
        size_widths(self.tree, {0: 280, 1: 95, 2: 140, 3: 130})
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemDoubleClicked.connect(lambda it, _c: open_location(it.data(0, PATH_ROLE)))
        self.tree.customContextMenuRequested.connect(self._menu)
        self.root.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        self.summary = label("Choose a location and press Scan.", "muted")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(button("Open location", "", self._open))
        self.btn_recycle = button("Move to Recycle Bin", "", lambda: self._delete(True))
        self.btn_delete = button("Delete permanently", "danger", lambda: self._delete(False))
        bottom.addWidget(self.btn_recycle)
        bottom.addWidget(self.btn_delete)
        self.root.addLayout(bottom)

    def scan_folder(self, path: str):
        self.location.set_folder(path)
        self.scan()

    def set_busy_ui(self, busy):
        for b in (self.btn_scan, self.btn_recycle, self.btn_delete):
            b.setEnabled(not busy)

    def scan(self):
        roots = self.location.roots()
        if not roots:
            return
        settings.set("large_file_min_mb", self.min_size.value())
        self.run(scanner.find_large_files, roots, self.min_size.value() * 1024 * 1024, self.age.currentData(),
                 int(settings.get("max_large_results")), progress=self.progress, text="Scanning…",
                 on_done=self._populate)

    def _populate(self, result):
        items = result["items"]
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        style = self.style()
        file_icon = style.standardIcon(QStyle.SP_FileIcon)
        rows = []
        for it in items:
            row = SortItem([it["name"], human_size(it["size"]), it["category"], fmt_time(it["mtime"]), it["folder"]])
            row.setIcon(0, file_icon)
            row.setData(0, PATH_ROLE, it["path"])
            row.setData(1, UR, it["size"])
            row.setData(3, UR, it["mtime"])
            row.setToolTip(0, it["path"])
            rows.append(row)
        self.tree.addTopLevelItems(rows)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(1, Qt.DescendingOrder)
        total = sum(i["size"] for i in items)
        note = " (stopped early)" if result.get("cancelled") else ""
        self.summary.setText(f"{len(items):,} files · {human_size(total)}{note}")
        self._filter()

    def _filter(self, *_):
        text = self.search.text().lower()
        cat = self.category.currentText()
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            visible = (not text or text in it.text(0).lower() or text in it.text(4).lower()) and \
                      (cat == "All types" or it.text(2) == cat)
            it.setHidden(not visible)

    def _selected(self):
        return [it for it in self.tree.selectedItems() if not it.isHidden()]

    def _selection(self):
        sel = self._selected()
        if sel:
            self.summary.setText(f"{len(sel)} selected · {human_size(sum(i.data(1, UR) for i in sel))}")

    def _open(self):
        sel = self._selected()
        if sel:
            open_location(sel[0].data(0, PATH_ROLE))

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        m.addAction("Open file location", lambda: open_location(it.data(0, PATH_ROLE)))
        m.addAction("Copy path", lambda: _copy(it.data(0, PATH_ROLE)))
        m.addSeparator()
        m.addAction("Move to Recycle Bin", lambda: self._delete(True))
        m.addAction("Delete permanently", lambda: self._delete(False))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _delete(self, recycle: bool):
        sel = self._selected()
        if not sel:
            self.main.toast("Select one or more files first.")
            return
        paths = [i.data(0, PATH_ROLE) for i in sel]
        size = sum(i.data(1, UR) for i in sel)
        action = "Move to the Recycle Bin" if recycle else "PERMANENTLY delete"
        if not confirm(self, "Remove files", f"{action} {len(paths)} file(s) totalling {human_size(size)}?"
                       + ("" if recycle else "\n\nThis cannot be undone."), "Remove"):
            return
        self.run(lambda ctx: delete_paths(paths, recycle, ctx), progress=self.progress, text="Removing…",
                 on_done=self._deleted)

    def _deleted(self, res):
        gone = set(res["deleted"])
        for i in reversed(range(self.tree.topLevelItemCount())):
            if self.tree.topLevelItem(i).data(0, PATH_ROLE) in gone:
                self.tree.takeTopLevelItem(i)
        _delete_summary(self, res)


# =========================================================================== Duplicates
class DuplicatesPage(BasePage):
    title = "Duplicate Finder"
    subtitle = "Byte-for-byte identical files, verified by their contents, not just their names."

    def __init__(self, main):
        super().__init__(main)
        self.btn_scan = self.add_action("Scan", "primary", self.scan)
        controls = QHBoxLayout()
        self.location = W.LocationPicker()
        self.min_size = QDoubleSpinBox()
        self.min_size.setRange(0.01, 100_000)
        self.min_size.setDecimals(2)
        self.min_size.setSuffix(" MB")
        self.min_size.setValue(float(settings.get("duplicate_min_mb")))
        controls.addWidget(self.location, 3)
        controls.addWidget(label("Ignore files under", "muted"))
        controls.addWidget(self.min_size)
        self.root.addLayout(controls)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        self.tree = make_tree(["File", "Size", "Modified", "Folder"], multi=False, sortable=False, stretch_col=3)
        self.tree.setRootIsDecorated(True)
        size_widths(self.tree, {0: 300, 1: 110, 2: 130})
        self.tree.itemChanged.connect(lambda *_: self._update_summary())
        self.tree.itemDoubleClicked.connect(lambda it, _c: it.data(0, PATH_ROLE) and open_location(it.data(0, PATH_ROLE)))
        self.root.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        self.summary = label("Choose a location and press Scan.", "muted")
        bottom.addWidget(self.summary, 1)
        auto = button("Auto-select ▾")
        menu = QMenu(auto)
        menu.addAction("Keep newest copy", lambda: self._auto("newest"))
        menu.addAction("Keep oldest copy", lambda: self._auto("oldest"))
        menu.addAction("Keep shortest path", lambda: self._auto("shortest"))
        menu.addAction("Clear selection", lambda: self._auto("none"))
        auto.setMenu(menu)
        bottom.addWidget(auto)
        self.btn_delete = button("Move checked to Recycle Bin", "danger", self._delete)
        bottom.addWidget(self.btn_delete)
        self.root.addLayout(bottom)

    def set_busy_ui(self, busy):
        self.btn_scan.setEnabled(not busy)
        self.btn_delete.setEnabled(not busy)

    def scan_folder(self, path: str):
        self.location.set_folder(path)
        self.scan()

    def scan(self):
        roots = self.location.roots()
        if not roots:
            return
        settings.set("duplicate_min_mb", self.min_size.value())
        self.run(scanner.find_duplicates, roots, int(self.min_size.value() * 1024 * 1024),
                 progress=self.progress, text="Indexing files…", on_done=self._populate)

    def _populate(self, result):
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = result["groups"]
        for g in groups:
            files = g["files"]
            head = QTreeWidgetItem([f"{len(files)} identical copies · {human_size(g['size'])} each",
                                    f"{human_size(g['wasted'])} wasted", "", ""])
            f = head.font(0)
            f.setBold(True)
            head.setFont(0, f)
            head.setFlags(Qt.ItemIsEnabled)
            head.setData(0, OBJ_ROLE, g["size"])
            for fi in files:
                ch = QTreeWidgetItem([os.path.basename(fi["path"]), human_size(g["size"]), fmt_time(fi["mtime"]),
                                      os.path.dirname(fi["path"])])
                ch.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                ch.setCheckState(0, Qt.Unchecked)
                ch.setData(0, PATH_ROLE, fi["path"])
                ch.setData(2, UR, fi["mtime"])
                ch.setToolTip(0, fi["path"])
                head.addChild(ch)
            self.tree.addTopLevelItem(head)
            head.setExpanded(True)
        self.tree.blockSignals(False)
        wasted = sum(g["wasted"] for g in groups)
        self.summary.setText(f"{len(groups):,} duplicate groups · {human_size(wasted)} recoverable" if groups
                             else "No duplicates found. Nice and tidy.")
        if groups:
            self._auto("newest")

    def _groups(self):
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            yield head, [head.child(j) for j in range(head.childCount())]

    def _auto(self, mode):
        self.tree.blockSignals(True)
        for _head, kids in self._groups():
            if not kids:
                continue
            if mode == "newest":
                keep = max(kids, key=lambda k: k.data(2, UR))
            elif mode == "oldest":
                keep = min(kids, key=lambda k: k.data(2, UR))
            elif mode == "shortest":
                keep = min(kids, key=lambda k: len(k.data(0, PATH_ROLE)))
            else:
                keep = None
            for k in kids:
                k.setCheckState(0, Qt.Unchecked if (keep is None or k is keep) else Qt.Checked)
        self.tree.blockSignals(False)
        self._update_summary()

    def _checked(self):
        out = []
        for head, kids in self._groups():
            out += [(head, k) for k in kids if k.checkState(0) == Qt.Checked]
        return out

    def _update_summary(self):
        chk = self._checked()
        if chk:
            size = sum(h.data(0, OBJ_ROLE) for h, _ in chk)
            self.summary.setText(f"{len(chk)} copies checked · {human_size(size)} will be freed")

    def _delete(self):
        paths, skipped = [], 0
        for _head, kids in self._groups():
            checked = [k for k in kids if k.checkState(0) == Qt.Checked]
            if checked and len(checked) == len(kids):
                skipped += 1  # never delete every copy
                continue
            paths += [k.data(0, PATH_ROLE) for k in checked]
        if skipped:
            info(self, "Kept a copy", f"{skipped} group(s) had every copy checked. Remedy always keeps at least "
                                      f"one copy, so those groups were left alone.")
        if not paths:
            return
        if not confirm(self, "Remove duplicates", f"Move {len(paths)} duplicate file(s) to the Recycle Bin?", "Remove"):
            return
        self.run(lambda ctx: delete_paths(paths, True, ctx), progress=self.progress, text="Removing…",
                 on_done=self._deleted)

    def _deleted(self, res):
        gone = set(res["deleted"])
        self.tree.blockSignals(True)
        for i in reversed(range(self.tree.topLevelItemCount())):
            head = self.tree.topLevelItem(i)
            for j in reversed(range(head.childCount())):
                if head.child(j).data(0, PATH_ROLE) in gone:
                    head.takeChild(j)
            if head.childCount() < 2:
                self.tree.takeTopLevelItem(i)
        self.tree.blockSignals(False)
        _delete_summary(self, res, "duplicate")


# =========================================================================== Disk analyzer
def _scan_tree(ctx, roots):
    results = []
    for r in roots:
        if ctx.cancelled():
            break
        results.append(scanner.build_tree(ctx, r))
    return results


class DiskPage(BasePage):
    title = "Disk Analyzer"
    subtitle = "See exactly which folders are using your space. Expand folders to drill down."

    def __init__(self, main):
        super().__init__(main)
        self.btn_scan = self.add_action("Analyze", "primary", self.scan)
        self.location = W.LocationPicker(allow_all=True)
        self.root.addWidget(self.location)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)

        split = QSplitter(Qt.Horizontal)
        self.tree = make_tree(["Folder", "Size", "Share of parent", "Files"], multi=False, stretch_col=0)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemDelegateForColumn(2, ShareDelegate(self.tree))
        size_widths(self.tree, {1: 100, 2: 200, 3: 90})
        self.tree.itemExpanded.connect(self._expand)
        self.tree.customContextMenuRequested.connect(self._menu)
        split.addWidget(self.tree)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 0, 0, 0)
        c1 = Card("By file type")
        self.bars = BarList()
        c1.body.addWidget(self.bars)
        c2 = Card("Top extensions")
        self.ext_bars = BarList()
        c2.body.addWidget(self.ext_bars)
        sl.addWidget(c1)
        sl.addWidget(c2)
        sl.addStretch(1)
        split.addWidget(side)
        split.setSizes([700, 380])
        self.root.addWidget(split, 1)
        self.dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)

    def set_busy_ui(self, busy):
        self.btn_scan.setEnabled(not busy)

    def scan(self):
        roots = self.location.roots()
        if roots:
            self.run(_scan_tree, roots, progress=self.progress, text="Measuring folders…", on_done=self._populate)

    def _item(self, node, parent_size):
        it = SortItem([node.name, human_size(node.size), "", f"{node.files:,}"])
        it.setIcon(0, self.dir_icon)
        it.setData(1, UR, node.size)
        it.setData(2, UR, (node.size / parent_size) if parent_size else 1.0)
        it.setData(3, UR, node.files)
        it.setData(0, PATH_ROLE, node.path)
        it.setData(0, OBJ_ROLE, node)
        it.setToolTip(0, node.path)
        if node.children or node.own_files:
            it.addChild(QTreeWidgetItem(["…"]))
        return it

    def _populate(self, results):
        self.tree.clear()
        cats: dict = {}
        exts: dict = {}
        for res in results:
            root = res["root"]
            top = self._item(root, root.size)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
            for k, v in res["categories"].items():
                cats[k] = cats.get(k, 0) + v
            for k, v in res["extensions"]:
                exts[k] = exts.get(k, 0) + v
        self.tree.sortItems(1, Qt.DescendingOrder)
        self.bars.set_data([(k, v, scanner.CATEGORY_COLORS.get(k, "#8a93a8"))
                            for k, v in sorted(cats.items(), key=lambda kv: kv[1], reverse=True)])
        self.ext_bars.set_data([(k, v, "#2dd4bf") for k, v in sorted(exts.items(), key=lambda kv: kv[1],
                                                                      reverse=True)[:10]])
        if any(r["cancelled"] for r in results):
            self.main.toast("Stopped early. Sizes cover what was scanned so far.")

    def _expand(self, item):
        node = item.data(0, OBJ_ROLE)
        if node is None or item.data(0, Qt.UserRole + 3):
            return
        item.setData(0, Qt.UserRole + 3, True)
        item.takeChildren()
        kids = [self._item(c, node.size) for c in node.children]
        if node.own_files:
            f = SortItem([f"[{node.own_files:,} files in this folder]", human_size(node.own_size), "",
                          f"{node.own_files:,}"])
            f.setIcon(0, self.file_icon)
            f.setData(1, UR, node.own_size)
            f.setData(2, UR, node.own_size / node.size if node.size else 0)
            f.setData(3, UR, node.own_files)
            f.setData(0, PATH_ROLE, node.path)
            fnt = QFont(f.font(0))
            fnt.setItalic(True)
            f.setFont(0, fnt)
            kids.append(f)
        item.addChildren(kids)
        item.sortChildren(1, Qt.DescendingOrder)

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if not it or not it.data(0, PATH_ROLE):
            return
        path = it.data(0, PATH_ROLE)
        m = QMenu(self)
        m.addAction("Open in Explorer", lambda: open_location(path))
        m.addAction("Copy path", lambda: _copy(path))
        m.addAction("Find large files here", lambda: self.main.navigate("large", folder=path))
        m.addAction("Find duplicates here", lambda: self.main.navigate("dupes", folder=path))
        m.exec(self.tree.viewport().mapToGlobal(pos))
