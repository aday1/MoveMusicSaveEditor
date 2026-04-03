"""
Performance Panel: Desktop testing interface for MIDI/OSC without VR.

Interactive controls for selected elements:
- HitZones: clickable buttons (Note On/Off)
- MorphZones: draggable sliders for each axis (CC output)
- XY Pads: 2D mouse draggable area
"""

from __future__ import annotations

import logging
from typing import Optional, Callable

from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QFont, QFontMetrics, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QScrollArea, QFrame, QGridLayout
)

from model import HitZone, MorphZone, Vec3


class InteractiveHitZoneWidget(QWidget):
    """Clickable button widget for HitZone Note On/Off testing."""
    
    midi_send_requested = pyqtSignal(str, dict)  # signal: (element_id, {"type": "note_on"|"note_off", ...})
    
    def __init__(self, element: HitZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.is_pressed = False
        
        self.setMinimumHeight(60)
        self.setStyleSheet("border: 2px solid #333; border-radius: 4px;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)
        
        name_label = QLabel(element.display_name)
        name_label.setFont(QFont("Courier", 10, QFont.Weight.Bold))
        layout.addWidget(name_label)
        
        if element.midi_note_mappings:
            note_map = element.midi_note_mappings[0]
            note_text = f"Note {note_map.note} Ch{note_map.channel}"
            note_label = QLabel(note_text)
            note_label.setFont(QFont("Courier", 9))
            note_label.setStyleSheet("color: #0a0;")
            layout.addWidget(note_label)
    
    def mousePressEvent(self, event: QMouseEvent):
        """Send Note On."""
        self.is_pressed = True
        if self.element.midi_note_mappings:
            m = self.element.midi_note_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "note_on",
                "note": m.note,
                "channel": m.channel,
                "velocity": int(self.element.fixed_midi_velocity_output or 127)
            })
        self.update()
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        """Send Note Off."""
        self.is_pressed = False
        if self.element.midi_note_mappings:
            m = self.element.midi_note_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "note_off",
                "note": m.note,
                "channel": m.channel,
                "velocity": 0
            })
        self.update()
    
    def paintEvent(self, event):
        """Draw pressed/unpressed state."""
        painter = QPainter(self)
        
        color = QColor(100, 200, 100) if self.is_pressed else QColor(50, 150, 50)
        painter.fillRect(self.rect(), QBrush(color))
        
        painter.setPen(QPen(QColor(200, 200, 200), 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        
        painter.end()


class InteractiveMorphZoneWidget(QWidget):
    """Slider widget for MorphZone axis control."""
    
    midi_send_requested = pyqtSignal(str, dict)
    
    def __init__(self, element: MorphZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.sliders = {}
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        
        title = QLabel(element.display_name)
        title.setFont(QFont("Courier", 10, QFont.Weight.Bold))
        layout.addWidget(title)
        
        # X-axis slider
        if element.is_x_axis_enabled and element.x_axis_cc_mappings:
            x_layout = QHBoxLayout()
            x_label = QLabel("X:")
            x_label.setMinimumWidth(30)
            x_layout.addWidget(x_label)
            
            x_slider = QSlider(Qt.Orientation.Horizontal)
            x_slider.setMinimum(0)
            x_slider.setMaximum(127)
            x_slider.setValue(0)
            x_slider.setMinimumWidth(150)
            x_slider.valueChanged.connect(
                lambda v: self._on_slider_changed("x", v)
            )
            x_layout.addWidget(x_slider)
            
            x_value = QLabel("0")
            x_value.setMinimumWidth(30)
            x_layout.addWidget(x_value)
            
            layout.addLayout(x_layout)
            self.sliders["x"] = (x_slider, x_value)
        
        # Y-axis slider
        if element.is_y_axis_enabled and element.y_axis_cc_mappings:
            y_layout = QHBoxLayout()
            y_label = QLabel("Y:")
            y_label.setMinimumWidth(30)
            y_layout.addWidget(y_label)
            
            y_slider = QSlider(Qt.Orientation.Horizontal)
            y_slider.setMinimum(0)
            y_slider.setMaximum(127)
            y_slider.setValue(0)
            y_slider.setMinimumWidth(150)
            y_slider.valueChanged.connect(
                lambda v: self._on_slider_changed("y", v)
            )
            y_layout.addWidget(y_slider)
            
            y_value = QLabel("0")
            y_value.setMinimumWidth(30)
            y_layout.addWidget(y_value)
            
            layout.addLayout(y_layout)
            self.sliders["y"] = (y_slider, y_value)
        
        # Z-axis slider
        if element.is_z_axis_enabled and element.z_axis_cc_mappings:
            z_layout = QHBoxLayout()
            z_label = QLabel("Z:")
            z_label.setMinimumWidth(30)
            z_layout.addWidget(z_label)
            
            z_slider = QSlider(Qt.Orientation.Horizontal)
            z_slider.setMinimum(0)
            z_slider.setMaximum(127)
            z_slider.setValue(0)
            z_slider.setMinimumWidth(150)
            z_slider.valueChanged.connect(
                lambda v: self._on_slider_changed("z", v)
            )
            z_layout.addWidget(z_slider)
            
            z_value = QLabel("0")
            z_value.setMinimumWidth(30)
            z_layout.addWidget(z_value)
            
            layout.addLayout(z_layout)
            self.sliders["z"] = (z_slider, z_value)
    
    def _on_slider_changed(self, axis: str, value: int):
        """Send CC output when slider moves."""
        slider, value_label = self.sliders[axis]
        value_label.setText(str(value))
        
        if axis == "x" and self.element.x_axis_cc_mappings:
            m = self.element.x_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc",
                "cc": m.control,
                "channel": m.channel,
                "value": value,
                "axis": "X"
            })
        elif axis == "y" and self.element.y_axis_cc_mappings:
            m = self.element.y_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc",
                "cc": m.control,
                "channel": m.channel,
                "value": value,
                "axis": "Y"
            })
        elif axis == "z" and self.element.z_axis_cc_mappings:
            m = self.element.z_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc",
                "cc": m.control,
                "channel": m.channel,
                "value": value,
                "axis": "Z"
            })


