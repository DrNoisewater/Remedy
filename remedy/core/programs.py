"""Installed programs, Microsoft Store (Appx) apps and the bloatware list."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from . import registry
from .utils import CREATE_NO_WINDOW, IS_WINDOWS, as_list, log, powershell, powershell_json, ps_quote

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
SOURCES = [("HKLM", UNINSTALL),
           ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
           ("HKCU", UNINSTALL)]


@dataclass
class Program:
    name: str
    version: str
    publisher: str
    install_date: str
    size: int
    uninstall: str
    quiet_uninstall: str
    location: str


def list_programs() -> list:
    if not IS_WINDOWS:
        return []
    seen: dict = {}
    for hive, base in SOURCES:
        for sub in registry.enum_subkeys(hive, base):
            vals = registry.read_values(hive, base + "\\" + sub)

            def g(k, default=""):
                return vals.get(k, (default, 0))[0]
            name = str(g("DisplayName")).strip()
            uninstall = str(g("UninstallString")).strip()
            if not name or not uninstall:
                continue
            if g("SystemComponent", 0) == 1 or g("ParentKeyName") or \
                    str(g("ReleaseType")) in ("Update", "Hotfix", "Security Update"):
                continue
            size_kb = g("EstimatedSize", 0)
            date = str(g("InstallDate"))
            if re.fullmatch(r"\d{8}", date):
                date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            prog = Program(name, str(g("DisplayVersion")), str(g("Publisher")), date,
                           int(size_kb) * 1024 if isinstance(size_kb, int) else 0,
                           uninstall, str(g("QuietUninstallString")).strip(), str(g("InstallLocation")).strip())
            seen.setdefault((name.lower(), prog.version), prog)
    return sorted(seen.values(), key=lambda p: p.name.lower())


def uninstall_program(prog: Program, quiet: bool = False) -> None:
    cmd = prog.quiet_uninstall if quiet and prog.quiet_uninstall else prog.uninstall
    if re.match(r"(?i)^\s*\"?msiexec(\.exe)?\"?\s+/i", cmd):
        cmd = re.sub(r"(?i)/i", "/X", cmd, count=1)
    log.info("Launching uninstaller for %s: %s", prog.name, cmd)
    subprocess.Popen(cmd, shell=True, creationflags=CREATE_NO_WINDOW)


# --------------------------------------------------------------------------- Store apps / bloatware
# name -> (friendly label, recommended for removal)
KNOWN_BLOAT = {
    "Microsoft.BingNews": ("Microsoft News", True),
    "Microsoft.BingWeather": ("MSN Weather", True),
    "Microsoft.BingSports": ("MSN Sports", True),
    "Microsoft.BingFinance": ("MSN Money", True),
    "Microsoft.BingSearch": ("Bing Search", True),
    "Microsoft.GetHelp": ("Get Help", True),
    "Microsoft.Getstarted": ("Tips", True),
    "Microsoft.MicrosoftSolitaireCollection": ("Solitaire Collection (ads)", True),
    "Microsoft.People": ("People", True),
    "Microsoft.MicrosoftOfficeHub": ("Office / Microsoft 365 hub", True),
    "Microsoft.SkypeApp": ("Skype", True),
    "Microsoft.WindowsFeedbackHub": ("Feedback Hub", True),
    "Microsoft.ZuneVideo": ("Movies & TV", True),
    "Microsoft.Microsoft3DViewer": ("3D Viewer", True),
    "Microsoft.MixedReality.Portal": ("Mixed Reality Portal", True),
    "Microsoft.549981C3F5F10": ("Cortana", True),
    "Clipchamp.Clipchamp": ("Clipchamp", True),
    "Microsoft.PowerAutomateDesktop": ("Power Automate", True),
    "Microsoft.WindowsMaps": ("Maps", True),
    "Microsoft.Messaging": ("Messaging", True),
    "Microsoft.OneConnect": ("Mobile Plans", True),
    "Microsoft.Wallet": ("Wallet", True),
    "Microsoft.Windows.DevHome": ("Dev Home", True),
    "MicrosoftCorporationII.MicrosoftFamily": ("Family Safety", True),
    "king.com.CandyCrushSaga": ("Candy Crush Saga", True),
    "king.com.CandyCrushSodaSaga": ("Candy Crush Soda Saga", True),
    "king.com.BubbleWitch3Saga": ("Bubble Witch 3", True),
    "Disney.37853FC22B2CE": ("Disney+", True),
    "SpotifyAB.SpotifyMusic": ("Spotify (preinstalled)", False),
    "Facebook.Facebook": ("Facebook", True),
    "Facebook.InstagramBeta": ("Instagram", True),
    "BytedancePte.Ltd.TikTok": ("TikTok", True),
    "AmazonVideo.PrimeVideo": ("Prime Video", True),
    "Microsoft.Todos": ("Microsoft To Do", False),
    "Microsoft.YourPhone": ("Phone Link", False),
    "Microsoft.OutlookForWindows": ("New Outlook", False),
    "MSTeams": ("Teams (personal)", False),
    "MicrosoftTeams": ("Teams (personal)", False),
    "Microsoft.GamingApp": ("Xbox app (keep for Game Pass)", False),
    "Microsoft.XboxGamingOverlay": ("Xbox Game Bar (keep for game capture)", False),
    "Microsoft.ZuneMusic": ("Media Player", False),
    "Microsoft.WindowsAlarms": ("Clock", False),
    "Microsoft.WindowsSoundRecorder": ("Sound Recorder", False),
}


def list_appx() -> list:
    if not IS_WINDOWS:
        return []
    script = ("Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.SignatureKind -ne 'System' "
              "-and -not $_.NonRemovable } | Select-Object Name, PackageFullName, Version | "
              "ConvertTo-Json -Depth 2 -Compress")
    apps = []
    for a in as_list(powershell_json(script, timeout=90)):
        if not isinstance(a, dict) or not a.get("Name"):
            continue
        label, rec = KNOWN_BLOAT.get(a["Name"], ("", False))
        apps.append({"name": a["Name"], "full_name": a.get("PackageFullName", ""), "version": str(a.get("Version", "")),
                     "label": label, "known": bool(label), "recommended": rec})
    apps.sort(key=lambda x: (not x["recommended"], not x["known"], x["name"].lower()))
    return apps


def remove_appx(full_name: str, all_users: bool = False) -> str:
    flag = " -AllUsers" if all_users else ""
    res = powershell(f"Remove-AppxPackage -Package {ps_quote(full_name)}{flag} -ErrorAction Stop", timeout=180)
    if not res.ok:
        raise RuntimeError(res.output or "Removal failed")
    log.info("Removed Store app %s", full_name)
    return res.output
