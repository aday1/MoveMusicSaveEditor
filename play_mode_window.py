"""Desktop Play window: 3D view, navigator, transport, and live MIDI property panel."""

from __future__ import annotations

import copy
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from model import HitZone, MorphZone, TextLabel, MidiNoteMapping, MidiCCMapping, Project
from performance_panel import MessageLogWidget, RoliblockStripWidget, TransportConfigBar

_DARK_SS = (
    "background: #0c1018; color: #c8daf0; "
    "font-family: 'Segoe UI', 'Arial', sans-serif;"
)
_COMBO_SS = (
    "font-size: 11px; color: #d0e0f0; background: #1c2438; "
    "border: 1px solid #2a3a50; padding: 4px 6px; min-height: 22px;"
)
_LIST_SS = (
    "QListWidget { font-size: 11px; color: #c0d8f0; background: #0a0f14; "
    "border: 1px solid #1e2a3a; }"
    "QListWidget::item { padding: 4px 6px; min-height: 20px; }"
    "QListWidget::item:selected { background: #1c3a50; color: #00e5ff; }"
)
_SPIN_SS = (
    "QSpinBox { font-size: 12px; color: #e0f0ff; background: #141e2c; "
    "border: 1px solid #2a3a50; padding: 4px 8px; min-height: 26px; min-width: 64px; }"
    "QSpinBox::up-button, QSpinBox::down-button { width: 22px; }"
)
_GROUP_SS = (
    "QGroupBox { color: #8ab4d8; border: 1px solid #1e2a3a; "
    "border-radius: 4px; margin-top: 10px; padding-top: 14px; font-size: 11px; font-weight: bold; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
)
_BTN_SS = (
    "QPushButton { font-size: 11px; color: #d0e0f0; background: #1a2840; "
    "border: 1px solid #2a4060; border-radius: 4px; padding: 6px 12px; min-height: 28px; }"
    "QPushButton:hover { background: #243558; }"
    "QPushButton:pressed { background: #0e1a2a; }"
)
_LABEL_HEADING_SS = (
    "color: #607890; font-size: 10px; font-weight: bold; border: none; background: transparent;"
)