class InteractiveXYPadWidget(QWidget):
    """2D mouse draggable XY pad for testing."""
    
    midi_send_requested = pyqtSignal(str, dict)
    
    def __init__(self, element: MorphZone, send_callback: Callable[[str, dict], None]):
        super().__init__()
        self.element = element
        self.send_callback = send_callback
        self.is_dragging = False
        self.last_pos = None
        
        self.setMinimumSize(200, 200)
        self.setStyleSheet("border: 2px solid #666;")
        self.setCursor(Qt.CursorShape.CrossCursor)
    
    def mousePressEvent(self, event: QMouseEvent):
        self.is_dragging = True
        self.last_pos = event.pos()
        self._send_xy_values(event.pos())
    
    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_dragging:
            self._send_xy_values(event.pos())
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        self.is_dragging = False
        self.last_pos = None
    
    def _send_xy_values(self, pos: QPoint):
        """Convert mouse position to 0-127 CC values."""
        x_norm = pos.x() / max(1, self.width())
        y_norm = 1.0 - (pos.y() / max(1, self.height()))  # invert Y
        
        x_cc = int(max(0, min(127, x_norm * 127)))
        y_cc = int(max(0, min(127, y_norm * 127)))
        
        # Send X
        if self.element.x_axis_cc_mappings:
            m = self.element.x_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc",
                "cc": m.control,
                "channel": m.channel,
                "value": x_cc,
                "axis": "X"
            })
        
        # Send Y
        if self.element.y_axis_cc_mappings:
            m = self.element.y_axis_cc_mappings[0]
            self.send_callback(self.element.unique_id, {
                "type": "cc",
                "cc": m.control,
                "channel": m.channel,
                "value": y_cc,
                "axis": "Y"
            })
        
        self.update()
    
    def paintEvent(self, event):
        """Draw crosshair at last position."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QBrush(QColor(30, 30, 30)))
        
        if self.last_pos:
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            painter.drawLine(0, self.last_pos.y(), self.width(), self.last_pos.y())
            painter.drawLine(self.last_pos.x(), 0, self.last_pos.x(), self.height())
            
            painter.setBrush(QBrush(QColor(0, 255, 0)))
            painter.drawEllipse(self.last_pos.x() - 5, self.last_pos.y() - 5, 10, 10)
        
        painter.end()


class PerformancePanel(QWidget):
    """
    Desktop performance testing panel.
    
    Shows interactive controls for selected elements and sends MIDI/OSC test messages.
    """
    
    midi_test_requested = pyqtSignal(str, dict)  # (element_id, payload)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_elements = []
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Header
        header = QLabel("Performance Test Panel")
        header.setFont(QFont("Courier", 12, QFont.Weight.Bold))
        header.setStyleSheet("background-color: #222; color: #0f0; padding: 8px;")
        layout.addWidget(header)
        
        # Scrollable area for controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.control_container = QWidget()
        self.control_layout = QVBoxLayout(self.control_container)
        self.control_layout.setContentsMargins(4, 4, 4, 4)
        self.control_layout.setSpacing(4)
        scroll.setWidget(self.control_container)
        layout.addWidget(scroll)
        
        # Status area
        self.status_label = QLabel("No element selected")
        self.status_label.setStyleSheet("color: #999; font-size: 9px; padding: 4px;")
        layout.addWidget(self.status_label)
    
    def set_selected_elements(self, elements: list, send_callback: Callable[[str, dict], None]):
        """Update performance panel when selection changes."""
        self.current_elements = elements
        
        # Clear old controls
        while self.control_layout.count():
            item = self.control_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not elements:
            self.status_label.setText("Select an element to test")
            return
        
        # Add controls for each element
        for elem in elements:
            if isinstance(elem, HitZone):
                widget = InteractiveHitZoneWidget(elem, send_callback)
                self.control_layout.addWidget(widget)
            elif isinstance(elem, MorphZone):
                # Show XY pad if both axes enabled
                if elem.is_x_axis_enabled and elem.is_y_axis_enabled:
                    xy_label = QLabel(f"{elem.display_name} (XY Pad)")
                    xy_label.setFont(QFont("Courier", 9, QFont.Weight.Bold))
                    self.control_layout.addWidget(xy_label)
                    
                    xy_widget = InteractiveXYPadWidget(elem, send_callback)
                    self.control_layout.addWidget(xy_widget)
                
                # Show individual sliders
                morph_widget = InteractiveMorphZoneWidget(elem, send_callback)
                self.control_layout.addWidget(morph_widget)
        
        self.control_layout.addStretch()
        
        num = len(elements)
        self.status_label.setText(
            f"Testing {num} element{'s' if num != 1 else ''} — "
            "Click buttons, drag sliders, or use XY pad to send MIDI"
        )
