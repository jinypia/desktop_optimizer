"""Dark theme tokens and application stylesheet.

Colors come from a categorical palette validated for the dark surface
(#1a1a19): lightness band, chroma floor, adjacent-pair colorblind
separation, normal-vision floor, and 3:1 contrast all pass. Status colors
are reserved for health state and always ship with an icon + label, never
color alone.
"""

# Chrome & ink (dark mode)
PAGE_BG = "#0d0d0d"
SURFACE = "#1a1a19"
TEXT = "#ffffff"
TEXT_2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
AXIS = "#383835"

# Categorical series, fixed slot order — never reassigned or cycled
SERIES_CPU = "#3987e5"       # slot 1 blue
SERIES_MEM = "#d95926"       # slot 2 orange
SERIES_DISK = "#199e70"      # slot 3 aqua
SERIES_NET_DOWN = "#c98500"  # slot 4 yellow
SERIES_NET_UP = "#d55181"    # slot 5 magenta

# Status palette (reserved; distinct from series colors)
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
}

# Shape is the secondary encoding so state never rides on color alone
STATUS_ICON = {
    "good": "●",
    "warning": "▲",
    "critical": "■",
    "info": "●",
}

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {PAGE_BG}; }}
QWidget {{ color: {TEXT_2}; font-family: 'Segoe UI'; font-size: 12px; }}
QFrame#card, QFrame#panel {{
    background: {SURFACE};
    border: 1px solid rgba(255,255,255,26);
    border-radius: 8px;
}}
QLabel {{ border: none; background: transparent; }}
QLabel#cardTitle {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#cardValue {{ color: {TEXT}; font-size: 26px; font-weight: 600; }}
QLabel#cardSub {{ color: {TEXT_2}; font-size: 11px; }}
QLabel#panelTitle {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
QLabel#panelNote {{ color: {MUTED}; font-size: 11px; }}
QLabel#statusLabel {{ font-size: 13px; font-weight: 600; }}
QPushButton {{
    background: #242423; color: {TEXT};
    border: 1px solid rgba(255,255,255,26);
    border-radius: 6px; padding: 7px 10px; text-align: left;
}}
QPushButton:hover {{ background: #2e2e2c; }}
QPushButton:pressed {{ background: #191918; }}
QPushButton:disabled {{ color: {MUTED}; background: #1f1f1e; }}
QListWidget, QTableWidget, QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid rgba(255,255,255,18);
    border-radius: 6px; color: {TEXT_2};
}}
QTableWidget {{ gridline-color: {GRID}; }}
QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none;
    border-bottom: 1px solid {AXIS}; padding: 4px 6px;
}}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{
    background: #3a3a38; border-radius: 4px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{
    background: #242423; color: {TEXT};
    border: 1px solid rgba(255,255,255,26);
}}
QMenu {{
    background: {SURFACE}; color: {TEXT_2};
    border: 1px solid rgba(255,255,255,26);
}}
QMenu::item:selected {{ background: #2e2e2c; color: {TEXT}; }}
"""