class PlayModeWindow(QMainWindow):
    """Hosts the 3D viewport in a resizable window with live MIDI property editing."""

    exiting = pyqtSignal()
    navigator_workspace_changed = pyqtSignal(int)
    navigator_element_clicked = pyqtSignal(object)
    camera_fit_requested = pyqtSignal(int)
    midi_property_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MMC -- Desktop Play")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(_DARK_SS)
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
            "padding: 6px 10px; font-family: Courier; font-size: 10px;"
        )
        self._hud_scroll = QScrollArea()
        self._hud_scroll.setWidget(self._hud)
        self._hud_scroll.setWidgetResizable(True)
        self._hud_scroll.setMaximumHeight(90)
        self._hud_scroll.setMinimumHeight(32)
        self._hud_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._hud_scroll.setStyleSheet(
            "QScrollArea { border: none; background: rgba(8, 10, 16, 235); }"
            "QScrollBar:vertical { width: 8px; background: #0c1018; }"
            "QScrollBar::handle:vertical { background: #2a3a50; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        outer.addWidget(self._hud_scroll)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(0)

        # --- LEFT NAV ---
        nav_split = QSplitter(Qt.Orientation.Vertical)
        nav_split.setStyleSheet(
            "QSplitter::handle { background: #1e2a3a; height: 3px; }"
        )
        nav_top = QWidget()
        nav_l = QVBoxLayout(nav_top)
        nav_l.setContentsMargins(6, 4, 6, 4)
        nav_l.setSpacing(4)

        nav_l.addWidget(self._heading("Workspace"))
        self._ws_combo = QComboBox()
        self._ws_combo.setStyleSheet(_COMBO_SS)
        self._ws_combo.currentIndexChanged.connect(self._on_ws_combo)
        nav_l.addWidget(self._ws_combo)

        nav_l.addWidget(self._heading("Camera (fit to workspace)"))
        self._cam_combo = QComboBox()
        self._cam_combo.setStyleSheet(_COMBO_SS)
        self._cam_combo.currentIndexChanged.connect(self._on_cam_combo)
        nav_l.addWidget(self._cam_combo)

        nav_l.addWidget(self._heading("Auto-Fly Camera"))
        fly_row = QHBoxLayout()
        fly_row.setContentsMargins(0, 0, 0, 0)
        fly_row.setSpacing(4)
        self._fly_orbit_btn = QPushButton("Orbit")
        self._fly_orbit_btn.setStyleSheet(_BTN_SS)
        self._fly_orbit_btn.setToolTip("Orbit around all visible elements")
        self._fly_orbit_btn.clicked.connect(lambda: self._start_autofly("orbit"))
        fly_row.addWidget(self._fly_orbit_btn)
        self._fly_through_btn = QPushButton("Flythrough")
        self._fly_through_btn.setStyleSheet(_BTN_SS)
        self._fly_through_btn.setToolTip("Smooth path through element clusters")
        self._fly_through_btn.clicked.connect(lambda: self._start_autofly("flythrough"))
        fly_row.addWidget(self._fly_through_btn)
        self._fly_tour_btn = QPushButton("Tour")
        self._fly_tour_btn.setStyleSheet(_BTN_SS)
        self._fly_tour_btn.setToolTip("Visit each workspace in turn")
        self._fly_tour_btn.clicked.connect(lambda: self._start_autofly("tour"))
        fly_row.addWidget(self._fly_tour_btn)
        nav_l.addLayout(fly_row)

        fly_row2 = QHBoxLayout()
        fly_row2.setContentsMargins(0, 0, 0, 0)
        fly_row2.setSpacing(4)
        self._fly_pause_btn = QPushButton("Pause")
        self._fly_pause_btn.setStyleSheet(_BTN_SS)
        self._fly_pause_btn.clicked.connect(self._toggle_autofly_pause)
        fly_row2.addWidget(self._fly_pause_btn)
        self._fly_stop_btn = QPushButton("Stop")
        self._fly_stop_btn.setStyleSheet(_BTN_SS)
        self._fly_stop_btn.clicked.connect(self._stop_autofly)
        fly_row2.addWidget(self._fly_stop_btn)
        self._fly_speed_combo = QComboBox()
        self._fly_speed_combo.setStyleSheet(_COMBO_SS)
        for label, val in (("0.5x", 0.5), ("1x", 1.0), ("1.5x", 1.5), ("2x", 2.0), ("3x", 3.0)):
            self._fly_speed_combo.addItem(label, val)
        self._fly_speed_combo.setCurrentIndex(1)
        self._fly_speed_combo.currentIndexChanged.connect(self._on_fly_speed_changed)
        fly_row2.addWidget(self._fly_speed_combo)
        nav_l.addLayout(fly_row2)

        nav_l.addWidget(self._heading("Elements (tap to select)"))
        self._elem_list = QListWidget()
        self._elem_list.setStyleSheet(_LIST_SS)
        self._elem_list.itemClicked.connect(self._on_elem_clicked)
        nav_l.addWidget(self._elem_list, 1)
        nav_split.addWidget(nav_top)

        self._msg_log = MessageLogWidget()
        self._msg_log.setMinimumHeight(60)
        self._msg_log.setMaximumHeight(140)
        nav_split.addWidget(self._msg_log)
        nav_split.setStretchFactor(0, 3)
        nav_split.setStretchFactor(1, 1)

        nav_wrap = QWidget()
        nav_wrap.setMinimumWidth(200)
        nav_wrap.setMaximumWidth(320)
        nv = QVBoxLayout(nav_wrap)
        nv.setContentsMargins(0, 0, 0, 0)
        nv.addWidget(nav_split)
        main_row.addWidget(nav_wrap)

        # --- CENTER: VIEWPORT ---
        self._vp_host = QWidget()
        self._vp_layout = QVBoxLayout(self._vp_host)
        self._vp_layout.setContentsMargins(0, 0, 0, 0)
        main_row.addWidget(self._vp_host, 1)

        # --- RIGHT: MIDI PROPERTY PANEL ---
        self._props_panel = self._build_props_panel()
        main_row.addWidget(self._props_panel)

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

        self._viewport = None
        self._selected_elem = None
        self._updating_props = False

    # ---- right-side property panel ----

    def _build_props_panel(self) -> QWidget:
        wrap = QWidget()
        wrap.setMinimumWidth(220)
        wrap.setMaximumWidth(300)
        root = QVBoxLayout(wrap)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        root.addWidget(self._heading("Selected Element"))
        self._prop_name_label = QLabel("(none)")
        self._prop_name_label.setWordWrap(True)
        self._prop_name_label.setStyleSheet("color: #00e5ff; font-size: 12px; font-weight: bold; padding: 2px;")
        root.addWidget(self._prop_name_label)

        self._prop_type_label = QLabel("")
        self._prop_type_label.setStyleSheet("color: #607890; font-size: 10px; padding: 0 2px;")
        root.addWidget(self._prop_type_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_inner = QWidget()
        self._prop_layout = QVBoxLayout(scroll_inner)
        self._prop_layout.setContentsMargins(0, 4, 0, 4)
        self._prop_layout.setSpacing(4)

        # HitZone note group
        self._hz_note_group = QGroupBox("Note Mapping")
        self._hz_note_group.setStyleSheet(_GROUP_SS)
        nf = QFormLayout()
        nf.setContentsMargins(6, 6, 6, 6)
        nf.setSpacing(6)
        self._hz_note_ch = self._spin(1, 16, 1)
        nf.addRow("Channel:", self._hz_note_ch)
        self._hz_note_num = self._spin(0, 127, 60)
        nf.addRow("Note:", self._hz_note_num)
        self._hz_note_vel = self._spin(0, 127, 127)
        nf.addRow("Velocity:", self._hz_note_vel)
        self._hz_note_group.setLayout(nf)
        self._prop_layout.addWidget(self._hz_note_group)

        # HitZone CC group
        self._hz_cc_group = QGroupBox("CC Mapping")
        self._hz_cc_group.setStyleSheet(_GROUP_SS)
        cf = QFormLayout()
        cf.setContentsMargins(6, 6, 6, 6)
        cf.setSpacing(6)
        self._hz_cc_ch = self._spin(1, 16, 1)
        cf.addRow("Channel:", self._hz_cc_ch)
        self._hz_cc_num = self._spin(0, 127, 69)
        cf.addRow("CC #:", self._hz_cc_num)
        self._hz_cc_val = self._spin(0, 127, 127)
        cf.addRow("Value:", self._hz_cc_val)
        self._hz_cc_group.setLayout(cf)
        self._prop_layout.addWidget(self._hz_cc_group)

        # MorphZone X/Y/Z groups
        self._morph_groups = {}
        for axis in ("X", "Y", "Z"):
            grp = QGroupBox(f"{axis} Axis CC")
            grp.setStyleSheet(_GROUP_SS)
            af = QFormLayout()
            af.setContentsMargins(6, 6, 6, 6)
            af.setSpacing(6)
            ch_spin = self._spin(1, 16, 1)
            af.addRow("Channel:", ch_spin)
            cc_spin = self._spin(0, 127, 70)
            af.addRow("CC #:", cc_spin)
            val_spin = self._spin(0, 127, 0)
            af.addRow("Value:", val_spin)
            grp.setLayout(af)
            self._morph_groups[axis] = (grp, ch_spin, cc_spin, val_spin)
            self._prop_layout.addWidget(grp)

        # TextLabel info
        self._text_label_group = QGroupBox("Text Label")
        self._text_label_group.setStyleSheet(_GROUP_SS)
        tlf = QFormLayout()
        tlf.setContentsMargins(6, 6, 6, 6)
        self._tl_text = QLabel("")
        self._tl_text.setWordWrap(True)
        self._tl_text.setStyleSheet("color: #c0d8f0; font-size: 11px;")
        tlf.addRow("Text:", self._tl_text)
        self._text_label_group.setLayout(tlf)
        self._prop_layout.addWidget(self._text_label_group)

        # Apply button
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setStyleSheet(_BTN_SS)
        self._apply_btn.clicked.connect(self._on_apply_props)
        self._prop_layout.addWidget(self._apply_btn)

        self._prop_layout.addStretch(1)

        scroll.setWidget(scroll_inner)
        root.addWidget(scroll, 1)

        self._hide_all_prop_groups()
        return wrap

    def _spin(self, lo: int, hi: int, default: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(default)
        s.setStyleSheet(_SPIN_SS)
        s.setButtonSymbols(QSpinBox.ButtonSymbols.PlusMinus)
        return s

    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_HEADING_SS)
        return lbl

    def _hide_all_prop_groups(self):
        self._hz_note_group.hide()
        self._hz_cc_group.hide()
        for _ax, (grp, *_) in self._morph_groups.items():
            grp.hide()
        self._text_label_group.hide()
        self._apply_btn.hide()

    def show_element_properties(self, elem) -> None:
        """Populate right panel from the selected element. Called by the editor."""
        self._selected_elem = elem
        self._updating_props = True
        self._hide_all_prop_groups()

        if elem is None:
            self._prop_name_label.setText("(none)")
            self._prop_type_label.setText("Tap an element in the 3D view or the list.")
            self._updating_props = False
            return

        name = getattr(elem, "display_name", "") or getattr(elem, "unique_id", "?")
        self._prop_name_label.setText(name)
        kind = type(elem).__name__
        self._prop_type_label.setText(kind)

        if isinstance(elem, HitZone):
            if elem.midi_note_mappings:
                m = elem.midi_note_mappings[0]
                self._hz_note_ch.setValue(int(m.channel))
                self._hz_note_num.setValue(int(m.note))
                vel = int(m.velocity) if m.velocity > 1.0 else int(round(m.velocity * 127))
                self._hz_note_vel.setValue(max(0, min(127, vel)))
                self._hz_note_group.show()
            if elem.midi_cc_mappings:
                m = elem.midi_cc_mappings[0]
                self._hz_cc_ch.setValue(int(m.channel))
                self._hz_cc_num.setValue(int(m.control))
                self._hz_cc_val.setValue(int(m.value))
                self._hz_cc_group.show()
            self._apply_btn.show()

        elif isinstance(elem, MorphZone):
            for axis, attr, enabled_attr in (
                ("X", "x_axis_cc_mappings", "is_x_axis_enabled"),
                ("Y", "y_axis_cc_mappings", "is_y_axis_enabled"),
                ("Z", "z_axis_cc_mappings", "is_z_axis_enabled"),
            ):
                grp, ch_sp, cc_sp, val_sp = self._morph_groups[axis]
                if getattr(elem, enabled_attr, False):
                    maps = getattr(elem, attr, [])
                    if maps:
                        m = maps[0]
                        ch_sp.setValue(int(m.channel))
                        cc_sp.setValue(int(m.control))
                        val_sp.setValue(int(m.value))
                    grp.show()
            self._apply_btn.show()

        elif isinstance(elem, TextLabel):
            self._tl_text.setText(elem.display_name or "(empty)")
            self._text_label_group.show()

        self._updating_props = False

    def _on_apply_props(self) -> None:
        """Write spin-box values back into the element and notify the editor."""
        elem = self._selected_elem
        if elem is None:
            return

        if isinstance(elem, HitZone):
            if elem.midi_note_mappings and self._hz_note_group.isVisible():
                m = elem.midi_note_mappings[0]
                m.channel = self._hz_note_ch.value()
                m.note = self._hz_note_num.value()
                m.velocity = float(self._hz_note_vel.value())
            if elem.midi_cc_mappings and self._hz_cc_group.isVisible():
                m = elem.midi_cc_mappings[0]
                m.channel = self._hz_cc_ch.value()
                m.control = self._hz_cc_num.value()
                m.value = self._hz_cc_val.value()

        elif isinstance(elem, MorphZone):
            for axis, attr in (
                ("X", "x_axis_cc_mappings"),
                ("Y", "y_axis_cc_mappings"),
                ("Z", "z_axis_cc_mappings"),
            ):
                grp, ch_sp, cc_sp, val_sp = self._morph_groups[axis]
                if not grp.isVisible():
                    continue
                maps = getattr(elem, attr, [])
                if maps:
                    m = maps[0]
                    m.channel = ch_sp.value()
                    m.control = cc_sp.value()
                    m.value = val_sp.value()

        self.midi_property_changed.emit(elem)

    # ---- auto-fly controls ----

    def _start_autofly(self, mode: str) -> None:
        if self._viewport is None:
            return
        speed = self._fly_speed_combo.currentData() or 1.0
        self._viewport.autofly_start(mode, float(speed))

    def _stop_autofly(self) -> None:
        if self._viewport is not None:
            self._viewport.autofly_stop()

    def _toggle_autofly_pause(self) -> None:
        if self._viewport is not None:
            self._viewport.autofly_toggle_pause()

    def _on_fly_speed_changed(self) -> None:
        if self._viewport is None or not self._viewport.autofly_active:
            return
        speed = self._fly_speed_combo.currentData() or 1.0
        self._viewport._autofly_speed = float(speed)

    # ---- standard helpers ----

    def set_roliblock_visible(self, visible: bool) -> None:
        self.roliblock_strip.setVisible(bool(visible))

    def _on_ws_combo(self, index: int) -> None:
        if index >= 0:
            self.navigator_workspace_changed.emit(index)

    def _on_cam_combo(self, index: int) -> None:
        if index < 0:
            return
        data = self._cam_combo.itemData(index)
        if data is None or int(data) < 0:
            return
        self.camera_fit_requested.emit(int(data))

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
        self._viewport = viewport
        while self._vp_layout.count():
            self._vp_layout.takeAt(0)
        self._vp_layout.addWidget(viewport)

    def set_hud_text(self, workspace_name: str, *, show_roliblock_tips: bool = False):
        base = (
            "Desktop Play"
            + (f"  --  workspace: {workspace_name}" if workspace_name else "")
            + "\n"
            "Touch/click elements to select. MIDI values show in the right panel -- edit and hit Apply.\n"
            "Drag MorphZones to send CC. "
        )
        roli = (
            "Roliblock (debug): bind a MorphZone, set Pad A/B to Block MIDI ports. "
        )
        tail = (
            "Right-drag orbits; middle-drag/two-finger pans. Esc exits."
        )
        self._hud.setText(base + (roli if show_roliblock_tips else "") + tail)

    def log_sent(self, msg_type: str, detail: str, transport: str, destination: str) -> None:
        self._msg_log.log_message(msg_type, detail, transport, destination)

    def refresh_navigator(self, project: Optional[Project], active_ws_index: int) -> None:
        if not project or not project.workspaces:
            self._ws_combo.blockSignals(True)
            self._ws_combo.clear()
            self._ws_combo.blockSignals(False)
            self._cam_combo.blockSignals(True)
            self._cam_combo.clear()
            self._cam_combo.addItem("Manual (no auto fit)", -1)
            self._cam_combo.blockSignals(False)
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
        self._cam_combo.blockSignals(True)
        self._cam_combo.clear()
        self._cam_combo.addItem("Manual (no auto fit)", -1)
        for i, ws in enumerate(project.workspaces):
            label = ws.display_name or ws.unique_id
            self._cam_combo.addItem(f"Fit: {label}", i)
        self._cam_combo.setCurrentIndex(0)
        self._cam_combo.blockSignals(False)
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
