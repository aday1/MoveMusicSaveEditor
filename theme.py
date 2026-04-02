"""
MoveMusic Editor — Futuristic VR/DAW Theme

Dark cyberpunk aesthetic with neon accents, inspired by
VR interfaces, DAW channel strips, and experimental electronica.
"""

# -- Color palette --
# Deep space blacks and charcoals
BG_DARK      = "#0a0c10"
BG_MID       = "#111520"
BG_PANEL     = "#151a28"
BG_INPUT     = "#0d1018"
BG_HOVER     = "#1c2438"
BG_PRESSED   = "#0f1420"

# Neon accents
CYAN         = "#00e5ff"
CYAN_DIM     = "#007a8a"
CYAN_GLOW    = "#00e5ff40"
MAGENTA      = "#ff00e5"
MAGENTA_DIM  = "#8a007a"
MAGENTA_GLOW = "#ff00e540"
LIME         = "#aaff00"
AMBER        = "#ffaa00"
RED          = "#ff2255"

# Text
TEXT_PRIMARY   = "#d0e0f0"
TEXT_SECONDARY = "#607890"
TEXT_DIM       = "#3a4a5a"

# Borders
BORDER         = "#1e2a3a"
BORDER_FOCUS   = CYAN
BORDER_ACTIVE  = MAGENTA

# Selection & highlights
SELECTION_BG   = "#00e5ff18"
SELECTION_ROW  = "#00e5ff22"
TREE_SELECTED  = "#00e5ff30"

SCROLLBAR_BG   = "#0a0e14"
SCROLLBAR_HANDLE = "#1e2a3a"
SCROLLBAR_HOVER = "#2a3a4e"


