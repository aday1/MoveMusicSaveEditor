"""
MoveMusic .mmc Save File Editor — PyQt6 UI

Tree view + property panel with add/duplicate/delete/mass-edit and undo/redo.
"""

from __future__ import annotations

import copy
import json
import hashlib
import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MMCEditor"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def _format_cc_mapping_summary(mappings: list, prefix: str = "") -> str:
    if not mappings:
        return f"{prefix}None"
    parts = [f"Ch{m.channel} CC{m.control} -> {m.value}" for m in mappings[:4]]
    if len(mappings) > 4:
        parts.append("...")
    return prefix + " | ".join(parts)


def _format_note_mapping_summary(mappings: list, prefix: str = "") -> str:
    if not mappings:
        return f"{prefix}None"
    parts = [f"Ch{m.channel} Note{m.note} -> Vel{int(m.velocity)}" for m in mappings[:4]]
    if len(mappings) > 4:
        parts.append("...")
    return prefix + " | ".join(parts)

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStatusBar,
    QTableWidget, QTableWidgetItem, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QInputDialog,
)

from model import (
    Project, Workspace, HitZone, MorphZone, TextLabel, GroupIE, UnknownElement,
    MidiNoteMapping, MidiCCMapping, Color, Vec3, Quat, Transform,
    load_project_from_file, save_project_to_file, save_project,
    duplicate_element,
)
from viewport3d import SceneViewport, QuadViewport
from template_generator import TEMPLATES, _row_positions, _grid_positions, _circle_positions, _make_group
from theme import STYLESHEET


# ---------------------------------------------------------------------------
# Undo commands
# ---------------------------------------------------------------------------

class SetPropertyCommand(QUndoCommand):
    def __init__(self, obj, attr: str, old_val, new_val, description: str = ""):
        super().__init__(description or f"Change {attr}")
        self.obj = obj
        self.attr = attr
        self.old_val = old_val
        self.new_val = new_val

    def redo(self):
        setattr(self.obj, self.attr, self.new_val)

    def undo(self):
        setattr(self.obj, self.attr, self.old_val)


class BatchSetPropertyCommand(QUndoCommand):
    def __init__(self, targets: list, attr: str, old_vals: list, new_val, description: str = ""):
        super().__init__(description or f"Mass change {attr}")
        self.targets = targets
        self.attr = attr
        self.old_vals = old_vals
        self.new_val = new_val

    def redo(self):
        for t in self.targets:
            setattr(t, self.attr, copy.deepcopy(self.new_val))

    def undo(self):
        for t, old in zip(self.targets, self.old_vals):
            setattr(t, self.attr, old)


class AddElementCommand(QUndoCommand):
    def __init__(self, project: Project, workspace: Workspace, element, description: str = ""):
        super().__init__(description or f"Add {type(element).__name__}")
        self.project = project
        self.workspace = workspace
        self.element = element

    def redo(self):
        self.project.elements.append(self.element)
        self.workspace.element_ids.append(self.element.unique_id)

    def undo(self):
        self.workspace.element_ids.remove(self.element.unique_id)
        self.project.elements.remove(self.element)


class DeleteElementCommand(QUndoCommand):
    def __init__(self, project: Project, element, workspace_refs: list, description: str = ""):
        super().__init__(description or f"Delete {element.unique_id}")
        self.project = project
        self.element = element
        self.elem_index = None
        self.workspace_refs = workspace_refs  # [(workspace, index_in_element_ids)]

    def redo(self):
        self.elem_index = self.project.elements.index(self.element)
        self.project.elements.remove(self.element)
        for ws, _ in self.workspace_refs:
            if self.element.unique_id in ws.element_ids:
                ws.element_ids.remove(self.element.unique_id)

    def undo(self):
        self.project.elements.insert(self.elem_index, self.element)
        for ws, idx in self.workspace_refs:
            ws.element_ids.insert(idx, self.element.unique_id)


class BatchDeleteCommand(QUndoCommand):
    """Delete multiple elements as a single undo step."""
    def __init__(self, project: Project, elements: list, description: str = ""):
        super().__init__(description or f"Delete {len(elements)} elements")
        self.project = project
        self.elements = elements[:]  # copy
        self.element_data = []  # will store (element, project_index, [(workspace, ws_index), ...])

    def redo(self):
        # Store deletion information
        self.element_data = []
        for element in self.elements:
            # Find project index
            project_index = self.project.elements.index(element)

            # Find all workspace references
            workspace_refs = []
            for ws in self.project.workspaces:
                if element.unique_id in ws.element_ids:
                    ws_index = ws.element_ids.index(element.unique_id)
                    workspace_refs.append((ws, ws_index))

            self.element_data.append((element, project_index, workspace_refs))

        # Remove elements (in reverse order to preserve indices)
        for element, _, workspace_refs in reversed(self.element_data):
            self.project.elements.remove(element)
            for ws, _ in workspace_refs:
                if element.unique_id in ws.element_ids:
                    ws.element_ids.remove(element.unique_id)

    def undo(self):
        # Restore elements
        for element, project_index, workspace_refs in self.element_data:
            self.project.elements.insert(project_index, element)
            for ws, ws_index in workspace_refs:
                ws.element_ids.insert(ws_index, element.unique_id)


class DuplicateElementCommand(QUndoCommand):
    def __init__(self, project: Project, workspace: Workspace, new_element, description: str = ""):
        super().__init__(description or f"Duplicate to {new_element.unique_id}")
        self.project = project
        self.workspace = workspace
        self.new_element = new_element

    def redo(self):
        self.project.elements.append(self.new_element)
        self.workspace.element_ids.append(self.new_element.unique_id)

    def undo(self):
        self.workspace.element_ids.remove(self.new_element.unique_id)
        self.project.elements.remove(self.new_element)


class AddTemplateCommand(QUndoCommand):
    """Add a batch of elements from a template (single undo step)."""
    def __init__(self, project: Project, workspace: Workspace, elements: list, description: str = ""):
        super().__init__(description or "Add template")
        self.project = project
        self.workspace = workspace
        self.elements = elements

    def redo(self):
        for elem in self.elements:
            self.project.elements.append(elem)
            self.workspace.element_ids.append(elem.unique_id)

    def undo(self):
        for elem in reversed(self.elements):
            self.workspace.element_ids.remove(elem.unique_id)
            self.project.elements.remove(elem)


class GroupMembershipCommand(QUndoCommand):
    """Add or remove elements from a group with bounding box update."""
    def __init__(self, group: GroupIE, old_members: list, new_members: list, description: str = ""):
        super().__init__(description or "Change group membership")
        self.group = group
        self.old_members = old_members[:]  # copy
        self.new_members = new_members[:]  # copy
        self.old_bbox = copy.deepcopy(group.bounding_box)
        self.new_bbox = None  # will be set by caller

    def redo(self):
        self.group.group_items = self.new_members[:]
        if self.new_bbox:
            self.group.bounding_box = copy.deepcopy(self.new_bbox)

    def undo(self):
        self.group.group_items = self.old_members[:]
        self.group.bounding_box = copy.deepcopy(self.old_bbox)


class BatchMoveCommand(QUndoCommand):
    """Move multiple elements as a single undo step."""
    def __init__(self, elements, old_positions, new_positions, description="Move elements"):
        super().__init__(description)
        self.elements = elements
        self.old_positions = [copy.deepcopy(p) for p in old_positions]
        self.new_positions = [copy.deepcopy(p) for p in new_positions]

    def redo(self):
        for elem, pos in zip(self.elements, self.new_positions):
            elem.transform.translation = copy.deepcopy(pos)

    def undo(self):
        for elem, pos in zip(self.elements, self.old_positions):
            elem.transform.translation = copy.deepcopy(pos)


class MultiSetPropertyCommand(QUndoCommand):
    """Apply multiple property assignments as a single undo step."""
    def __init__(self, changes: list, description: str = "Change properties"):
        super().__init__(description)
        self.changes = [
            (obj, attr, copy.deepcopy(old_val), copy.deepcopy(new_val))
            for obj, attr, old_val, new_val in changes
        ]

    def redo(self):
        for obj, attr, _, new_val in self.changes:
            setattr(obj, attr, copy.deepcopy(new_val))

    def undo(self):
        for obj, attr, old_val, _ in self.changes:
            setattr(obj, attr, copy.deepcopy(old_val))


# ---------------------------------------------------------------------------
# Property panel widgets
# ---------------------------------------------------------------------------

class CollapsibleGroup(QGroupBox):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.toggled.connect(self._on_toggle)
        self._layout = QFormLayout()
        self.setLayout(self._layout)

    def _on_toggle(self, checked):
        for i in range(self._layout.count()):
            w = self._layout.itemAt(i).widget()
            if w:
                w.setVisible(checked)

    def form(self) -> QFormLayout:
        return self._layout


class ColorButton(QPushButton):
    color_changed = pyqtSignal(object)

    def __init__(self, color: Color = None, parent=None):
        super().__init__(parent)
        self._color = color or Color()
        self._update_style()
        self.clicked.connect(self._pick_color)
        self.setFixedHeight(28)

    def set_color(self, c: Color):
        self._color = c
        self._update_style()

    def get_color(self) -> Color:
        return self._color

    def _update_style(self):
        r = int(min(1.0, self._color.r) * 255)
        g = int(min(1.0, self._color.g) * 255)
        b = int(min(1.0, self._color.b) * 255)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg = "#0a0c10" if lum > 128 else "#d0e0f0"
        self.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {fg};"
            f"border: 1px solid #1e2a3a; border-radius: 3px;"
            f"font-family: Consolas, monospace; font-weight: 600;"
        )
        self.setText(f"{r:3d} {g:3d} {b:3d}")

    def _pick_color(self):
        r = int(min(1.0, self._color.r) * 255)
        g = int(min(1.0, self._color.g) * 255)
        b = int(min(1.0, self._color.b) * 255)
        qc = QColorDialog.getColor(QColor(r, g, b), self, "Pick Color")
        if qc.isValid():
            new_color = Color(qc.redF(), qc.greenF(), qc.blueF(), self._color.a)
            self._color = new_color
            self._update_style()
            self.color_changed.emit(new_color)


class MidiNoteTable(QTableWidget):
    data_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Channel", "Note", "Velocity"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setMaximumHeight(150)
        # Note: data_changed signal will be connected when spin boxes are created

    def _create_spin_widgets(self, row):
        """Create enhanced spin box widgets for a table row."""
        # Channel spin box (1-16)
        channel_spin = _make_midi_channel_spin(1)
        channel_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 0, channel_spin)

        # Note spin box (0-127)
        note_spin = _make_midi_note_spin(60)
        note_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 1, note_spin)

        # Velocity spin box (0.0-1.0, 2 decimal places)
        velocity_spin = _make_float_spin(1.0, 0.0, 1.0, decimals=2, step=0.01)
        velocity_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 2, velocity_spin)

    def set_mappings(self, mappings: list):
        self.blockSignals(True)

        # Clear existing rows
        self.setRowCount(0)

        # Add rows for each mapping
        self.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self._create_spin_widgets(i)

            # Set values
            self.cellWidget(i, 0).setValue(m.channel)
            self.cellWidget(i, 1).setValue(m.note)
            self.cellWidget(i, 2).setValue(m.velocity)

        self.blockSignals(False)

    def get_mappings(self) -> list:
        mappings = []
        for i in range(self.rowCount()):
            try:
                channel_widget = self.cellWidget(i, 0)
                note_widget = self.cellWidget(i, 1)
                velocity_widget = self.cellWidget(i, 2)

                if channel_widget and note_widget and velocity_widget:
                    ch = channel_widget.value()
                    note = note_widget.value()
                    vel = velocity_widget.value()
                    mappings.append(MidiNoteMapping(ch, note, vel))
            except (ValueError, AttributeError):
                pass
        return mappings

    def add_row(self):
        """Add a new row with default values."""
        row = self.rowCount()
        self.setRowCount(row + 1)
        self._create_spin_widgets(row)
        self.data_changed.emit()

    def remove_selected_row(self):
        """Remove the currently selected row."""
        current_row = self.currentRow()
        if current_row >= 0:
            self.removeRow(current_row)
            self.data_changed.emit()


class MidiCCTable(QTableWidget):
    data_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Channel", "CC #", "-> Value"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setMaximumHeight(150)

    def _create_spin_widgets(self, row):
        """Create enhanced spin box widgets for a table row."""
        # Channel spin box (1-16)
        channel_spin = _make_midi_channel_spin(1)
        channel_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 0, channel_spin)

        # Control spin box (0-127)
        control_spin = _make_midi_cc_spin(0)
        control_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 1, control_spin)

        # Value spin box (0-127)
        value_spin = _make_midi_cc_spin(0)
        value_spin.valueChanged.connect(lambda: self.data_changed.emit())
        self.setCellWidget(row, 2, value_spin)

    def set_mappings(self, mappings: list):
        self.blockSignals(True)

        # Clear existing rows
        self.setRowCount(0)

        # Add rows for each mapping
        self.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self._create_spin_widgets(i)

            # Set values
            self.cellWidget(i, 0).setValue(m.channel)
            self.cellWidget(i, 1).setValue(m.control)
            self.cellWidget(i, 2).setValue(m.value)

        self.blockSignals(False)

    def get_mappings(self) -> list:
        mappings = []
        for i in range(self.rowCount()):
            try:
                channel_widget = self.cellWidget(i, 0)
                control_widget = self.cellWidget(i, 1)
                value_widget = self.cellWidget(i, 2)

                if channel_widget and control_widget and value_widget:
                    ch = channel_widget.value()
                    ctrl = control_widget.value()
                    val = value_widget.value()
                    mappings.append(MidiCCMapping(ch, ctrl, val))
            except (ValueError, AttributeError):
                pass
        return mappings

    def add_row(self):
        """Add a new row with default values."""
        row = self.rowCount()
        self.setRowCount(row + 1)
        self._create_spin_widgets(row)
        self.data_changed.emit()

    def remove_selected_row(self):
        """Remove the currently selected row."""
        current_row = self.currentRow()
        if current_row >= 0:
            self.removeRow(current_row)
            self.data_changed.emit()


