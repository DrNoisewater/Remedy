# Remedy: PC optimization suite
**by Noisewater Labs** · Windows 10 & 11 · v1.1.0

Remedy cleans, speeds up, repairs and sets up Windows PCs from one dark, modern app.

## Run it

1. Install **Python 3.10+** from python.org (tick *Add Python to PATH*).
2. Double-click **`Run_Remedy.bat`**. It installs the 3 dependencies the first time, then launches Remedy.
3. For full power, click **Restart as admin** in the sidebar (system cleanup, tweaks, services and repairs need it).

Manual alternative: `pip install -r requirements.txt` then `python main.py`.

## Build a standalone Remedy.exe

Double-click **`Build_EXE.bat`**. It produces `dist\Remedy.exe`, a single file that runs on any Windows 10/11 PC
without Python and asks for administrator rights on launch.

## What's inside

| Section | Feature | What it does |
|---|---|---|
| Overview | **Dashboard** | Live CPU/RAM graphs, drive usage, and a one-click **health check** with a 0 to 100 score and a fix list |
| Clean up | **Junk Cleaner** | User/Windows temp, Windows Update & Delivery Optimization caches, error reports, crash dumps, thumbnail cache, old logs, Recycle Bin, Chrome/Edge/Brave/Vivaldi/Opera/Firefox caches, app caches (Discord, Slack, Teams, VS Code, Spotify, Steam), GPU shader caches, dev package caches |
| | **Large Files** | Finds the biggest files on any drive/folder; filter by type and age ("not touched in a year"); recycle or delete |
| | **Duplicates** | Content-verified duplicates (size → partial hash → full hash); auto-select keep newest/oldest/shortest path; never deletes every copy |
| | **Disk Analyzer** | Folder-size tree with share bars, breakdown by file type and top extensions; jump straight to Large Files/Duplicates for any folder |
| Optimize | **Startup Apps** | Registry Run keys, Startup folders and logon scheduled tasks; enable/disable using the same mechanism as Task Manager |
| | **Tweaks** | 27 reversible tweaks across Performance, Gaming, Privacy and Explorer: Ultimate Performance plan, Game Mode, Game DVR off, HAGS, mouse acceleration off, telemetry/ads/Bing/Copilot off, classic Win11 context menu, End task in taskbar, and more. Checkboxes show what is applied; tick or untick and press Apply changes. Original values are backed up and restored exactly when undone |
| | **Services** | All services with plain-English notes on which are safe to trim and which to keep; start/stop/startup type |
| | **Processes** | Live task list with CPU/memory, end task / process tree, priority; critical system processes are blocked |
| Apps | **New PC Setup** | 58 curated must-have apps in 10 categories, plus Essentials / Gamer / Developer / Creator bundles; silent batch install via winget with live output |
| | **Bloatware Remover** | Lists removable Store apps and flags known bloat (Candy Crush, News, Solitaire, Clipchamp, Cortana, TikTok…); remove for current or all users |
| | **Installed Programs** | Everything installed, sortable by size/date; launch uninstallers |
| System | **Maintenance** | Restore point, SFC, DISM repair, component cleanup, TRIM/defrag, drive SMART health, battery report, memory test, reliability history, DNS flush, network reset, ping test, check/update all apps (winget), Restart Explorer, rebuild icon cache |
| | **System Info** | CPU, GPU, RAM modules, motherboard, BIOS, disks + health, network, battery; copy it as a report |
| | **Settings & Log** | Safety options, excluded folders, privileges, About, and a live activity log |

## Safety by design

- **Recycle Bin by default** for anything you delete from file tools (switchable in Settings).
- **Protected paths:** Windows, Program Files, ProgramData\Microsoft, boot/recovery folders and system files
  (pagefile, hiberfil…) can never be deleted from the file tools.
- Junk cleaning only touches temp/cache locations, skips files in use, and leaves temp files younger than 24 hours.
- Symlinks/junctions are never followed; OneDrive cloud-only placeholders are skipped.
- Tweaks back up original values to `%LOCALAPPDATA%\Noisewater Labs\Remedy\tweak_backup.json` and can create a
  **restore point** first (on by default).
- Every action is logged to `%LOCALAPPDATA%\Noisewater Labs\Remedy\remedy.log`.

## Project layout

```
main.py                 entry point
remedy/app.py           main window, sidebar, toasts, startup sequence
remedy/theme.py         colors + stylesheet
remedy/core/            engine (no UI): scanner, junk, startup, programs, appstore,
                        services, tweaks, maintenance, sysinfo, registry, utils
remedy/ui/              splash (Nw tile), widgets, and the page modules
```
