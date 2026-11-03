# Remedy: PC optimization suite
**by Noisewater Labs** · Windows 10 & 11 · v1.3.1

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

Remedy detects your Windows (10 or 11, release, edition) and whether it's a laptop or desktop, and tailors
itself: tweaks for another Windows version are greyed out with the reason, battery-hungry tweaks are never
pre-selected on laptops, and the health check and One-click optimize only offer what fits this PC.

| Section | Feature | What it does |
|---|---|---|
| Technician | **Diagnose & Repair** | Quick / Full / Custom read-only scans (performance sampling, storage, startup, Windows, security, drive health, network). Each finding shows severity, how sure Remedy is, the evidence (value, source, time window) and next steps. Approved repairs run from a reviewed Repair Plan and are re-measured to prove they worked |
| | **New PC Setup** | Reusable profiles (Everyday, Gaming, Business, Laptop, Creator, your own): inspect the PC, configure, review a dry-run plan, deploy apps and settings with a resumable journal, verify, and save a client handoff report |
| | **Security Center** | Defender, firewall and antivirus status, Defender's own detections, and a review of everything that starts with Windows (signatures, lead-only notes; nothing is quarantined by Remedy) |
| | **Sessions & Reports** | Every diagnosis and setup saved on this PC: resume, review, export HTML reports or anonymized JSON |
| Overview | **Dashboard** | Live CPU/RAM graphs, drive usage, detected Windows version, and a **health check** with a 0 to 100 score and a fix list tailored to this PC (junk, disk space, startup apps, tweaks not yet on, bloatware, battery) |
| | **One-click optimize** | Restore point → junk clean → recommended tweaks for this Windows → (optional) bloat removal → DNS refresh, with a live checklist, per-step progress and a summary |
| Clean up | **Junk Cleaner** | User/Windows temp, Windows Update & Delivery Optimization caches, error reports, crash dumps, thumbnail cache, old logs, Recycle Bin, Chrome/Edge/Brave/Vivaldi/Opera/Firefox and Windows web caches, app caches (Discord, Slack, Teams, VS Code, Spotify, Steam), GPU shader caches, dev package caches, recent-files list |
| | **Large Files** | Finds the biggest files on any drive/folder; filter by type and age ("not touched in a year"); recycle or delete |
| | **Duplicates** | Content-verified duplicates (size → partial hash → full hash); auto-select keep newest/oldest/shortest path; never deletes every copy |
| | **Disk Analyzer** | Folder-size tree with share bars, breakdown by file type and top extensions; jump straight to Large Files/Duplicates for any folder |
| Optimize | **Startup Apps** | Registry Run keys, Startup folders and logon scheduled tasks; enable/disable using the same mechanism as Task Manager |
| | **Tweaks** | 58 reversible tweaks in six groups: **Windows 11** (classic right-click menu, End task, no Start recommendations or account nags, hide Widgets/Chat, taskbar left, never combine, more pins, no snap flyout, Spotlight icon, clock seconds), **Performance** (Ultimate Performance, animations, Fast Startup, Edge background, update sharing, Storage Sense…), **Gaming**, **Privacy & AI** (Copilot, Recall, Click to Do, telemetry, ads, Bing, feedback…), **Taskbar & Explorer** and **Background services**. Every tweak says what you gain (speed, space, gaming, privacy, security, stability or comfort) with an honest estimate, what you'll notice and the trade-off. Presets: Recommended for this PC, Gaming, Privacy, Windows 11 clean-up, Speed. Original values are backed up and restored exactly when undone |
| | **Services** | All services with plain-English notes on which are safe to trim and which to keep; start/stop/startup type |
| | **Processes** | Live task list with CPU/memory, end task / process tree, priority; critical system processes are blocked |
| Apps | **New PC Setup** | 58 curated must-have apps in 10 categories, plus Essentials / Gamer / Developer / Creator bundles; silent batch install via winget with live output |
| | **Bloatware Remover** | Lists removable Store apps and flags 70+ known bloat apps, including Windows 11's (Copilot, Clipchamp, Dev Home, Journal, Mail & Calendar, Candy Crush, TikTok…); remove for current or all users |
| | **Installed Programs** | Everything installed, sortable by size/date; launch uninstallers |
| System | **Maintenance** | 27 one-click actions in sections: restore point; SFC, DISM, drive error scan, Store reset, Explorer and icon-cache repair; component cleanup, TRIM/defrag, OneDrive removal; Defender quick scan and definition updates; app updates via winget; drive SMART health, battery and Wi-Fi reports, memory test, reliability history; DNS flush, network reset, clock sync, ping test. SFC/DISM/chkdsk/defrag show real percentages |
| | **System Info** | CPU, GPU, RAM modules, motherboard, BIOS, disks + health, network, battery; copy it as a report |
| | **Settings & Log** | Safety options, excluded folders, privileges, About (with detected system), and a live activity log |

**Feedback everywhere:** every task shows a progress strip with percent, elapsed time and time left, a sidebar
"Running" panel keeps progress visible from any page, and results arrive as colour-coded notifications
(success, info, warning, error) with a details or follow-up button when useful.
Shortcuts: Ctrl+K search everything, Alt+Left/Right or the mouse side buttons go back/forward, F5 refresh/scan, Ctrl+F search on a page, Esc asks before stopping a task, Ctrl+1–9 jump between pages. The **Tasks** button (top right) lists everything running or finished.

## Safety by design

- **Recycle Bin by default** for anything you delete from file tools (switchable in Settings).
- **Protected paths:** Windows, Program Files, ProgramData\Microsoft, boot/recovery folders and system files
  (pagefile, hiberfil…) can never be deleted from the file tools.
- Junk cleaning only touches temp/cache locations, skips files in use, and leaves temp files younger than 24 hours.
- Symlinks/junctions are never followed; OneDrive cloud-only placeholders are skipped.
- Tweaks back up original values to `%LOCALAPPDATA%\Noisewater Labs\Remedy\tweak_backup.json` and can create a
  **restore point** first (on by default).
- **Restore point first, or stop:** if Windows can't create the restore point before tweaks or One-click optimize,
  Remedy stops and asks instead of carrying on without a safety net. The temporary Windows setting it uses to allow
  the restore point is put back afterwards.
- **The Recycle Bin is never emptied unless you tick it**, and One-click optimize always leaves it alone.
- **Essential Windows services** (networking, Wi-Fi, sound, sign-in, updates, security and core services) can't be
  stopped or disabled from Remedy; the Services page marks them with a lock and says why.
- **Protected apps:** the Microsoft Store, App Installer, Windows Security, Windows components, media codecs and
  your PC maker's sound/graphics/touchpad panels can't be removed from the Bloatware page.
- **Duplicates skip app and game folders** (Program Files, AppData, Steam, developer folders) unless you opt in,
  because removing one copy there can break a program.
- **Tested on real Windows:** every pull request applies and undoes every registry tweak on a throwaway Windows
  machine and checks the original values come back exactly.
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
