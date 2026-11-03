"""Optimize section: Startup apps, Tweaks, Services, Processes."""
from __future__ import annotations

import os

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QMenu, QTreeWidgetItem

from .. import theme
from ..core import maintenance, osinfo, services, startup, tweaks
from ..core.utils import IS_WINDOWS, human_size, is_admin, log, open_location, settings
from .widgets import (OBJ_ROLE, UR, BasePage, Card, ProgressRow, SortItem, attach_empty, button, confirm, error,
                      error_report, label, make_tree, pill, size_widths)


def _windows_only(page) -> bool:
    if IS_WINDOWS:
        return True
    page.root.addWidget(label("This feature is available on Windows.", "muted"))
    return False


# =========================================================================== Startup
class StartupPage(BasePage):
    title = "Startup Apps"
    subtitle = "Fewer startup apps = faster boot. Changes match Task Manager's Startup tab and are fully reversible."

    def __init__(self, main):
        super().__init__(main)
        self.add_action("Refresh", "", self.refresh)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Name", "Status", "Scope", "Type", "Command"], stretch_col=4)
        size_widths(self.tree, {0: 230, 1: 95, 2: 140, 3: 120})
        self.tree.customContextMenuRequested.connect(self._menu)
        self.root.addWidget(self.tree, 1)
        attach_empty(self.tree, "Reading startup entries…")
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(button("Open location", "", self._open))
        bottom.addWidget(button("Remove entry", "danger", self._remove))
        bottom.addWidget(button("Enable", "", lambda: self._set(True)))
        bottom.addWidget(button("Disable", "primary", lambda: self._set(False)))
        self.root.addLayout(bottom)

    def on_first_show(self):
        if _windows_only(self):
            self.refresh()

    def refresh(self):
        self.run(lambda ctx: startup.list_startup_items(True), progress=self.progress,
                 text="Reading startup entries…", cancellable=False, on_done=self._populate,
                 summary=lambda items: f"{len(items)} startup entries found")

    def refresh_action(self):
        self.refresh()

    def _populate(self, items):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for item in items:
            row = SortItem([item.name, "", item.scope, item.kind, item.command])
            row.setData(0, OBJ_ROLE, item)
            row.setToolTip(4, item.command)
            self._paint(row)
            self.tree.addTopLevelItem(row)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        self._summary()

    def _paint(self, row):
        item = row.data(0, OBJ_ROLE)
        row.setText(1, "Enabled" if item.enabled else "Disabled")
        row.setForeground(1, QColor(theme.OK if item.enabled else theme.MUTED))

    def _summary(self):
        items = [self.tree.topLevelItem(i).data(0, OBJ_ROLE) for i in range(self.tree.topLevelItemCount())]
        on = sum(1 for i in items if i.enabled)
        tip = " · Aim for 5 or fewer for a quick boot." if on > 5 else " · Nice and lean."
        self.summary.setText(f"{on} of {len(items)} startup items enabled{tip if items else ''}")

    def _sel(self):
        return [r for r in self.tree.selectedItems()]

    def _set(self, enabled):
        rows = self._sel()
        if not rows:
            self.main.notify("info", "Select an app first", "Click one (Ctrl or Shift to pick several).")
            return
        errors = []
        for r in rows:
            item = r.data(0, OBJ_ROLE)
            try:
                startup.set_enabled(item, enabled)
                self._paint(r)
            except Exception as exc:  # noqa: BLE001
                hint = " (needs administrator)" if item.needs_admin and not is_admin() else ""
                errors.append((item.name, f"{exc}{hint}"))
        self._summary()
        changed = len(rows) - len(errors)
        if changed:
            verb = "will start with Windows again" if enabled else "won't start with Windows any more"
            self.main.notify("success", f"{changed} app{'s' if changed != 1 else ''} {verb}",
                             "Takes effect next time you sign in." + ("" if enabled else " Re-enable any time."))
        if errors:
            self.main.notify("warning", f"{len(errors)} couldn't be changed",
                             "System-wide entries need administrator rights.",
                             action=("Show details", lambda: error(self, "Some changes failed",
                                                                   "These were left as they were:",
                                                                   error_report(errors))))

    def _open(self):
        rows = self._sel()
        if rows:
            path = rows[0].data(0, OBJ_ROLE).target_path
            if os.path.exists(path):
                open_location(path)
            else:
                self.main.notify("warning", "File not found", "The program this entry points to no longer exists. "
                                 "It's safe to remove the entry.")

    def _remove(self):
        rows = self._sel()
        if not rows:
            return
        if not confirm(self, f"Remove {len(rows)} startup entr{'y' if len(rows) == 1 else 'ies'}?",
                       "This deletes the startup entry only, not the program. Disable is usually better "
                       "because it can be undone.", "Remove", danger=True, bullets=[r.text(0) for r in rows]):
            return
        errors = []
        for r in rows:
            try:
                startup.delete_item(r.data(0, OBJ_ROLE))
            except Exception as exc:  # noqa: BLE001
                errors.append((r.text(0), str(exc)))
        if errors:
            error(self, "Some entries could not be removed", "They may need administrator rights.",
                  error_report(errors))
        else:
            self.main.notify("success", f"Removed {len(rows)} startup entr{'y' if len(rows) == 1 else 'ies'}")
        self.refresh()

    def _menu(self, pos):
        if not self.tree.itemAt(pos):
            return
        m = QMenu(self)
        m.addAction("Enable", lambda: self._set(True))
        m.addAction("Disable", lambda: self._set(False))
        m.addAction("Open file location", self._open)
        m.addSeparator()
        m.addAction("Remove entry", self._remove)
        m.exec(self.tree.viewport().mapToGlobal(pos))


