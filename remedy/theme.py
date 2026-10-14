"""Remedy visual theme: dark, high-contrast, teal/violet accent."""
from __future__ import annotations

ACCENT = "#2dd4bf"      # Remedy teal
ACCENT2 = "#8b5cf6"     # violet
BG = "#0b0e14"
SIDE = "#0f131b"
SURFACE = "#141923"
SURFACE2 = "#1b2130"
BORDER = "#262d3d"
TEXT = "#e8ebf2"
MUTED = "#8a93a8"
DANGER = "#f05252"
WARN = "#f5a524"
OK = "#2fd07a"
INFO = "#60a5fa"

FONT = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
MONO = '"Cascadia Mono", "Consolas", monospace'


def apply_palette(app) -> None:
    from PySide6.QtGui import QColor, QPalette
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE,
        QPalette.AlternateBase: "#171c28", QPalette.Text: TEXT, QPalette.Button: SURFACE2,
        QPalette.ButtonText: TEXT, QPalette.Highlight: "#1f4e4a", QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: SURFACE2, QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: MUTED,
        QPalette.Link: ACCENT, QPalette.BrightText: "#ffffff",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#5b6377"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5b6377"))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#5b6377"))
    app.setPalette(pal)


def stylesheet() -> str:
    return f"""
* {{ font-family: {FONT}; font-size: 10pt; }}
QMainWindow, QStackedWidget, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel#h1 {{ font-size: 21pt; font-weight: 700; }}
QLabel#muted, QLabel#sub {{ color: {MUTED}; }}
QLabel#cardtitle {{ color: {MUTED}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#bignum {{ font-size: 26pt; font-weight: 700; }}
QLabel#brand {{ font-size: 17pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#brandsub {{ color: {MUTED}; font-size: 8pt; letter-spacing: 2px; }}
QLabel#pill {{ border-radius: 9px; padding: 2px 9px; font-size: 8.5pt; font-weight: 600; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#sidebar {{ background: {SIDE}; border-right: 1px solid {BORDER}; }}
QFrame#toast {{ background: {SURFACE2}; border: 1px solid {ACCENT}; border-radius: 12px; }}

QPushButton {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 9px;
              padding: 7px 15px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #232a3b; }}
QPushButton:disabled {{ color: #5b6377; border-color: #1f2533; }}
QPushButton[kind="primary"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 #22b8cf);
              color: #04130f; border: none; font-weight: 700; }}
QPushButton[kind="primary"]:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5eead4, stop:1 #3cc9dd); }}
QPushButton[kind="primary"]:disabled {{ background: #1d3b3a; color: #5b7a76; }}
QPushButton[kind="danger"] {{ background: #2f151b; color: #ffb4b4; border: 1px solid #5c2430; }}
QPushButton[kind="danger"]:hover {{ background: #3d1a22; border-color: {DANGER}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {SURFACE}; border: 1px solid {BORDER};
              border-radius: 9px; padding: 6px 10px; color: {TEXT}; selection-background-color: #1f4e4a; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: #1f4e4a; }}

QTreeWidget, QListWidget, QTableWidget {{ background: {SURFACE}; alternate-background-color: #161b26;
              border: 1px solid {BORDER}; border-radius: 12px; outline: 0; }}
QTreeWidget::item, QTableWidget::item {{ padding: 5px 4px; border: none; }}
QTreeWidget::item:selected {{ background: #1f4e4a; color: #ffffff; }}
QHeaderView::section {{ background: {SURFACE2}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
              padding: 7px 8px; font-weight: 600; }}
QHeaderView {{ background: {SURFACE2}; border-top-left-radius: 12px; border-top-right-radius: 12px; }}

QListWidget#nav {{ background: transparent; border: none; }}
QListWidget#nav::item {{ color: {MUTED}; padding: 9px 12px; margin: 1px 10px; border-radius: 9px; }}
QListWidget#nav::item:selected {{ background: rgba(45,212,191,0.13); color: {TEXT}; }}
QListWidget#nav::item:hover:!selected {{ background: #161b26; color: {TEXT}; }}

QProgressBar {{ background: {SURFACE2}; border: none; border-radius: 5px; min-height: 9px; max-height: 9px;
              text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2}); }}
QProgressBar[level="warn"]::chunk {{ background: {WARN}; }}
QProgressBar[level="danger"]::chunk {{ background: {DANGER}; }}

QPlainTextEdit#console {{ background: #080a0f; color: #c9d1e3; border: 1px solid {BORDER}; border-radius: 12px;
              font-family: {MONO}; font-size: 9pt; padding: 8px; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeWidget::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a4256;
              background: {SURFACE2}; }}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
              image: none; }}
QTreeWidget::indicator:indeterminate {{ background: #1f4e4a; border-color: {ACCENT}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #2a3142; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4358; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 4px; }}
QScrollBar::handle:horizontal {{ background: #2a3142; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: #1f4e4a; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
QSplitter::handle {{ background: transparent; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QMessageBox {{ background: {SURFACE}; }}
"""
