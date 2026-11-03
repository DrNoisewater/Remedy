# Remedy technician suite: implementation status

Last updated: 3 Nov 2026. Branch `claude/gifted-gates-44p49x`. Source spec: *Remedy Claude Code Master Blueprint*.

**Legend:** ✅ done and tested here · 🟡 done, needs a real Windows PC to confirm · ⬜ not started

## 1. Audit before coding (Phase 0)

| Current capability | Reuse / extend / replace | Risk | UI destination | Tests |
|---|---|---|---|---|
| Dashboard live tiles + health score (`pages_system.DashboardPage`, `score_health`) | Extend: added last-diagnosis, security and "workflows" row; kept health check + One-click optimize | Low | Dashboard | existing smoke + new nav tests |
| One-click optimize (`core/optimize.py`, wizard) | Reused unchanged | — | Dashboard | existing |
| Junk/Large/Duplicates/Disk tools | Reused; junk scanner now also feeds diagnostics and the "Clean safe junk" repair (same categories, Recycle Bin excluded) | Low | Storage & Cleanup | existing + `test_workflows` |
| Startup / Processes / Services / Tweaks | Reused; Tweaks and Processes gained deep links (`{"tweak": id}`, `{"search": name}`) | Low | Performance | nav tests |
| Tweak catalogue + backups (`core/tweaks.py`) | Reused as-is by New PC Setup (ids unchanged; apply/undo backup unchanged) | Medium (system settings) | New PC Setup, Tweaks | existing roundtrip (CI only) + plan tests |
| App catalogue + winget (`core/appstore.py`, "New PC Setup" page) | Reused by the new wizard; old page kept and renamed **App Installer** (key `setup` unchanged) | Medium | Software, New PC Setup | plan/deploy tests |
| Bloatware / Programs | Unchanged | — | Software | existing |
| Maintenance (27 actions: SFC/DISM/Defender…) | Unchanged; gained deep links from findings | — | System › Windows & Repair | existing |
| System Info | Unchanged (renamed in sidebar "Hardware & System") | — | System | existing |
| `BasePage.run`, progress strip, sidebar "Running", toasts | Extended: every task also registers in the new **Task Center** | Low | everywhere | nav tests |
| Sidebar `NAV` + `navigate()` | Replaced by grouped, foldable nav + central history router (keys kept) | Medium (touches every page) | app shell | nav tests |
| Theme (`theme.py`) | Replaced values with tokenised graphite/teal system; all old constant names kept | Low | everywhere | screenshots |

## 2. What is done

### Phase 1: design system and navigation ✅
- **Design tokens** in `remedy/theme.py`: graphite surfaces, teal accent, copper secondary, restrained status colours; spacing/radius/type scales; severity colours always paired with text.
- **One app-wide history** (`remedy/ui/navigation.py`): Back/Forward buttons in the top bar, **mouse side buttons**, Alt+Left/Right and keyboard Back keys. Sub-views are routes (a selected finding, the Repair Plan, each New PC Setup step), so Back steps through them. Going back never re-runs or undoes work.
- **Breadcrumbs** (Remedy / Group / Page / sub-view), clickable ancestors; sidebar selection follows history.
- **Grouped, foldable sidebar**: Dashboard, Diagnose & Repair, New PC Setup, then Performance, Security, Storage & Cleanup, Software, System, Technician, Settings. Folding is remembered; the current page is never hidden.
- **Command palette (Ctrl+K)**: pages, read-only actions (Quick Scan, health check, My Standard Setup), every maintenance tool, tweak and catalogue app, key settings. It only navigates; changes still go through each page's review/confirm.
- **Task Center** (`remedy/ui/taskcenter.py`): running/finished/failed/cancelled jobs from every page, elapsed time, per-task log, "Go to page", and **Cancel… with a confirmation**. Jobs survive page switches.
- **Esc** no longer silently cancels: it asks "Stop this task?" first. Closing the window while tasks run asks first.
- **Responsive layout**: `ResponsiveGrid` + `BasePage.make_scrollable()`; page headers move buttons under the title when narrow. Checked at 980×640 (new minimum), 1440×900 and 1920×1080.