# =========================================================================== Tweaks
RISK_COLORS = {"Safe": theme.OK, "Moderate": theme.WARN, "Advanced": theme.DANGER}
AREA_COLORS = {"Speed": theme.ACCENT, "Space": theme.INFO, "Gaming": "#a78bfa", "Privacy": "#f472b6",
               "Security": "#fbbf24", "Stability": "#34d399", "Comfort": "#9aa4b8"}


def _statuses(ctx, items):
    out = {}
    for i, t in enumerate(items):
        ctx.progress(f"Checking {t.name}…", int(i * 100 / max(1, len(items))))
        try:
            out[t.id] = t.status()
        except Exception:  # noqa: BLE001
            out[t.id] = None
    return out


def _apply(ctx, to_apply, to_revert, restore_point):
    results = []
    if restore_point:
        ctx.progress("Creating a restore point first…", 0, force=True)
        ok, msg = maintenance.create_restore_point()
        results.append(("Restore point", ok, msg.splitlines()[-1] if msg else ""))
    jobs = [(t, False) for t in to_apply] + [(t, True) for t in to_revert]
    for i, (t, revert) in enumerate(jobs):
        ctx.progress(f"{'Reverting' if revert else 'Applying'} {t.name}…", int((i + 1) * 100 / len(jobs)), force=True)
        try:
            msg = t.revert() if revert else t.apply()
            results.append((t.name, True, msg))
            log.info("Tweak %s: %s", "reverted" if revert else "applied", t.name)
        except Exception as exc:  # noqa: BLE001
            results.append((t.name, False, str(exc)))
            log.warning("Tweak failed: %s (%s)", t.name, exc)
    return results


