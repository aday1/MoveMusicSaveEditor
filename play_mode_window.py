"""Fullscreen Desktop Play window: 3D view, navigator, transport, optional Roliblock, MIDI/OSC log."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from model import Project
from performance_panel import MessageLogWidget, RoliblockStripWidget, TransportConfigBar


class PlayModeWindow(QMainWindow):
    """Hosts SceneViewport fullscreen with testing controls (no main editor chrome)."""

    exiting = pyqtSignal()
    navigator_workspace_changed = pyqtSignal(int)
    navigator_element_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MMC — Desktop Play")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        if parent is not None:
            self.setWindowFlag(Qt.WindowType.Window, True)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._hud = QLabel()
        self._hud.setWordWrap(True)
        self._hud.setStyleSheet(
            "background-color: rgba(8, 10, 16, 235); color: #9dd8ff; "
            "padding: 8px 12px; font-family: Courier; font-size: 10px;"
        )
        outer.addWidget(self._hud)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)

        nav_split = QSplitter(Qt.Orientation.Vertical)
        nav_split.setStyleSheet(
            "QSplitter::handle { background: #1e2a3a; height: 3px; }"
        )

        nav_top = QWidget()
        nav_l = QVBoxLayout(nav_top)
        nav_l.setContentsMargins(6, 4, 6, 4)
        nav_l.setSpacing(4)
        nav_l.addWidget(self._nav_label("Workspace"))
        self._ws_combo = QComboBox()
        self._ws_combo.setStyleSheet(
            "font-size: 9px; color: #d0e0f0; background: #1c2438; border: 1px solid #2a3a50;"
        )
        self._ws_combo.currentIndexChanged.connect(self._on_ws_combo)
        nav_l.addWidget(self._ws_combo)
        nav_l.addWidget(self._nav_label("Elements (click to select)"))
        self._elem_list = QListWidget()
        self._elem_list.setStyleSheet(
            "QListWidget { font-size: 9px; color: #c0d8f0; background: #0a0f14; "
            "border: 1px solid #1e2a3a; }"
            "QListWidget::item:selected { background: #1c3a50; color: #00e5ff; }"
        )
        self._elem_list.itemClicked.connect(self._on_elem_clicked)
        nav_l.addWidget(self._elem_list, 1)
        nav_split.addWidget(nav_top)

        self._msg_log = MessageLogWidget()
        self._msg_log.setMinimumHeight(72)
        self._msg_log.setMaximumHeight(160)
        nav_split.addWidget(self._msg_log)
        nav_split.setStretchFactor(0, 3)
        nav_split.setStretchFactor(1, 1)

        nav_wrap = QWidget()
        nav_wrap.setMinimumWidth(200)
        nav_wrap.setMaximumWidth(320)
        nav_v = QVBoxLayout(nav_wrap)
        nav_v.setContentsMargins(0, 0, 0, 0)
        nav_v.addWidget(nav_split)

        main_row.addWidget(nav_wrap)

        self._vp_host = QWidget()
        self._vp_layout = QVBoxLayout(self._vp_host)
        self._vp_layout.setContentsMargins(0, 0, 0, 0)
        main_row.addWidget(self._vp_host, 1)

        wrap = QWidget()
        wrap.setLayout(main_row)
        outer.addWidget(wrap, 1)

        self.transport_bar = TransportConfigBar()
        outer.addWidget(self.transport_bar)

        self.roliblock_strip = RoliblockStripWidget()
        outer.addWidget(self.roliblock_strip)

        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self._on_esc)

    def set_roliblock_visible(self, visible: bool) -> None:
        self.roliblock_strip.setVisible(bool(visible))

    def _nav_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #607890; font-size: 8px; font-weight: bold; border: none; background: transparent;"
        )
        return lbl

    def _on_ws_combo(self, index: int) -> None:
        if index >= 0:
            self.navigator_workspace_changed.emit(index)

    def _on_elem_clicked(self, item: QListWidgetItem) -> None:
        elem = item.data(Qt.ItemDataRole.UserRole)
        if elem is not None:
            self.navigator_element_clicked.emit(elem)

    def _on_esc(self):
        self.close()

    def closeEvent(self, event):
        self.exiting.emit()
        super().closeEvent(event)

    def attach_viewport(self, viewport: QWidget):
        while self._vp_layout.count():
            self._vp_layout.takeAt(0)
        self._vp_layout.addWidget(viewport)

    def set_hud_text(self, workspace_name: str, *, show_roliblock_tips: bool = False):
        base = (
            "Desktop Play"
            + (f" — workspace: {workspace_name}" if workspace_name else "")
            + "\n"
            "Tips: Left-drag MorphZones to send CC (see log for ch/CC/value and OSC path). "
        )
        roli = (
            "Roliblock (debug): bind a MorphZone, set Pad A (and Pad B if you have two Blocks) to each "
            "Block MIDI port so Desktop Play opens every matching input; touch CC drives the morph; "
            "MPE pitch bend updates X when your X axis maps to channel 1 (or the same channel as the bend). "
        )
        tail = (
            "Small pivot cross at the control center: drag to move (Shift+drag for screen-depth). "
            "Morph CC drag (on pad): Alt=X, Ctrl=Y, Shift=Z only; combine for a plane; no keys = all morph axes. "
            "Click HitZones for notes (hold) or toggle if configured. "
            "Right-click for quick-add Morph X/XY/XYZ and HitZone presets. "
            "Bottom-left MIDI summary: right-click there to set channel, note or CC number, and value. "
            "Right-drag orbits; middle-drag pans. Esc exits."
        )
        self._hud.setText(base + (roli if show_roliblock_tips else "") + tail)

    def log_sent(self, msg_type: str, detail: str, transport: str, destination: str) -> None:
        self._msg_log.log_message(msg_type, detail, transport, destination)

    def refresh_navigator(self, project: Optional[Project], active_ws_index: int) -> None:
        if not project or not project.workspaces:
            self._ws_combo.blockSignals(True)
            self._ws_combo.clear()
            self._ws_combo.blockSignals(False)
            self._elem_list.clear()
            return
        self._ws_combo.blockSignals(True)
        self._ws_combo.clear()
        for i, ws in enumerate(project.workspaces):
            label = ws.display_name or ws.unique_id
            self._ws_combo.addItem(label, i)
        idx = max(0, min(active_ws_index, len(project.workspaces) - 1))
        self._ws_combo.setCurrentIndex(idx)
        self._ws_combo.blockSignals(False)
        self._fill_element_list(project, idx)

    def _fill_element_list(self, project: Project, ws_index: int) -> None:
        self._elem_list.clear()
        if not (0 <= ws_index < len(project.workspaces)):
            return
        ws = project.workspaces[ws_index]
        id_to_el = {e.unique_id: e for e in project.elements}
        for uid in ws.element_ids:
            el = id_to_el.get(uid)
            if el is None:
                continue
            name = el.display_name or uid
            kind = type(el).__name__
            it = QListWidgetItem(f"{kind}: {name}")
            it.setData(Qt.ItemDataRole.UserRole, el)
            self._elem_list.addItem(it)

    def set_navigator_workspace_index(self, index: int) -> None:
        if 0 <= index < self._ws_combo.count():
            self._ws_combo.blockSignals(True)
            self._ws_combo.setCurrentIndex(index)
            self._ws_combo.blockSignals(False)
