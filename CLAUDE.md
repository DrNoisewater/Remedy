# Remedy: notes for Claude

## About the owner

The owner is **not a programmer**. Explain everything in plain words: what changed, why, and what they need
to click. Avoid code talk and jargon; if a technical term is unavoidable, say what it means in one line.

## What this project is

- **Remedy** by Noisewater Labs: a Windows 10/11 PC-optimization desktop app.
- Language: **Python** (3.10+; the cloud builds use 3.12.10). GUI framework: **PySide6 (Qt)**.
- Packaged into a single `Remedy.exe` with **PyInstaller** (asks for admin on launch).
- **Version lives in one place:** `APP_VERSION` in `remedy/core/utils.py`. `make_version_info.py` copies it into
  `version_info.txt` (the file details Windows shows for the exe). The `v1.1.0` in README.md is just text.
- Dependencies: `requirements.txt` (loose, for running from source) and `requirements-ci.txt` (exact versions
  used by the cloud builds; update both together when upgrading a library).
- Tests: `tests/test_smoke.py` (every module loads, the main window builds, version format),
  `tests/test_features.py` (Windows-version tailoring, tweak/preset/maintenance/bloat catalogues, progress
  parsing, One-click optimize runner, health score, toasts), `tests/test_workflows.py` (diagnostics rules,
  repair verification, setup profiles/plan/journal/resume, sessions, reports, security heuristics) and
  `tests/test_navigation_ui.py` (Back/Forward incl. mouse side buttons, tasks surviving page switches, palette,
  New PC Setup never repeating work).
- `IMPLEMENTATION_STATUS.md` tracks what is done / needs Windows testing / not started for the technician suite.
- `tests/test_windows_roundtrip.py` CHANGES WINDOWS SETTINGS (applies and undoes every registry tweak). It only
  runs when `REMEDY_TWEAK_ROUNDTRIP=1`, which only the CI and Release workflows set on their throwaway Windows
  machine. Never set it on a real PC.
- Safety rules (each has a test): Recycle Bin is never a default; `services.ESSENTIAL` can't be stopped/disabled;
  `programs.appx_protection()` blocks removing the Store, App Installer, Windows parts and OEM panels;
  duplicates skip `scanner.APP_FOLDER_NAMES` by default; a failed restore point stops and asks.
- Tweak ids in `remedy/core/tweaks.py` key the undo backups on users' PCs: never rename or remove one
  (a test guards the v1.1.0 ids).

## How the app is organised

- `remedy/core/` = the engine, no UI. `osinfo.py` detects Windows 10/11, release, edition, laptop/desktop;
  every tweak and maintenance action has a `target` ("", "win10", "win11") and tweaks may have a
  `laptop_note`. Every tweak also needs an entry in `tweaks.IMPACT` (area, short gain, what you'll notice,
  trade-off; keep estimates honest); a test enforces it. `optimize.py` is One-click optimize. `tweaks.PRESETS` holds the preset lists.
- Technician suite engine: `diagnostics.py` (providers -> evidence -> findings, thresholds in `THRESHOLDS`),
  `repair.py` (approved repairs + before/after verification), `provision.py` (setup profiles, plan, deploy
  journal, resume, verify), `sessions.py` (local session JSON), `report.py` (HTML/JSON with redaction),
  `security.py` (lead-only startup heuristics). Diagnosis never changes the PC; nothing runs without review.
- Navigation: always use `main.navigate(key, params)`; params restore sub-views via `page.open_route(params)`
  and `page.route_label(params)`. NAV keys are stable (old `setup` = App Installer, wizard is `newpc`).
  `BasePage.run` also registers jobs in the Task Center (`main.tasks`). Use `ResponsiveGrid` /
  `make_scrollable()` so pages work at the 980x640 minimum.
- `remedy/ui/` = the pages. Shared pieces: `notify.py` (toasts: `main.notify(kind, title, body, action=...)`),
  `dialogs.py` (themed confirm/info/error), `widgets.py` (`BasePage.run(...)` gives every background task a
  progress strip, a sidebar "Running" entry and error toasts; pass `summary=` for the green "Done" line;
  `attach_empty` for empty-list messages), `optimize_wizard.py`.