### Phase 2: Diagnose & Repair ✅ (🟡 Windows-only providers)
- Engine `remedy/core/diagnostics.py`: typed `Evidence` (value, unit, source, time, sampling window, samples, reliability) and `Finding` (severity, **Confirmed / Suspected / Needs review / Not assessed**, confidence, evidence ids, what/why/alternatives/next steps, repair actions, links, verification metrics).
- Seven isolated providers: performance (timed CPU/memory/page-file/disk-latency sampling + busiest processes), storage (free space, safe junk), startup, Windows (uptime, pending restart, crash events, device problem codes), security (Defender, Security Center AV, firewall, Defender's own threat history), hardware (drive health, battery), network (DNS + TCP timing). A failed/unavailable provider is shown as not assessed and makes the session "partial", never "healthy".
- Documented thresholds (`THRESHOLDS`) and deterministic rules; correlation/dedup (memory pressure absorbs disk latency into one card). Slowness is never called malware.
- Page: Quick / Full / Custom scans with live per-area status, cancel keeps partial results, findings list with severity filter + search, inspector tabs (What's happening / Why we think so / What to do / Evidence), Dismiss with reason, links into existing tools (e.g. "View process" opens Processes filtered to that process).
- **Repair Plan** (`remedy/core/repair.py`): Clean safe junk, Flush DNS, Update Defender definitions. Preflight, "Preview every change", confirm, one-at-a-time run with receipts, then **re-measures and shows Before / After / Verified?** (Verified, No change, Worse, Not verified). Honest undo text for each.
- Sessions saved automatically; HTML/JSON report export with privacy redaction.

### Phase 3: New PC Setup ✅ (🟡 real installs/tweaks need Windows)
- Engine `remedy/core/provision.py`: versioned **profiles** (Everyday, Gaming, Business, Laptop, Creator/Developer, My Technician Kit), built-ins use only "Safe" tweaks. Duplicate / import / export `.remedyprofile`; imports are validated (only winget IDs, known tweak ids, known checklist items; scripts/unknown fields dropped with a warning; can't overwrite built-ins). Portable mode: a `RemedyData` folder next to Remedy.exe holds profiles.
- **Inspect** (read-only, each fact labelled Known / Not available / Requires admin / Requires restart): Windows, model, CPU, RAM, GPU, BIOS, activation, TPM, Secure Boot, BitLocker, free space, battery/AC, devices with problems, pending restart, internet, winget and installed apps.
- **Configure**: categories (Apps, Windows settings by group, Checklist), tick/untick, add catalogue apps, "Save as my profile". Plan is tailored: already installed / already set → "Already in place"; other Windows version or no admin → "Blocked" with reason; laptop-sensitive or non-Safe tweaks → "Needs review", unticked.
- **Review**: one sortable table (#, Action, Why, Selected, Status, Risk, Undo, Restart), preflight, **Dry run**.
- **Deploy**: restore point first (fails → stops and asks), settings then apps one at a time, journal saved after every step, step-based progress, pause after current step. Idempotent: finished items are never repeated.
- **Resume**: on start-up an unfinished setup shows a banner (Resume / Review before resume / Discard); "running" steps become "interrupted" and are re-checked.
- **Verify**: re-reads winget's installed list and each setting (not exit codes); manual checklist. **Handoff**: outcome pills, technician note, client report (HTML) and JSON record; client label excluded unless ticked.
- **My Standard Setup**: last profile → inspect → lands on Review; never runs anything by itself.

### Phase 4 (partial): Security Center ✅ (🟡 Windows)
- Status tiles (antivirus incl. third-party, definitions age, last quick scan, firewall), Defender's own detections ("Confirmed by provider"), startup persistence review with Authenticode signatures and **lead-only** assessments (Needs review / Broken entry / No concerns noticed). Open file location, local SHA-256, "Scan file with Defender…" (confirm, admin). No uploads or reputation lookups.

### Phase 5 (partial): Sessions & Reports ✅
- List/search/filter all sessions, details, Open/Resume, save report, anonymized JSON export, delete. Retention setting (default 90 days) and privacy mode in Settings.

## 3. Safety rules kept (all existing tests still pass)
Recycle Bin never a default; `services.ESSENTIAL` protected; `programs.appx_protection()` unchanged; duplicates skip app folders; failed restore point stops and asks (also in New PC Setup); tweak ids unchanged; roundtrip tests still only run with `REMEDY_TWEAK_ROUNDTRIP=1` on CI.

## 4. Testing done here (Linux container)
- `python -m pytest -q` → **105 passed, 52 skipped** (Windows-only). New: `tests/test_workflows.py` (29 engine tests), `tests/test_navigation_ui.py` (6 UI tests incl. real mouse Back/Forward events, page switches during a running task, wizard Back never repeating installs).
- PyInstaller one-file build succeeded (Linux binary); start check passed (still running after 10 s).
- Off-screen screenshots of every new screen at 980×640, 1440×900, 1920×1080, plus Windows 11 preview mode.

## 5. Needs a real Windows PC (CI builds the exe on every pull request)
- Mouse side buttons on real hardware; Segoe icons in the sidebar.
- Every Windows provider (Defender/Security Center/firewall/events/devices/drive health), the inspection script (TPM, Secure Boot, BitLocker, activation), winget installs and verification, Defender file scan, restore point in New PC Setup.
- 125/150/200% display scaling.

## 6. Not done yet (next highest value first)
1. More repair actions linked to findings (disable selected startup entries with undo, SFC/DISM as reviewed plan items, Windows Update open/check).
2. Compare two sessions (before/after visits); repair playbooks ("Slow boot", "Disk full", "Wi-Fi broken"…).
3. Basic/Technician detail toggle (setting exists, not used yet); client privacy mode for on-screen display.
4. Network page with gateway/adapter details; Hardware page SMART detail; reliability history parsing.
5. Optional offline package cache; restart planner; first-run onboarding.
6. PDF report (HTML report prints to PDF from any browser today).

## 7. Commands
```bash
.venv/bin/python -m pytest -q
QT_QPA_PLATFORM=offscreen .venv/bin/python main.py      # or plain `python main.py` on Windows
```
