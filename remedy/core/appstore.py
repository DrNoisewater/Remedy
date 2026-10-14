"""New PC setup: a curated catalog of must-have apps installed silently through winget."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from .utils import IS_WINDOWS, log, run_command


@dataclass(frozen=True)
class CatalogApp:
    winget_id: str
    name: str
    category: str
    description: str


CATALOG = [
    # Browsers
    CatalogApp("Google.Chrome", "Google Chrome", "Browsers", "Fast, popular browser with sync"),
    CatalogApp("Mozilla.Firefox", "Firefox", "Browsers", "Privacy-focused open-source browser"),
    CatalogApp("Brave.Brave", "Brave", "Browsers", "Chromium browser with built-in ad blocking"),
    CatalogApp("Vivaldi.Vivaldi", "Vivaldi", "Browsers", "Highly customizable power-user browser"),
    # Communication
    CatalogApp("Discord.Discord", "Discord", "Communication", "Voice, video and text chat"),
    CatalogApp("Zoom.Zoom", "Zoom", "Communication", "Video meetings"),
    CatalogApp("SlackTechnologies.Slack", "Slack", "Communication", "Team messaging"),
    CatalogApp("Telegram.TelegramDesktop", "Telegram", "Communication", "Cloud messaging"),
    CatalogApp("OpenWhisperSystems.Signal", "Signal", "Communication", "Encrypted messaging"),
    # Media
    CatalogApp("VideoLAN.VLC", "VLC media player", "Media", "Plays practically every video/audio format"),
    CatalogApp("Spotify.Spotify", "Spotify", "Media", "Music streaming"),
    CatalogApp("OBSProject.OBSStudio", "OBS Studio", "Media", "Screen recording and live streaming"),
    CatalogApp("Audacity.Audacity", "Audacity", "Media", "Audio editor"),
    CatalogApp("HandBrake.HandBrake", "HandBrake", "Media", "Video converter / compressor"),
    CatalogApp("GIMP.GIMP", "GIMP", "Media", "Image editor"),
    CatalogApp("KDE.Krita", "Krita", "Media", "Digital painting"),
    CatalogApp("ShareX.ShareX", "ShareX", "Media", "Screenshots, GIFs and screen recording"),
    # Productivity
    CatalogApp("TheDocumentFoundation.LibreOffice", "LibreOffice", "Productivity", "Free office suite"),
    CatalogApp("SumatraPDF.SumatraPDF", "SumatraPDF", "Productivity", "Tiny, fast PDF/eBook reader"),
    CatalogApp("Adobe.Acrobat.Reader.64-bit", "Adobe Acrobat Reader", "Productivity", "PDF reader with form filling"),
    CatalogApp("Obsidian.Obsidian", "Obsidian", "Productivity", "Markdown notes and knowledge base"),
    CatalogApp("Notion.Notion", "Notion", "Productivity", "Notes, docs and projects"),
    # Utilities
    CatalogApp("7zip.7zip", "7-Zip", "Utilities", "Open ZIP, RAR, 7z and more"),
    CatalogApp("voidtools.Everything", "Everything", "Utilities", "Instant file search across all drives"),
    CatalogApp("Microsoft.PowerToys", "PowerToys", "Utilities", "FancyZones, PowerRename, color picker…"),
    CatalogApp("Notepad++.Notepad++", "Notepad++", "Utilities", "Better text editor"),
    CatalogApp("Bitwarden.Bitwarden", "Bitwarden", "Utilities", "Open-source password manager"),
    CatalogApp("KeePassXCTeam.KeePassXC", "KeePassXC", "Utilities", "Offline password manager"),
    CatalogApp("qBittorrent.qBittorrent", "qBittorrent", "Utilities", "Ad-free torrent client"),
    CatalogApp("Rufus.Rufus", "Rufus", "Utilities", "Create bootable USB drives"),
    CatalogApp("WinDirStat.WinDirStat", "WinDirStat", "Utilities", "Visual disk usage map"),
    CatalogApp("Flow-Launcher.Flow-Launcher", "Flow Launcher", "Utilities", "Keyboard launcher (Spotlight for Windows)"),
    CatalogApp("Malwarebytes.Malwarebytes", "Malwarebytes", "Utilities", "Second-opinion malware scanner"),
    # System monitoring
    CatalogApp("REALiX.HWiNFO", "HWiNFO", "Hardware tools", "Detailed sensors: temps, clocks, voltages"),
    CatalogApp("CPUID.CPU-Z", "CPU-Z", "Hardware tools", "CPU, RAM and motherboard details"),
    CatalogApp("TechPowerUp.GPU-Z", "GPU-Z", "Hardware tools", "Graphics card details"),
    CatalogApp("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo", "Hardware tools", "SSD/HDD health (SMART)"),
    CatalogApp("CrystalDewWorld.CrystalDiskMark", "CrystalDiskMark", "Hardware tools", "Drive speed benchmark"),
    CatalogApp("Guru3D.Afterburner", "MSI Afterburner", "Hardware tools", "GPU tuning and in-game overlay"),
    CatalogApp("Microsoft.Sysinternals.Autoruns", "Autoruns", "Hardware tools", "Every auto-starting item on Windows"),
    # Gaming
    CatalogApp("Valve.Steam", "Steam", "Gaming", "PC game store and launcher"),
    CatalogApp("EpicGames.EpicGamesLauncher", "Epic Games Launcher", "Gaming", "Epic store + free weekly games"),
    CatalogApp("GOG.Galaxy", "GOG Galaxy", "Gaming", "DRM-free games launcher"),
    CatalogApp("ElectronicArts.EADesktop", "EA app", "Gaming", "EA games launcher"),
    CatalogApp("Ubisoft.Connect", "Ubisoft Connect", "Gaming", "Ubisoft games launcher"),
    CatalogApp("Playnite.Playnite", "Playnite", "Gaming", "One library for every launcher"),
    # Developer
    CatalogApp("Microsoft.VisualStudioCode", "Visual Studio Code", "Developer", "Code editor"),
    CatalogApp("Git.Git", "Git", "Developer", "Version control"),
    CatalogApp("Python.Python.3.13", "Python 3.13", "Developer", "Python runtime"),
    CatalogApp("OpenJS.NodeJS.LTS", "Node.js LTS", "Developer", "JavaScript runtime"),
    CatalogApp("Microsoft.WindowsTerminal", "Windows Terminal", "Developer", "Modern tabbed terminal"),
    CatalogApp("Microsoft.PowerShell", "PowerShell 7", "Developer", "Latest cross-platform PowerShell"),
    CatalogApp("Docker.DockerDesktop", "Docker Desktop", "Developer", "Containers"),
    CatalogApp("Postman.Postman", "Postman", "Developer", "API testing"),
    CatalogApp("WinSCP.WinSCP", "WinSCP", "Developer", "SFTP/FTP client"),
    # Runtimes
    CatalogApp("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributable (x64)", "Runtimes", "Needed by most games and apps"),
    CatalogApp("Microsoft.DotNet.DesktopRuntime.8", ".NET 8 Desktop Runtime", "Runtimes", "Needed by many modern Windows apps"),
    CatalogApp("Oracle.JavaRuntimeEnvironment", "Java Runtime", "Runtimes", "For Java apps (e.g. Minecraft mods tools)"),
]

PRESETS = {
    "Essentials": ["Google.Chrome", "7zip.7zip", "VideoLAN.VLC", "voidtools.Everything", "Microsoft.PowerToys",
                   "Notepad++.Notepad++", "SumatraPDF.SumatraPDF", "Bitwarden.Bitwarden",
                   "Microsoft.VCRedist.2015+.x64", "Microsoft.DotNet.DesktopRuntime.8"],
    "Gamer": ["Valve.Steam", "EpicGames.EpicGamesLauncher", "Discord.Discord", "Guru3D.Afterburner",
              "REALiX.HWiNFO", "OBSProject.OBSStudio", "Microsoft.VCRedist.2015+.x64"],
    "Developer": ["Microsoft.VisualStudioCode", "Git.Git", "Python.Python.3.13", "OpenJS.NodeJS.LTS",
                  "Microsoft.WindowsTerminal", "Microsoft.PowerShell", "Postman.Postman"],
    "Creator": ["OBSProject.OBSStudio", "GIMP.GIMP", "KDE.Krita", "Audacity.Audacity", "HandBrake.HandBrake",
                "ShareX.ShareX"],
}


def winget_available() -> bool:
    return IS_WINDOWS and run_command(["winget", "--version"], timeout=30).ok


def installed_ids() -> set:
    """Package IDs winget knows are installed (via `winget export`)."""
    if not IS_WINDOWS:
        return set()
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        run_command(["winget", "export", "-o", path, "--accept-source-agreements",
                     "--disable-interactivity"], timeout=180)
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        ids = set()
        for src in data.get("Sources", []):
            for pkg in src.get("Packages", []):
                ids.add(str(pkg.get("PackageIdentifier", "")).lower())
        return ids
    except (OSError, ValueError):
        return set()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def install_args(winget_id: str) -> list:
    return ["winget", "install", "--id", winget_id, "--exact", "--silent", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]


# winget exit codes that still mean "fine"
OK_CODES = {0, -1978335189, -1978335135}  # 0x8A15002B / 0x8A150061: already installed / up to date


def normalize_code(code: int) -> int:
    """Windows exit codes come back unsigned; winget documents them as signed HRESULTs."""
    return code - (1 << 32) if code > 0x7FFFFFFF else code


def describe_exit(code: int) -> str:
    code = normalize_code(code)
    known = {-1978335189: "already installed", -1978335135: "already installed",
             -1978335212: "package not found", -1978335216: "no applicable installer",
             -1978335215: "installer failed"}
    return known.get(code, f"exit code {code}")


def log_install(name: str, ok: bool, detail: str) -> None:
    log.info("winget %s: %s (%s)", "installed" if ok else "failed", name, detail)