STYLESHEET = f"""

/* ═══════════════════════════════════════════════════════════════
   GLOBAL
   ═══════════════════════════════════════════════════════════════ */

QMainWindow, QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Consolas", "Cascadia Code", monospace;
    font-size: 11px;
}}

/* ═══════════════════════════════════════════════════════════════
   MENU BAR — floating neon strip
   ═══════════════════════════════════════════════════════════════ */

QMenuBar {{
    background-color: {BG_MID};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
    padding: 2px 0px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}

QMenuBar::item {{
    padding: 4px 12px;
    background: transparent;
    border-radius: 3px;
    margin: 1px 2px;
}}

QMenuBar::item:selected {{
    background-color: {CYAN_GLOW};
    color: {CYAN};
}}

QMenu {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 0px;
}}

QMenu::item {{
    padding: 6px 28px 6px 16px;
}}

QMenu::item:selected {{
    background-color: {CYAN_GLOW};
    color: {CYAN};
}}

QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 8px;
}}

/* ═══════════════════════════════════════════════════════════════
   TOOLBAR — DAW transport bar vibe
   ═══════════════════════════════════════════════════════════════ */

QToolBar {{
    background-color: {BG_MID};
    border-bottom: 1px solid {BORDER};
    spacing: 3px;
    padding: 3px 6px;
}}

QToolBar::separator {{
    width: 1px;
    background-color: {BORDER};
    margin: 4px 6px;
}}

QToolButton {{
    background-color: transparent;
    color: {TEXT_SECONDARY};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px 10px;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}}

QToolButton:hover {{
    background-color: {BG_HOVER};
    color: {CYAN};
    border-color: {CYAN_DIM};
}}

QToolButton:pressed {{
    background-color: {BG_PRESSED};
    color: {MAGENTA};
    border-color: {MAGENTA_DIM};
}}

QToolButton:checked {{
    background-color: {CYAN_GLOW};
    color: {CYAN};
    border-color: {CYAN_DIM};
}}

/* ═══════════════════════════════════════════════════════════════
   TREE VIEW — channel rack / mixer list
   ═══════════════════════════════════════════════════════════════ */

QTreeWidget {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    outline: none;
    font-size: 11px;
    selection-background-color: {TREE_SELECTED};
}}

QTreeWidget::item {{
    padding: 3px 4px;
    border-bottom: 1px solid #0e1320;
    min-height: 22px;
}}

QTreeWidget::item:selected {{
    background-color: {TREE_SELECTED};
    color: {CYAN};
    border-left: 2px solid {CYAN};
}}

QTreeWidget::item:hover:!selected {{
    background-color: {SELECTION_BG};
}}

QTreeWidget::branch {{
    background-color: transparent;
}}

QHeaderView::section {{
    background-color: {BG_MID};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    font-weight: 700;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ═══════════════════════════════════════════════════════════════
   SCROLL AREA
   ═══════════════════════════════════════════════════════════════ */

QScrollArea {{
    background-color: {BG_DARK};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}

QScrollBar:vertical {{
    background-color: {SCROLLBAR_BG};
    width: 10px;
    margin: 0;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background-color: {SCROLLBAR_HANDLE};
    min-height: 30px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {SCROLLBAR_HOVER};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background-color: {SCROLLBAR_BG};
    height: 10px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background-color: {SCROLLBAR_HANDLE};
    min-width: 30px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {SCROLLBAR_HOVER};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ═══════════════════════════════════════════════════════════════
   GROUP BOX — module rack panels
   ═══════════════════════════════════════════════════════════════ */

QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding: 12px 8px 8px 8px;
    font-weight: 700;
    font-size: 10px;
    color: {CYAN};
    text-transform: uppercase;
    letter-spacing: 1.2px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 8px;
    background-color: {BG_MID};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}

QGroupBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {CYAN_DIM};
    border-radius: 3px;
    background-color: {BG_INPUT};
}}

QGroupBox::indicator:checked {{
    background-color: {CYAN_DIM};
    border-color: {CYAN};
}}

/* ═══════════════════════════════════════════════════════════════
   INPUTS — fader/knob value fields
   ═══════════════════════════════════════════════════════════════ */

QLineEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 8px;
    selection-background-color: {CYAN_DIM};
    selection-color: white;
    font-family: "Consolas", "Cascadia Code", monospace;
}}

QLineEdit:focus {{
    border-color: {CYAN};
    background-color: #0e1320;
}}

QDoubleSpinBox, QSpinBox {{
    background-color: {BG_INPUT};
    color: {CYAN};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 11px;
    font-weight: 600;
}}

QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {CYAN};
}}

QDoubleSpinBox::up-button, QSpinBox::up-button,
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    background-color: {BG_HOVER};
    border: none;
    border-radius: 2px;
    width: 16px;
}}

QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{
    background-color: {CYAN_DIM};
}}

/* ═══════════════════════════════════════════════════════════════
   COMBO BOX — parameter selector
   ═══════════════════════════════════════════════════════════════ */

QComboBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}}

QComboBox:hover {{
    border-color: {CYAN_DIM};
}}

QComboBox:focus {{
    border-color: {CYAN};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
    background-color: {BG_HOVER};
    border-top-right-radius: 3px;
    border-bottom-right-radius: 3px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    selection-background-color: {CYAN_GLOW};
    selection-color: {CYAN};
    outline: none;
}}

/* ═══════════════════════════════════════════════════════════════
   CHECKBOX — toggle switches
   ═══════════════════════════════════════════════════════════════ */

QCheckBox {{
    color: {TEXT_PRIMARY};
    spacing: 8px;
    font-size: 11px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background-color: {BG_INPUT};
}}

QCheckBox::indicator:hover {{
    border-color: {CYAN_DIM};
}}

QCheckBox::indicator:checked {{
    background-color: {CYAN_DIM};
    border-color: {CYAN};
}}

/* ═══════════════════════════════════════════════════════════════
   PUSH BUTTON — transport / action buttons
   ═══════════════════════════════════════════════════════════════ */

QPushButton {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

QPushButton:hover {{
    background-color: {CYAN_GLOW};
    color: {CYAN};
    border-color: {CYAN_DIM};
}}

QPushButton:pressed {{
    background-color: {MAGENTA_GLOW};
    color: {MAGENTA};
    border-color: {MAGENTA_DIM};
}}

/* ═══════════════════════════════════════════════════════════════
   TABLE — MIDI mapping tables (channel strip vibe)
   ═══════════════════════════════════════════════════════════════ */

QTableWidget {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    gridline-color: #141a28;
    selection-background-color: {CYAN_GLOW};
    selection-color: {CYAN};
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 11px;
}}

QTableWidget::item {{
    padding: 2px 6px;
    border-bottom: 1px solid #10151f;
}}

QTableWidget::item:selected {{
    background-color: {CYAN_GLOW};
    color: {CYAN};
}}

QTableWidget QHeaderView::section {{
    background-color: {BG_MID};
    color: {CYAN_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid #10151f;
    padding: 4px;
    font-weight: 700;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

/* ═══════════════════════════════════════════════════════════════
   LABEL
   ═══════════════════════════════════════════════════════════════ */

QLabel {{
    color: {TEXT_SECONDARY};
    font-size: 11px;
}}

QFormLayout > QLabel {{
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: {TEXT_SECONDARY};
}}

/* ═══════════════════════════════════════════════════════════════
   STATUS BAR — bottom telemetry strip
   ═══════════════════════════════════════════════════════════════ */

QStatusBar {{
    background-color: {BG_MID};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
    font-size: 10px;
    font-family: "Consolas", monospace;
    letter-spacing: 0.5px;
}}

QStatusBar QLabel {{
    color: {TEXT_DIM};
    padding: 0px 8px;
    font-size: 10px;
}}

/* ═══════════════════════════════════════════════════════════════
   SPLITTER — subtle neon seams
   ═══════════════════════════════════════════════════════════════ */

QSplitter::handle {{
    background-color: {BORDER};
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:hover {{
    background-color: {CYAN_DIM};
}}

/* ═══════════════════════════════════════════════════════════════
   INPUT DIALOG / MESSAGE BOX
   ═══════════════════════════════════════════════════════════════ */

QInputDialog, QMessageBox, QFileDialog, QColorDialog {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
}}

/* ═══════════════════════════════════════════════════════════════
   TOOLTIP — holographic data readout
   ═══════════════════════════════════════════════════════════════ */

QToolTip {{
    background-color: {BG_PANEL};
    color: {CYAN};
    border: 1px solid {CYAN_DIM};
    border-radius: 3px;
    padding: 4px 8px;
    font-family: "Consolas", monospace;
    font-size: 10px;
}}
"""