- **Unslop design pass (trial, may be undone for the next version):** commit `4ff36ac` "Try an unslop design
  pass (heydamir/unslop)" changed only the look (solid logo, sentence-case labels, one teal button per screen,
  no gradients/glows, 4px spacing) in `theme.py`, `app.py` and `ui/*.py`. To go back to the old look:
  `git revert 4ff36ac` on a new branch, run the tests, open a pull request. Before/after pictures are in
  `unslop-preview/` (safe to delete).
- `build/`, `dist/` and `__pycache__` folders are old build leftovers committed to the repo; builds overwrite them.

## Commands that were run and worked (Linux cloud container, Nov 2026)

Setup (one time per container):

```bash
apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3   # Qt needs these on Linux (~20 s)
python3.12 -m venv .venv                                                       # ~4 s
.venv/bin/pip install -r requirements-ci.txt                                   # ~2 min (PySide6 is large)
```

Tests (~4 s, 105 passed; 52 Windows-only tests are skipped on Linux):

```bash
.venv/bin/python -m pytest -q
```

Build (~35 s). On Linux this makes a Linux program `dist/Remedy`, not `Remedy.exe`, but it proves the recipe works:

```bash
.venv/bin/python make_version_info.py
.venv/bin/python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name Remedy --icon remedy.ico --version-file version_info.txt --collect-submodules remedy main.py
```

Start check (the app should still be running after 10 s; exit code 124 means "still running", i.e. good):

```bash
QT_QPA_PLATFORM=offscreen timeout 10 ./dist/Remedy; echo $?
```

Do the build in a scratch copy, or discard the changes afterwards, so the committed `build/`, `dist/` and
`version_info.txt` are not modified by accident.

## What Linux can't do here, and how to check instead

- **Can't make the real `Remedy.exe` or see the window.** Use the CI run on a pull request: it builds the real
  exe on Windows and attaches the zip to the run (Actions > the run > Artifacts) for 7 days, so the owner can
  download and try it.
- **Can't test Windows-only features** (registry, services, winget, tweaks, SFC/DISM, UAC prompt). On Linux
  these are switched off by the app itself. They can only be checked by running the exe on a Windows PC.
- **Can see the layout off-screen:** `QT_QPA_PLATFORM=offscreen` lets the window be built and screenshotted
  with Qt's `grab()` (Linux fonts, no Windows data, no Segoe icons in the sidebar). To preview the Windows 11
  layout, set `remedy.core.osinfo._CACHE = OSInfo(True, 26100, "24H2", "Professional", laptop=True)` before
  building the window. Never run One-click optimize for real in the container (it would clean /tmp).

## CI and releases

- `.github/workflows/ci.yml` (**CI**): runs on every pull request (or by hand) on Windows: installs the pinned
  versions, runs the tests, builds the exe, zips it and keeps the zip for 7 days. A newer push to the same
  pull request cancels the older run.
- `.github/workflows/release.yml` (**Release**): started by hand with a version like `1.3.0`. It first refuses if
  the version isn't three numbers, doesn't match `APP_VERSION`, the tag `v1.3.0` already exists, or it wasn't
  started from the default branch. Then it runs the tests (stops on any failure), builds the exe, checks the exe
  carries the right version, zips it as `Remedy-v1.3.0-win-x64.zip`, tags that exact commit `v1.3.0` and
  publishes the GitHub Release "Remedy v1.3.0" with only that zip and auto-generated notes (plus short download
  and "unknown publisher" instructions at the top). Only one release runs at a time.

### Steps to release (e.g. 1.3.0)

1. Change `APP_VERSION = "1.3.0"` in `remedy/core/utils.py` (optionally also the version in README.md), in a
   pull request; let CI pass and merge it into the default branch.
2. On GitHub: **Actions > Release > Run workflow**, leave "Use workflow from" on the default branch, type
   `1.3.0`, click **Run workflow**.
3. When it finishes (about 5 minutes), the download is at
   `https://github.com/DrNoisewater/Remedy/releases/tag/v1.3.0`.
