"""Remedy visual theme: Noisewater Labs graphite surfaces, a calm teal accent and restrained status colours.

Design tokens live here so every page draws from one system. Names used by older pages (BG, SURFACE, ACCENT,
ACCENT2, OK, WARN, DANGER, INFO...) are kept; their values follow the new palette.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- colour tokens
CANVAS = "#111416"      # window background
SIDEBAR = "#171B1E"     # navigation surface
SURFACE = "#1D2225"     # cards / panels
RAISED = "#252B2E"      # popovers, hover, selected
BORDER = "#333B3F"      # delicate separation
TEXT = "#EDF1EF"        # primary text
MUTED = "#A6B0AC"       # secondary text
FAINT = "#6F7A76"       # disabled / tertiary text
ACCENT = "#71C7AE"      # primary actions, focus
ACCENT_HOVER = "#8AD6C0"
ACCENT_INK = "#0C1A16"  # text on accent fills
ACCENT_TINT = "#21362F" # selected rows / nav item
ACCENT2 = "#D2A27A"     # warm copper: second data series only
OK = "#7FCBA2"          # verified success only
WARN = "#E8B86A"        # important attention
DANGER = "#E0837F"      # confirmed error / critical
INFO = "#9BBFC6"        # neutral information

# legacy names (older pages use these)
BG = CANVAS
SIDE = SIDEBAR
SURFACE2 = RAISED

# --------------------------------------------------------------------------- spacing, radius, type scale (px)
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 8, "md": 12, "lg": 16}
TYPE = {"caption": 11, "small": 12, "body": 13, "lead": 15, "title": 18, "h1": 26}

# severity -> (colour, short label); text labels always accompany colour (never colour alone)
SEVERITY = {"high": (DANGER, "High"), "medium": (WARN, "Medium"), "low": (INFO, "Low"), "info": (MUTED, "Info"),
            "ok": (OK, "OK")}

FONT = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
MONO = '"Cascadia Mono", "Consolas", monospace'


def apply_palette(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE,
        QPalette.AlternateBase: "#1A1F22", QPalette.Text: TEXT, QPalette.Button: SURFACE2,
        QPalette.ButtonText: TEXT, QPalette.Highlight: ACCENT_TINT, QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: SURFACE2, QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: MUTED,
        QPalette.Link: ACCENT, QPalette.BrightText: "#ffffff",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    app.setPalette(pal)


_CHECK_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
              '<path d="M3.5 8.4 L6.6 11.4 L12.6 4.8" fill="none" stroke="#0C1A16" stroke-width="2.2" '
              'stroke-linecap="round" stroke-linejoin="round"/></svg>')
_DASH_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
             '<path d="M4.5 8 H11.5" stroke="#71C7AE" stroke-width="2.2" stroke-linecap="round"/></svg>')


def _asset(name: str, content: str) -> str:
    """Write a small SVG next to Remedy's settings and return a stylesheet-friendly path ("" on failure)."""
    try:
        from .core.utils import APP_DIR
        folder = APP_DIR / "ui"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        return path.as_posix()
    except OSError:
        return ""


