"""Security Center: Defender/firewall status, Defender's own detections, and a startup-persistence review."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QTabWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .. import theme
from ..core import diagnostics as D
from ..core import security, startup
from ..core.utils import IS_WINDOWS, is_admin, open_location, open_target, run_stream
from .widgets import BasePage, Card, ProgressRow, attach_empty, button, confirm, label, make_tree, size_widths

LEVEL_COLOR = {"provider": theme.DANGER, "review": theme.WARN, "broken": theme.INFO, "none": theme.MUTED}


class StatusTile(Card):
    def __init__(self, title):
        super().__init__(title)
        self.value = label("—", "h2")
        self.sub = label("Not checked yet", "muted", wrap=True)
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self.body.addStretch(1)

    def set(self, value, sub, color=None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.sub.setText(sub)


class SecurityPage(BasePage):
    title = "Security Center"
    subtitle = ("Protection status, Microsoft Defender's own detections, and a review of what starts with Windows. "
                "Remedy points out leads; only your antivirus gives verdicts.")

    def __init__(self, main):
        super().__init__(main)
        self.ev = {}
        self.entries = []
        self.add_action("Open Windows Security", "", lambda: open_target("windowsdefender:"),
                        "Microsoft's own app: protection history, scans, firewall")
        self.btn_refresh = self.add_action("Check now", "primary", self.refresh, "Read-only status check")
        grid = QGridLayout()
        grid.setSpacing(12)
        self.t_av = StatusTile("Antivirus")
        self.t_sig = StatusTile("Virus definitions")
        self.t_scan = StatusTile("Last quick scan")
        self.t_fw = StatusTile("Firewall")
        for i, t in enumerate((self.t_av, self.t_sig, self.t_scan, self.t_fw)):
            grid.addWidget(t, 0, i)
        self.root.addLayout(grid)
        self.progress = ProgressRow()
        self.root.addWidget(self.progress)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._detections(), "Defender detections")
        self.tabs.addTab(self._persistence(), "What starts with Windows")
        self.root.addWidget(self.tabs, 1)
        self.root.addWidget(label("Privacy: Remedy never uploads files or hashes, and runs no online reputation "
                                  "lookups. Scans use Microsoft Defender on this PC.", "faint", wrap=True))

    def _detections(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.addWidget(label("These come straight from Microsoft Defender's history (Get-MpThreat). Handle them in "
                            "Windows Security > Protection history, which shows Defender's recommended action.",
                            "muted", wrap=True))
        self.det_tree = make_tree(["Threat (named by Defender)", "Status", "Source"], multi=False, stretch_col=0)
        size_widths(self.det_tree, {1: 200, 2: 200})
        self.det_empty = attach_empty(self.det_tree, "Press Check now to read Defender's history.")
        lay.addWidget(self.det_tree, 1)
        return w

    def _persistence(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.addWidget(label("Run keys, Startup folders and logon tasks, with each file's digital signature. "
                            "“Needs review” is a lead (e.g. unsigned and running from AppData), not a malware verdict. "
                            "To switch an entry off, use Startup Apps (reversible).", "muted", wrap=True))
        self.p_tree = make_tree(["Entry", "Assessment", "Signature", "Type", "File"], multi=False, stretch_col=4)
        size_widths(self.p_tree, {0: 200, 1: 160, 2: 120, 3: 110})
        self.p_tree.currentItemChanged.connect(self._p_picked)
        self.p_empty = attach_empty(self.p_tree, "Press Check now to review startup entries.")
        lay.addWidget(self.p_tree, 1)
        self.p_detail = label("", "muted", wrap=True)
        lay.addWidget(self.p_detail)
        row = QHBoxLayout()
        row.addWidget(button("Open file location", "", self._open))
        row.addWidget(button("Copy SHA-256", "", self._hash, "Computed on this PC; nothing is sent anywhere"))
        self.btn_scan = button("Scan file with Defender…", "", self._scan)
        row.addWidget(self.btn_scan)
        row.addStretch(1)
        row.addWidget(button("Disable in Startup Apps", "", lambda: self.main.navigate("startup")))
        lay.addLayout(row)
        return w

    def on_first_show(self):
        if IS_WINDOWS:
            self.refresh()
        else:
            for t in (self.t_av, self.t_sig, self.t_scan, self.t_fw):
                t.set("Not assessed", "Windows Security can only be read on Windows.", theme.FAINT)
            self.det_empty.setText("Not assessed: Defender's history can only be read on Windows.")
            self.p_empty.setText("Not assessed: startup entries can only be read on Windows.")

    def refresh_action(self):
        self.refresh()

    def set_busy_ui(self, busy):
        self.btn_refresh.setEnabled(not busy)
        self.btn_scan.setEnabled(not busy)

    def refresh(self):
        if not IS_WINDOWS:
            return

        def work(ctx):
            ctx.progress("Reading Windows Security status…", 10, force=True)
            ev, st = D.run_providers(ctx, ["security"], {})
            ctx.progress("Reading startup entries…", 50, force=True)
            items = startup.list_startup_items(include_tasks=True)
            ctx.progress("Checking digital signatures…", 70, force=True)
            sigs = security.signatures([i.target_path for i in items])
            return ev, st["security"], items, sigs

        self.run(work, progress=self.progress, text="Checking security status…", on_done=self._show,
                 summary=lambda r: f"{len(r[2])} startup entries reviewed")

    def _show(self, res):
        ev, st, items, sigs = res
        self.ev = ev
        v = lambda k: ev[k].value if k in ev else None  # noqa: E731
        if st.status != "complete":
            for t in (self.t_av, self.t_sig, self.t_scan, self.t_fw):
                t.set("Not assessed", st.message, theme.FAINT)
        others = [n for n in (v("security.av_products") or []) if "defender" not in n.lower()]
        rt = v("security.defender_realtime")
        if rt:
            self.t_av.set("Protected", "Microsoft Defender real-time protection is on", theme.OK)
        elif others:
            self.t_av.set("Third-party", f"{', '.join(others)} is registered and on. Defender steps back.", theme.INFO)
        elif rt is False:
            self.t_av.set("Off", "No active antivirus found. Turn protection on in Windows Security.", theme.DANGER)
        else:
            self.t_av.set("Unknown", str(v("security.defender_error") or "Defender status not available"), theme.FAINT)
        age = v("security.defender_sig_age")
        if age is not None and rt is not False:
            self.t_sig.set(f"{age} day{'s' if age != 1 else ''} old", "Up to date" if age < D.THRESHOLDS["sig_age_days"]
                           else "Update from Diagnose & Repair or Windows Security",
                           theme.OK if age < D.THRESHOLDS["sig_age_days"] else theme.WARN)
        else:
            self.t_sig.set("Not assessed", "Managed by the active antivirus" if others else "Not available", theme.FAINT)
        q = v("security.defender_quick_age")
        if q is not None and rt is not False:
            self.t_scan.set(f"{q} day{'s' if q != 1 else ''} ago", "Defender scans on its own schedule",
                            theme.OK if q <= D.THRESHOLDS["quick_scan_days"] else theme.WARN)
        else:
            self.t_scan.set("Not assessed", "Not available", theme.FAINT)
        off = v("security.firewall_off")
        if off is None:
            self.t_fw.set("Not assessed", "Firewall status not available", theme.FAINT)
        elif off:
            self.t_fw.set("Partly off", "Off for: " + ", ".join(off), theme.WARN)
        else:
            self.t_fw.set("On", "All profiles (domain, private, public)", theme.OK)

        self.det_tree.clear()
        active = set(v("security.threats_active") or [])
        hist = v("security.threats_history")
        for name in hist or []:
            it = QTreeWidgetItem([name, "Active: Defender says act now" if name in active else "Handled / in history",
                                  "Confirmed by provider (Defender)"])
            it.setForeground(1, QColor(theme.DANGER if name in active else theme.MUTED))
            self.det_tree.addTopLevelItem(it)
        self.det_empty.setText("Defender's history is empty: no detections recorded." if hist is not None else
                               "Defender's history couldn't be read (needs administrator, or another antivirus is "
                               "in charge).")

        self.p_tree.clear()
        self.entries = items
        counts = {}
        for item in items:
            path = item.target_path
            status, signer = sigs.get(path, ("Unknown", ""))
            level, why = security.assess_entry(path, status, signer)
            counts[level] = counts.get(level, 0) + 1
            row = QTreeWidgetItem([item.name, security.LEVELS[level], status or "Unknown", item.kind, path])
            row.setForeground(1, QColor(LEVEL_COLOR[level]))
            row.setData(0, Qt.UserRole, (item, path, why, signer))
            row.setToolTip(1, why)
            self.p_tree.addTopLevelItem(row)
        self.p_empty.setText("No startup entries found.")
        rv = counts.get("review", 0)
        if rv:
            self.main.notify("info", f"{rv} startup entr{'ies' if rv != 1 else 'y'} worth a look",
                             "These are leads, not malware verdicts. Select one to see why.")

    def _current(self):
        it = self.p_tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _p_picked(self, item, _prev):
        d = item.data(0, Qt.UserRole) if item else None
        if not d:
            self.p_detail.setText("")
            return
        entry, path, why, signer = d
        self.p_detail.setText(f"{why}\nCommand: {entry.command}\nScope: {entry.scope} · "
                              f"{'Enabled' if entry.enabled else 'Disabled'}" + (f"\nSigner: {signer}" if signer else ""))

    def _open(self):
        d = self._current()
        if d and d[1] and os.path.exists(d[1]):
            open_location(d[1])
        elif d:
            self.main.notify("info", "File not found", "This entry points at a file that doesn't exist.")

    def _hash(self):
        d = self._current()
        if not d or not d[1] or not os.path.isfile(d[1]):
            self.main.notify("info", "Pick an entry whose file exists")
            return
        try:
            digest = security.sha256(d[1])
        except (OSError, ValueError) as exc:
            self.main.notify("warning", "Couldn't hash the file", str(exc))
            return
        QGuiApplication.clipboard().setText(digest)
        self.main.notify("success", "SHA-256 copied", digest[:32] + "…  (computed locally)")

    def _scan(self):
        d = self._current()
        if not d or not d[1] or not os.path.isfile(d[1]):
            self.main.notify("info", "Pick an entry whose file exists")
            return
        if not is_admin():
            self.main.notify("warning", "Needs administrator", "Use Restart as admin in the sidebar to scan files.")
            return
        path = d[1]
        if not confirm(self, "Scan this file with Microsoft Defender?",
                       f"{path}\n\nIf Defender finds a threat it handles it according to its own settings (usually "
                       "quarantine, which can be restored from Windows Security).", "Scan with Defender"):
            return
        lines = []

        def work(ctx):
            code = run_stream(security.defender_scan_args(path), lambda ln: lines.append(ln), ctx.cancelled)
            return code

        def done(code):
            found = any("found" in ln.lower() and "no threats" not in ln.lower() for ln in lines)
            if code == 0 and not found:
                self.main.notify("success", "Defender found no threat in this file", os.path.basename(path))
            elif code == 2 or found:
                self.main.notify("error", "Defender reported a threat", "Open Windows Security > Protection history.",
                                 action=("Open Windows Security", lambda: open_target("windowsdefender:")))
            else:
                self.main.notify("warning", f"Defender scan ended with code {code}", "\n".join(lines[-3:])[:200])

        self.run(work, progress=self.progress, text=f"Defender is scanning {os.path.basename(path)}…", on_done=done,
                 summary=lambda c: "Scan finished")