class EnhancedDoubleSpinBox(QDoubleSpinBox):
    """Enhanced double spin box with better keyboard and mouse support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccelerated(True)  # Accelerate when holding keys
        self.setKeyboardTracking(True)  # Update immediately on key press

    def keyPressEvent(self, event):
        """Handle enhanced keyboard input for better control."""
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key.Key_Up:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Up: Large increment (10x)
                self.setValue(self.value() + self.singleStep() * 10)
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Up: Small increment (0.1x)
                self.setValue(self.value() + self.singleStep() * 0.1)
            else:
                # Normal up
                super().keyPressEvent(event)
        elif key == Qt.Key.Key_Down:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Down: Large decrement (10x)
                self.setValue(self.value() - self.singleStep() * 10)
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Down: Small decrement (0.1x)
                self.setValue(self.value() - self.singleStep() * 0.1)
            else:
                # Normal down
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        """Handle mouse wheel for increment/decrement."""
        if self.hasFocus():
            delta = event.angleDelta().y()
            if delta > 0:
                self.stepUp()
            elif delta < 0:
                self.stepDown()
            event.accept()
        else:
            super().wheelEvent(event)


class EnhancedSpinBox(QSpinBox):
    """Enhanced integer spin box with better keyboard and mouse support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccelerated(True)  # Accelerate when holding keys
        self.setKeyboardTracking(True)  # Update immediately on key press

    def keyPressEvent(self, event):
        """Handle enhanced keyboard input for better control."""
        key = event.key()
        modifiers = event.modifiers()

        if key == Qt.Key.Key_Up:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Up: Large increment (10x)
                self.setValue(self.value() + self.singleStep() * 10)
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Up: Small increment (but at least 1)
                self.setValue(self.value() + max(1, self.singleStep() // 10))
            else:
                # Normal up
                super().keyPressEvent(event)
        elif key == Qt.Key.Key_Down:
            if modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+Down: Large decrement (10x)
                self.setValue(self.value() - self.singleStep() * 10)
            elif modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Down: Small decrement (but at least 1)
                self.setValue(self.value() - max(1, self.singleStep() // 10))
            else:
                # Normal down
                super().keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        """Handle mouse wheel for increment/decrement."""
        if self.hasFocus():
            delta = event.angleDelta().y()
            if delta > 0:
                self.stepUp()
            elif delta < 0:
                self.stepDown()
            event.accept()
        else:
            super().wheelEvent(event)


def _make_float_spin(value=0.0, min_val=-99999.0, max_val=99999.0, decimals=2, step=1.0):
    """Create enhanced double spin box with better keyboard/mouse support."""
    spin = EnhancedDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setValue(value)
    return spin


def _make_int_spin(value=0, min_val=-999999, max_val=999999, step=1):
    """Create enhanced integer spin box with better keyboard/mouse support."""
    spin = EnhancedSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setValue(value)
    return spin


# Specialized spin boxes for different use cases
def _make_position_spin(value=0.0):
    """Position values - step by 1 unit, 1 decimal place."""
    return _make_float_spin(value, -9999.0, 9999.0, decimals=1, step=1.0)

def _make_scale_spin(value=1.0):
    """Scale values - step by 0.1, 2 decimal places, positive only."""
    return _make_float_spin(value, 0.01, 100.0, decimals=2, step=0.1)

def _make_midi_cc_spin(value=64):
    """MIDI CC values - 0-127 range, step by 1."""
    return _make_int_spin(value, 0, 127, step=1)

def _make_midi_note_spin(value=60):
    """MIDI note values - 0-127 range, step by 1."""
    return _make_int_spin(value, 0, 127, step=1)

def _make_midi_channel_spin(value=1):
    """MIDI channel values - 1-16 range, step by 1."""
    return _make_int_spin(value, 1, 16, step=1)


# ---------------------------------------------------------------------------
# Property panels
# ---------------------------------------------------------------------------

class ProjectPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.timestamp_label = QLabel()
        self.location_label = QLabel()
        layout.addRow("Project Name:", self.name_edit)
        layout.addRow("Timestamp:", self.timestamp_label)
        layout.addRow("User Location:", self.location_label)

    def load(self, project: Project):
        self.name_edit.blockSignals(True)
        self.name_edit.setText(project.project_name)
        self.name_edit.blockSignals(False)
        self.timestamp_label.setText(str(project.timestamp))
        loc = project.user_location
        self.location_label.setText(f"({loc.x:.2f}, {loc.y:.2f}, {loc.z:.2f})")


class WorkspacePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.enabled_check = QCheckBox()
        self.elements_label = QLabel()
        layout.addRow("Display Name:", self.name_edit)
        layout.addRow("Enabled:", self.enabled_check)
        layout.addRow("Elements:", self.elements_label)

    def load(self, ws: Workspace):
        self.name_edit.blockSignals(True)
        self.name_edit.setText(ws.display_name)
        self.name_edit.blockSignals(False)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(ws.enabled)
        self.enabled_check.blockSignals(False)
        self.elements_label.setText(str(len(ws.element_ids)))


class HitZonePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = None
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Identity
        id_group = CollapsibleGroup("Identity")
        self.unique_id_label = QLabel()
        self.name_edit = QLineEdit()
        id_group.form().addRow("Unique ID:", self.unique_id_label)
        id_group.form().addRow("Display Name:", self.name_edit)
        main_layout.addWidget(id_group)

        # Transform
        t_group = CollapsibleGroup("Transform")
        self.pos_x = _make_position_spin()
        self.pos_y = _make_position_spin()
        self.pos_z = _make_position_spin()
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X")); pos_row.addWidget(self.pos_x)
        pos_row.addWidget(QLabel("Y")); pos_row.addWidget(self.pos_y)
        pos_row.addWidget(QLabel("Z")); pos_row.addWidget(self.pos_z)
        pos_widget = QWidget(); pos_widget.setLayout(pos_row)
        t_group.form().addRow("Position:", pos_widget)

        self.rot_x = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_y = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_z = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_w = _make_float_spin(-1, 1, decimals=3, step=0.1)
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("X")); rot_row.addWidget(self.rot_x)
        rot_row.addWidget(QLabel("Y")); rot_row.addWidget(self.rot_y)
        rot_row.addWidget(QLabel("Z")); rot_row.addWidget(self.rot_z)
        rot_row.addWidget(QLabel("W")); rot_row.addWidget(self.rot_w)
        rot_widget = QWidget(); rot_widget.setLayout(rot_row)
        t_group.form().addRow("Rotation:", rot_widget)

        self.scale_x = _make_scale_spin()
        self.scale_y = _make_scale_spin()
        self.scale_z = _make_scale_spin()
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("X")); scale_row.addWidget(self.scale_x)
        scale_row.addWidget(QLabel("Y")); scale_row.addWidget(self.scale_y)
        scale_row.addWidget(QLabel("Z")); scale_row.addWidget(self.scale_z)
        scale_widget = QWidget(); scale_widget.setLayout(scale_row)
        t_group.form().addRow("Scale:", scale_widget)
        main_layout.addWidget(t_group)

        # MIDI
        midi_group = CollapsibleGroup("MIDI")
        self.note_table = MidiNoteTable()
        self.note_summary_label = QLabel()
        self.note_summary_label.setWordWrap(True)
        self.note_add_btn = QPushButton("Add Note")
        self.note_remove_btn = QPushButton("Remove Note")
        note_btns = QHBoxLayout()
        note_btns.addWidget(self.note_add_btn)
        note_btns.addWidget(self.note_remove_btn)
        note_btn_w = QWidget(); note_btn_w.setLayout(note_btns)
        midi_group.form().addRow("Note Mappings:", self.note_table)
        midi_group.form().addRow("Note Summary:", self.note_summary_label)
        midi_group.form().addRow("", note_btn_w)

        self.cc_table = MidiCCTable()
        self.cc_summary_label = QLabel()
        self.cc_summary_label.setWordWrap(True)
        self.cc_add_btn = QPushButton("Add CC")
        self.cc_remove_btn = QPushButton("Remove CC")
        cc_btns = QHBoxLayout()
        cc_btns.addWidget(self.cc_add_btn)
        cc_btns.addWidget(self.cc_remove_btn)
        cc_btn_w = QWidget(); cc_btn_w.setLayout(cc_btns)
        midi_group.form().addRow("CC Mappings:", self.cc_table)
        midi_group.form().addRow("CC Summary:", self.cc_summary_label)
        midi_group.form().addRow("", cc_btn_w)

        self.msg_type_combo = QComboBox()
        self.msg_type_combo.addItems(["EMidiMessageType::Note", "EMidiMessageType::CC"])
        midi_group.form().addRow("Message Type:", self.msg_type_combo)
        main_layout.addWidget(midi_group)

        # Behavior
        beh_group = CollapsibleGroup("Behavior")
        self.behavior_combo = QComboBox()
        self.behavior_combo.addItems([
            "EHitZoneBehavior::Hold", "EHitZoneBehavior::Toggle",
            "EHitZoneBehavior::TimedClose", "EHitZoneBehavior::OneShot"])
        beh_group.form().addRow("Behavior:", self.behavior_combo)
        self.timed_close = _make_float_spin(1.0, 0.0, 60.0, 2)
        beh_group.form().addRow("Timed Close (s):", self.timed_close)
        self.one_shot_combo = QComboBox()
        self.one_shot_combo.addItems([
            "EOneShotSample::None",
            "EOneShotSample::DrumKick",
            "EOneShotSample::DrumSnare",
            "EOneShotSample::DrumHiHat",
            "EOneShotSample::DrumTom",
            "EOneShotSample::DrumCymbal",
            "EOneShotSample::DrumClap",
            "EOneShotSample::DrumRim",
        ])
        self.one_shot_combo.setEditable(True)  # allow custom values
        beh_group.form().addRow("One-Shot Sample:", self.one_shot_combo)
        main_layout.addWidget(beh_group)

        # Velocity
        vel_group = CollapsibleGroup("Velocity")
        self.use_vel_check = QCheckBox()
        vel_group.form().addRow("Use Velocity Sensitivity:", self.use_vel_check)
        self.fixed_vel = _make_float_spin(1.0, 0.0, 1.0, 2)
        vel_group.form().addRow("Fixed MIDI Velocity:", self.fixed_vel)
        self.min_phys = _make_float_spin(0.0, 0.0, 10000.0, 1)
        vel_group.form().addRow("Min Physics Velocity:", self.min_phys)
        self.max_phys = _make_float_spin(600.0, 0.0, 10000.0, 1)
        vel_group.form().addRow("Max Physics Velocity:", self.max_phys)
        self.min_midi = _make_float_spin(0.0, 0.0, 1.0, 2)
        vel_group.form().addRow("Min MIDI Velocity:", self.min_midi)
        self.max_midi = _make_float_spin(1.0, 0.0, 1.0, 2)
        vel_group.form().addRow("Max MIDI Velocity:", self.max_midi)
        main_layout.addWidget(vel_group)

        # Appearance / state
        app_group = CollapsibleGroup("Appearance")
        self.color_btn = ColorButton()
        app_group.form().addRow("Color:", self.color_btn)
        self.enabled_check = QCheckBox()
        app_group.form().addRow("Enabled:", self.enabled_check)
        self.locked_check = QCheckBox()
        app_group.form().addRow("Locked:", self.locked_check)
        self.toggle_state_check = QCheckBox()
        app_group.form().addRow("Toggle State:", self.toggle_state_check)
        main_layout.addWidget(app_group)

        main_layout.addStretch()

        # Connect table buttons
        self.note_add_btn.clicked.connect(self._add_note_row)
        self.note_remove_btn.clicked.connect(self._remove_note_row)
        self.cc_add_btn.clicked.connect(self._add_cc_row)
        self.cc_remove_btn.clicked.connect(self._remove_cc_row)
        self.note_table.data_changed.connect(self._refresh_midi_summaries)
        self.cc_table.data_changed.connect(self._refresh_midi_summaries)

    def _refresh_midi_summaries(self):
        self.note_summary_label.setText(_format_note_mapping_summary(self.note_table.get_mappings()))
        self.cc_summary_label.setText(_format_cc_mapping_summary(self.cc_table.get_mappings()))

    def _add_note_row(self):
        self.note_table.add_row()

    def _remove_note_row(self):
        self.note_table.remove_selected_row()

    def _add_cc_row(self):
        self.cc_table.add_row()

    def _remove_cc_row(self):
        self.cc_table.remove_selected_row()

    def load(self, hz: HitZone):
        self._target = hz
        self.unique_id_label.setText(hz.unique_id)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(hz.display_name)
        self.name_edit.blockSignals(False)

        t = hz.transform
        for spin, val in [
            (self.pos_x, t.translation.x), (self.pos_y, t.translation.y), (self.pos_z, t.translation.z),
            (self.rot_x, t.rotation.x), (self.rot_y, t.rotation.y),
            (self.rot_z, t.rotation.z), (self.rot_w, t.rotation.w),
            (self.scale_x, t.scale.x), (self.scale_y, t.scale.y), (self.scale_z, t.scale.z),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self.note_table.set_mappings(hz.midi_note_mappings)
        self.cc_table.set_mappings(hz.midi_cc_mappings)
        self._refresh_midi_summaries()

        self.msg_type_combo.blockSignals(True)
        idx = self.msg_type_combo.findText(hz.midi_message_type)
        if idx >= 0:
            self.msg_type_combo.setCurrentIndex(idx)
        self.msg_type_combo.blockSignals(False)

        self.behavior_combo.blockSignals(True)
        idx = self.behavior_combo.findText(hz.behavior)
        if idx >= 0:
            self.behavior_combo.setCurrentIndex(idx)
        self.behavior_combo.blockSignals(False)

        self.timed_close.blockSignals(True)
        self.timed_close.setValue(hz.timed_close_seconds)
        self.timed_close.blockSignals(False)

        self.one_shot_combo.blockSignals(True)
        idx = self.one_shot_combo.findText(hz.one_shot_sample)
        if idx >= 0:
            self.one_shot_combo.setCurrentIndex(idx)
        else:
            self.one_shot_combo.setCurrentText(hz.one_shot_sample)
        self.one_shot_combo.blockSignals(False)

        self.use_vel_check.blockSignals(True)
        self.use_vel_check.setChecked(hz.should_use_velocity_sensitivity)
        self.use_vel_check.blockSignals(False)

        for spin, val in [
            (self.fixed_vel, hz.fixed_midi_velocity_output),
            (self.min_phys, hz.min_physics_velocity_input),
            (self.max_phys, hz.max_physics_velocity_input),
            (self.min_midi, hz.min_midi_velocity_output),
            (self.max_midi, hz.max_midi_velocity_output),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self.color_btn.set_color(hz.color)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(hz.is_enabled)
        self.enabled_check.blockSignals(False)
        self.locked_check.blockSignals(True)
        self.locked_check.setChecked(hz.is_locked)
        self.locked_check.blockSignals(False)
        self.toggle_state_check.blockSignals(True)
        self.toggle_state_check.setChecked(hz.toggle_state)
        self.toggle_state_check.blockSignals(False)


class MorphZonePanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = None
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Identity
        id_group = CollapsibleGroup("Identity")
        self.unique_id_label = QLabel()
        self.name_edit = QLineEdit()
        id_group.form().addRow("Unique ID:", self.unique_id_label)
        id_group.form().addRow("Display Name:", self.name_edit)
        main_layout.addWidget(id_group)

        # Transform
        t_group = CollapsibleGroup("Transform")
        self.pos_x = _make_position_spin()
        self.pos_y = _make_position_spin()
        self.pos_z = _make_position_spin()
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X")); pos_row.addWidget(self.pos_x)
        pos_row.addWidget(QLabel("Y")); pos_row.addWidget(self.pos_y)
        pos_row.addWidget(QLabel("Z")); pos_row.addWidget(self.pos_z)
        pos_widget = QWidget(); pos_widget.setLayout(pos_row)
        t_group.form().addRow("Position:", pos_widget)

        self.rot_x = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_y = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_z = _make_float_spin(-1, 1, decimals=3, step=0.1)
        self.rot_w = _make_float_spin(-1, 1, decimals=3, step=0.1)
        rot_row = QHBoxLayout()
        rot_row.addWidget(QLabel("X")); rot_row.addWidget(self.rot_x)
        rot_row.addWidget(QLabel("Y")); rot_row.addWidget(self.rot_y)
        rot_row.addWidget(QLabel("Z")); rot_row.addWidget(self.rot_z)
        rot_row.addWidget(QLabel("W")); rot_row.addWidget(self.rot_w)
        rot_widget = QWidget(); rot_widget.setLayout(rot_row)
        t_group.form().addRow("Rotation:", rot_widget)

        self.scale_x = _make_scale_spin()
        self.scale_y = _make_scale_spin()
        self.scale_z = _make_scale_spin()
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("X")); scale_row.addWidget(self.scale_x)
        scale_row.addWidget(QLabel("Y")); scale_row.addWidget(self.scale_y)
        scale_row.addWidget(QLabel("Z")); scale_row.addWidget(self.scale_z)
        scale_widget = QWidget(); scale_widget.setLayout(scale_row)
        t_group.form().addRow("Scale:", scale_widget)
        main_layout.addWidget(t_group)

        # X Axis
        x_group = CollapsibleGroup("X Axis MIDI CC")
        self.x_enabled = QCheckBox()
        x_group.form().addRow("Enabled:", self.x_enabled)
        self.x_cc_table = MidiCCTable()
        self.x_summary_label = QLabel()
        self.x_summary_label.setWordWrap(True)
        x_group.form().addRow("Mappings:", self.x_cc_table)
        x_group.form().addRow("Summary:", self.x_summary_label)
        x_btns = QHBoxLayout()
        self.x_add = QPushButton("Add"); self.x_remove = QPushButton("Remove")
        x_btns.addWidget(self.x_add); x_btns.addWidget(self.x_remove)
        x_btn_w = QWidget(); x_btn_w.setLayout(x_btns)
        x_group.form().addRow("", x_btn_w)
        main_layout.addWidget(x_group)

        # Y Axis
        y_group = CollapsibleGroup("Y Axis MIDI CC")
        self.y_enabled = QCheckBox()
        y_group.form().addRow("Enabled:", self.y_enabled)
        self.y_cc_table = MidiCCTable()
        self.y_summary_label = QLabel()
        self.y_summary_label.setWordWrap(True)
        y_group.form().addRow("Mappings:", self.y_cc_table)
        y_group.form().addRow("Summary:", self.y_summary_label)
        y_btns = QHBoxLayout()
        self.y_add = QPushButton("Add"); self.y_remove = QPushButton("Remove")
        y_btns.addWidget(self.y_add); y_btns.addWidget(self.y_remove)
        y_btn_w = QWidget(); y_btn_w.setLayout(y_btns)
        y_group.form().addRow("", y_btn_w)
        main_layout.addWidget(y_group)

        # Z Axis
        z_group = CollapsibleGroup("Z Axis MIDI CC")
        self.z_enabled = QCheckBox()
        z_group.form().addRow("Enabled:", self.z_enabled)
        self.z_cc_table = MidiCCTable()
        self.z_summary_label = QLabel()
        self.z_summary_label.setWordWrap(True)
        z_group.form().addRow("Mappings:", self.z_cc_table)
        z_group.form().addRow("Summary:", self.z_summary_label)
        z_btns = QHBoxLayout()
        self.z_add = QPushButton("Add"); self.z_remove = QPushButton("Remove")
        z_btns.addWidget(self.z_add); z_btns.addWidget(self.z_remove)
        z_btn_w = QWidget(); z_btn_w.setLayout(z_btns)
        z_group.form().addRow("", z_btn_w)
        main_layout.addWidget(z_group)

        # Settings
        s_group = CollapsibleGroup("Settings")
        self.dimensions_combo = QComboBox()
        self.dimensions_combo.addItems(["EDimensions::One", "EDimensions::Two", "EDimensions::Three"])
        s_group.form().addRow("Dimensions:", self.dimensions_combo)
        self.soloed_combo = QComboBox()
        self.soloed_combo.addItems(["EAxis::None", "EAxis::X", "EAxis::Y", "EAxis::Z"])
        s_group.form().addRow("Soloed Axis:", self.soloed_combo)
        self.release_combo = QComboBox()
        self.release_combo.addItems([
            "EMorphZoneReleaseBehavior::Stop",
            "EMorphZoneReleaseBehavior::Return",
            "EMorphZoneReleaseBehavior::Continue"
        ])
        s_group.form().addRow("Release Behavior:", self.release_combo)
        main_layout.addWidget(s_group)

        # Appearance
        app_group = CollapsibleGroup("Appearance")
        self.color_btn = ColorButton()
        app_group.form().addRow("Color:", self.color_btn)
        self.enabled_check = QCheckBox()
        app_group.form().addRow("Enabled:", self.enabled_check)
        self.locked_check = QCheckBox()
        app_group.form().addRow("Locked:", self.locked_check)
        main_layout.addWidget(app_group)

        main_layout.addStretch()

        # CC table buttons
        self.x_add.clicked.connect(lambda: self._add_cc_row(self.x_cc_table))
        self.x_remove.clicked.connect(lambda: self._remove_cc_row(self.x_cc_table))
        self.y_add.clicked.connect(lambda: self._add_cc_row(self.y_cc_table))
        self.y_remove.clicked.connect(lambda: self._remove_cc_row(self.y_cc_table))
        self.z_add.clicked.connect(lambda: self._add_cc_row(self.z_cc_table))
        self.z_remove.clicked.connect(lambda: self._remove_cc_row(self.z_cc_table))
        self.x_cc_table.data_changed.connect(self._refresh_axis_summaries)
        self.y_cc_table.data_changed.connect(self._refresh_axis_summaries)
        self.z_cc_table.data_changed.connect(self._refresh_axis_summaries)

    def _refresh_axis_summaries(self):
        self.x_summary_label.setText(_format_cc_mapping_summary(self.x_cc_table.get_mappings(), prefix="X: "))
        self.y_summary_label.setText(_format_cc_mapping_summary(self.y_cc_table.get_mappings(), prefix="Y: "))
        self.z_summary_label.setText(_format_cc_mapping_summary(self.z_cc_table.get_mappings(), prefix="Z: "))

    def _add_cc_row(self, table):
        table.add_row()

    def _remove_cc_row(self, table):
        table.remove_selected_row()

    def load(self, mz: MorphZone):
        self._target = mz
        self.unique_id_label.setText(mz.unique_id)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(mz.display_name)
        self.name_edit.blockSignals(False)

        t = mz.transform
        for spin, val in [
            (self.pos_x, t.translation.x), (self.pos_y, t.translation.y), (self.pos_z, t.translation.z),
            (self.rot_x, t.rotation.x), (self.rot_y, t.rotation.y),
            (self.rot_z, t.rotation.z), (self.rot_w, t.rotation.w),
            (self.scale_x, t.scale.x), (self.scale_y, t.scale.y), (self.scale_z, t.scale.z),
        ]:
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

        self.x_enabled.blockSignals(True)
        self.x_enabled.setChecked(mz.is_x_axis_enabled)
        self.x_enabled.blockSignals(False)
        self.x_cc_table.set_mappings(mz.x_axis_cc_mappings)

        self.y_enabled.blockSignals(True)
        self.y_enabled.setChecked(mz.is_y_axis_enabled)
        self.y_enabled.blockSignals(False)
        self.y_cc_table.set_mappings(mz.y_axis_cc_mappings)

        self.z_enabled.blockSignals(True)
        self.z_enabled.setChecked(mz.is_z_axis_enabled)
        self.z_enabled.blockSignals(False)
        self.z_cc_table.set_mappings(mz.z_axis_cc_mappings)
        self._refresh_axis_summaries()

        self.dimensions_combo.blockSignals(True)
        idx = self.dimensions_combo.findText(mz.dimensions)
        if idx >= 0:
            self.dimensions_combo.setCurrentIndex(idx)
        self.dimensions_combo.blockSignals(False)

        self.soloed_combo.blockSignals(True)
        idx = self.soloed_combo.findText(mz.soloed_axis)
        if idx >= 0:
            self.soloed_combo.setCurrentIndex(idx)
        self.soloed_combo.blockSignals(False)

        self.release_combo.blockSignals(True)
        idx = self.release_combo.findText(mz.release_behavior)
        if idx >= 0:
            self.release_combo.setCurrentIndex(idx)
        self.release_combo.blockSignals(False)

        self.color_btn.set_color(mz.color)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(mz.is_enabled)
        self.enabled_check.blockSignals(False)
        self.locked_check.blockSignals(True)
        self.locked_check.setChecked(mz.is_locked)
        self.locked_check.blockSignals(False)


class TextLabelPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = None
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Identity
        id_group = CollapsibleGroup("Identity")
        self.unique_id_label = QLabel()
        self.name_edit = QLineEdit()
        id_group.form().addRow("Unique ID:", self.unique_id_label)
        id_group.form().addRow("Label Text:", self.name_edit)
        main_layout.addWidget(id_group)

        # Transform
        t_group = CollapsibleGroup("Transform")
        self.pos_x = _make_position_spin()
        self.pos_y = _make_position_spin()
        self.pos_z = _make_position_spin()
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X")); pos_row.addWidget(self.pos_x)
        pos_row.addWidget(QLabel("Y")); pos_row.addWidget(self.pos_y)
        pos_row.addWidget(QLabel("Z")); pos_row.addWidget(self.pos_z)
        pos_widget = QWidget(); pos_widget.setLayout(pos_row)
        t_group.form().addRow("Position:", pos_widget)

        self.scale_x = _make_scale_spin()
        self.scale_y = _make_scale_spin()
        self.scale_z = _make_scale_spin()
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("X")); scale_row.addWidget(self.scale_x)
        scale_row.addWidget(QLabel("Y")); scale_row.addWidget(self.scale_y)
        scale_row.addWidget(QLabel("Z")); scale_row.addWidget(self.scale_z)
        scale_widget = QWidget(); scale_widget.setLayout(scale_row)
        t_group.form().addRow("Scale:", scale_widget)
        main_layout.addWidget(t_group)

        # Appearance
        app_group = CollapsibleGroup("Appearance")
        self.color_btn = ColorButton()
        self.enabled_check = QCheckBox("Enabled")
        self.locked_check = QCheckBox("Locked")
        app_group.form().addRow("Color:", self.color_btn)
        app_group.form().addRow(self.enabled_check)
        app_group.form().addRow(self.locked_check)
        main_layout.addWidget(app_group)

        main_layout.addStretch()

    def load(self, tl: TextLabel):
        self._target = tl
        self.unique_id_label.setText(tl.unique_id)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(tl.display_name)
        self.name_edit.blockSignals(False)
        p = tl.transform.translation
        for spin, val in [(self.pos_x, p.x), (self.pos_y, p.y), (self.pos_z, p.z)]:
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)
        s = tl.transform.scale
        for spin, val in [(self.scale_x, s.x), (self.scale_y, s.y), (self.scale_z, s.z)]:
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)
        self.color_btn.set_color(tl.color)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(tl.is_enabled)
        self.enabled_check.blockSignals(False)
        self.locked_check.blockSignals(True)
        self.locked_check.setChecked(tl.is_locked)
        self.locked_check.blockSignals(False)


class GroupIEPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._target = None
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Identity
        id_group = CollapsibleGroup("Group Identity")
        self.unique_id_label = QLabel()
        self.name_edit = QLineEdit()
        id_group.form().addRow("Unique ID:", self.unique_id_label)
        id_group.form().addRow("Display Name:", self.name_edit)
        main_layout.addWidget(id_group)

        # Transform
        t_group = CollapsibleGroup("Transform")
        self.pos_x = _make_position_spin()
        self.pos_y = _make_position_spin()
        self.pos_z = _make_position_spin()
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X")); pos_row.addWidget(self.pos_x)
        pos_row.addWidget(QLabel("Y")); pos_row.addWidget(self.pos_y)
        pos_row.addWidget(QLabel("Z")); pos_row.addWidget(self.pos_z)
        pos_widget = QWidget(); pos_widget.setLayout(pos_row)
        t_group.form().addRow("Position:", pos_widget)

        self.scale_x = _make_float_spin(0, 10)
        self.scale_y = _make_float_spin(0, 10)
        self.scale_z = _make_float_spin(0, 10)
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("X")); scale_row.addWidget(self.scale_x)
        scale_row.addWidget(QLabel("Y")); scale_row.addWidget(self.scale_y)
        scale_row.addWidget(QLabel("Z")); scale_row.addWidget(self.scale_z)
        scale_widget = QWidget(); scale_widget.setLayout(scale_row)
        t_group.form().addRow("Scale:", scale_widget)
        main_layout.addWidget(t_group)

        # Appearance & Flags
        app_group = CollapsibleGroup("Appearance")
        self.color_btn = ColorButton()
        self.enabled_check = QCheckBox("Enabled")
        self.locked_check = QCheckBox("Locked")
        self.damageable_check = QCheckBox("Can Be Damaged")
        app_group.form().addRow("Color:", self.color_btn)
        app_group.form().addRow(self.enabled_check)
        app_group.form().addRow(self.locked_check)
        app_group.form().addRow(self.damageable_check)
        main_layout.addWidget(app_group)

        # Group Members
        members_group = CollapsibleGroup("Group Members")

        # List widget to show members with better formatting
        self.members_list = QListWidget()
        self.members_list.setMaximumHeight(120)
        members_group.layout().addWidget(QLabel("Members:"))
        members_group.layout().addWidget(self.members_list)

        # Buttons for adding/removing members
        members_buttons = QHBoxLayout()
        self.add_member_btn = QPushButton("Add Selected")
        self.remove_member_btn = QPushButton("Remove")
        self.remove_member_btn.setEnabled(False)  # disabled until selection
        members_buttons.addWidget(self.add_member_btn)
        members_buttons.addWidget(self.remove_member_btn)
        members_buttons.addStretch()

        members_btn_widget = QWidget()
        members_btn_widget.setLayout(members_buttons)
        members_group.layout().addWidget(members_btn_widget)

        main_layout.addWidget(members_group)

        main_layout.addStretch()

        # Connect member list selection change
        self.members_list.itemSelectionChanged.connect(self._on_member_selection_changed)

    def _on_member_selection_changed(self):
        """Enable/disable remove button based on selection."""
        self.remove_member_btn.setEnabled(len(self.members_list.selectedItems()) > 0)

    def load(self, grp: GroupIE):
        self._target = grp
        self.unique_id_label.setText(grp.unique_id)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(grp.display_name)
        self.name_edit.blockSignals(False)
        p = grp.transform.translation
        for spin, val in [(self.pos_x, p.x), (self.pos_y, p.y), (self.pos_z, p.z)]:
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)
        s = grp.transform.scale
        for spin, val in [(self.scale_x, s.x), (self.scale_y, s.y), (self.scale_z, s.z)]:
            spin.blockSignals(True); spin.setValue(val); spin.blockSignals(False)
        self.color_btn.set_color(grp.color)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(grp.is_enabled)
        self.enabled_check.blockSignals(False)
        self.locked_check.blockSignals(True)
        self.locked_check.setChecked(grp.is_locked)
        self.locked_check.blockSignals(False)
        self.damageable_check.blockSignals(True)
        self.damageable_check.setChecked(grp.b_can_be_damaged)
        self.damageable_check.blockSignals(False)

        # Update members list
        self.members_list.clear()
        for member_id in grp.group_items:
            self.members_list.addItem(member_id)