def stylesheet() -> str:
    check = _asset("check.svg", _CHECK_SVG)
    dash = _asset("dash.svg", _DASH_SVG)
    check_img = f"image: url({check});" if check else "image: none;"
    dash_img = f"image: url({dash});" if dash else ""
    r_sm, r_md, r_lg = RADIUS["sm"], RADIUS["md"], RADIUS["lg"]
    return f"""
* {{ font-family: {FONT}; font-size: 10pt; }}
QMainWindow, QStackedWidget, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {CANVAS}; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel#h1 {{ font-size: 19pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 13pt; font-weight: 600; }}
QLabel#muted, QLabel#sub {{ color: {MUTED}; }}
QLabel#faint {{ color: {FAINT}; font-size: 9pt; }}
QLabel#cardtitle {{ color: {MUTED}; font-size: 9.5pt; font-weight: 500; }}
QLabel#bignum {{ font-size: 24pt; font-weight: 500; }}
QLabel#brand {{ font-size: 17pt; font-weight: 600; }}
QLabel#brandsub {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#pill {{ border-radius: 6px; padding: 2px 8px; font-size: 8.5pt; font-weight: 500; }}
QLabel#empty {{ color: {MUTED}; font-size: 11pt; }}
QLabel#section {{ color: {TEXT}; font-size: 11.5pt; font-weight: 600; padding-top: 8px; }}
QLabel#kicker {{ color: {TEXT}; font-size: 10.5pt; font-weight: 600; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {r_md}px; }}
QFrame#card[tone="accent"] {{ border-color: #3A4A45; }}
QFrame#hero {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {r_md}px; }}
QFrame#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QFrame#topbar {{ background: {CANVAS}; border-bottom: 1px solid {BORDER}; }}
QFrame#progressrow {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {r_sm}px; }}
QFrame#progressrow[state="running"] {{ border-color: #2F5A4E; }}
QFrame#progressrow[state="done"] {{ border-color: #2E5541; background: #18221D; }}
QFrame#progressrow[state="stopped"] {{ border-color: #5C4A2A; background: #221E17; }}
QFrame#activity {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {r_sm}px; }}
QFrame#activityitem {{ background: transparent; }}
QFrame#popover {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: {r_md}px; }}
QFrame#row {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {r_sm}px; }}
QFrame#row:hover {{ border-color: #46524F; }}
QFrame#row[selected="true"] {{ border-color: {ACCENT}; background: #1F2926; }}
QFrame#banner {{ background: #1F2926; border: 1px solid #2F5A4E; border-radius: {r_sm}px; }}
QFrame#banner[tone="warn"] {{ background: #26211A; border-color: #5C4A2A; }}
QFrame#divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QProgressBar#mini {{ min-height: 4px; max-height: 4px; border-radius: 2px; }}
QProgressBar#mini::chunk {{ border-radius: 2px; }}
QDialog {{ background: {SURFACE}; }}
QFrame#recommend {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: {r_sm}px; }}

QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: {r_sm}px;
              padding: 8px 16px; min-height: 16px; }}
QPushButton:hover {{ border-color: #55625E; background: #2A3134; }}
QPushButton:pressed {{ background: #20262A; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: #262C2F; background: #1B1F22; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: {ACCENT_INK}; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:focus {{ border: 1px solid #DDF3EC; }}
QPushButton[kind="primary"]:disabled {{ background: #2A3D37; color: #6E8981; border-color: #2A3D37; }}
QPushButton[kind="danger"] {{ background: #2E1F1F; color: #F2B8B5; border: 1px solid #5A3433; }}
QPushButton[kind="danger"]:hover {{ background: #3A2524; border-color: {DANGER}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; background: {RAISED}; }}
QPushButton[kind="link"] {{ background: transparent; border: none; color: {ACCENT}; padding: 2px 4px; }}
QPushButton[kind="link"]:hover {{ color: {ACCENT_HOVER}; text-decoration: underline; }}
QPushButton[kind="crumb"] {{ background: transparent; border: none; color: {MUTED}; padding: 2px 4px; }}
QPushButton[kind="crumb"]:hover {{ color: {TEXT}; text-decoration: underline; }}
QPushButton[kind="crumb"]:disabled {{ color: {TEXT}; background: transparent; font-weight: 600; }}
QPushButton[kind="nav"] {{ background: transparent; border: 1px solid transparent; color: {TEXT}; padding: 4px 8px;
              font-size: 12pt; min-width: 16px; }}
QPushButton[kind="nav"]:hover {{ background: {RAISED}; border-color: {BORDER}; }}
QPushButton[kind="nav"]:disabled {{ color: #4A5451; background: transparent; border-color: transparent; }}
QPushButton[kind="search"] {{ background: {SURFACE}; color: {MUTED}; text-align: left; padding: 6px 12px; }}
QPushButton[kind="search"]:hover {{ color: {TEXT}; }}
QPushButton[kind="choice"] {{ background: {SURFACE}; text-align: left; padding: 12px 16px; border-radius: {r_md}px; }}
QPushButton[kind="choice"]:checked {{ border: 1px solid {ACCENT}; background: #1F2926; }}
QPushButton[kind="step"] {{ background: transparent; border: none; color: {FAINT}; padding: 4px 6px; }}
QPushButton[kind="step"][state="current"] {{ color: {TEXT}; font-weight: 600; }}
QPushButton[kind="step"][state="done"] {{ color: {OK}; }}
QPushButton[kind="step"]:hover {{ color: {TEXT}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit#note {{ background: {SURFACE}; border: 1px solid {BORDER};
              border-radius: {r_sm}px; padding: 6px 10px; color: {TEXT}; selection-background-color: {ACCENT_TINT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit#note:focus {{ border-color: {ACCENT}; }}
QLineEdit#palette {{ font-size: 13pt; padding: 12px 16px; border-radius: {r_md}px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_TINT}; }}

QTreeWidget, QListWidget, QTableWidget {{ background: {SURFACE}; alternate-background-color: #1A1F22;
              border: 1px solid {BORDER}; border-radius: {r_md}px; outline: 0; }}
QTreeWidget:focus, QListWidget:focus {{ border-color: #3F6F62; }}
QTreeWidget::item, QTableWidget::item {{ padding: 4px; border: none; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {ACCENT_TINT}; color: #FFFFFF; }}
QListWidget::item {{ padding: 6px 8px; }}
QHeaderView::section {{ background: {RAISED}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
              padding: 8px; font-weight: 500; }}
QHeaderView {{ background: {RAISED}; border-top-left-radius: {r_md}px; border-top-right-radius: {r_md}px; }}

QListWidget#nav {{ background: transparent; border: none; }}
QListWidget#nav::item {{ color: {MUTED}; padding: 6px 12px; margin: 0px 8px; border-radius: {r_sm}px; }}
QListWidget#nav::item:selected {{ background: {ACCENT_TINT}; color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: #1F2427; color: {TEXT}; }}
QListWidget#palettelist {{ background: transparent; border: none; }}
QListWidget#palettelist::item {{ padding: 8px 12px; border-radius: {r_sm}px; color: {TEXT}; }}
QListWidget#palettelist::item:selected {{ background: {ACCENT_TINT}; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 4px; min-height: 8px; max-height: 8px;
              text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 4px; background: {ACCENT}; }}
QProgressBar[level="warn"]::chunk {{ background: {WARN}; }}
QProgressBar[level="danger"]::chunk {{ background: {DANGER}; }}

QPlainTextEdit#console {{ background: #0D1012; color: #C9D2CE; border: 1px solid {BORDER}; border-radius: {r_md}px;
              font-family: {MONO}; font-size: 9pt; padding: 8px; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4A5552;
              background: {RAISED}; }}
QCheckBox::indicator:hover, QTreeWidget::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
              {check_img} }}
QTreeWidget::indicator:indeterminate {{ background: #22403A; border-color: {ACCENT}; {dash_img} }}
QCheckBox::indicator:disabled, QTreeWidget::indicator:disabled {{ background: #1B1F22; border-color: #2C3336; }}
QCheckBox::indicator:checked:disabled, QTreeWidget::indicator:checked:disabled {{ background: #2A3D37; }}
QCheckBox:focus {{ color: {ACCENT_HOVER}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #394245; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #4A5457; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #394245; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: {r_sm}px; padding: 4px; }}
QMenu::item {{ padding: 8px 20px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_TINT}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
QSplitter::handle {{ background: transparent; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
"""
