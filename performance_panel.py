"""
Performance Panel: Desktop testing interface for MIDI/OSC without VR.

Interactive controls for selected elements:
- HitZones: clickable buttons (Note On/Off)
- MorphZones: draggable sliders for each axis (CC output)
- XY Pads: 2D mouse draggable area

Top bar lets you override transport/device without opening MIDI Overview.
Message log shows each sent message with timestamp.
"""

from __future__ import annotations

import logging
import datetime
from typing import Optional, Callable

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QScrollArea, QSizePolicy, QComboBox, QLineEdit, QSpinBox,
    QFrame, QTextEdit, QPushButton, QCheckBox,
)

from model import HitZone, MorphZone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dark_label(text: str, color: str = "#8aa0b8", bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    weight = "bold;" if bold else ""
    lbl.setStyleSheet(f"color: {color}; font-size: 9px; {weight} border: none; background: transparent;")
    return lbl


# ---------------------------------------------------------------------------
# Interactive element widgets
# ---------------------------------------------------------------------------

class InteractiveHitZoneWidget(QWidget):
    """Clickable button for HitZone Note On/Off testing."""

    def __init__(self, element: HitZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.is_pressed = False

        self.setMinimumHeight(70)
        self.setStyleSheet("border: none;")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)

        name_label = QLabel(element.display_name)
        name_label.setFont(QFont("Courier", 10, QFont.Weight.Bold))
        name_label.setStyleSheet("color: #d0e0f0; border: none; background: transparent;")
        layout.addWidget(name_label)

        info_parts = ["HitZone  ·  Click = Note On / Release = Note Off"]
        if element.midi_note_mappings:
            m = element.midi_note_mappings[0]
            vel = int(element.fixed_midi_velocity_output or 127)
            info_parts.append(f"Note {m.note}  Ch {m.channel}  Vel {vel}")
        info_label = QLabel("  ·  ".join(info_parts))
        info_label.setStyleSheet("color: #00e5ff; font-size: 9px; border: none; background: transparent;")
        layout.addWidget(info_label)

        hint = QLabel("▶ Press and hold")
        hint.setStyleSheet("color: #607890; font-size: 8px; border: none; background: transparent;")
        layout.addWidget(hint)

    def mousePressEvent(self, event: QMouseEvent):
        event.accept()
        self.is_pressed = True
        if self.element.midi_note_mappings:
            m = self.element.midi_note_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "note_on", "note": m.note, "channel": m.channel,
                "velocity": int(self.element.fixed_midi_velocity_output or 127),
            })
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        event.accept()
        self.is_pressed = False
        if self.element.midi_note_mappings:
            m = self.element.midi_note_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "note_off", "note": m.note, "channel": m.channel, "velocity": 0,
            })
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(0, 200, 90) if self.is_pressed else QColor(15, 60, 30)
        painter.fillRect(self.rect(), QBrush(bg))
        border = QColor(0, 229, 255) if self.is_pressed else QColor(30, 42, 58)
        painter.setPen(QPen(border, 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.end()


class InteractiveMorphZoneWidget(QWidget):
    """Axis sliders for MorphZone CC testing."""

    def __init__(self, element: MorphZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.sliders: dict = {}

        self.setStyleSheet("background-color: #131925; border: 1px solid #1e2a3a; border-radius: 4px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title = QLabel(element.display_name)
        title.setFont(QFont("Courier", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #d0e0f0; border: none; background: transparent;")
        title_row.addWidget(title)
        hint = QLabel("← drag sliders")
        hint.setStyleSheet("color: #406080; font-size: 8px; border: none; background: transparent;")
        title_row.addStretch()
        title_row.addWidget(hint)
        layout.addLayout(title_row)

        AXIS_CFG = [
            ("x", "X", "#00e5ff", "is_x_axis_enabled", "x_axis_cc_mappings"),
            ("y", "Y", "#ff00e5", "is_y_axis_enabled", "y_axis_cc_mappings"),
            ("z", "Z", "#ffaa00", "is_z_axis_enabled", "z_axis_cc_mappings"),
        ]
        for key, label, color, enabled_attr, mappings_attr in AXIS_CFG:
            if not getattr(element, enabled_attr, False):
                continue
            mappings = getattr(element, mappings_attr, [])
            if not mappings:
                continue
            cc_num = mappings[0].control
            ch_num = mappings[0].channel

            row = QHBoxLayout()
            lbl = QLabel(f"{label} CC{cc_num} Ch{ch_num}:")
            lbl.setFixedWidth(90)
            lbl.setStyleSheet(f"color: {color}; font-size: 9px; border: none; background: transparent;")
            row.addWidget(lbl)

            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(127)
            slider.setValue(0)
            slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            slider.valueChanged.connect(lambda v, k=key: self._on_changed(k, v))
            row.addWidget(slider)

            val_lbl = QLabel("0")
            val_lbl.setFixedWidth(26)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet("color: #aaff00; font-size: 9px; font-weight: bold; border: none; background: transparent;")
            row.addWidget(val_lbl)

            layout.addLayout(row)
            self.sliders[key] = (slider, val_lbl)

    def _on_changed(self, axis: str, value: int):
        _, val_lbl = self.sliders[axis]
        val_lbl.setText(str(value))
        mappings_attr = f"{axis}_axis_cc_mappings"
        mappings = getattr(self.element, mappings_attr, [])
        if mappings:
            m = mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc", "cc": m.control, "channel": m.channel,
                "value": value, "axis": axis.upper(),
            })


class InteractiveXYPadWidget(QWidget):
    """2D draggable pad — sends X and Y CC simultaneously."""

    def __init__(self, element: MorphZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.is_dragging = False
        self.last_pos: Optional[QPoint] = None
        self._last_x_cc = 0
        self._last_y_cc = 0

        self.setMinimumSize(240, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #0d1117; border: 2px solid #00e5ff;")
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event: QMouseEvent):
        event.accept()
        self.is_dragging = True
        self.last_pos = event.pos()
        self.grabMouse()
        self._send_xy(event.pos())

    def mouseMoveEvent(self, event: QMouseEvent):
        event.accept()
        if self.is_dragging:
            self.last_pos = event.pos()
            self._send_xy(event.pos())

    def mouseReleaseEvent(self, event: QMouseEvent):
        event.accept()
        self.is_dragging = False
        self.releaseMouse()

    def _send_xy(self, pos: QPoint):
        w, h = max(1, self.width()), max(1, self.height())
        x_cc = int(max(0, min(127, pos.x() / w * 127)))
        y_cc = int(max(0, min(127, (1.0 - pos.y() / h) * 127)))
        self._last_x_cc = x_cc
        self._last_y_cc = y_cc
        if self.element.x_axis_cc_mappings:
            m = self.element.x_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc", "cc": m.control, "channel": m.channel, "value": x_cc, "axis": "X",
            })
        if self.element.y_axis_cc_mappings:
            m = self.element.y_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc", "cc": m.control, "channel": m.channel, "value": y_cc, "axis": "Y",
            })
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QBrush(QColor(13, 17, 23)))

        # Grid
        painter.setPen(QPen(QColor(30, 45, 60), 1))
        for i in range(1, 4):
            painter.drawLine(w * i // 4, 0, w * i // 4, h)
            painter.drawLine(0, h * i // 4, w, h * i // 4)

        # Corner labels (CC numbers)
        x_cc_num = self.element.x_axis_cc_mappings[0].control if self.element.x_axis_cc_mappings else "?"
        y_cc_num = self.element.y_axis_cc_mappings[0].control if self.element.y_axis_cc_mappings else "?"
        painter.setPen(QColor(40, 70, 90))
        f = QFont("Courier", 8)
        painter.setFont(f)
        painter.drawText(4, 12, f"← X=CC{x_cc_num}")
        painter.drawText(4, h - 4, f"↑ Y=CC{y_cc_num}")

        if self.last_pos:
            px = max(0, min(w - 1, self.last_pos.x()))
            py = max(0, min(h - 1, self.last_pos.y()))

            # Crosshair
            painter.setPen(QPen(QColor(0, 229, 255), 1))
            painter.drawLine(0, py, w, py)
            painter.drawLine(px, 0, px, h)

            # Dot
            painter.setBrush(QBrush(QColor(0, 255, 100)))
            painter.setPen(QPen(QColor(0, 229, 255), 2))
            painter.drawEllipse(px - 7, py - 7, 14, 14)

            # Value readout bubble
            painter.setPen(QColor(200, 240, 255))
            painter.setFont(QFont("Courier", 8, QFont.Weight.Bold))
            painter.drawText(px + 10, py - 4, f"X={self._last_x_cc}")
            painter.drawText(px + 10, py + 10, f"Y={self._last_y_cc}")

        painter.end()


# ---------------------------------------------------------------------------
# Transport config bar (embedded in panel, overrides editor config)
# ---------------------------------------------------------------------------

class TransportConfigBar(QWidget):
    """Compact transport selector shown at top of performance panel."""

    config_changed = pyqtSignal(dict)  # emitted when any field changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #0d1520; border-bottom: 1px solid #1e2a3a;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        # Row 1: mode + MIDI port
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(_dark_label("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("OSC", "osc")
        self.mode_combo.addItem("MIDI", "midi")
        self.mode_combo.addItem("OSC + MIDI", "both")
        self.mode_combo.setFixedWidth(90)
        self.mode_combo.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        row1.addWidget(self.mode_combo)

        row1.addSpacing(8)
        row1.addWidget(_dark_label("MIDI Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(160)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.port_combo.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        row1.addWidget(self.port_combo)

        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setFixedWidth(24)
        self.refresh_btn.setFixedHeight(20)
        self.refresh_btn.setToolTip("Refresh MIDI output ports")
        self.refresh_btn.setStyleSheet(
            "QPushButton { font-size: 11px; color: #00e5ff; background: #1c2438; border: 1px solid #2a3a50; }"
            "QPushButton:hover { background: #2a3a50; }"
        )
        self.refresh_btn.clicked.connect(self.refresh_ports)
        row1.addWidget(self.refresh_btn)

        layout.addLayout(row1)

        # Row 2: OSC host + port + namespace
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        row2.addWidget(_dark_label("OSC Host:"))
        self.osc_host = QLineEdit("127.0.0.1")
        self.osc_host.setFixedWidth(110)
        self.osc_host.setFixedHeight(20)
        self.osc_host.setPlaceholderText("127.0.0.1")
        self.osc_host.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438; border: 1px solid #2a3a50;")
        row2.addWidget(self.osc_host)

        row2.addWidget(_dark_label("Port:"))
        self.osc_port = QSpinBox()
        self.osc_port.setRange(1, 65535)
        self.osc_port.setValue(9001)
        self.osc_port.setFixedWidth(60)
        self.osc_port.setFixedHeight(20)
        self.osc_port.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438; border: 1px solid #2a3a50;")
        row2.addWidget(self.osc_port)

        row2.addWidget(_dark_label("NS:"))
        self.osc_ns = QLineEdit("/mmc")
        self.osc_ns.setFixedWidth(70)
        self.osc_ns.setFixedHeight(20)
        self.osc_ns.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438; border: 1px solid #2a3a50;")
        row2.addWidget(self.osc_ns)

        row2.addStretch()
        layout.addLayout(row2)

        # Wire changes
        self.mode_combo.currentIndexChanged.connect(self._emit)
        self.osc_host.textChanged.connect(self._emit)
        self.osc_port.valueChanged.connect(self._emit)
        self.osc_ns.textChanged.connect(self._emit)
        self.port_combo.currentIndexChanged.connect(self._emit)

        self.refresh_ports()

    def refresh_ports(self):
        prev = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem("— none —", None)
        try:
            import mido
            for name in mido.get_output_names():
                self.port_combo.addItem(name, name)
        except Exception:
            pass
        # restore previous selection
        for i in range(self.port_combo.count()):
            if self.port_combo.itemData(i) == prev:
                self.port_combo.setCurrentIndex(i)
                break
        self.port_combo.blockSignals(False)

    def load_from_config(self, cfg: dict):
        self.mode_combo.blockSignals(True)
        self.osc_host.blockSignals(True)
        self.osc_port.blockSignals(True)
        self.osc_ns.blockSignals(True)

        mode = cfg.get("midi_test_transport", "osc")
        for i in range(self.mode_combo.count()):
            if self.mode_combo.itemData(i) == mode:
                self.mode_combo.setCurrentIndex(i)
                break
        self.osc_host.setText(str(cfg.get("osc_bridge_host", "127.0.0.1")))
        self.osc_port.setValue(int(cfg.get("osc_bridge_port", 9001)))
        self.osc_ns.setText(str(cfg.get("osc_namespace", "/mmc")))

        self.mode_combo.blockSignals(False)
        self.osc_host.blockSignals(False)
        self.osc_port.blockSignals(False)
        self.osc_ns.blockSignals(False)

        # Try to select saved port
        saved_port = cfg.get("midi_test_port")
        if saved_port:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == saved_port:
                    self.port_combo.setCurrentIndex(i)
                    break

    def get_config(self) -> dict:
        return {
            "mode": self.mode_combo.currentData() or "osc",
            "osc_host": self.osc_host.text().strip() or "127.0.0.1",
            "osc_port": self.osc_port.value(),
            "osc_ns": self.osc_ns.text().strip() or "/mmc",
            "midi_port": self.port_combo.currentData(),
        }

    def load_from_runtime_dict(self, cfg: dict):
        """Sync from get_config() output (used by Desktop Play duplicate transport bar)."""
        self.mode_combo.blockSignals(True)
        self.osc_host.blockSignals(True)
        self.osc_port.blockSignals(True)
        self.osc_ns.blockSignals(True)
        self.port_combo.blockSignals(True)
        mode = cfg.get("mode", "osc")
        for i in range(self.mode_combo.count()):
            if self.mode_combo.itemData(i) == mode:
                self.mode_combo.setCurrentIndex(i)
                break
        self.osc_host.setText(str(cfg.get("osc_host", "127.0.0.1")))
        self.osc_port.setValue(int(cfg.get("osc_port", 9001)))
        self.osc_ns.setText(str(cfg.get("osc_ns", "/mmc")))
        self.mode_combo.blockSignals(False)
        self.osc_host.blockSignals(False)
        self.osc_port.blockSignals(False)
        self.osc_ns.blockSignals(False)
        self.port_combo.blockSignals(False)
        self.refresh_ports()
        midi_port = cfg.get("midi_port")
        if midi_port:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == midi_port:
                    self.port_combo.setCurrentIndex(i)
                    break

    def _emit(self):
        self.config_changed.emit(self.get_config())


# ---------------------------------------------------------------------------
# Roliblock strip (shared by Performance panel and Desktop Play window)
# ---------------------------------------------------------------------------

class RoliblockStripWidget(QFrame):
    """Roli Lightpad LED mirror: pad routing, mode, bind MorphZone."""

    config_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bind_cb: Optional[Callable] = None
        self._bound_id: Optional[str] = None
        self.setStyleSheet("background-color: #0d1520; border-bottom: 1px solid #1e2a3a;")
        roli_l = QVBoxLayout(self)
        roli_l.setContentsMargins(8, 4, 8, 6)
        roli_l.setSpacing(4)
        rh = QLabel("Roliblock LED mirror (Lightpad SysEx)")
        rh.setFont(QFont("Courier", 9, QFont.Weight.Bold))
        rh.setStyleSheet("color: #88aaff; border: none; background: transparent;")
        roli_l.addWidget(rh)
        row0 = QHBoxLayout()
        self.roli_enable = QCheckBox("Enable")
        self.roli_enable.setStyleSheet("color: #c0d8f0; font-size: 9px;")
        self.roli_enable.toggled.connect(self._emit_cfg)
        row0.addWidget(self.roli_enable)
        row0.addStretch()
        roli_l.addLayout(row0)
        row1 = QHBoxLayout()
        row1.addWidget(_dark_label("Pad A (XY):"))
        self.roli_pad_a = QComboBox()
        self.roli_pad_a.setMinimumWidth(140)
        self.roli_pad_a.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        self.roli_pad_a.currentIndexChanged.connect(self._emit_cfg)
        row1.addWidget(self.roli_pad_a)
        roli_l.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(_dark_label("Pad B (Z):"))
        self.roli_pad_b = QComboBox()
        self.roli_pad_b.setMinimumWidth(140)
        self.roli_pad_b.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        self.roli_pad_b.currentIndexChanged.connect(self._emit_cfg)
        row2.addWidget(self.roli_pad_b)
        roli_l.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(_dark_label("Mode:"))
        self.roli_mode = QComboBox()
        self.roli_mode.addItem("Off", "off")
        self.roli_mode.addItem("XY on Pad A", "xy")
        self.roli_mode.addItem("XYZ: XY on A, Z on B", "xyz_split")
        self.roli_mode.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        self.roli_mode.currentIndexChanged.connect(self._emit_cfg)
        row3.addWidget(self.roli_mode)
        roli_l.addLayout(row3)
        row_dev = QHBoxLayout()
        row_dev.addWidget(_dark_label("Block IDs:"))
        self.roli_device_ids = QLineEdit("0")
        self.roli_device_ids.setToolTip(
            "SysEx device index per BLOCK on the chain. Use 0 for one block; 0,1,2 mirrors the same LEDs to each."
        )
        self.roli_device_ids.setPlaceholderText("0 or 0,1,2")
        self.roli_device_ids.setFixedWidth(72)
        self.roli_device_ids.setStyleSheet("font-size: 9px; color: #d0e0f0; background: #1c2438;")
        self.roli_device_ids.textChanged.connect(self._emit_cfg)
        row_dev.addWidget(self.roli_device_ids)
        row_dev.addStretch()
        roli_l.addLayout(row_dev)
        row4 = QHBoxLayout()
        self.roli_bind_btn = QPushButton("Bind selected MorphZone")
        self.roli_bind_btn.setStyleSheet(
            "QPushButton { font-size: 9px; color: #00e5ff; background: #1c2438; border: 1px solid #2a3a50; padding: 3px 8px; }"
            "QPushButton:hover { background: #2a3a50; }"
        )
        self.roli_bind_btn.clicked.connect(self._on_bind_clicked)
        row4.addWidget(self.roli_bind_btn)
        self.roli_bound_lbl = QLabel("Bound: (none)")
        self.roli_bound_lbl.setStyleSheet("color: #607890; font-size: 9px; border: none; background: transparent;")
        row4.addWidget(self.roli_bound_lbl)
        row4.addStretch()
        roli_l.addLayout(row4)
        roli_refresh = QPushButton("Refresh MIDI outs")
        roli_refresh.setStyleSheet(
            "QPushButton { font-size: 8px; color: #607890; background: #1c2438; border: 1px solid #2a3a50; }"
        )
        roli_refresh.clicked.connect(self.refresh_ports)
        roli_l.addWidget(roli_refresh)
        self.refresh_ports()

    def _on_bind_clicked(self) -> None:
        if self._bind_cb:
            self._bind_cb()

    def set_bind_callback(self, cb: Optional[Callable]) -> None:
        self._bind_cb = cb

    def _emit_cfg(self) -> None:
        self.config_changed.emit(self.get_config())

    def refresh_ports(self) -> None:
        for combo in (self.roli_pad_a, self.roli_pad_b):
            combo.blockSignals(True)
            prev = combo.currentData()
            combo.clear()
            combo.addItem("-- none --", None)
            names: list[str] = []
            err_msg: Optional[str] = None
            try:
                import mido
            except ImportError:
                err_msg = "Install mido + python-rtmidi"
            else:
                try:
                    names = list(mido.get_output_names())
                except Exception as exc:
                    err_msg = str(exc)[:80] or "get_output_names() failed"
            if err_msg:
                combo.addItem(f"(Roliblock MIDI: {err_msg})", None)
            elif not names:
                combo.addItem("(No MIDI output ports — device may be input-only)", None)
            else:
                for name in names:
                    combo.addItem(name, name)
            for i in range(combo.count()):
                if combo.itemData(i) == prev:
                    combo.setCurrentIndex(i)
                    break
            combo.blockSignals(False)

    def get_config(self) -> dict:
        return {
            "roliblock_enabled": self.roli_enable.isChecked(),
            "roliblock_pad_a": self.roli_pad_a.currentData(),
            "roliblock_pad_b": self.roli_pad_b.currentData(),
            "roliblock_mode": self.roli_mode.currentData() or "off",
            "roliblock_bound_id": self._bound_id,
            "roliblock_device_ids": self.roli_device_ids.text().strip(),
        }

    def load_from_dict(self, cfg: dict, emit: bool = False) -> None:
        self._bound_id = cfg.get("roliblock_bound_id")
        for w in (
            self.roli_enable,
            self.roli_pad_a,
            self.roli_pad_b,
            self.roli_mode,
            self.roli_device_ids,
        ):
            w.blockSignals(True)
        self.roli_enable.setChecked(bool(cfg.get("roliblock_enabled", False)))
        self.refresh_ports()
        pa = cfg.get("roliblock_pad_a")
        pb = cfg.get("roliblock_pad_b")
        if pa:
            for i in range(self.roli_pad_a.count()):
                if self.roli_pad_a.itemData(i) == pa:
                    self.roli_pad_a.setCurrentIndex(i)
                    break
        if pb:
            for i in range(self.roli_pad_b.count()):
                if self.roli_pad_b.itemData(i) == pb:
                    self.roli_pad_b.setCurrentIndex(i)
                    break
        mode = cfg.get("roliblock_mode", "off")
        for i in range(self.roli_mode.count()):
            if self.roli_mode.itemData(i) == mode:
                self.roli_mode.setCurrentIndex(i)
                break
        if "roliblock_device_ids" in cfg:
            self.roli_device_ids.setText(str(cfg.get("roliblock_device_ids") or "0"))
        for w in (
            self.roli_enable,
            self.roli_pad_a,
            self.roli_pad_b,
            self.roli_mode,
            self.roli_device_ids,
        ):
            w.blockSignals(False)
        self._update_bound_label()
        if emit:
            self.config_changed.emit(self.get_config())

    def set_bound_id(self, uid: Optional[str], emit: bool = True) -> None:
        self._bound_id = uid
        self._update_bound_label()
        if emit:
            self.config_changed.emit(self.get_config())

    def _update_bound_label(self) -> None:
        self.roli_bound_lbl.setText(f"Bound: {self._bound_id or '(none)'}")


# ---------------------------------------------------------------------------
# Message log widget
# ---------------------------------------------------------------------------

class MessageLogWidget(QWidget):
    """Scrolling log of sent MIDI/OSC messages."""

    MAX_LINES = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(120)
        self.setMinimumHeight(80)
        self.setStyleSheet("background: #080c12; border-top: 1px solid #1e2a3a;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(6, 2, 6, 2)
        lbl = QLabel("Sent messages")
        lbl.setStyleSheet("color: #607890; font-size: 9px; font-weight: bold; border: none; background: transparent;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedHeight(16)
        clear_btn.setFixedWidth(38)
        clear_btn.setStyleSheet(
            "QPushButton { font-size: 8px; color: #607890; background: #1c2438; border: 1px solid #2a3a50; }"
            "QPushButton:hover { color: #00e5ff; }"
        )
        clear_btn.clicked.connect(self.clear)
        hdr.addWidget(clear_btn)
        hdr_w = QWidget()
        hdr_w.setLayout(hdr)
        hdr_w.setStyleSheet("background: #0a0f1a; border-bottom: 1px solid #1a2030;")
        layout.addWidget(hdr_w)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier", 8))
        self._log.setStyleSheet(
            "QTextEdit { background: #080c12; color: #8aa0b8; border: none; padding: 2px 6px; }"
        )
        layout.addWidget(self._log)
        self._lines: list[str] = []

    def log_message(self, msg_type: str, detail: str, transport: str, destination: str):
        """Add a line to the log."""
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        icon = {"note_on": ">", "note_off": "x", "cc": "~"}.get(msg_type, "*")

        color = {"note_on": "#00ff88", "note_off": "#ff6060", "cc": "#00e5ff"}.get(msg_type, "#8aa0b8")
        dest_color = "#ffaa00" if transport == "osc" else "#ff88ff" if transport == "midi" else "#ffcc44"

        line = (
            f'<span style="color:#3a5a7a">{ts}</span> '
            f'<span style="color:{color}">{icon} {msg_type.upper()}</span> '
            f'<span style="color:#c0d8f0">{detail}</span> '
            f'<span style="color:#3a5a7a">→</span> '
            f'<span style="color:{dest_color}">[{transport.upper()}] {destination}</span>'
        )
        self._lines.append(line)
        if len(self._lines) > self.MAX_LINES:
            self._lines = self._lines[-self.MAX_LINES:]
        self._log.setHtml("<br>".join(self._lines))
        # Scroll to bottom
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self):
        self._lines.clear()
        self._log.clear()


# ---------------------------------------------------------------------------
# Main performance panel
# ---------------------------------------------------------------------------

class PerformancePanel(QWidget):
    """Desktop MIDI/OSC performance testing panel."""

    roliblock_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_elements: list = []
        self._send_callback: Optional[Callable] = None
        self._override_cfg: dict = {}

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header = QLabel("⚡ Performance Test — Click/drag to send MIDI/OSC")
        header.setFont(QFont("Courier", 10, QFont.Weight.Bold))
        header.setStyleSheet(
            "background-color: #0a0c10; color: #00e5ff; padding: 6px 12px;"
            "border-bottom: 2px solid #00e5ff;"
        )
        main_layout.addWidget(header)

        # Transport config bar
        self.transport_bar = TransportConfigBar()
        self.transport_bar.config_changed.connect(self._on_transport_changed)
        main_layout.addWidget(self.transport_bar)

        self._roliblock_bind_callback = None
        self.roliblock_strip = RoliblockStripWidget()
        self.roliblock_strip.config_changed.connect(self.roliblock_changed.emit)
        self.roliblock_strip.set_bind_callback(self._on_roliblock_bind_click)
        main_layout.addWidget(self.roliblock_strip)

        # Scrollable controls area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background-color: #0a0c10;")
        self.control_container = QWidget()
        self.control_container.setStyleSheet("background-color: #0a0c10;")
        self.control_layout = QVBoxLayout(self.control_container)
        self.control_layout.setContentsMargins(6, 6, 6, 6)
        self.control_layout.setSpacing(8)
        scroll.setWidget(self.control_container)
        main_layout.addWidget(scroll, 1)

        # Message log
        self.msg_log = MessageLogWidget()
        main_layout.addWidget(self.msg_log)

        # Status bar
        self.status_label = QLabel("Select a HitZone or MorphZone element to begin testing")
        self.status_label.setStyleSheet(
            "color: #607890; font-size: 9px; padding: 3px 8px;"
            "border-top: 1px solid #1e2a3a; background-color: #111520;"
        )
        main_layout.addWidget(self.status_label)

    def load_config(self, cfg: dict):
        """Load saved config into the transport bar."""
        self.transport_bar.load_from_config(cfg)
        self._override_cfg = self.transport_bar.get_config()
        self.load_roliblock_from_cfg(cfg)

    def _on_transport_changed(self, cfg: dict):
        self._override_cfg = cfg

    def get_transport_config(self) -> dict:
        """Return the current override config from the transport bar."""
        return dict(self._override_cfg)

    def _on_roliblock_bind_click(self) -> None:
        if self._roliblock_bind_callback:
            self._roliblock_bind_callback()

    def get_roliblock_config(self) -> dict:
        return self.roliblock_strip.get_config()

    def load_roliblock_from_cfg(self, cfg: dict) -> None:
        self.roliblock_strip.load_from_dict(cfg, emit=False)

    def set_roliblock_bind_callback(self, cb) -> None:
        self._roliblock_bind_callback = cb

    def set_roliblock_visible(self, visible: bool) -> None:
        """Hide experimental Roliblock strip unless debug mode is on."""
        self.roliblock_strip.setVisible(bool(visible))

    def set_bound_morphzone_id(self, uid: Optional[str]) -> None:
        self.roliblock_strip.set_bound_id(uid, emit=True)

    def set_selected_elements(self, elements: list, send_callback: Callable[[str, dict], None]):
        self.current_elements = elements
        self._send_callback = send_callback

        while self.control_layout.count():
            item = self.control_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not elements:
            empty = QLabel("Click a HitZone or MorphZone in the viewport\n(press F5 to enable Performance Lock first)")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            empty.setStyleSheet("color: #607890; font-size: 11px; padding: 20px; background: transparent;")
            self.control_layout.addWidget(empty)
            self.control_layout.addStretch()
            self.status_label.setText("No element selected — use F5 Performance Lock then click an element")
            return

        for elem in elements:
            if isinstance(elem, HitZone):
                self.control_layout.addWidget(InteractiveHitZoneWidget(elem, send_callback))

            elif isinstance(elem, MorphZone):
                # XY pad when both X and Y axes active
                if elem.is_x_axis_enabled and elem.is_y_axis_enabled:
                    x_cc = elem.x_axis_cc_mappings[0].control if elem.x_axis_cc_mappings else "?"
                    y_cc = elem.y_axis_cc_mappings[0].control if elem.y_axis_cc_mappings else "?"
                    pad_label = QLabel(
                        f"· XY Pad · {elem.display_name} · X=CC{x_cc}  Y=CC{y_cc} · drag to send both axes"
                    )
                    pad_label.setFont(QFont("Courier", 8, QFont.Weight.Bold))
                    pad_label.setStyleSheet(
                        "color: #ff00e5; padding: 4px 2px; background: transparent; border: none;"
                    )
                    self.control_layout.addWidget(pad_label)
                    self.control_layout.addWidget(InteractiveXYPadWidget(elem, send_callback))

                # Individual axis sliders (always shown)
                self.control_layout.addWidget(InteractiveMorphZoneWidget(elem, send_callback))

        self.control_layout.addStretch()

        num = len(elements)
        elem_names = ", ".join(e.display_name or e.unique_id for e in elements[:3])
        self.status_label.setText(
            f"Testing {num} element{'s' if num != 1 else ''}: {elem_names}"
        )

    def log_sent(self, msg_type: str, detail: str, transport: str, destination: str):
        """Called by editor._perf_send to record each sent message."""
        self.msg_log.log_message(msg_type, detail, transport, destination)