class UnknownElementPanel(QWidget):
    """
    Read-only inspector for unmodeled interface elements.
    Shows preserved metadata and raw PropertyData fingerprint.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        info_group = CollapsibleGroup("Unknown Element")
        self.type_label = QLabel()
        self.id_label = QLabel()
        self.class_label = QLabel()
        self.class_label.setWordWrap(True)
        self.raw_size_label = QLabel()
        self.sha1_label = QLabel()
        self.sha1_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_group.form().addRow("Type:", self.type_label)
        info_group.form().addRow("Unique ID:", self.id_label)
        info_group.form().addRow("Class Path:", self.class_label)
        info_group.form().addRow("Raw PropertyData:", self.raw_size_label)
        info_group.form().addRow("SHA1:", self.sha1_label)
        main_layout.addWidget(info_group)

        common_group = CollapsibleGroup("Common Parsed Fields")
        self.name_label = QLabel()
        self.enabled_label = QLabel()
        self.locked_label = QLabel()
        self.damageable_label = QLabel()
        self.color_label = QLabel()
        common_group.form().addRow("Display Name:", self.name_label)
        common_group.form().addRow("Enabled:", self.enabled_label)
        common_group.form().addRow("Locked:", self.locked_label)
        common_group.form().addRow("Can Be Damaged:", self.damageable_label)
        common_group.form().addRow("Color (RGBA):", self.color_label)
        main_layout.addWidget(common_group)

        note = QLabel(
            "This class is not fully modeled yet. Values shown here are read-only and "
            "its raw PropertyData bytes are preserved during save."
        )
        note.setWordWrap(True)
        main_layout.addWidget(note)
        main_layout.addStretch()

    def load(self, unk: UnknownElement):
        self.type_label.setText(type(unk).__name__)
        self.id_label.setText(unk.unique_id)
        self.class_label.setText(unk.class_path or "(empty)")

        raw = unk.raw_property_data or b""
        self.raw_size_label.setText(f"{len(raw)} bytes")
        self.sha1_label.setText(hashlib.sha1(raw).hexdigest() if raw else "(none)")

        self.name_label.setText(unk.display_name or "(empty)")
        self.enabled_label.setText(str(bool(unk.is_enabled)))
        self.locked_label.setText(str(bool(unk.is_locked)))
        self.damageable_label.setText(str(bool(unk.b_can_be_damaged)))
        c = unk.color
        self.color_label.setText(f"{c.r:.4f}, {c.g:.4f}, {c.b:.4f}, {c.a:.4f}")

ITEM_TYPE_PROJECT = 0
ITEM_TYPE_VI = 1
ITEM_TYPE_WORKSPACE = 2
ITEM_TYPE_ELEMENT = 3


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MMC Editor // MOVEMUSIC SAVE EDITOR")
        self.setMinimumSize(1100, 700)

        self.project: Optional[Project] = None
        self.file_path: Optional[str] = None
        self.undo_stack = QUndoStack(self)
        self._modified = False
        cfg = _load_config()
        self.debug_mode = bool(cfg.get("debug_mode", False))

        self._setup_ui()
        self._setup_toolbar()
        self._setup_menu()
        self._setup_statusbar()
        self._connect_signals()
        self._setup_global_shortcuts()

        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

    def _setup_global_shortcuts(self):
        """Editor-wide shortcuts for MIDI nudging regardless of focused widget."""
        self._shortcut_cc_up = QShortcut(QKeySequence("Alt+Up"), self)
        self._shortcut_cc_up.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_cc_up.activated.connect(lambda: self._trigger_active_viewport_midi_cc(1))

        self._shortcut_cc_down = QShortcut(QKeySequence("Alt+Down"), self)
        self._shortcut_cc_down.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_cc_down.activated.connect(lambda: self._trigger_active_viewport_midi_cc(-1))

        self._shortcut_note_up = QShortcut(QKeySequence("Alt+Shift+Up"), self)
        self._shortcut_note_up.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_note_up.activated.connect(lambda: self._trigger_active_viewport_midi_note(1))

        self._shortcut_note_down = QShortcut(QKeySequence("Alt+Shift+Down"), self)
        self._shortcut_note_down.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_note_down.activated.connect(lambda: self._trigger_active_viewport_midi_note(-1))

    def _trigger_active_viewport_midi_cc(self, delta: int):
        vp = self._active_viewport()
        if vp is not None and hasattr(vp, '_on_shortcut_midi_cc'):
            vp._on_shortcut_midi_cc(delta)

    def _trigger_active_viewport_midi_note(self, delta: int):
        vp = self._active_viewport()
        if vp is not None and hasattr(vp, '_on_shortcut_midi_note'):
            vp._on_shortcut_midi_note(delta)

        # Start with a blank project so users can immediately add templates
        self._create_new_project()

    def _setup_ui(self):
        # Outer vertical splitter: 3D viewport on top, tree+props on bottom
        self.outer_splitter = QSplitter(Qt.Orientation.Vertical)
        self.setCentralWidget(self.outer_splitter)

        # 3D Viewports (single and quad)
        self.viewport = SceneViewport()
        self.quad_viewport = QuadViewport()
        self._quad_mode = False

        self.outer_splitter.addWidget(self.viewport)
        # Quad viewport starts hidden — added when toggled
        self.quad_viewport.hide()

        # Inner horizontal splitter: tree + property panel
        inner_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.outer_splitter.addWidget(inner_splitter)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Project")
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setMinimumWidth(280)
        inner_splitter.addWidget(self.tree)

        # Property panel (in scroll area)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner_splitter.addWidget(self.scroll)

        inner_splitter.setStretchFactor(0, 1)
        inner_splitter.setStretchFactor(1, 3)

        self.outer_splitter.setStretchFactor(0, 3)  # viewport gets more space
        self.outer_splitter.setStretchFactor(1, 2)

        # Create panels
        self.empty_panel = QLabel("// SELECT AN ELEMENT TO VIEW PROPERTIES")
        self.empty_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_panel.setStyleSheet("color: #607890; font-size: 12px; font-weight: 600; letter-spacing: 2px;")
        self.unknown_normal_panel = QLabel(
            "// UNKNOWN ELEMENT\n"
            "Enable Debug Mode (View > Debug Mode) to inspect raw metadata."
        )
        self.unknown_normal_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unknown_normal_panel.setStyleSheet("color: #607890; font-size: 11px; letter-spacing: 1px;")
        self.project_panel = ProjectPanel()
        self.workspace_panel = WorkspacePanel()
        self.hitzone_panel = HitZonePanel()
        self.morphzone_panel = MorphZonePanel()
        self.textlabel_panel = TextLabelPanel()
        self.groupie_panel = GroupIEPanel()
        self.unknown_panel = UnknownElementPanel()

        self.scroll.setWidget(self.empty_panel)

    def _set_panel(self, widget):
        """Safely switch the scroll area's widget without Qt deleting the old one."""
        old = self.scroll.takeWidget()
        if old and old is not widget:
            old.setParent(None)  # detach but don't delete
        self.scroll.setWidget(widget)

    def _active_viewport(self):
        """Return the currently visible viewport (single or quad)."""
        return self.quad_viewport if self._quad_mode else self.viewport

    def _sync_viewports(self):
        """Update both viewports."""
        self.viewport.update()
        self.quad_viewport.refresh()

    def _sync_selection(self, elem_or_list):
        """Set selection on both viewports. Accepts element, list, or None."""
        self.viewport.set_selected(elem_or_list)
        self.quad_viewport.set_selected(elem_or_list)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_new = QAction("New", self)
        self.action_new.setShortcut("Ctrl+N")
        toolbar.addAction(self.action_new)

        self.action_open = QAction("Open", self)
        self.action_open.setShortcut("Ctrl+O")
        toolbar.addAction(self.action_open)

        self.action_save = QAction("Save", self)
        self.action_save.setShortcut("Ctrl+S")
        toolbar.addAction(self.action_save)

        self.action_save_as = QAction("Save As", self)
        self.action_save_as.setShortcut("Ctrl+Shift+S")
        toolbar.addAction(self.action_save_as)

        toolbar.addSeparator()

        self.action_undo = self.undo_stack.createUndoAction(self, "Undo")
        self.action_undo.setShortcut("Ctrl+Z")
        toolbar.addAction(self.action_undo)

        self.action_redo = self.undo_stack.createRedoAction(self, "Redo")
        self.action_redo.setShortcut("Ctrl+Y")
        toolbar.addAction(self.action_redo)

        toolbar.addSeparator()

        self.action_add = QAction("Add Element", self)
        toolbar.addAction(self.action_add)

        self.action_duplicate = QAction("Duplicate", self)
        self.action_duplicate.setShortcut("Ctrl+D")
        toolbar.addAction(self.action_duplicate)

        self.action_delete = QAction("Delete", self)
        self.action_delete.setShortcut("Delete")
        toolbar.addAction(self.action_delete)

        toolbar.addSeparator()

        self.action_fit_all = QAction("Fit All", self)
        self.action_fit_all.setShortcut("Home")
        toolbar.addAction(self.action_fit_all)

        self.action_top_view = QAction("Top", self)
        toolbar.addAction(self.action_top_view)

        self.action_front_view = QAction("Front", self)
        toolbar.addAction(self.action_front_view)

        self.action_side_view = QAction("Side", self)
        toolbar.addAction(self.action_side_view)

        toolbar.addSeparator()

        self.action_quad_view = QAction("Quad View", self)
        self.action_quad_view.setCheckable(True)
        toolbar.addAction(self.action_quad_view)

        toolbar.addSeparator()

        self.action_layout_row = QAction("Layout: Row", self)
        self.action_layout_row.setShortcut("Ctrl+L")
        toolbar.addAction(self.action_layout_row)

        self.action_layout_grid = QAction("Layout: Grid", self)
        toolbar.addAction(self.action_layout_grid)

        self.action_layout_circle = QAction("Layout: Circle", self)
        toolbar.addAction(self.action_layout_circle)

    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction(self.action_new)
        file_menu.addSeparator()
        file_menu.addAction(self.action_open)
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_save_as)

        edit_menu = menubar.addMenu("Edit")
        edit_menu.addAction(self.action_undo)
        edit_menu.addAction(self.action_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_add)
        edit_menu.addAction(self.action_duplicate)
        edit_menu.addAction(self.action_delete)

        view_menu = menubar.addMenu("View")
        self.action_debug_mode = view_menu.addAction("Debug Mode")
        self.action_debug_mode.setCheckable(True)
        self.action_debug_mode.setChecked(self.debug_mode)
        self.action_debug_mode.triggered.connect(self._on_toggle_debug_mode)

        # Templates menu
        template_menu = menubar.addMenu("Templates")

        def _template_category(name: str) -> str:
            n = name.lower()
            if "keyboard" in n:
                return "Keyboard"
            if "drum" in n:
                return "Drums"
            if "fader" in n or "knob" in n or "xy" in n or "button" in n:
                return "Controllers"
            if "mixer" in n:
                return "Utility"
            if "debug" in n:
                return "Debug"
            return "Other"

        category_order = ["Keyboard", "Controllers", "Drums", "Utility", "Debug", "Other"]
        categorized = {k: [] for k in category_order}
        for tpl_name in TEMPLATES:
            categorized[_template_category(tpl_name)].append(tpl_name)

        for cat in category_order:
            names = categorized[cat]
            if not names:
                continue
            submenu = template_menu.addMenu(cat)
            for tpl_name in names:
                action = submenu.addAction(tpl_name)
                action.setData(tpl_name)
                action.triggered.connect(self._on_template_menu_action)

        # Import menu
        import_menu = file_menu.addMenu("Import 3D")
        self.action_import_glb = import_menu.addAction("GLB/glTF File...")
        self.action_import_obj = import_menu.addAction("Wavefront OBJ...")
        self.action_import_glb.triggered.connect(self._on_import_glb)
        self.action_import_obj.triggered.connect(self._on_import_obj)

        # Export menu
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Export 3D")
        self.action_export_obj = export_menu.addAction("Wavefront OBJ...")
        self.action_export_glb = export_menu.addAction("glTF Binary (.glb)...")
        self.action_export_glb_orbit = export_menu.addAction("glTF with Orbit Camera...")
        export_menu.addSeparator()
        self.action_export_gif = export_menu.addAction("Orbit Animation GIF...")
        self.action_export_obj.triggered.connect(self._on_export_obj)
        self.action_export_glb.triggered.connect(self._on_export_glb)
        self.action_export_glb_orbit.triggered.connect(lambda: self._on_export_glb(orbit=True))
        self.action_export_gif.triggered.connect(self._on_export_gif)

    def _setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.status_file = QLabel("No file loaded")
        self.status_elements = QLabel("")
        self.status_modified = QLabel("")
        self.statusbar.addWidget(self.status_file, 1)
        self.statusbar.addWidget(self.status_elements)
        self.statusbar.addWidget(self.status_modified)

    def _connect_signals(self):
        self.action_new.triggered.connect(self._on_new)
        self.action_open.triggered.connect(self._on_open)
        self.action_save.triggered.connect(self._on_save)
        self.action_save_as.triggered.connect(self._on_save_as)
        self.action_add.triggered.connect(self._on_add)
        self.action_duplicate.triggered.connect(self._on_duplicate)
        self.action_delete.triggered.connect(self._on_delete)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        # 3D viewport signals
        self.viewport.element_selected.connect(self._on_viewport_select)
        self.viewport.element_moved.connect(self._on_viewport_move)
        self.viewport.elements_moved.connect(self._on_viewport_elements_moved)
        self.viewport.midi_mappings_nudged.connect(self._on_viewport_midi_nudged)
        self.viewport.status_message.connect(lambda msg: self.statusbar.showMessage(msg, 2500))
        self.viewport.element_scaled.connect(self._on_viewport_scale)
        self.viewport.add_element_requested.connect(self._on_viewport_add_element)
        self.viewport.duplicate_element_requested.connect(self._on_viewport_duplicate_element)
        self.viewport.delete_element_requested.connect(self._on_viewport_delete_element)
        self.viewport.auto_layout_requested.connect(self._on_auto_layout)
        self.viewport.add_to_group_requested.connect(self._on_add_to_group)
        self.viewport.remove_from_group_requested.connect(self._on_remove_from_group)
        self.viewport.create_group_requested.connect(self._on_create_group)
        self.viewport.delete_elements_requested.connect(self._on_batch_delete)
        self.viewport.edit_text_requested.connect(self._on_edit_text)
        self.viewport.change_color_requested.connect(self._on_change_color)
        self.viewport.toggle_lock_requested.connect(self._on_toggle_lock)
        self.viewport.move_to_workspace_requested.connect(self._on_move_to_workspace)
        self.action_fit_all.triggered.connect(self._on_fit_all)
        self.action_top_view.triggered.connect(self._on_top_view)
        self.action_front_view.triggered.connect(self._on_front_view)
        self.action_side_view.triggered.connect(self._on_side_view)
        self.action_quad_view.triggered.connect(self._on_toggle_quad_view)

        # Also connect quad viewport signals
        self.quad_viewport.element_selected.connect(self._on_viewport_select)
        self.quad_viewport.element_moved.connect(self._on_viewport_move)
        self.quad_viewport.elements_moved.connect(self._on_viewport_elements_moved)
        self.quad_viewport.midi_mappings_nudged.connect(self._on_viewport_midi_nudged)
        self.quad_viewport.status_message.connect(lambda msg: self.statusbar.showMessage(msg, 2500))
        self.quad_viewport.element_scaled.connect(self._on_viewport_scale)
        self.quad_viewport.add_element_requested.connect(self._on_viewport_add_element)
        self.quad_viewport.duplicate_element_requested.connect(self._on_viewport_duplicate_element)
        self.quad_viewport.delete_element_requested.connect(self._on_viewport_delete_element)
        self.quad_viewport.auto_layout_requested.connect(self._on_auto_layout)
        self.quad_viewport.add_to_group_requested.connect(self._on_add_to_group)
        self.quad_viewport.remove_from_group_requested.connect(self._on_remove_from_group)
        self.quad_viewport.create_group_requested.connect(self._on_create_group)
        self.quad_viewport.delete_elements_requested.connect(self._on_batch_delete)
        self.quad_viewport.edit_text_requested.connect(self._on_edit_text)
        self.quad_viewport.change_color_requested.connect(self._on_change_color)
        self.quad_viewport.toggle_lock_requested.connect(self._on_toggle_lock)
        self.quad_viewport.move_to_workspace_requested.connect(self._on_move_to_workspace)

        # Layout toolbar actions
        self.action_layout_row.triggered.connect(lambda: self._on_auto_layout("Row"))
        self.action_layout_grid.triggered.connect(lambda: self._on_auto_layout("Grid"))
        self.action_layout_circle.triggered.connect(lambda: self._on_auto_layout("Circle"))

    def _on_clean_changed(self, clean):
        self._modified = not clean
        self._update_statusbar()
        title = "MMC Editor // MOVEMUSIC SAVE EDITOR"
        if self.file_path:
            title = f"{self.file_path} - {title}"
        if self._modified:
            title = f"* {title}"
        self.setWindowTitle(title)

    def _update_statusbar(self):
        if self.file_path:
            self.status_file.setText(self.file_path)
        else:
            self.status_file.setText("No file loaded")
        if self.project:
            self.status_elements.setText(f"{len(self.project.elements)} elements")
        else:
            self.status_elements.setText("")
        self.status_modified.setText("Modified" if self._modified else "")

    # -- File operations --

    def _on_new(self):
        """Create a new blank project."""
        if not self._check_unsaved_changes():
            return
        self._create_new_project()

    def _create_new_project(self):
        """Create a new blank project without checking for unsaved changes."""
        # Create blank project with one workspace
        self.project = Project()
        self.project.project_name = "Untitled"
        self.project.workspaces = [Workspace(
            unique_id=self.project.generate_id("Workspace"),
            display_name="Main Workspace",
            enabled=True
        )]
        self.project.user_location = Vec3(756.0, 200.0, 160.0)  # Default VR position

        self.file_path = None
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self._rebuild_tree()
        self.viewport.set_project(self.project)
        self.quad_viewport.set_project(self.project)
        self._update_statusbar()

    def _check_unsaved_changes(self):
        """Check for unsaved changes and ask user if they want to save. Returns True if OK to proceed."""
        if self.project and not self.undo_stack.isClean():
            result = QMessageBox.question(self, "Unsaved Changes",
                "The current project has unsaved changes. Do you want to save before continuing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)

            if result == QMessageBox.StandardButton.Save:
                if self.file_path:
                    self._on_save()
                else:
                    self._on_save_as()
                return self.undo_stack.isClean()  # Save might have been canceled
            elif result == QMessageBox.StandardButton.Cancel:
                return False
            # else: Discard changes, continue
        return True

    def closeEvent(self, event):
        """Handle window close event - check for unsaved changes."""
        if self._check_unsaved_changes():
            event.accept()
        else:
            event.ignore()

    def _on_open(self):
        if not self._check_unsaved_changes():
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Open .mmc File", "", "MoveMusic Save (*.mmc);;All Files (*)")
        if not path:
            return
        self._open_file(path)

    def _open_file(self, path: str):
        try:
            self.project = load_project_from_file(path)
            self.file_path = path
            self.undo_stack.clear()
            self.undo_stack.setClean()  # Mark as clean since we just loaded
            self._rebuild_tree()
            self.viewport.set_project(self.project)
            self.quad_viewport.set_project(self.project)
            self._update_statusbar()
            # Remember last opened file
            cfg = _load_config()
            cfg["last_file"] = path
            _save_config(cfg)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file:\n{e}")

    def _on_save(self):
        if not self.project:
            return
        if not self.file_path:
            self._on_save_as()
            return
        self._apply_current_panel()
        try:
            save_project_to_file(self.file_path, self.project)
            self.undo_stack.setClean()
            self._update_statusbar()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def _on_save_as(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save .mmc File", "", "MoveMusic Save (*.mmc);;All Files (*)")
        if not path:
            return
        self._apply_current_panel()
        try:
            save_project_to_file(path, self.project)
            self.file_path = path
            self.undo_stack.setClean()
            self._update_statusbar()
            cfg = _load_config()
            cfg["last_file"] = path
            _save_config(cfg)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    # -- Tree --

    def _rebuild_tree(self):
        self.tree.clear()
        self._sync_viewports()
        if not self.project:
            return

        # Project root
        root = QTreeWidgetItem(self.tree, [f"Project: {self.project.project_name}"])
        root.setData(0, Qt.ItemDataRole.UserRole, (ITEM_TYPE_PROJECT, None))
        root.setExpanded(True)

        # Virtual Interface
        vi_item = QTreeWidgetItem(root, [self.project.vi_name])
        vi_item.setData(0, Qt.ItemDataRole.UserRole, (ITEM_TYPE_VI, None))
        vi_item.setExpanded(True)

        # Workspaces
        for ws in self.project.workspaces:
            ws_label = f"{ws.display_name} ({len(ws.element_ids)} elements)"
            if ws.enabled:
                ws_label += " [active]"
            ws_item = QTreeWidgetItem(vi_item, [ws_label])
            ws_item.setData(0, Qt.ItemDataRole.UserRole, (ITEM_TYPE_WORKSPACE, ws))
            ws_item.setExpanded(True)

            for eid in ws.element_ids:
                elem = self.project.find_element(eid)
                if not elem:
                    continue
                etype = type(elem).__name__
                label = f"{etype}: {elem.display_name or elem.unique_id}"
                elem_item = QTreeWidgetItem(ws_item, [label])
                elem_item.setData(0, Qt.ItemDataRole.UserRole, (ITEM_TYPE_ELEMENT, elem))

                # Color indicator
                c = elem.color
                r = int(min(1.0, c.r) * 255)
                g = int(min(1.0, c.g) * 255)
                b = int(min(1.0, c.b) * 255)
                elem_item.setForeground(0, QColor(r, g, b))

    def _on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self._set_panel(self.empty_panel)
            self._sync_selection(None)
            return

        if len(items) > 1:
            # Mass edit: collect all selected elements
            elements = []
            for item in items:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and data[0] == ITEM_TYPE_ELEMENT:
                    elements.append(data[1])
            if elements:
                self._show_mass_edit(elements)
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        item_type, obj = data
        if item_type == ITEM_TYPE_PROJECT:
            self.project_panel.load(self.project)
            self._connect_project_panel()
            self._set_panel(self.project_panel)
            self._sync_selection(None)
        elif item_type == ITEM_TYPE_WORKSPACE:
            self.workspace_panel.load(obj)
            self._connect_workspace_panel(obj)
            self._set_panel(self.workspace_panel)
            self._sync_selection(None)
        elif item_type == ITEM_TYPE_ELEMENT:
            self._sync_selection(obj)
            if isinstance(obj, HitZone):
                self.hitzone_panel.load(obj)
                self._connect_hitzone_panel(obj)
                self._set_panel(self.hitzone_panel)
            elif isinstance(obj, MorphZone):
                self.morphzone_panel.load(obj)
                self._connect_morphzone_panel(obj)
                self._set_panel(self.morphzone_panel)
            elif isinstance(obj, TextLabel):
                self.textlabel_panel.load(obj)
                self._connect_textlabel_panel(obj)
                self._set_panel(self.textlabel_panel)
            elif isinstance(obj, GroupIE):
                self.groupie_panel.load(obj)
                self._connect_groupie_panel(obj)
                self._set_panel(self.groupie_panel)
            elif isinstance(obj, UnknownElement):
                if self.debug_mode:
                    self.unknown_panel.load(obj)
                    self._set_panel(self.unknown_panel)
                else:
                    self._set_panel(self.unknown_normal_panel)

    def _apply_current_panel(self):
        """Apply any pending changes from the current panel to the model."""
        widget = self.scroll.widget()
        if widget is self.hitzone_panel and self.hitzone_panel._target:
            self._apply_hitzone_panel(self.hitzone_panel._target)
        elif widget is self.morphzone_panel and self.morphzone_panel._target:
            self._apply_morphzone_panel(self.morphzone_panel._target)
        elif widget is self.textlabel_panel and self.textlabel_panel._target:
            # Force any pending line edit changes by clearing focus
            for child in widget.findChildren((QLineEdit, QDoubleSpinBox)):
                if child.hasFocus():
                    child.clearFocus()
                    break
        elif widget is self.groupie_panel and self.groupie_panel._target:
            # Force any pending line edit changes by clearing focus
            for child in widget.findChildren((QLineEdit, QDoubleSpinBox)):
                if child.hasFocus():
                    child.clearFocus()
                    break

    # -- Panel connections (property -> undo command) --

    def _connect_project_panel(self):
        p = self.project_panel
        try: p.name_edit.editingFinished.disconnect()
        except: pass
        def on_name():
            old = self.project.project_name
            new = p.name_edit.text()
            if old != new:
                cmd = SetPropertyCommand(self.project, "project_name", old, new, "Change project name")
                self.undo_stack.push(cmd)
                self._rebuild_tree()
        p.name_edit.editingFinished.connect(on_name)

    def _connect_workspace_panel(self, ws):
        p = self.workspace_panel
        try: p.name_edit.editingFinished.disconnect()
        except: pass
        try: p.enabled_check.toggled.disconnect()
        except: pass
        def on_name():
            old = ws.display_name
            new = p.name_edit.text()
            if old != new:
                cmd = SetPropertyCommand(ws, "display_name", old, new, "Rename workspace")
                self.undo_stack.push(cmd)
                self._rebuild_tree()
        def on_enabled(checked):
            old = ws.enabled
            if old != checked:
                cmd = SetPropertyCommand(ws, "enabled", old, checked, "Toggle workspace")
                self.undo_stack.push(cmd)
                self._rebuild_tree()
        p.name_edit.editingFinished.connect(on_name)
        p.enabled_check.toggled.connect(on_enabled)

    def _connect_hitzone_panel(self, hz):
        p = self.hitzone_panel
        self._disconnect_all(p)

        def _prop(attr, old, new, desc=""):
            if old != new:
                cmd = SetPropertyCommand(hz, attr, old, new, desc or f"Change {attr}")
                self.undo_stack.push(cmd)

        p.name_edit.editingFinished.connect(
            lambda: _prop("display_name", hz.display_name, p.name_edit.text(), "Rename HitZone")
            or setattr(hz, "display_name", p.name_edit.text()) or self._rebuild_tree())

        # Transform
        for spin, axis_getter in [
            (p.pos_x, lambda: hz.transform.translation.x),
            (p.pos_y, lambda: hz.transform.translation.y),
            (p.pos_z, lambda: hz.transform.translation.z),
        ]:
            spin.valueChanged.connect(lambda val, ag=axis_getter, s=spin: None)

        # Behavior combos
        p.behavior_combo.currentTextChanged.connect(
            lambda t: _prop("behavior", hz.behavior, t) or setattr(hz, "behavior", t))
        p.msg_type_combo.currentTextChanged.connect(
            lambda t: _prop("midi_message_type", hz.midi_message_type, t) or setattr(hz, "midi_message_type", t))
        p.timed_close.valueChanged.connect(
            lambda v: _prop("timed_close_seconds", hz.timed_close_seconds, v) or setattr(hz, "timed_close_seconds", v))
        p.one_shot_combo.currentTextChanged.connect(
            lambda t: _prop("one_shot_sample", hz.one_shot_sample, t) or setattr(hz, "one_shot_sample", t))

        # Velocity
        p.use_vel_check.toggled.connect(
            lambda v: _prop("should_use_velocity_sensitivity", hz.should_use_velocity_sensitivity, v)
            or setattr(hz, "should_use_velocity_sensitivity", v))
        p.fixed_vel.valueChanged.connect(
            lambda v: _prop("fixed_midi_velocity_output", hz.fixed_midi_velocity_output, v)
            or setattr(hz, "fixed_midi_velocity_output", v))
        p.min_phys.valueChanged.connect(
            lambda v: _prop("min_physics_velocity_input", hz.min_physics_velocity_input, v)
            or setattr(hz, "min_physics_velocity_input", v))
        p.max_phys.valueChanged.connect(
            lambda v: _prop("max_physics_velocity_input", hz.max_physics_velocity_input, v)
            or setattr(hz, "max_physics_velocity_input", v))
        p.min_midi.valueChanged.connect(
            lambda v: _prop("min_midi_velocity_output", hz.min_midi_velocity_output, v)
            or setattr(hz, "min_midi_velocity_output", v))
        p.max_midi.valueChanged.connect(
            lambda v: _prop("max_midi_velocity_output", hz.max_midi_velocity_output, v)
            or setattr(hz, "max_midi_velocity_output", v))

        # Appearance
        p.color_btn.color_changed.connect(
            lambda c: _prop("color", hz.color, c, "Change color") or setattr(hz, "color", c) or self._rebuild_tree())
        p.enabled_check.toggled.connect(
            lambda v: _prop("is_enabled", hz.is_enabled, v) or setattr(hz, "is_enabled", v))
        p.locked_check.toggled.connect(
            lambda v: _prop("is_locked", hz.is_locked, v) or setattr(hz, "is_locked", v))
        p.toggle_state_check.toggled.connect(
            lambda v: _prop("toggle_state", hz.toggle_state, v) or setattr(hz, "toggle_state", v))

        # MIDI tables
        p.note_table.data_changed.connect(lambda: setattr(hz, "midi_note_mappings", p.note_table.get_mappings()))
        p.cc_table.data_changed.connect(lambda: setattr(hz, "midi_cc_mappings", p.cc_table.get_mappings()))

    def _connect_morphzone_panel(self, mz):
        p = self.morphzone_panel
        self._disconnect_all(p)

        def _prop(attr, old, new, desc=""):
            if old != new:
                cmd = SetPropertyCommand(mz, attr, old, new, desc or f"Change {attr}")
                self.undo_stack.push(cmd)

        p.name_edit.editingFinished.connect(
            lambda: _prop("display_name", mz.display_name, p.name_edit.text(), "Rename MorphZone")
            or setattr(mz, "display_name", p.name_edit.text()) or self._rebuild_tree())

        # Settings
        p.dimensions_combo.currentTextChanged.connect(
            lambda t: _prop("dimensions", mz.dimensions, t) or setattr(mz, "dimensions", t))
        p.soloed_combo.currentTextChanged.connect(
            lambda t: _prop("soloed_axis", mz.soloed_axis, t) or setattr(mz, "soloed_axis", t))
        p.release_combo.currentTextChanged.connect(
            lambda t: _prop("release_behavior", mz.release_behavior, t) or setattr(mz, "release_behavior", t))

        # Axis enables
        p.x_enabled.toggled.connect(
            lambda v: _prop("is_x_axis_enabled", mz.is_x_axis_enabled, v) or setattr(mz, "is_x_axis_enabled", v))
        p.y_enabled.toggled.connect(
            lambda v: _prop("is_y_axis_enabled", mz.is_y_axis_enabled, v) or setattr(mz, "is_y_axis_enabled", v))
        p.z_enabled.toggled.connect(
            lambda v: _prop("is_z_axis_enabled", mz.is_z_axis_enabled, v) or setattr(mz, "is_z_axis_enabled", v))

        # CC tables
        p.x_cc_table.data_changed.connect(lambda: setattr(mz, "x_axis_cc_mappings", p.x_cc_table.get_mappings()))
        p.y_cc_table.data_changed.connect(lambda: setattr(mz, "y_axis_cc_mappings", p.y_cc_table.get_mappings()))
        p.z_cc_table.data_changed.connect(lambda: setattr(mz, "z_axis_cc_mappings", p.z_cc_table.get_mappings()))

        # Appearance
        p.color_btn.color_changed.connect(
            lambda c: _prop("color", mz.color, c, "Change color") or setattr(mz, "color", c) or self._rebuild_tree())
        p.enabled_check.toggled.connect(
            lambda v: _prop("is_enabled", mz.is_enabled, v) or setattr(mz, "is_enabled", v))
        p.locked_check.toggled.connect(
            lambda v: _prop("is_locked", mz.is_locked, v) or setattr(mz, "is_locked", v))

    def _connect_textlabel_panel(self, tl):
        p = self.textlabel_panel
        self._disconnect_all(p)

        def _prop(attr, old, new, desc=""):
            if old != new:
                cmd = SetPropertyCommand(tl, attr, old, new, desc or f"Change {attr}")
                self.undo_stack.push(cmd)

        p.name_edit.editingFinished.connect(
            lambda: _prop("display_name", tl.display_name, p.name_edit.text(), "Change label text")
            or setattr(tl, "display_name", p.name_edit.text()) or self._rebuild_tree() or self._sync_viewports())

        p.pos_x.valueChanged.connect(lambda v: setattr(tl.transform.translation, "x", v) or self._sync_viewports())
        p.pos_y.valueChanged.connect(lambda v: setattr(tl.transform.translation, "y", v) or self._sync_viewports())
        p.pos_z.valueChanged.connect(lambda v: setattr(tl.transform.translation, "z", v) or self._sync_viewports())
        p.scale_x.valueChanged.connect(lambda v: setattr(tl.transform.scale, "x", v) or self._sync_viewports())
        p.scale_y.valueChanged.connect(lambda v: setattr(tl.transform.scale, "y", v) or self._sync_viewports())
        p.scale_z.valueChanged.connect(lambda v: setattr(tl.transform.scale, "z", v) or self._sync_viewports())

        p.color_btn.color_changed.connect(
            lambda c: _prop("color", tl.color, c, "Change color") or setattr(tl, "color", c) or self._rebuild_tree())
        p.enabled_check.toggled.connect(
            lambda v: _prop("is_enabled", tl.is_enabled, v) or setattr(tl, "is_enabled", v))
        p.locked_check.toggled.connect(
            lambda v: _prop("is_locked", tl.is_locked, v) or setattr(tl, "is_locked", v))

    def _connect_groupie_panel(self, grp):
        p = self.groupie_panel
        self._disconnect_all(p)

        def _prop(attr, old, new, desc=""):
            if old != new:
                cmd = SetPropertyCommand(grp, attr, old, new, desc or f"Change {attr}")
                self.undo_stack.push(cmd)

        p.name_edit.editingFinished.connect(
            lambda: _prop("display_name", grp.display_name, p.name_edit.text(), "Change group name")
            or setattr(grp, "display_name", p.name_edit.text()) or self._rebuild_tree() or self._sync_viewports())

        p.pos_x.valueChanged.connect(lambda v: setattr(grp.transform.translation, "x", v) or self._sync_viewports())
        p.pos_y.valueChanged.connect(lambda v: setattr(grp.transform.translation, "y", v) or self._sync_viewports())
        p.pos_z.valueChanged.connect(lambda v: setattr(grp.transform.translation, "z", v) or self._sync_viewports())
        p.scale_x.valueChanged.connect(lambda v: setattr(grp.transform.scale, "x", v) or self._sync_viewports())
        p.scale_y.valueChanged.connect(lambda v: setattr(grp.transform.scale, "y", v) or self._sync_viewports())
        p.scale_z.valueChanged.connect(lambda v: setattr(grp.transform.scale, "z", v) or self._sync_viewports())

        p.color_btn.color_changed.connect(
            lambda c: _prop("color", grp.color, c, "Change color") or setattr(grp, "color", c) or self._rebuild_tree())
        p.enabled_check.toggled.connect(
            lambda v: _prop("is_enabled", grp.is_enabled, v) or setattr(grp, "is_enabled", v))
        p.locked_check.toggled.connect(
            lambda v: _prop("is_locked", grp.is_locked, v) or setattr(grp, "is_locked", v))
        p.damageable_check.toggled.connect(
            lambda v: _prop("b_can_be_damaged", grp.b_can_be_damaged, v) or setattr(grp, "b_can_be_damaged", v))

        # Group member management buttons
        p.add_member_btn.clicked.connect(lambda: self._on_add_group_member(grp))
        p.remove_member_btn.clicked.connect(lambda: self._on_remove_group_member(grp))

    def _disconnect_all(self, panel):
        """Disconnect all panel signals to avoid stale connections."""
        for attr_name in dir(panel):
            attr = getattr(panel, attr_name, None)
            if isinstance(attr, (QLineEdit, QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox)):
                try: attr.disconnect()
                except: pass
            if isinstance(attr, ColorButton):
                try: attr.color_changed.disconnect()
                except: pass
            if isinstance(attr, (MidiNoteTable, MidiCCTable)):
                try: attr.data_changed.disconnect()
                except: pass

    def _apply_hitzone_panel(self, hz):
        p = self.hitzone_panel
        hz.display_name = p.name_edit.text()
        hz.transform.translation = Vec3(p.pos_x.value(), p.pos_y.value(), p.pos_z.value())
        hz.transform.rotation = Quat(p.rot_x.value(), p.rot_y.value(), p.rot_z.value(), p.rot_w.value())
        hz.transform.scale = Vec3(p.scale_x.value(), p.scale_y.value(), p.scale_z.value())
        hz.midi_note_mappings = p.note_table.get_mappings()
        hz.midi_cc_mappings = p.cc_table.get_mappings()
        hz.behavior = p.behavior_combo.currentText()
        hz.midi_message_type = p.msg_type_combo.currentText()
        hz.timed_close_seconds = p.timed_close.value()
        hz.one_shot_sample = p.one_shot_combo.currentText()
        hz.should_use_velocity_sensitivity = p.use_vel_check.isChecked()
        hz.fixed_midi_velocity_output = p.fixed_vel.value()
        hz.min_physics_velocity_input = p.min_phys.value()
        hz.max_physics_velocity_input = p.max_phys.value()
        hz.min_midi_velocity_output = p.min_midi.value()
        hz.max_midi_velocity_output = p.max_midi.value()
        hz.color = p.color_btn.get_color()
        hz.is_enabled = p.enabled_check.isChecked()
        hz.is_locked = p.locked_check.isChecked()
        hz.toggle_state = p.toggle_state_check.isChecked()

    def _apply_morphzone_panel(self, mz):
        p = self.morphzone_panel
        mz.display_name = p.name_edit.text()
        mz.transform.translation = Vec3(p.pos_x.value(), p.pos_y.value(), p.pos_z.value())
        mz.transform.rotation = Quat(p.rot_x.value(), p.rot_y.value(), p.rot_z.value(), p.rot_w.value())
        mz.transform.scale = Vec3(p.scale_x.value(), p.scale_y.value(), p.scale_z.value())
        mz.x_axis_cc_mappings = p.x_cc_table.get_mappings()
        mz.y_axis_cc_mappings = p.y_cc_table.get_mappings()
        mz.z_axis_cc_mappings = p.z_cc_table.get_mappings()
        mz.is_x_axis_enabled = p.x_enabled.isChecked()
        mz.is_y_axis_enabled = p.y_enabled.isChecked()
        mz.is_z_axis_enabled = p.z_enabled.isChecked()
        mz.dimensions = p.dimensions_combo.currentText()
        mz.soloed_axis = p.soloed_combo.currentText()
        mz.release_behavior = p.release_combo.currentText()
        mz.color = p.color_btn.get_color()
        mz.is_enabled = p.enabled_check.isChecked()
        mz.is_locked = p.locked_check.isChecked()

    # -- Mass edit --

    def _show_mass_edit(self, elements: list):
        """Show a simplified panel for editing common properties across multiple elements."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel(f"Mass editing {len(elements)} elements"))

        form = QFormLayout()

        # Common fields: color, is_enabled, is_locked
        color_btn = ColorButton(elements[0].color)
        def on_color(c):
            old_vals = [copy.deepcopy(e.color) for e in elements]
            cmd = BatchSetPropertyCommand(elements, "color", old_vals, c, "Mass change color")
            self.undo_stack.push(cmd)
            self._rebuild_tree()
        color_btn.color_changed.connect(on_color)
        form.addRow("Color:", color_btn)

        enabled = QCheckBox()
        enabled.setChecked(all(e.is_enabled for e in elements))
        def on_enabled(v):
            old_vals = [e.is_enabled for e in elements]
            cmd = BatchSetPropertyCommand(elements, "is_enabled", old_vals, v, "Mass toggle enabled")
            self.undo_stack.push(cmd)
        enabled.toggled.connect(on_enabled)
        form.addRow("Enabled:", enabled)

        locked = QCheckBox()
        locked.setChecked(all(e.is_locked for e in elements))
        def on_locked(v):
            old_vals = [e.is_locked for e in elements]
            cmd = BatchSetPropertyCommand(elements, "is_locked", old_vals, v, "Mass toggle locked")
            self.undo_stack.push(cmd)
        locked.toggled.connect(on_locked)
        form.addRow("Locked:", locked)

        # If all HitZones: show behavior, message type
        if all(isinstance(e, HitZone) for e in elements):
            beh = QComboBox()
            beh.addItems(["EHitZoneBehavior::Hold", "EHitZoneBehavior::Toggle",
                          "EHitZoneBehavior::TimedClose", "EHitZoneBehavior::OneShot"])
            def on_beh(t):
                old_vals = [e.behavior for e in elements]
                cmd = BatchSetPropertyCommand(elements, "behavior", old_vals, t, "Mass change behavior")
                self.undo_stack.push(cmd)
            beh.currentTextChanged.connect(on_beh)
            form.addRow("Behavior:", beh)

            msg = QComboBox()
            msg.addItems(["EMidiMessageType::Note", "EMidiMessageType::CC"])
            def on_msg(t):
                old_vals = [e.midi_message_type for e in elements]
                cmd = BatchSetPropertyCommand(elements, "midi_message_type", old_vals, t, "Mass change msg type")
                self.undo_stack.push(cmd)
            msg.currentTextChanged.connect(on_msg)
            form.addRow("Message Type:", msg)

        layout.addLayout(form)
        layout.addStretch()
        self._set_panel(panel)

    # -- Add / Duplicate / Delete --

    def _get_selected_workspace(self) -> Optional[Workspace]:
        items = self.tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return None
        if data[0] == ITEM_TYPE_WORKSPACE:
            return data[1]
        if data[0] == ITEM_TYPE_ELEMENT:
            # Find parent workspace
            parent = items[0].parent()
            if parent:
                pdata = parent.data(0, Qt.ItemDataRole.UserRole)
                if pdata and pdata[0] == ITEM_TYPE_WORKSPACE:
                    return pdata[1]
        return None

    def _on_add(self):
        if not self.project:
            return
        ws = self._get_selected_workspace()
        if not ws:
            QMessageBox.warning(self, "Select Workspace", "Select a workspace to add an element to.")
            return

        choices = ["HitZone", "MorphZone", "TextLabel", "GroupIE"]
        choice, ok = QInputDialog.getItem(self, "Add Element", "Element type:", choices, 0, False)
        if not ok:
            return

        if choice == "HitZone":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
        elif choice == "MorphZone":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
        elif choice == "TextLabel":
            elem = TextLabel(unique_id=self.project.generate_id("TextLabel_C"))
        elif choice == "GroupIE":
            elem = GroupIE(unique_id=self.project.generate_id("GroupIE"))
        else:
            return

        cmd = AddElementCommand(self.project, ws, elem, f"Add {choice}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()

    def _on_duplicate(self):
        if not self.project:
            return
        items = self.tree.selectedItems()
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data or data[0] != ITEM_TYPE_ELEMENT:
                continue
            elem = data[1]
            ws = self._get_workspace_for_item(item)
            if not ws:
                continue

            self._apply_current_panel()
            new_elem = copy.deepcopy(elem)
            prefix = (
                "HitZone" if isinstance(elem, HitZone)
                else "MorphZone" if isinstance(elem, MorphZone)
                else "GroupIE" if isinstance(elem, GroupIE)
                else "UnknownIE" if isinstance(elem, UnknownElement)
                else "TextLabel_C"
            )
            new_elem.unique_id = self.project.generate_id(prefix)
            new_elem.display_name = (elem.display_name or elem.unique_id) + " (copy)"
            new_elem.transform.translation.x += 10.0

            cmd = DuplicateElementCommand(self.project, ws, new_elem,
                                          f"Duplicate {elem.unique_id}")
            self.undo_stack.push(cmd)

        self._rebuild_tree()
        self._update_statusbar()

    def _on_delete(self):
        if not self.project:
            return
        items = self.tree.selectedItems()
        elems_to_delete = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == ITEM_TYPE_ELEMENT:
                elems_to_delete.append(data[1])

        if not elems_to_delete:
            return

        if len(elems_to_delete) > 1:
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Delete {len(elems_to_delete)} elements?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        for elem in elems_to_delete:
            ws_refs = []
            for ws in self.project.workspaces:
                if elem.unique_id in ws.element_ids:
                    idx = ws.element_ids.index(elem.unique_id)
                    ws_refs.append((ws, idx))
            cmd = DeleteElementCommand(self.project, elem, ws_refs, f"Delete {elem.unique_id}")
            self.undo_stack.push(cmd)

        self._rebuild_tree()
        self._update_statusbar()

    def _get_workspace_for_item(self, item) -> Optional[Workspace]:
        parent = item.parent()
        if parent:
            pdata = parent.data(0, Qt.ItemDataRole.UserRole)
            if pdata and pdata[0] == ITEM_TYPE_WORKSPACE:
                return pdata[1]
        return None

    # -- Viewport callbacks --

    def _on_viewport_select(self, elem):
        """Viewport clicked an element — sync tree selection and open property panel."""
        logging.info(f"_on_viewport_select: {elem.unique_id if elem else 'None'}")
        if elem is None:
            self.tree.clearSelection()
            self._set_panel(self.empty_panel)
            return
        # Find the tree item for this element
        item = self._find_tree_item_for_element(elem)
        logging.info(f"  tree item found: {item is not None}")
        if item:
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.scrollToItem(item)
            self.tree.blockSignals(False)
        # Always open property panel for the selected element
        if isinstance(elem, HitZone):
            self.hitzone_panel.load(elem)
            self._connect_hitzone_panel(elem)
            self._set_panel(self.hitzone_panel)
        elif isinstance(elem, MorphZone):
            self.morphzone_panel.load(elem)
            self._connect_morphzone_panel(elem)
            self._set_panel(self.morphzone_panel)
        elif isinstance(elem, TextLabel):
            self.textlabel_panel.load(elem)
            self._connect_textlabel_panel(elem)
            self._set_panel(self.textlabel_panel)
        elif isinstance(elem, GroupIE):
            self.groupie_panel.load(elem)
            self._connect_groupie_panel(elem)
            self._set_panel(self.groupie_panel)
        elif isinstance(elem, UnknownElement):
            if self.debug_mode:
                self.unknown_panel.load(elem)
                self._set_panel(self.unknown_panel)
            else:
                self._set_panel(self.unknown_normal_panel)

    def _on_toggle_debug_mode(self, checked: bool):
        self.debug_mode = bool(checked)
        cfg = _load_config()
        cfg["debug_mode"] = self.debug_mode
        _save_config(cfg)
        self.statusbar.showMessage(
            "Debug mode enabled" if self.debug_mode else "Debug mode disabled",
            2500,
        )
        # Refresh panel for current selection so mode applies immediately.
        self._on_selection_changed()

    def _on_viewport_move(self, elem, old_x, old_y, old_z):
        """Element was dragged in the viewport — create undo command."""
        new_x = elem.transform.translation.x
        new_y = elem.transform.translation.y
        new_z = elem.transform.translation.z
        # Restore old position, then push command that sets new position
        old_trans = Vec3(old_x, old_y, old_z)
        new_trans = Vec3(new_x, new_y, new_z)
        elem.transform.translation = old_trans
        cmd = SetPropertyCommand(elem.transform, "translation", old_trans, new_trans, "Move element")
        self.undo_stack.push(cmd)
        self._sync_viewports()
        # Refresh property panel if this element is displayed
        widget = self.scroll.widget()
        if widget is self.hitzone_panel and self.hitzone_panel._target is elem:
            self.hitzone_panel.load(elem)
        elif widget is self.morphzone_panel and self.morphzone_panel._target is elem:
            self.morphzone_panel.load(elem)
        elif widget is self.textlabel_panel and self.textlabel_panel._target is elem:
            self.textlabel_panel.load(elem)
        elif widget is self.groupie_panel and self.groupie_panel._target is elem:
            self.groupie_panel.load(elem)

    def _on_viewport_scale(self, elem, old_sx, old_sy, old_sz):
        """Element was resized in the viewport — create undo command."""
        new_scale = Vec3(elem.transform.scale.x, elem.transform.scale.y, elem.transform.scale.z)
        old_scale = Vec3(old_sx, old_sy, old_sz)
        elem.transform.scale = old_scale
        cmd = SetPropertyCommand(elem.transform, "scale", old_scale, new_scale, "Resize element")
        self.undo_stack.push(cmd)
        self._sync_viewports()
        widget = self.scroll.widget()
        if widget is self.hitzone_panel and self.hitzone_panel._target is elem:
            self.hitzone_panel.load(elem)
        elif widget is self.morphzone_panel and self.morphzone_panel._target is elem:
            self.morphzone_panel.load(elem)
        elif widget is self.textlabel_panel and self.textlabel_panel._target is elem:
            self.textlabel_panel.load(elem)
        elif widget is self.groupie_panel and self.groupie_panel._target is elem:
            self.groupie_panel.load(elem)

    def _on_viewport_elements_moved(self, elements, old_positions):
        """Batch move from multi-select drag — single undo step."""
        new_positions = [Vec3(e.transform.translation.x, e.transform.translation.y,
                              e.transform.translation.z) for e in elements]
        old_vecs = [Vec3(x, y, z) for x, y, z in old_positions]
        # Restore old positions, then push command
        for elem, (ox, oy, oz) in zip(elements, old_positions):
            elem.transform.translation = Vec3(ox, oy, oz)
        cmd = BatchMoveCommand(elements, old_vecs, new_positions, "Move elements")
        self.undo_stack.push(cmd)
        self._sync_viewports()

    def _on_viewport_midi_nudged(self, changes, description):
        """MIDI mappings nudged from viewport keyboard shortcut — single undo step."""
        if not changes:
            return

        cmd = MultiSetPropertyCommand(changes, description or "Nudge MIDI mappings")
        self.undo_stack.push(cmd)
        self._sync_viewports()

        affected_elements = {obj for obj, _, _, _ in changes}
        widget = self.scroll.widget()
        if widget is self.hitzone_panel and self.hitzone_panel._target in affected_elements:
            self.hitzone_panel.load(self.hitzone_panel._target)
        elif widget is self.morphzone_panel and self.morphzone_panel._target in affected_elements:
            self.morphzone_panel.load(self.morphzone_panel._target)
        elif widget is self.textlabel_panel and self.textlabel_panel._target in affected_elements:
            self.textlabel_panel.load(self.textlabel_panel._target)
        elif widget is self.groupie_panel and self.groupie_panel._target in affected_elements:
            self.groupie_panel.load(self.groupie_panel._target)

    # -- Auto-layout --

    def _on_auto_layout(self, arrangement: str):
        """Arrange selected elements in the given layout."""
        vp = self._active_viewport()
        if hasattr(vp, 'selected_elements'):
            elements = vp.selected_elements
        else:
            elements = [vp.selected_element] if vp.selected_element else []

        if len(elements) < 2:
            QMessageBox.information(self, "Auto Layout",
                f"Select at least 2 elements to arrange in {arrangement} layout.")
            return

        n = len(elements)
        if arrangement == "Row":
            positions_2d = _row_positions(n, 35.0)
        elif arrangement == "Grid":
            cols = math.ceil(math.sqrt(n))
            positions_2d = _grid_positions(n, cols, 35.0)
        elif arrangement == "Circle":
            radius = max(30.0, n * 8.0)
            positions_2d = _circle_positions(n, radius)
        else:
            return

        # Compute centroid
        cx = sum(e.transform.translation.x for e in elements) / n
        cy = sum(e.transform.translation.y for e in elements) / n

        old_positions = [Vec3(e.transform.translation.x, e.transform.translation.y,
                              e.transform.translation.z) for e in elements]
        new_positions = [Vec3(cx + dx, cy + dy, e.transform.translation.z)
                         for e, (dx, dy) in zip(elements, positions_2d)]

        cmd = BatchMoveCommand(elements, old_positions, new_positions, f"Auto Layout: {arrangement}")
        self.undo_stack.push(cmd)
        self._sync_viewports()

    # -- Export --

    def _on_export_obj(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export OBJ", "", "Wavefront OBJ (*.obj)")
        if path:
            from export3d import export_obj
            try:
                export_obj(self.project, path)
                self.statusbar.showMessage(f"Exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _on_export_glb(self, orbit=False):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export glTF", "", "glTF Binary (*.glb)")
        if path:
            from export3d import export_glb
            try:
                export_glb(self.project, path, include_camera_orbit=orbit)
                self.statusbar.showMessage(f"Exported to {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", str(e))

    def _on_import_glb(self):
        """Import GLB/glTF file and add elements to current project."""
        if not self.project:
            self._on_new()  # Create new project if none exists

        path, _ = QFileDialog.getOpenFileName(
            self, "Import GLB/glTF File", "",
            "glTF Files (*.glb *.gltf);;All Files (*.*)"
        )
        if path:
            try:
                from import3d import load_glb, Import3DError
                elements = load_glb(path, self.project)

                # Add imported elements to current workspace
                if self.project.workspaces:
                    workspace = self.project.workspaces[self.project.active_workspace_index]
                    for element in elements:
                        self.project.elements.append(element)
                        workspace.element_ids.append(element.unique_id)

                # Update UI
                self._refresh_ui()
                self._mark_modified()
                self.statusbar.showMessage(f"Imported {len(elements)} elements from {path}", 5000)

                # Fit view to show imported elements
                self._on_fit_all()

            except Import3DError as e:
                QMessageBox.critical(self, "Import Error", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Import Error", f"Failed to import GLB file: {str(e)}")

    def _on_import_obj(self):
        """Import OBJ file and add elements to current project."""
        if not self.project:
            self._on_new()  # Create new project if none exists

        path, _ = QFileDialog.getOpenFileName(
            self, "Import OBJ File", "",
            "Wavefront OBJ (*.obj);;All Files (*.*)"
        )
        if path:
            try:
                from import3d import load_obj, Import3DError
                elements = load_obj(path, self.project)

                # Add imported elements to current workspace
                if self.project.workspaces:
                    workspace = self.project.workspaces[self.project.active_workspace_index]
                    for element in elements:
                        self.project.elements.append(element)
                        workspace.element_ids.append(element.unique_id)

                # Update UI
                self._refresh_ui()
                self._mark_modified()
                self.statusbar.showMessage(f"Imported {len(elements)} elements from {path}", 5000)

                # Fit view to show imported elements
                self._on_fit_all()

            except Import3DError as e:
                QMessageBox.critical(self, "Import Error", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Import Error", f"Failed to import OBJ file: {str(e)}")

    def _on_export_gif(self):
        """Export project as orbit animation GIF."""
        if not self.project:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Orbit GIF", "",
            "Animated GIF (*.gif);;All Files (*.*)"
        )
        if path:
            try:
                from gif_export import export_orbit_gif, GifExportError

                # Show progress dialog since GIF export can take time
                progress = QMessageBox(self)
                progress.setWindowTitle("Exporting GIF")
                progress.setText("Capturing orbit animation frames...")
                progress.setStandardButtons(QMessageBox.StandardButton.NoButton)
                progress.setModal(True)
                progress.show()

                # Process events to show dialog
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()

                # Export GIF (4 second animation at 15fps)
                success = export_orbit_gif(
                    self.project, path, self._active_viewport(),
                    duration=4.0, fps=15, size=(800, 600)
                )

                progress.close()

                if success:
                    self.statusbar.showMessage(f"GIF exported to {path}", 5000)
                    # Ask user if they want to open the file
                    reply = QMessageBox.question(
                        self, "Export Complete",
                        f"GIF exported successfully!\n\nWould you like to open the file?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        import os
                        os.startfile(path)  # Windows
                else:
                    QMessageBox.warning(self, "Export Warning", "GIF export completed with warnings.")

            except GifExportError as e:
                QMessageBox.critical(self, "Export Error", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Failed to export GIF: {str(e)}")

    def _on_fit_all(self):
        self._active_viewport()._fit_all()

    def _on_top_view(self):
        self.viewport.camera.pitch = 89.0
        self.viewport.camera.yaw = 0.0
        self._sync_viewports()

    def _on_front_view(self):
        self.viewport.camera.pitch = 0.0
        self.viewport.camera.yaw = 0.0
        self._sync_viewports()

    def _on_side_view(self):
        self.viewport.camera.pitch = 0.0
        self.viewport.camera.yaw = 90.0
        self._sync_viewports()

    def _on_toggle_quad_view(self):
        self._quad_mode = self.action_quad_view.isChecked()
        if self._quad_mode:
            self.viewport.hide()
            self.outer_splitter.insertWidget(0, self.quad_viewport)
            self.quad_viewport.set_project(self.project)
            self.quad_viewport.set_selected(self.viewport.selected_elements)
            self.quad_viewport.show()
        else:
            self.quad_viewport.hide()
            self.outer_splitter.insertWidget(0, self.viewport)
            self.viewport.show()

    def _on_template_menu_action(self, checked=False):
        action = self.sender()
        if action is None:
            return
        template_name = action.data()
        if template_name:
            self._on_add_template(template_name)
        self.outer_splitter.setStretchFactor(0, 3)
        self.outer_splitter.setStretchFactor(1, 2)

    def _find_tree_item_for_element(self, elem) -> Optional[QTreeWidgetItem]:
        """Walk the tree to find the item matching this element."""
        root = self.tree.topLevelItem(0)
        if not root:
            return None
        return self._search_tree(root, elem)

    def _search_tree(self, item: QTreeWidgetItem, elem) -> Optional[QTreeWidgetItem]:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == ITEM_TYPE_ELEMENT and data[1] is elem:
            return item
        for i in range(item.childCount()):
            result = self._search_tree(item.child(i), elem)
            if result:
                return result
        return None

    # -- Context menu handlers --

    def _on_viewport_add_element(self, element_type: str):
        """Handle add element from 3D viewport context menu."""
        if not self.project:
            return
        # Use the first enabled workspace, or create one if none exist
        active_ws = None
        if self.project.workspaces:
            for ws in self.project.workspaces:
                if ws.enabled:
                    active_ws = ws
                    break
        if not active_ws and self.project.workspaces:
            active_ws = self.project.workspaces[0]

        if not active_ws:
            QMessageBox.warning(self, "No Workspace", "Create a workspace first to add elements.")
            return

        if element_type == "HitZone":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
        elif element_type == "MorphZone":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
        elif element_type == "TextLabel":
            elem = TextLabel(unique_id=self.project.generate_id("TextLabel_C"))
        elif element_type == "GroupIE":
            elem = GroupIE(unique_id=self.project.generate_id("GroupIE"))
        else:
            return

        # Position new element slightly offset from camera target
        target = self.viewport.camera.target
        elem.transform.translation = Vec3(target[0] + 20, target[1], target[2])

        cmd = AddElementCommand(self.project, active_ws, elem, f"Add {element_type}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

    def _on_viewport_duplicate_element(self, elem):
        """Handle duplicate element from 3D viewport context menu."""
        if not self.project or not elem:
            return

        # Find which workspace contains this element
        target_ws = None
        for ws in self.project.workspaces:
            if elem.unique_id in ws.element_ids:
                target_ws = ws
                break

        if not target_ws:
            return

        self._apply_current_panel()
        new_elem = copy.deepcopy(elem)
        prefix = (
            "HitZone" if isinstance(elem, HitZone)
            else "MorphZone" if isinstance(elem, MorphZone)
            else "GroupIE" if isinstance(elem, GroupIE)
            else "UnknownIE" if isinstance(elem, UnknownElement)
            else "TextLabel_C"
        )
        new_elem.unique_id = self.project.generate_id(prefix)
        new_elem.display_name = (elem.display_name or elem.unique_id) + " (copy)"
        new_elem.transform.translation.x += 15.0
        new_elem.transform.translation.y += 10.0

        cmd = DuplicateElementCommand(self.project, target_ws, new_elem,
                                      f"Duplicate {elem.unique_id}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

    def _on_viewport_delete_element(self, elem):
        """Handle delete element from 3D viewport context menu."""
        if not self.project or not elem:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {elem.unique_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        ws_refs = []
        for ws in self.project.workspaces:
            if elem.unique_id in ws.element_ids:
                idx = ws.element_ids.index(elem.unique_id)
                ws_refs.append((ws, idx))

        cmd = DeleteElementCommand(self.project, elem, ws_refs, f"Delete {elem.unique_id}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

    # -- Group membership management --

    def _compute_group_bounding_box(self, group: GroupIE) -> BoundingBox:
        """Compute bounding box for a group based on its current members."""
        if not group.group_items:
            # Empty group gets a small default box
            return BoundingBox(
                min=Vec3(-25, -25, -25),
                max=Vec3(25, 25, 25),
                is_valid=1
            )

        # Find member elements by ID
        member_elements = []
        for member_id in group.group_items:
            elem = next((e for e in self.project.elements if e.unique_id == member_id), None)
            if elem:
                member_elements.append(elem)

        if not member_elements:
            # No valid members found, keep default box
            return BoundingBox(
                min=Vec3(-25, -25, -25),
                max=Vec3(25, 25, 25),
                is_valid=1
            )

        # Compute bounding box from member transforms (same logic as _make_group)
        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')
        for elem in member_elements:
            t = elem.transform.translation
            s = elem.transform.scale
            half = 25  # base cube half-extent
            min_x = min(min_x, t.x - half * s.x)
            max_x = max(max_x, t.x + half * s.x)
            min_y = min(min_y, t.y - half * s.y)
            max_y = max(max_y, t.y + half * s.y)
            min_z = min(min_z, t.z - half * s.z)
            max_z = max(max_z, t.z + half * s.z)

        # Add padding
        pad = 10
        min_x -= pad; min_y -= pad; min_z -= pad
        max_x += pad; max_y += pad; max_z += pad

        # BoundingBox is relative to group center
        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        cz = (min_z + max_z) / 2
        hx = (max_x - min_x) / 2
        hy = (max_y - min_y) / 2
        hz = (max_z - min_z) / 2

        # Update group position to the computed center
        group.transform.translation = Vec3(cx, cy, cz)

        return BoundingBox(
            min=Vec3(-hx, -hy, -hz),
            max=Vec3(hx, hy, hz),
            is_valid=1
        )

    def _on_add_group_member(self, group: GroupIE):
        """Add currently selected elements to the group."""
        if not self.project:
            return

        # Get currently selected elements from viewport
        vp = self._active_viewport()
        if hasattr(vp, 'selected_elements'):
            selected = vp.selected_elements
        else:
            selected = [vp.selected_element] if vp.selected_element else []

        if not selected:
            QMessageBox.information(self, "Add to Group",
                "Select elements in the 3D view to add them to this group.")
            return

        # Filter out elements already in group and the group itself
        new_members = []
        for elem in selected:
            if elem is not group and elem.unique_id not in group.group_items:
                new_members.append(elem.unique_id)

        if not new_members:
            QMessageBox.information(self, "Add to Group",
                "Selected elements are already in this group or include the group itself.")
            return

        # Create new member list and compute new bounding box
        old_members = group.group_items[:]
        new_member_list = old_members + new_members

        # Temporarily update group to compute new bbox
        temp_items = group.group_items
        group.group_items = new_member_list
        new_bbox = self._compute_group_bounding_box(group)
        group.group_items = temp_items  # restore

        # Create and execute command
        cmd = GroupMembershipCommand(group, old_members, new_member_list,
                                     f"Add {len(new_members)} element(s) to group")
        cmd.new_bbox = new_bbox
        self.undo_stack.push(cmd)

        # Refresh UI
        self.groupie_panel.load(group)
        self._sync_viewports()
        self._rebuild_tree()

    def _on_remove_group_member(self, group: GroupIE):
        """Remove selected member from the group."""
        panel = self.groupie_panel
        selected_items = panel.members_list.selectedItems()
        if not selected_items:
            return

        # Get IDs of selected members to remove
        members_to_remove = [item.text() for item in selected_items]

        # Create new member list
        old_members = group.group_items[:]
        new_member_list = [m for m in old_members if m not in members_to_remove]

        # Temporarily update group to compute new bbox
        temp_items = group.group_items
        group.group_items = new_member_list
        new_bbox = self._compute_group_bounding_box(group)
        group.group_items = temp_items  # restore

        # Create and execute command
        cmd = GroupMembershipCommand(group, old_members, new_member_list,
                                     f"Remove {len(members_to_remove)} element(s) from group")
        cmd.new_bbox = new_bbox
        self.undo_stack.push(cmd)

        # Refresh UI
        self.groupie_panel.load(group)
        self._sync_viewports()
        self._rebuild_tree()

    # -- Context menu group operations --

    def _on_add_to_group(self, elements: list, group_id: str):
        """Add elements to an existing group via context menu."""
        if not self.project or not elements:
            return

        # Find the target group
        target_group = None
        for group in self.project.elements:
            if hasattr(group, 'group_items') and group.unique_id == group_id:
                target_group = group
                break

        if not target_group:
            return

        # Filter out elements already in group
        new_members = []
        for elem in elements:
            if elem.unique_id not in target_group.group_items:
                new_members.append(elem.unique_id)

        if not new_members:
            return

        # Create new member list and compute new bounding box
        old_members = target_group.group_items[:]
        new_member_list = old_members + new_members

        # Temporarily update group to compute new bbox
        temp_items = target_group.group_items
        target_group.group_items = new_member_list
        new_bbox = self._compute_group_bounding_box(target_group)
        target_group.group_items = temp_items  # restore

        # Create and execute command
        cmd = GroupMembershipCommand(target_group, old_members, new_member_list,
                                     f"Add {len(new_members)} element(s) to {target_group.display_name or target_group.unique_id}")
        cmd.new_bbox = new_bbox
        self.undo_stack.push(cmd)

        # Refresh UI and selection
        if isinstance(self.scroll.widget(), GroupIEPanel) and self.groupie_panel._target is target_group:
            self.groupie_panel.load(target_group)
        self._sync_viewports()
        self._rebuild_tree()

    def _on_remove_from_group(self, elements: list, group_id: str):
        """Remove elements from a group via context menu."""
        if not self.project or not elements:
            return

        # Find the target group
        target_group = None
        for group in self.project.elements:
            if hasattr(group, 'group_items') and group.unique_id == group_id:
                target_group = group
                break

        if not target_group:
            return

        # Get element IDs to remove
        element_ids = [elem.unique_id for elem in elements]
        members_to_remove = [eid for eid in element_ids if eid in target_group.group_items]

        if not members_to_remove:
            return

        # Create new member list
        old_members = target_group.group_items[:]
        new_member_list = [m for m in old_members if m not in members_to_remove]

        # Temporarily update group to compute new bbox
        temp_items = target_group.group_items
        target_group.group_items = new_member_list
        new_bbox = self._compute_group_bounding_box(target_group)
        target_group.group_items = temp_items  # restore

        # Create and execute command
        cmd = GroupMembershipCommand(target_group, old_members, new_member_list,
                                     f"Remove {len(members_to_remove)} element(s) from {target_group.display_name or target_group.unique_id}")
        cmd.new_bbox = new_bbox
        self.undo_stack.push(cmd)

        # Refresh UI
        if isinstance(self.scroll.widget(), GroupIEPanel) and self.groupie_panel._target is target_group:
            self.groupie_panel.load(target_group)
        self._sync_viewports()
        self._rebuild_tree()

    def _on_create_group(self, elements: list):
        """Create a new group from selected elements via context menu."""
        if not self.project or len(elements) < 1:
            return

        # Use the first enabled workspace
        active_ws = None
        for ws in self.project.workspaces:
            if ws.enabled:
                active_ws = ws
                break
        if not active_ws and self.project.workspaces:
            active_ws = self.project.workspaces[0]

        if not active_ws:
            QMessageBox.warning(self, "No Workspace", "Create a workspace first to add groups.")
            return

        # Get center of selected elements for group position
        total_x = total_y = total_z = 0.0
        for elem in elements:
            t = elem.transform.translation
            total_x += t.x
            total_y += t.y
            total_z += t.z
        center = Vec3(total_x / len(elements), total_y / len(elements), total_z / len(elements))

        # Create group using template generator helper
        member_ids = [elem.unique_id for elem in elements]
        group_name = f"Group from {len(elements)} elements"

        new_group = _make_group(self.project, group_name, center, member_ids, elements)

        # Add group to project and workspace
        cmd = AddElementCommand(self.project, active_ws, new_group, f"Create group from {len(elements)} elements")
        self.undo_stack.push(cmd)

        # Refresh UI and select the new group
        self._rebuild_tree()
        self._sync_viewports()
        self._sync_selection(new_group)

    # -- Quick edit operations --

    def _on_edit_text(self, element):
        """Handle text editing from context menu."""
        if not element:
            return

        from model import TextLabel
        if isinstance(element, TextLabel):
            # Edit the actual text content
            current_text = getattr(element, 'text', '')
            new_text, ok = QInputDialog.getText(self, "Edit Text",
                f"Edit text for {element.unique_id}:", text=current_text)
            if ok and new_text != current_text:
                old_text = element.text
                cmd = SetPropertyCommand(element, "text", old_text, new_text, "Edit text")
                self.undo_stack.push(cmd)
                element.text = new_text
                self._rebuild_tree()
                self._sync_viewports()
                # Refresh panel if it's currently showing this element
                if isinstance(self.scroll.widget(), TextLabelPanel) and self.textlabel_panel._target is element:
                    self.textlabel_panel.load(element)
        else:
            # Edit display name for other elements
            current_name = element.display_name
            new_name, ok = QInputDialog.getText(self, "Edit Display Name",
                f"Edit display name for {element.unique_id}:", text=current_name)
            if ok and new_name != current_name:
                old_name = element.display_name
                cmd = SetPropertyCommand(element, "display_name", old_name, new_name, "Edit display name")
                self.undo_stack.push(cmd)
                element.display_name = new_name
                self._rebuild_tree()
                self._sync_viewports()
                # Refresh appropriate panel if it's currently showing this element
                current_widget = self.scroll.widget()
                if hasattr(current_widget, '_target') and current_widget._target is element:
                    current_widget.load(element)

    def _on_change_color(self, elements: list):
        """Handle color changing from context menu."""
        if not elements:
            return

        # Use the first element's color as the starting color
        start_color = elements[0].color
        color = QColorDialog.getColor(
            QColor.fromRgbF(start_color.r, start_color.g, start_color.b, start_color.a),
            self, "Choose Color"
        )

        if color.isValid():
            # Convert QColor back to our Color class
            new_color = Color(
                color.redF(),
                color.greenF(),
                color.blueF(),
                color.alphaF()
            )

            if len(elements) == 1:
                # Single element
                elem = elements[0]
                old_color = elem.color
                cmd = SetPropertyCommand(elem, "color", old_color, new_color, "Change color")
                self.undo_stack.push(cmd)
                elem.color = new_color
            else:
                # Multiple elements - batch command
                old_colors = [elem.color for elem in elements]
                cmd = BatchSetPropertyCommand(elements, "color", old_colors, new_color,
                                            f"Change color of {len(elements)} elements")
                self.undo_stack.push(cmd)
                for elem in elements:
                    elem.color = new_color

            self._rebuild_tree()
            self._sync_viewports()

            # Refresh panel if it's currently showing one of the changed elements
            current_widget = self.scroll.widget()
            if hasattr(current_widget, '_target') and current_widget._target in elements:
                current_widget.load(current_widget._target)

    def _on_batch_delete(self, elements: list):
        """Handle batch deletion from context menu."""
        if not self.project or not elements:
            return

        # Create batch delete command
        cmd = BatchDeleteCommand(self.project, elements, f"Delete {len(elements)} elements")
        self.undo_stack.push(cmd)

        # Clear selection and refresh UI
        self._sync_selection(None)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

    def _on_toggle_lock(self, elements: list):
        """Handle lock/unlock toggle from context menu."""
        if not elements:
            return

        # Determine what action to take based on current lock states
        locked_count = sum(1 for e in elements if e.is_locked)
        new_lock_state = locked_count < len(elements)  # lock if any are unlocked

        if len(elements) == 1:
            # Single element
            elem = elements[0]
            old_lock = elem.is_locked
            cmd = SetPropertyCommand(elem, "is_locked", old_lock, new_lock_state,
                                   "Lock" if new_lock_state else "Unlock")
            self.undo_stack.push(cmd)
            elem.is_locked = new_lock_state
        else:
            # Multiple elements - batch command
            old_locks = [elem.is_locked for elem in elements]
            action = "Lock" if new_lock_state else "Unlock"
            cmd = BatchSetPropertyCommand(elements, "is_locked", old_locks, new_lock_state,
                                        f"{action} {len(elements)} elements")
            self.undo_stack.push(cmd)
            for elem in elements:
                elem.is_locked = new_lock_state

        self._rebuild_tree()
        self._sync_viewports()

        # Refresh panel if it's currently showing one of the changed elements
        current_widget = self.scroll.widget()
        if hasattr(current_widget, '_target') and current_widget._target in elements:
            current_widget.load(current_widget._target)

    def _on_move_to_workspace(self, elements: list, workspace_id: str):
        """Handle moving elements to another workspace from context menu."""
        if not self.project or not elements:
            return

        # Find target workspace
        target_workspace = None
        for ws in self.project.workspaces:
            if ws.unique_id == workspace_id:
                target_workspace = ws
                break

        if not target_workspace:
            return

        # Find current workspace(s) for these elements and create removal commands
        elements_to_move = []
        workspace_changes = []  # [(workspace, elements_to_remove_from_it, elements_to_add_to_it)]

        for element in elements:
            # Find which workspaces currently contain this element
            current_workspaces = []
            for ws in self.project.workspaces:
                if element.unique_id in ws.element_ids:
                    current_workspaces.append(ws)

            if current_workspaces:
                elements_to_move.append(element)
                # Remove from all current workspaces
                for ws in current_workspaces:
                    workspace_changes.append((ws, [element.unique_id], []))

        if not elements_to_move:
            return

        # Add to target workspace
        new_element_ids = [elem.unique_id for elem in elements_to_move]
        existing_target_ids = [eid for eid in new_element_ids if eid in target_workspace.element_ids]
        elements_to_add = [eid for eid in new_element_ids if eid not in target_workspace.element_ids]

        if elements_to_add:
            workspace_changes.append((target_workspace, [], elements_to_add))

        # Execute all workspace changes
        for workspace, to_remove, to_add in workspace_changes:
            for elem_id in to_remove:
                if elem_id in workspace.element_ids:
                    workspace.element_ids.remove(elem_id)
            for elem_id in to_add:
                if elem_id not in workspace.element_ids:
                    workspace.element_ids.append(elem_id)

        # For simplicity, we're not making this undoable - workspace management
        # is considered a structural change. In a full implementation, you'd want
        # a WorkspaceTransferCommand that tracks all the moves.

        self._rebuild_tree()
        self._sync_viewports()

        # Show feedback
        target_name = target_workspace.display_name or target_workspace.unique_id
        moved_count = len(elements_to_move)
        self.statusbar.showMessage(f"Moved {moved_count} element(s) to workspace '{target_name}'", 3000)

    # -- Template generation --

    def _on_add_template(self, template_name: str):
        """Generate a preset layout from a template and add to active workspace."""
        # Project should always exist since we start with a blank one

        # Find active workspace
        active_ws = self._get_selected_workspace()
        if not active_ws:
            for ws in self.project.workspaces:
                if ws.enabled:
                    active_ws = ws
                    break
        if not active_ws and self.project.workspaces:
            active_ws = self.project.workspaces[0]
        if not active_ws:
            # This shouldn't happen since new projects get a default workspace
            QMessageBox.warning(self, "No Workspace", "Something went wrong - no active workspace found.")
            return

        # Origin near camera target
        target = self.viewport.camera.target
        origin = Vec3(target[0], target[1], target[2])

        generator = TEMPLATES.get(template_name)
        if not generator:
            return

        elements = generator(self.project, origin)
        if not elements:
            return

        cmd = AddTemplateCommand(self.project, active_ws, elements, f"Add {template_name}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()


def run_editor():
    import traceback, logging
    log_path = os.path.join(os.path.dirname(__file__), "editor.log")
    logging.basicConfig(filename=log_path, level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s", force=True)

    def exception_hook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.critical(msg)
        print(msg, file=sys.stderr)

    sys.excepthook = exception_hook

    print("Starting QApplication...")
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    print("Creating window...")
    window = MainWindow()
    print("Showing window...")
    window.show()
    print("Window shown, editor started")

    # Auto-open last file
    cfg = _load_config()
    last = cfg.get("last_file")
    if last and os.path.isfile(last):
        print(f"Auto-opening: {last}")
        window._open_file(last)
        print("File opened")

    print("Starting event loop...")
    sys.exit(app.exec())
