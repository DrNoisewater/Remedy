"""Startup apps: registry Run keys, Startup folders and logon scheduled tasks.

Enable/disable uses the same StartupApproved mechanism as Task Manager, so changes are
visible (and reversible) in Task Manager and Settings > Apps > Startup too.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from . import registry
from .utils import IS_WINDOWS, as_list, delete_paths, log, powershell, powershell_json, ps_quote

APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
RUN_SOURCES = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run", "Run", "Current user"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run", "Run", "All users"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32", "All users (32-bit)"),
]


@dataclass
class StartupItem:
    name: str
    command: str
    kind: str          # Registry | Startup folder | Scheduled task
    scope: str
    enabled: bool
    hive: str = ""
    key: str = ""
    approved_key: str = ""
    approved_name: str = ""
    file_path: str = ""
    task_path: str = ""

    @property
    def needs_admin(self) -> bool:
        return self.hive == "HKLM" or self.kind == "Scheduled task"

    @property
    def target_path(self) -> str:
        if self.kind == "Startup folder":
            return self.file_path
        return exe_from_command(self.command)


def exe_from_command(cmd: str) -> str:
    cmd = os.path.expandvars((cmd or "").strip())
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 0 else cmd[1:]
    m = re.match(r"(.+?\.(?:exe|bat|cmd|com|lnk|vbs|ps1))(?:\s|$)", cmd, re.IGNORECASE)
    return m.group(1) if m else cmd.split(" ")[0]


def _approved_enabled(hive: str, sub: str, name: str) -> bool:
    found = registry.get_value(hive, APPROVED + "\\" + sub, name)
    if not found:
        return True
    data = found[0]
    if isinstance(data, (bytes, bytearray)) and data:
        return not (data[0] & 1)
    return True


def _filetime_now() -> bytes:
    return int((time.time() + 11644473600) * 10_000_000).to_bytes(8, "little")


def list_startup_items(include_tasks: bool = True) -> list:
    items: list = []
    if not IS_WINDOWS:
        return items
    for hive, key, sub, scope in RUN_SOURCES:
        for name, (value, _typ) in registry.read_values(hive, key).items():
            if not name:
                continue
            items.append(StartupItem(name, str(value), "Registry", scope,
                                     _approved_enabled(hive, sub, name), hive=hive, key=key,
                                     approved_key=APPROVED + "\\" + sub, approved_name=name))
    folders = [(os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
                "HKCU", "Current user"),
               (os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                             r"Microsoft\Windows\Start Menu\Programs\StartUp"), "HKLM", "All users")]
    for folder, hive, scope in folders:
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower() != "desktop.ini":
                items.append(StartupItem(os.path.splitext(entry.name)[0], entry.path, "Startup folder", scope,
                                         _approved_enabled(hive, "StartupFolder", entry.name), hive=hive,
                                         approved_key=APPROVED + r"\StartupFolder", approved_name=entry.name,
                                         file_path=entry.path))
    if include_tasks:
        items.extend(_list_tasks())
    return items


def _list_tasks() -> list:
    script = (
        "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft\\*' -and "
        "($_.Triggers | Where-Object { $_.CimClass.CimClassName -in 'MSFT_TaskLogonTrigger','MSFT_TaskBootTrigger' }) } | "
        "ForEach-Object { [pscustomobject]@{ Name=$_.TaskName; Path=$_.TaskPath; State=[string]$_.State; "
        "Action=(($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\".Trim() }) -join '; ') } } | "
        "ConvertTo-Json -Depth 3 -Compress")
    items = []
    for t in as_list(powershell_json(script, timeout=60)):
        if not isinstance(t, dict) or not t.get("Name"):
            continue
        items.append(StartupItem(t["Name"], t.get("Action") or "", "Scheduled task", "Logon task",
                                 str(t.get("State")) != "Disabled", task_path=t.get("Path") or "\\"))
    return items


def set_enabled(item: StartupItem, enabled: bool) -> None:
    if item.kind == "Scheduled task":
        verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
        res = powershell(f"{verb} -TaskName {ps_quote(item.name)} -TaskPath {ps_quote(item.task_path)} | Out-Null",
                         timeout=60)
        if not res.ok:
            raise RuntimeError(res.output or "Task Scheduler refused the change (try running as administrator)")
    else:
        data = (b"\x02" + b"\x00" * 11) if enabled else (b"\x03\x00\x00\x00" + _filetime_now())
        registry.set_value(item.hive, item.approved_key, item.approved_name, registry.REG_BINARY, data)
    item.enabled = enabled
    log.info("%s startup item: %s", "Enabled" if enabled else "Disabled", item.name)


def delete_item(item: StartupItem) -> None:
    if item.kind == "Registry":
        registry.delete_value(item.hive, item.key, item.name)
    elif item.kind == "Startup folder":
        res = delete_paths([item.file_path], use_recycle_bin=True)
        if res["errors"]:
            raise RuntimeError(res["errors"][0][1])
    else:
        res = powershell(f"Unregister-ScheduledTask -TaskName {ps_quote(item.name)} "
                         f"-TaskPath {ps_quote(item.task_path)} -Confirm:$false", timeout=60)
        if not res.ok:
            raise RuntimeError(res.output or "Could not remove task")
    if item.approved_key:
        try:
            registry.delete_value(item.hive, item.approved_key, item.approved_name)
        except OSError:
            pass
    log.info("Removed startup entry: %s (%s)", item.name, item.command)