class TweaksPage(BasePage):
    title = "Tweaks"
    subtitle = ("Ticked means on. Click any tweak to see what it does and what you'll gain, tick or untick, "
                "then press Apply changes. Original settings are saved, so you can untick to undo.")

    def __init__(self, main):
        super().__init__(main)
        self.tweaks = tweaks.build_tweaks()
        self.os = osinfo.detect()
        self.state: dict = {}          # tweak id -> True (applied) / False / None (unknown)
        presets = button("Presets ▾", "", None, "Tick a ready-made set of tweaks suited to this PC")
        pm = QMenu(presets)
        pm.addAction("Recommended for this PC", self._recommended)
        pm.addSeparator()
        pm.addAction("Untick everything (undo all Remedy tweaks)", self._untick_all)
        pm.addSeparator()
        for name in tweaks.PRESETS:
            if name == "Windows 11 clean-up" and not self.os.is_win11:
                continue
            pm.addAction(name, lambda n=name: self._preset(n))
        presets.setMenu(pm)
        self.actions.addWidget(presets)
        self.btn_reset = self.add_action("Discard changes", "", self._sync_checks)
        self.btn_apply = self.add_action("Apply changes", "primary", self._go)
        banner = QHBoxLayout()
        banner.addWidget(pill(f"Tailored for {self.os.short()}", theme.ACCENT))
        if self.os.laptop:
            banner.addWidget(pill("Laptop: battery-hungry tweaks aren't pre-selected", theme.WARN))
        hidden = sum(1 for t in self.tweaks if not t.availability(self.os)[0])
        if hidden and self.os.is_windows:
            banner.addWidget(label(f"{hidden} tweaks for other Windows versions are greyed out.", "muted"))
        banner.addStretch(1)
        self.root.addLayout(banner)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Tweak", "What you gain", "Risk", "Status", "Takes effect"], multi=False,
                              sortable=False)
        self.tree.setRootIsDecorated(True)
        size_widths(self.tree, {1: 330, 2: 95, 3: 130, 4: 140})
        self.tree.currentItemChanged.connect(self._describe)
        self.tree.itemChanged.connect(self._changed)
        self.root.addWidget(self.tree, 1)
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        self.root.addLayout(bottom)
        card = Card()
        self.desc_title = label("Click a tweak to see what it does, what you'll notice and the trade-off.", "")
        self.desc_title.setStyleSheet("font-weight: 700; font-size: 11pt;")
        self.desc = label("Gains are typical results, not promises; every PC is different.", "muted", wrap=True)
        self.desc.setTextFormat(Qt.RichText)
        self.desc.setMinimumHeight(64)
        card.body.addWidget(self.desc_title)
        card.body.addWidget(self.desc)
        self.root.addWidget(card)
        self._build()
        self.btn_apply.setEnabled(False)   # enabled once there is something to change
        self.btn_reset.setEnabled(False)

    def _build(self):
        admin = is_admin()
        groups = {}
        self.tree.blockSignals(True)
        for t in self.tweaks:
            g = groups.get(t.category)
            if g is None:
                g = QTreeWidgetItem([t.category, "", "", "", ""])
                g.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                self.tree.addTopLevelItem(g)
                groups[t.category] = g
            it = QTreeWidgetItem([t.name, f"{t.area}  ·  {t.gain}", t.risk, "Checking…", t.restart or "Immediately"])
            it.setData(0, OBJ_ROLE, t)
            it.setForeground(1, QColor(AREA_COLORS.get(t.area, theme.MUTED)))
            it.setToolTip(1, f"{t.notice}\nTrade-off: {t.tradeoff}")
            it.setForeground(2, QColor(RISK_COLORS.get(t.risk, theme.MUTED)))
            flags = Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            usable, why = t.availability(self.os)
            if usable and (admin or not t.admin):
                flags |= Qt.ItemIsEnabled
            elif usable:
                it.setText(4, "Needs administrator")
            else:
                it.setText(3, why)
                it.setText(4, "")
            if self.os.laptop and t.laptop_note:
                it.setToolTip(0, "On laptops: " + t.laptop_note)
            it.setFlags(flags)
            it.setCheckState(0, Qt.Unchecked)
            g.addChild(it)
        for g in groups.values():
            g.setExpanded(True)
        self.tree.blockSignals(False)

    def _children(self):
        for gi in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(gi)
            for ci in range(g.childCount()):
                yield g.child(ci)

    def on_first_show(self):
        if IS_WINDOWS:
            self.refresh()
        else:
            self.root.addWidget(label("Tweaks apply to Windows only.", "muted"))

    def refresh(self):
        self.run(_statuses, self.tweaks, progress=self.progress, text="Checking current settings…",
                 cancellable=False, on_done=self._show_status)

    def _show_status(self, states):
        self.state = dict(states)
        self._sync_checks()

    def _sync_checks(self):
        """Make every checkbox match what is actually applied on this PC."""
        self.tree.blockSignals(True)
        for it in self._children():
            applied = bool(self.state.get(it.data(0, OBJ_ROLE).id))
            it.setCheckState(0, Qt.Checked if applied else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._refresh_status_column()

    def _pending(self):
        to_apply, to_revert = [], []
        for it in self._children():
            if not it.flags() & Qt.ItemIsEnabled:
                continue
            t = it.data(0, OBJ_ROLE)
            applied = bool(self.state.get(t.id))
            checked = it.checkState(0) == Qt.Checked
            if checked and not applied:
                to_apply.append(t)
            elif applied and not checked:
                to_revert.append(t)
        return to_apply, to_revert

    def _changed(self, _item, column):
        if column == 0:  # a tweak or a whole group was ticked/unticked
            self._refresh_status_column()

    def _refresh_status_column(self):
        self.tree.blockSignals(True)
        for it in self._children():
            t = it.data(0, OBJ_ROLE)
            st = self.state.get(t.id)
            checked = it.checkState(0) == Qt.Checked
            usable, why = t.availability(self.os)
            if not usable:
                it.setText(3, why)
                it.setForeground(3, QColor(theme.MUTED))
                continue
            if checked and not st:
                text, color = "Will be applied", theme.ACCENT
            elif st and not checked:
                text, color = "Will be undone", theme.WARN
            elif st:
                text, color = "Applied", theme.OK
            elif st is None and self.state:
                text, color = "Unknown", theme.MUTED
            else:
                text, color = "Off", theme.MUTED
            it.setText(3, text)
            it.setForeground(3, QColor(color))
        self.tree.blockSignals(False)
        to_apply, to_revert = self._pending()
        on = sum(1 for v in self.state.values() if v)
        usable = sum(1 for t in self.tweaks if t.availability(self.os)[0])
        parts = [f"{on} of {usable} tweaks on"]
        if to_apply or to_revert:
            gains = f" ({tweaks.gain_tally(to_apply)})" if to_apply else ""
            parts.append(f"{len(to_apply)} to apply{gains}, {len(to_revert)} to undo")
        self.summary.setText(" · ".join(parts))
        self.btn_apply.setEnabled(bool(to_apply or to_revert) and (self.worker is None))
        self.btn_reset.setEnabled(bool(to_apply or to_revert))

    def _describe(self, item, _prev):
        t = item.data(0, OBJ_ROLE) if item else None
        if not t:
            return
        from html import escape
        color = AREA_COLORS.get(t.area, theme.MUTED)
        notes = []
        if t.admin:
            notes.append("Needs administrator.")
        if t.restart:
            notes.append(f"Takes effect after: {t.restart.lower()}.")
        usable, why = t.availability(self.os)
        if not usable:
            notes.append(f"Not available here ({why}).")
        if t.laptop_note:
            notes.append(f"On laptops: {t.laptop_note}")
        notes.append("Gains are typical results; every PC is different.")
        self.desc_title.setText(f"{t.name}  ·  {t.risk}")
        self.desc.setText(
            f"<span style='color:{color}; font-weight:700'>{escape(t.area)}: {escape(t.gain)}</span><br>"
            f"<b style='color:{theme.TEXT}'>What it does:</b> {escape(t.description)}<br>"
            f"<b style='color:{theme.TEXT}'>What you'll notice:</b> {escape(t.notice)}<br>"
            f"<b style='color:{theme.TEXT}'>Trade-off:</b> {escape(t.tradeoff)}<br>"
            f"<span style='color:{theme.MUTED}'>{escape(' '.join(notes))}</span>")

    def _tick(self, ids, label_text):
        ids = set(ids)
        added = 0
        self.tree.blockSignals(True)
        for it in self._children():
            t = it.data(0, OBJ_ROLE)
            if it.flags() & Qt.ItemIsEnabled and t.id in ids and it.checkState(0) != Qt.Checked:
                it.setCheckState(0, Qt.Checked)
                added += 1
        self.tree.blockSignals(False)
        self._refresh_status_column()
        if added:
            self.main.notify("info", f"{label_text}: ticked {added} tweak{'s' if added != 1 else ''}",
                             "Review them, then press Apply changes. Nothing changes until you do.")
        else:
            self.main.notify("success", f"{label_text}: already set", "Everything in this set is already ticked "
                                                                      "or not available on this PC.")

    def _untick_all(self):
        """Untick every tweak, so Apply changes puts every original setting back."""
        n = 0
        self.tree.blockSignals(True)
        for it in self._children():
            if it.flags() & Qt.ItemIsEnabled and it.checkState(0) == Qt.Checked:
                it.setCheckState(0, Qt.Unchecked)
                n += 1
        self.tree.blockSignals(False)
        self._refresh_status_column()
        if n:
            self.main.notify("info", f"Unticked {n} tweak{'s' if n != 1 else ''}",
                             "Press Apply changes to put the original settings back.")
        else:
            self.main.notify("success", "Nothing to undo", "No Remedy tweaks are on.")

    def _recommended(self):
        """Tick every tweak recommended for this PC (already-applied ones stay ticked)."""
        self._tick([t.id for t in self.tweaks if t.suggested(self.os)], "Recommended")

    def _preset(self, name):
        self._tick(tweaks.preset_ids(name, self.tweaks, self.os), name)

    def refresh_action(self):
        self.refresh()

    def set_busy_ui(self, busy):
        if busy:
            self.btn_apply.setEnabled(False)
            self.btn_reset.setEnabled(False)

    def _go(self):
        to_apply, to_revert = self._pending()
        if not to_apply and not to_revert:
            self.main.notify("info", "Nothing to change yet", "Tick tweaks to turn them on, or untick to undo them.")
            return
        lines = []
        if to_apply:
            lines.append(f"turn on {len(to_apply)}")
        if to_revert:
            lines.append(f"undo {len(to_revert)}")
        bullets = [f"On: {t.name} → {t.gain}" + ("" if t.risk == "Safe" else f" ({t.risk})") for t in to_apply]
        bullets += [f"Undo: {t.name}" for t in to_revert]
        rp = bool(settings.get("restore_point_before_tweaks")) and is_admin() and bool(to_apply)
        text = ("A restore point is created first, so you can roll everything back." if rp else
                "Each original setting is saved, so you can undo any tweak later.")
        if not confirm(self, f"Apply changes: {' and '.join(lines)}?", text, "Apply changes", bullets=bullets):
            return
        self._restarts = {t.restart for t in to_apply + to_revert} - {""}

        def apply_now():
            self.run(_apply, to_apply, to_revert, False, progress=self.progress, text="Applying tweaks…",
                     cancellable=False, on_done=self._done,
                     summary=lambda r: f"{sum(1 for x in r if x[1])} change(s) applied")

        if not rp:
            apply_now()
            return

        def after_restore(res):
            ok, msg = res
            if ok:
                apply_now()
                return
            why = (msg or "unknown error").strip().splitlines()[-1] if (msg or "").strip() else "unknown error"
            if confirm(self, "Couldn't create a restore point",
                       f"Windows said: {why}. Each tweak still saves its original setting, so you can undo it here "
                       "later, but there won't be a System Restore snapshot.", "Apply without it"):
                apply_now()
            else:
                self.main.notify("info", "Nothing was changed", "Turn on System Protection for drive C: in Windows "
                                 "settings, then try again.")

        self.run(lambda ctx: maintenance.create_restore_point(), progress=self.progress,
                 text="Creating a restore point first (can take a minute)…", cancellable=False,
                 on_done=after_restore, summary=lambda r: "Restore point created" if r[0] else "Restore point failed")

    def _done(self, results):
        fails = [(n, m) for n, ok, m in results if not ok]
        done = [n for n, ok, _ in results if ok and n != "Restore point"]
        restarts = getattr(self, "_restarts", set())
        self.refresh()
        if fails:
            self.main.notify("warning", f"{len(done)} applied, {len(fails)} didn't work",
                             "Usually that means administrator rights are needed.",
                             action=("Show details", lambda: error(self, "Some tweaks failed",
                                                                   "These were left unchanged:", error_report(fails))))
        else:
            self.main.notify("success", f"{len(done)} tweak{'s' if len(done) != 1 else ''} updated",
                             "Untick any of them later to put the original setting back.")
        if "Restart Explorer" in restarts and confirm(self, "Restart Explorer",
                                                      "Some changes need Explorer to restart. Do it now? "
                                                      "(Your taskbar will flash briefly.)", "Restart Explorer"):
            tweaks.restart_explorer()
        others = restarts - {"Restart Explorer"}
        if others:
            self.main.notify("info", "Almost done", "Some changes take effect after you "
                             + " / ".join(o.lower() for o in sorted(others)) + ".")


# =========================================================================== Services
class ServicesPage(BasePage):
    title = "Services"
    subtitle = "Background Windows services. Notes flag the ones that are safe to trim and the ones to leave alone."

    def __init__(self, main):
        super().__init__(main)
        self.add_action("Refresh", "", self.refresh)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter services…")
        self.search.textChanged.connect(self._filter)
        self.view = QComboBox()
        self.view.addItems(["All services", "Running", "Has a note", "Third-party"])
        self.view.currentIndexChanged.connect(self._filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.view)
        self.root.addLayout(top)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tree = make_tree(["Service", "Status", "Startup", "Note", "Name"], stretch_col=3)
        size_widths(self.tree, {0: 280, 1: 90, 2: 100, 4: 150})
        self.tree.currentItemChanged.connect(self._describe)
        self.root.addWidget(self.tree, 1)
        self.desc = label("", "muted", wrap=True)
        self.root.addWidget(self.desc)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        for text, act in (("Start", "start"), ("Stop", "stop"), ("Restart", "restart")):
            bottom.addWidget(button(text, "", lambda _=False, a=act: self._control(a)))
        startbtn = button("Set startup type ▾", "primary")
        m = QMenu(startbtn)
        for text, val in (("Automatic", "automatic"), ("Manual", "manual"), ("Disabled", "disabled")):
            m.addAction(text, lambda v=val: self._start_type(v))
        startbtn.setMenu(m)
        bottom.addWidget(startbtn)
        self.root.addLayout(bottom)

    def on_first_show(self):
        if _windows_only(self):
            self.refresh()

    def refresh(self):
        self.run(lambda ctx: services.list_services(), progress=self.progress, text="Reading services…",
                 cancellable=False, on_done=self._populate, summary=lambda items: f"{len(items)} services listed")

    def refresh_action(self):
        self.refresh()

    def _populate(self, items):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for s in items:
            essential = services.ESSENTIAL.get(s["name"], "")
            note = f"🔒 Essential: {essential}" if essential else s["note"]
            r = SortItem([s["display"], s["status"].capitalize(), s["start"].capitalize(), note, s["name"]])
            r.setData(0, OBJ_ROLE, s)
            r.setForeground(1, QColor(theme.OK if s["status"] == "running" else theme.MUTED))
            if essential:
                r.setForeground(3, QColor(theme.MUTED))
                r.setToolTip(3, "Remedy won't stop or disable this service. " + essential + ".")
            elif s["note"]:
                r.setForeground(3, QColor(theme.WARN if "keep" in s["note"].lower() or "needed" in s["note"].lower()
                                          else theme.ACCENT))
            self.tree.addTopLevelItem(r)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(0, Qt.AscendingOrder)
        self._filter()

    def _filter(self, *_):
        text = self.search.text().lower()
        mode = self.view.currentIndex()
        for i in range(self.tree.topLevelItemCount()):
            r = self.tree.topLevelItem(i)
            s = r.data(0, OBJ_ROLE)
            ok = not text or text in s["display"].lower() or text in s["name"].lower()
            if mode == 1:
                ok = ok and s["status"] == "running"
            elif mode == 2:
                ok = ok and bool(s["note"])
            elif mode == 3:
                ok = ok and "\\windows\\" not in s["binpath"].lower()
            r.setHidden(not ok)

    def _describe(self, item, _prev):
        if item:
            s = item.data(0, OBJ_ROLE)
            self.desc.setText(s["description"] or s["binpath"])

    def _selected(self):
        return [r.data(0, OBJ_ROLE) for r in self.tree.selectedItems()]

    def _control(self, action):
        sel = self._selected()
        if not sel:
            self.main.notify("info", "Select a service first")
            return
        if self._blocked(sel, action):
            return
        if action != "start" and not confirm(self, f"{action.capitalize()} {len(sel)} service(s)?",
                                             "Apps that rely on them may stop working until they're started again.",
                                             action.capitalize(), bullets=[x["display"] for x in sel]):
            return
        self._batch(lambda s: services.control(s["name"], action))

    def _blocked(self, sel, action) -> bool:
        """Refuse to stop/disable essential services, and say why in plain words."""
        blocked = [(s["display"], services.protection(s["name"], action, s["start"])) for s in sel]
        blocked = [(n, why) for n, why in blocked if why]
        if blocked:
            name, why = blocked[0]
            more = f" (and {len(blocked) - 1} more)" if len(blocked) > 1 else ""
            self.main.notify("error", "Blocked to protect Windows", f"{name}{more}: {why}.")
        return bool(blocked)

    def _start_type(self, value):
        sel = self._selected()
        if not sel:
            return
        if self._blocked(sel, value):
            return
        if value == "disabled":
            vital = [s["display"] for s in sel if "keep" in s["note"].lower() or "needed" in s["note"].lower()]
            text = f"Disable {len(sel)} service(s)?"
            if vital:
                text += "\n\nHeads up, these are flagged as needed: " + ", ".join(vital)
            if not confirm(self, "Disable services", text, "Disable"):
                return
        self._batch(lambda s: services.set_start_type(s["name"], value))

    def _batch(self, fn):
        sel = self._selected()

        def work(ctx):
            errs = []
            for i, s in enumerate(sel):
                ctx.progress(f"{s['display']} ({i + 1} of {len(sel)})", int(i * 100 / max(1, len(sel))), force=True)
                try:
                    fn(s)
                except Exception as exc:  # noqa: BLE001
                    errs.append((s["display"], str(exc)))
            return errs

        def done(errs):
            ok = len(sel) - len(errs)
            if ok:
                self.main.notify("success", f"Updated {ok} service{'s' if ok != 1 else ''}")
            if errs:
                self.main.notify("warning", f"{len(errs)} service change(s) failed",
                                 "Most service changes need administrator rights.",
                                 action=("Show details", lambda: error(self, "Some changes failed", "",
                                                                       error_report(errs))))
            self.refresh()

        self.run(work, progress=self.progress, text="Applying…", cancellable=False, on_done=done)


# =========================================================================== Processes
_CRITICAL = {"system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
             "lsass.exe", "memory compression", "secure system", "fontdrvhost.exe", "dwm.exe"}

PRIORITIES = [("High", "HIGH_PRIORITY_CLASS", -10), ("Above normal", "ABOVE_NORMAL_PRIORITY_CLASS", -5),
              ("Normal", "NORMAL_PRIORITY_CLASS", 0), ("Below normal", "BELOW_NORMAL_PRIORITY_CLASS", 5),
              ("Low", "IDLE_PRIORITY_CLASS", 19)]


def _snapshot(_ctx):
    n = psutil.cpu_count() or 1
    rows = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_info", "exe", "cpu_percent"]):
        i = p.info
        if i["pid"] == 0:
            continue
        mem = i["memory_info"].rss if i["memory_info"] else 0
        rows.append((i["pid"], i["name"] or "?", (i["cpu_percent"] or 0.0) / n, mem,
                     (i["username"] or "").split("\\")[-1], i["exe"] or ""))
    return rows


class ProcessesPage(BasePage):
    title = "Processes"
    subtitle = "What's running right now, live. End runaway apps or change their priority."

    def __init__(self, main):
        super().__init__(main)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name…")
        self.search.textChanged.connect(self._filter)
        self.pause = QCheckBox("Pause updates")
        top.addWidget(self.search, 1)
        top.addWidget(self.pause)
        self.root.addLayout(top)
        self.tree = make_tree(["Name", "PID", "CPU", "Memory", "User", "Path"], stretch_col=5)
        size_widths(self.tree, {0: 240, 1: 70, 2: 70, 3: 100, 4: 120})
        self.tree.sortItems(3, Qt.DescendingOrder)  # biggest memory users first by default
        self.tree.customContextMenuRequested.connect(self._menu)
        self.root.addWidget(self.tree, 1)
        bottom = QHBoxLayout()
        self.summary = label("", "muted")
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(button("Open location", "", self._open))
        prio = button("Priority ▾")
        m = QMenu(prio)
        for text, cls, nice in PRIORITIES:
            m.addAction(text, lambda c=cls, n=nice: self._priority(c, n))
        prio.setMenu(m)
        bottom.addWidget(prio)
        bottom.addWidget(button("End process tree", "danger", lambda: self._kill(True)))
        bottom.addWidget(button("End task", "danger", lambda: self._kill(False)))
        self.root.addLayout(bottom)
        self.rows: dict = {}
        self.refreshing = False
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self.timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def refresh(self):
        if self.refreshing or self.pause.isChecked():
            return
        self.refreshing = True
        from .widgets import start_task
        start_task(_snapshot, on_done=self._apply, on_error=lambda _m: setattr(self, "refreshing", False))

    def _apply(self, rows):
        self.refreshing = False
        self.tree.setSortingEnabled(False)
        seen = set()
        for pid, name, cpu, mem, user, exe in rows:
            seen.add(pid)
            r = self.rows.get(pid)
            if r is None:
                r = SortItem([name, str(pid), "", "", user, exe])
                r.setData(1, UR, pid)
                r.setToolTip(5, exe)
                self.tree.addTopLevelItem(r)
                self.rows[pid] = r
            r.setText(2, f"{cpu:.1f}%")
            r.setData(2, UR, cpu)
            r.setText(3, human_size(mem))
            r.setData(3, UR, mem)
            r.setForeground(2, QColor(theme.DANGER if cpu > 50 else theme.WARN if cpu > 15 else theme.TEXT))
        for pid in list(self.rows):
            if pid not in seen:
                r = self.rows.pop(pid)
                idx = self.tree.indexOfTopLevelItem(r)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)
        self.tree.setSortingEnabled(True)  # re-sorts by the user's current column
        vm = psutil.virtual_memory()
        self.summary.setText(f"{len(rows)} processes · CPU {sum(r[2] for r in rows):.0f}% · "
                             f"RAM {human_size(vm.used)} / {human_size(vm.total)}")
        self._filter()

    def _filter(self, *_):
        text = self.search.text().lower()
        for r in self.rows.values():
            r.setHidden(bool(text) and text not in r.text(0).lower())

    def _selected(self):
        return [(int(r.text(1)), r.text(0)) for r in self.tree.selectedItems()]

    def _kill(self, tree):
        sel = self._selected()
        if not sel:
            return
        crit = [n for _p, n in sel if n.lower() in _CRITICAL]
        if crit:
            self.main.notify("error", "Blocked to protect Windows", "Ending " + ", ".join(crit) +
                             " would crash your PC.")
            return
        if not confirm(self, f"End {len(sel)} process{'es' if len(sel) != 1 else ''}?",
                       "Unsaved work in those apps will be lost.", "End now", danger=True,
                       bullets=[n for _p, n in sel]):
            return
        errs = []
        for pid, name in sel:
            try:
                p = psutil.Process(pid)
                victims = (p.children(recursive=True) if tree else []) + [p]
                for v in victims:
                    try:
                        v.kill()
                    except psutil.NoSuchProcess:
                        pass
                log.info("Ended process %s (%d)%s", name, pid, " and children" if tree else "")
            except Exception as exc:  # noqa: BLE001
                errs.append((name, str(exc)))
        if errs:
            error(self, "Could not end some processes", "Some processes need administrator rights.",
                  error_report(errs))
        QTimer.singleShot(400, self.refresh)

    def _priority(self, cls, nice):
        errs = []
        for pid, name in self._selected():
            try:
                value = getattr(psutil, cls) if IS_WINDOWS else nice
                psutil.Process(pid).nice(value)
            except Exception as exc:  # noqa: BLE001
                errs.append((name, str(exc)))
        if errs:
            error(self, "Could not change priority", "", error_report(errs))
        else:
            self.main.notify("success", "Priority updated", "Lasts until the app is closed.")

    def _open(self):
        sel = self.tree.selectedItems()
        if sel and sel[0].text(5):
            open_location(sel[0].text(5))

    def _menu(self, pos):
        if not self.tree.itemAt(pos):
            return
        m = QMenu(self)
        m.addAction("Open file location", self._open)
        pm = m.addMenu("Set priority")
        for text, cls, nice in PRIORITIES:
            pm.addAction(text, lambda c=cls, n=nice: self._priority(c, n))
        m.addSeparator()
        m.addAction("End task", lambda: self._kill(False))
        m.addAction("End process tree", lambda: self._kill(True))
        m.exec(self.tree.viewport().mapToGlobal(pos))
