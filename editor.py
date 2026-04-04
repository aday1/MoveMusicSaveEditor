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
import re
import sys
from pathlib import Path
from typing import List, Optional

try:
    import mido
except Exception:
    mido = None

try:
    from pythonosc.udp_client import SimpleUDPClient
except Exception:
    SimpleUDPClient = None

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


MIDI_MESSAGE_TYPES = ["EMidiMessageType::Note", "EMidiMessageType::CC"]
MIDI_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MIDI_CHANNEL_OPTIONS = [(f"Ch {channel}", channel) for channel in range(1, 17)]
MIDI_CC_OPTIONS = [(str(value), value) for value in range(128)]
MIDI_VELOCITY_OPTIONS = [(f"{step / 100.0:.2f}", step / 100.0) for step in range(101)]


def _midi_note_label(note: int) -> str:
    octave = (note // 12) - 1
    return f"{MIDI_NOTE_NAMES[note % 12]}{octave}"


MIDI_NOTE_OPTIONS = [(f"{note} ({_midi_note_label(note)})", note) for note in range(128)]


def _pick_loopback_midi_port(port_names: list[str]) -> Optional[str]:
    """Prefer virtual loopback ports when available."""
    if not port_names:
        return None

    strong_tokens = [
        "loopback midi",
        "loopmidi",
        "loopbe",
        "loop be",
        "virtual midi",
        "rtp-midi",
        "rtpmidi",
    ]
    weak_tokens = [
        "loopback",
        "virtual",
        "midi loop",
    ]

    lowered = [(name, name.lower()) for name in port_names]
    for token in strong_tokens:
        for original, lowered_name in lowered:
            if token in lowered_name:
                return original
    for token in weak_tokens:
        for original, lowered_name in lowered:
            if token in lowered_name:
                return original
    return None

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QDialog, QTabWidget,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QPushButton,
    QProgressDialog,
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
from play_mode_window import PlayModeWindow
from desktop_play_midi_in import DesktopPlayMidiInThread, resolve_midi_input_port
from template_generator import TEMPLATES, _row_positions, _grid_positions, _circle_positions, _make_group
from performance_panel import PerformancePanel
from roliblock_led import default_mirror
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
        # Store deletion information — capture indices BEFORE any removal
        self.element_data = []
        for element in self.elements:
            if element not in self.project.elements:
                continue
            project_index = self.project.elements.index(element)
            workspace_refs = []
            for ws in self.project.workspaces:
                if element.unique_id in ws.element_ids:
                    ws_index = ws.element_ids.index(element.unique_id)
                    workspace_refs.append((ws, ws_index))
            self.element_data.append((element, project_index, workspace_refs))

        # Remove elements (reverse order to preserve earlier indices)
        for element, _, workspace_refs in reversed(self.element_data):
            if element in self.project.elements:
                self.project.elements.remove(element)
            for ws, _ in workspace_refs:
                if element.unique_id in ws.element_ids:
                    ws.element_ids.remove(element.unique_id)

    def undo(self):
        # Restore in forward order (lowest index first) so inserts don't shift
        for element, project_index, workspace_refs in self.element_data:
            idx = min(project_index, len(self.project.elements))
            self.project.elements.insert(idx, element)
            for ws, ws_index in workspace_refs:
                ws_idx = min(ws_index, len(ws.element_ids))
                ws.element_ids.insert(ws_idx, element.unique_id)


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
            if elem in self.project.elements:
                self.project.elements.remove(elem)
            if elem.unique_id in self.workspace.element_ids:
                self.workspace.element_ids.remove(elem.unique_id)


class BatchRotateCommand(QUndoCommand):
    """Rotate one or more elements as a single undo step."""
    def __init__(self, elements, old_rotations, new_rotations, description="Rotate elements"):
        super().__init__(description)
        self.elements = elements
        self.old_rotations = old_rotations  # list of (x, y, z, w) tuples
        self.new_rotations = new_rotations

    def redo(self):
        for elem, (qx, qy, qz, qw) in zip(self.elements, self.new_rotations):
            elem.transform.rotation = Quat(x=qx, y=qy, z=qz, w=qw)

    def undo(self):
        for elem, (qx, qy, qz, qw) in zip(self.elements, self.old_rotations):
            elem.transform.rotation = Quat(x=qx, y=qy, z=qz, w=qw)


class BatchScaleCommand(QUndoCommand):
    """Scale one or more elements as a single undo step."""
    def __init__(self, elements, old_scales, new_scales, description="Resize elements"):
        super().__init__(description)
        self.elements = elements
        self.old_scales = old_scales  # list of (sx, sy, sz) tuples
        self.new_scales = new_scales

    def redo(self):
        for elem, (sx, sy, sz) in zip(self.elements, self.new_scales):
            elem.transform.scale = Vec3(sx, sy, sz)

    def undo(self):
        for elem, (sx, sy, sz) in zip(self.elements, self.old_scales):
            elem.transform.scale = Vec3(sx, sy, sz)


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


class AddWorkspaceCommand(QUndoCommand):
    """Add a new workspace to the project."""
    def __init__(self, project: Project, workspace: Workspace, description: str = "Add Workspace"):
        super().__init__(description)
        self.project = project
        self.workspace = workspace
        self._old_active = project.active_workspace_index

    def redo(self):
        self.project.workspaces.append(self.workspace)
        self.project.active_workspace_index = len(self.project.workspaces) - 1

    def undo(self):
        idx = self.project.workspaces.index(self.workspace)
        self.project.workspaces.remove(self.workspace)
        if self.project.workspaces:
            self.project.active_workspace_index = max(0, min(self._old_active, len(self.project.workspaces) - 1))
        else:
            self.project.active_workspace_index = 0


class DeleteWorkspaceCommand(QUndoCommand):
    """Remove a workspace from the project (elements remain in project.elements)."""
    def __init__(self, project: Project, workspace: Workspace, description: str = "Delete Workspace"):
        super().__init__(description)
        self.project = project
        self.workspace = workspace
        self._index = project.workspaces.index(workspace)
        self._old_active = project.active_workspace_index

    def redo(self):
        self.project.workspaces.remove(self.workspace)
        if self.project.active_workspace_index >= len(self.project.workspaces):
            self.project.active_workspace_index = max(0, len(self.project.workspaces) - 1)

    def undo(self):
        self.project.workspaces.insert(self._index, self.workspace)
        self.project.active_workspace_index = self._old_active


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
        self.active_label = QLabel()
        layout.addRow("Display Name:", self.name_edit)
        layout.addRow("Enabled:", self.enabled_check)
        layout.addRow("Elements:", self.elements_label)
        layout.addRow("Status:", self.active_label)

        btn_row = QHBoxLayout()
        self.set_active_btn = QPushButton("Set as Active Workspace")
        self.add_ws_btn = QPushButton("Add Workspace")
        self.delete_ws_btn = QPushButton("Delete Workspace")
        btn_row.addWidget(self.set_active_btn)
        btn_row.addWidget(self.add_ws_btn)
        btn_row.addWidget(self.delete_ws_btn)
        btn_widget = QWidget()
        btn_widget.setLayout(btn_row)
        layout.addRow(btn_widget)

    def load(self, ws: Workspace, is_active: bool = False):
        self.name_edit.blockSignals(True)
        self.name_edit.setText(ws.display_name)
        self.name_edit.blockSignals(False)
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(ws.enabled)
        self.enabled_check.blockSignals(False)
        self.elements_label.setText(str(len(ws.element_ids)))
        if is_active:
            self.active_label.setText("Active (starting workspace)")
            self.set_active_btn.setEnabled(False)
        else:
            self.active_label.setText("")
            self.set_active_btn.setEnabled(True)


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
        self._workspace_clipboard: Optional[dict] = None
        cfg = _load_config()
        self.debug_mode = bool(cfg.get("debug_mode", False))
        self._label_font_size: int = int(cfg.get("label_font_size", 10))

        self._settings_dialog: Optional[SettingsDialog] = None
        self._midi_overview_dialog: Optional[MidiOverviewDialog] = None
        self._roliblock_mirror = default_mirror()
        self._play_mode_window: Optional[PlayModeWindow] = None
        self._play_viewport_placeholder: Optional[QWidget] = None
        self._play_viewport_split_index: int = 0
        self._desktop_play_midi_in_threads: List[DesktopPlayMidiInThread] = []
        # Mirrors action_perf_lock; must exist before Desktop Play (not only after first F5 toggle).
        self._performance_lock: bool = False

        self._setup_ui()
        self._setup_toolbar()
        self._setup_menu()
        self._setup_statusbar()
        self._connect_signals()
        self._setup_global_shortcuts()

        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        # Restore saved window geometry
        saved_geom = cfg.get("window_geometry")
        if saved_geom:
            try:
                from PyQt6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromHex(bytes(saved_geom, "ascii")))
            except Exception:
                pass

        # Blank project so the viewport and Desktop Play Mode work before File > New.
        self._create_new_project()

    def _setup_global_shortcuts(self):
        """Editor-wide shortcuts for MIDI nudging regardless of focused widget."""
        self._shortcut_delete = QShortcut(QKeySequence("Delete"), self)
        self._shortcut_delete.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_delete.activated.connect(self._on_global_delete_shortcut)

        self._shortcut_shift_delete = QShortcut(QKeySequence("Shift+Delete"), self)
        self._shortcut_shift_delete.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_shift_delete.activated.connect(self._on_global_delete_shortcut)

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

        # Fast workspace cycling while editing scenes.
        self._shortcut_ws_prev = QShortcut(QKeySequence("Alt+PgUp"), self)
        self._shortcut_ws_prev.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_ws_prev.activated.connect(lambda: self._cycle_active_workspace(-1))

        self._shortcut_ws_next = QShortcut(QKeySequence("Alt+PgDown"), self)
        self._shortcut_ws_next.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_ws_next.activated.connect(lambda: self._cycle_active_workspace(1))

    def _on_global_delete_shortcut(self):
        """Delete selected elements even when focus is on non-viewport widgets."""
        if not self.project:
            return

        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget)):
            return

        self._on_delete()

    def _trigger_active_viewport_midi_cc(self, delta: int):
        vp = self._active_viewport()
        if vp is not None and hasattr(vp, '_on_shortcut_midi_cc'):
            vp._on_shortcut_midi_cc(delta)

    def _trigger_active_viewport_midi_note(self, delta: int):
        vp = self._active_viewport()
        if vp is not None and hasattr(vp, '_on_shortcut_midi_note'):
            vp._on_shortcut_midi_note(delta)

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

        # Create all panels first (before tabs)
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
        
        # Performance/test panel for desktop MIDI testing
        self.performance_panel = PerformancePanel()
        # Pre-populate transport bar from saved config
        self.performance_panel.load_config(_load_config())
        self.performance_panel.set_roliblock_bind_callback(self._on_roliblock_bind)
        self.performance_panel.roliblock_changed.connect(self._on_roliblock_config_changed)
        self._apply_roliblock_debug_visibility()
        self._sync_roliblock_mirror_from_panel()

        # Property panel with tabs (Properties + Performance)
        self._props_tabs = QTabWidget()
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._props_tabs.addTab(self.scroll, "Properties")
        
        perf_scroll = QScrollArea()
        perf_scroll.setWidgetResizable(True)
        perf_scroll.setWidget(self.performance_panel)
        self._props_tabs.addTab(perf_scroll, "Performance Test")
        
        inner_splitter.addWidget(self._props_tabs)

        inner_splitter.setStretchFactor(0, 1)
        inner_splitter.setStretchFactor(1, 3)

        self.outer_splitter.setStretchFactor(0, 3)  # viewport gets more space
        self.outer_splitter.setStretchFactor(1, 2)
        
        # Set initial empty panel in Properties tab
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
        pw = getattr(self, "_play_mode_window", None)
        if pw is not None and self.project:
            pw.refresh_navigator(self.project, self.project.active_workspace_index)

    def _sync_selection(self, elem_or_list):
        """Set selection on both viewports. Accepts element, list, or None."""
        self.viewport.set_selected(elem_or_list)
        self.quad_viewport.set_selected(elem_or_list)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self._toolbar = toolbar

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

        # Top-row workspace switcher for fast scene context changes.
        toolbar.addWidget(QLabel("Workspace:"))
        self.workspace_switcher = QComboBox()
        self.workspace_switcher.setMinimumWidth(220)
        self.workspace_switcher.currentIndexChanged.connect(self._on_toolbar_workspace_changed)
        toolbar.addWidget(self.workspace_switcher)

        self.action_add_workspace_toolbar = QAction("+ Workspace", self)
        self.action_add_workspace_toolbar.setShortcut("Ctrl+Shift+W")
        toolbar.addAction(self.action_add_workspace_toolbar)

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

        self.action_copy = QAction("Copy", self)
        self.action_copy.setShortcut("Ctrl+C")

        self.action_cut = QAction("Cut", self)
        self.action_cut.setShortcut("Ctrl+X")

        self.action_paste = QAction("Paste", self)
        self.action_paste.setShortcut("Ctrl+V")

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

        toolbar.addSeparator()

        # Performance Lock — disables element drag/resize/rotate so clicks select-only
        self.action_perf_lock = QAction("⚡ Perf Lock", self)
        self.action_perf_lock.setCheckable(True)
        self.action_perf_lock.setToolTip(
            "Performance Lock: blocks element dragging so you can click/select freely.\n"
            "Selecting an element auto-switches to the Performance Test tab."
        )
        self.action_perf_lock.setShortcut("F5")
        self.action_perf_lock.toggled.connect(self._on_perf_lock_toggled)
        toolbar.addAction(self.action_perf_lock)

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
        edit_menu.addSeparator()
        edit_menu.addAction(self.action_copy)
        edit_menu.addAction(self.action_cut)
        edit_menu.addAction(self.action_paste)

        view_menu = menubar.addMenu("View")
        self.action_debug_mode = view_menu.addAction("Debug Mode")
        self.action_debug_mode.setCheckable(True)
        self.action_debug_mode.setChecked(self.debug_mode)
        self.action_debug_mode.triggered.connect(self._on_toggle_debug_mode)
        view_menu.addSeparator()
        self.action_midi_overview = view_menu.addAction("MIDI Overview Table...")
        self.action_midi_overview.setShortcut("Ctrl+M")
        self.action_midi_overview.triggered.connect(self._on_open_midi_overview)
        view_menu.addSeparator()
        self.action_desktop_play = view_menu.addAction("Desktop Play Mode...")
        self.action_desktop_play.setShortcut("F11")
        self.action_desktop_play.setToolTip("Fullscreen 3D play session (Esc exits)")
        self.action_desktop_play.triggered.connect(self._on_desktop_play_mode)
        view_menu.addSeparator()
        self.action_settings = view_menu.addAction("Settings...")
        self.action_settings.triggered.connect(self._on_open_settings)

        # Templates menu
        template_menu = menubar.addMenu("Templates")

        def _template_category(name: str) -> str:
            n = name.lower()
            if n.startswith("shape:"):
                return "Fun Shapes"
            if "aum" in n or "ios" in n:
                return "iOS / AUM"
            if "ruismaker" in n:
                return "Ruismaker"
            if "renoise" in n:
                return "Renoise"
            if "reaktor" in n:
                return "Reaktor"
            if "serumfx" in n:
                return "Serum"
            if "serum" in n:
                return "Serum"
            if "reason:" in n:
                return "Reason"
            if "code49" in n or "x-touch" in n or "behringer" in n or "m-audio" in n or "x-station" in n or "novation" in n:
                return "Hardware Controllers"
            if "bitwig" in n:
                return "Bitwig"
            if "reaper" in n:
                return "Reaper"
            if "resolume" in n:
                return "Resolume"
            if "mc-303" in n or "mc-505" in n or "groovebox" in n:
                return "Grooveboxes"
            if "sugarbytes" in n or "drumcomputer" in n:
                return "Sugarbytes"
            if "keyboard" in n:
                return "Keyboards"
            if "drum" in n:
                return "Drum Pads"
            if "fader" in n:
                return "Faders"
            if "knob" in n:
                return "Knobs"
            if "xy" in n:
                return "XY Pads"
            if "button" in n:
                return "Buttons"
            if "mixer" in n:
                return "Mixer"
            if "debug" in n:
                return "Debug"
            return "Other"

        category_order = [
            "Faders", "Knobs", "XY Pads", "Buttons", "Drum Pads", "Keyboards", "Mixer",
            "iOS / AUM", "Ruismaker", "Renoise", "Reaktor", "Serum", "Reason", "Hardware Controllers",
            "Bitwig", "Reaper", "Resolume", "Grooveboxes", "Sugarbytes",
            "Fun Shapes", "Debug", "Other"
        ]
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

        # Template orientation submenu
        template_menu.addSeparator()
        orient_menu = template_menu.addMenu("Placement Orientation")
        self._template_orientation = "Flat"
        orient_group = QActionGroup(self)
        for label in ("Flat (XY)", "Vertical (XZ)", "Side (YZ)"):
            act = orient_menu.addAction(label)
            act.setCheckable(True)
            act.setActionGroup(orient_group)
            if label.startswith("Flat"):
                act.setChecked(True)
            act.triggered.connect(lambda checked, l=label: self._set_template_orientation(l))

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
        self.action_export_blend_script = export_menu.addAction("Blender Python Script (.py)...")
        export_menu.addSeparator()
        self.action_export_gif = export_menu.addAction("Orbit Animation GIF...")
        self.action_export_mp4 = export_menu.addAction("Orbit Animation MP4...")
        export_menu.addSeparator()
        self.action_export_touchosc = export_menu.addAction("TouchOSC Layout Blueprint (.json)...")
        self.action_export_obj.triggered.connect(self._on_export_obj)
        self.action_export_glb.triggered.connect(self._on_export_glb)
        self.action_export_glb_orbit.triggered.connect(lambda: self._on_export_glb(orbit=True))
        self.action_export_blend_script.triggered.connect(self._on_export_blend_script)
        self.action_export_gif.triggered.connect(self._on_export_gif)
        self.action_export_mp4.triggered.connect(self._on_export_mp4)
        self.action_export_touchosc.triggered.connect(self._on_export_touchosc)

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
        self.action_add_workspace_toolbar.triggered.connect(self._on_add_workspace)
        self.action_copy.triggered.connect(self._on_copy_elements)
        self.action_cut.triggered.connect(self._on_cut_elements)
        self.action_paste.triggered.connect(self._on_paste_elements)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

        # 3D viewport signals
        self.viewport.element_selected.connect(self._on_viewport_select)
        self.viewport.element_moved.connect(self._on_viewport_move)
        self.viewport.elements_moved.connect(self._on_viewport_elements_moved)
        self.viewport.midi_mappings_nudged.connect(self._on_viewport_midi_nudged)
        self.viewport.status_message.connect(lambda msg: self.statusbar.showMessage(msg, 2500))
        self.viewport.element_scaled.connect(self._on_viewport_scale)
        self.viewport.elements_scaled.connect(self._on_viewport_elements_scaled)
        self.viewport.elements_rotated.connect(self._on_viewport_elements_rotated)
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
        self.viewport.play_perf_send.connect(self._on_play_perf_send)
        self.viewport.play_morph_interaction.connect(self._on_play_morph_interaction)
        self.viewport.play_mode_exit_requested.connect(self._exit_desktop_play_mode)
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
        self.quad_viewport.elements_scaled.connect(self._on_viewport_elements_scaled)
        self.quad_viewport.elements_rotated.connect(self._on_viewport_elements_rotated)
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
            # Save window geometry
            geom_hex = bytes(self.saveGeometry().toHex()).decode("ascii")
            cfg = _load_config()
            cfg["window_geometry"] = geom_hex
            _save_config(cfg)
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
        self._workspace_items = {}
        if not self.project:
            self._refresh_workspace_switcher()
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
        for i, ws in enumerate(self.project.workspaces):
            ws_label = f"{ws.display_name} ({len(ws.element_ids)} elements)"
            if i == self.project.active_workspace_index:
                ws_label += " [active]"
            if ws.enabled:
                ws_label += " [enabled]"
            ws_item = QTreeWidgetItem(vi_item, [ws_label])
            ws_item.setData(0, Qt.ItemDataRole.UserRole, (ITEM_TYPE_WORKSPACE, ws))
            ws_item.setExpanded(True)
            self._workspace_items[ws.unique_id] = ws_item

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

        self._refresh_workspace_switcher()

    def _refresh_workspace_switcher(self):
        self.workspace_switcher.blockSignals(True)
        self.workspace_switcher.clear()
        if not self.project or not self.project.workspaces:
            self.workspace_switcher.setEnabled(False)
            self.workspace_switcher.blockSignals(False)
            return

        self.workspace_switcher.setEnabled(True)
        for i, ws in enumerate(self.project.workspaces):
            label = ws.display_name or ws.unique_id
            if i == self.project.active_workspace_index:
                label += " [active]"
            self.workspace_switcher.addItem(label, ws.unique_id)

        idx = max(0, min(self.project.active_workspace_index, self.workspace_switcher.count() - 1))
        self.workspace_switcher.setCurrentIndex(idx)
        self.workspace_switcher.blockSignals(False)

    def _on_toolbar_workspace_changed(self, idx: int):
        if idx < 0 or not self.project:
            return
        ws_id = self.workspace_switcher.itemData(idx)
        if not ws_id:
            return
        for i, ws in enumerate(self.project.workspaces):
            if ws.unique_id == ws_id:
                self._set_active_workspace_index(i)
                return

    def _set_active_workspace_index(self, new_idx: int):
        if not self.project:
            return
        old_idx = self.project.active_workspace_index
        if old_idx == new_idx or not (0 <= new_idx < len(self.project.workspaces)):
            return

        cmd = SetPropertyCommand(
            self.project,
            "active_workspace_index",
            old_idx,
            new_idx,
            "Set active workspace",
        )
        self.undo_stack.push(cmd)
        self._rebuild_tree()

        # Select the workspace node so it's obvious what scene is active.
        ws = self.project.workspaces[new_idx]
        ws_item = self._workspace_items.get(ws.unique_id)
        if ws_item is not None:
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            ws_item.setSelected(True)
            self.tree.scrollToItem(ws_item)
            self.tree.blockSignals(False)
            self._on_selection_changed()

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
            is_active = (self.project.workspaces.index(obj) == self.project.active_workspace_index
                         if obj in self.project.workspaces else False)
            self.workspace_panel.load(obj, is_active=is_active)
            self._connect_workspace_panel(obj)
            self._set_panel(self.workspace_panel)
            self._sync_selection(None)
        elif item_type == ITEM_TYPE_ELEMENT:
            elements_to_show = [obj]
            self._sync_selection(obj)
            
            # Update performance panel for testing
            self.performance_panel.set_selected_elements(
                elements_to_show, 
                self._perf_send
            )
            
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
        try: p.set_active_btn.clicked.disconnect()
        except: pass
        try: p.add_ws_btn.clicked.disconnect()
        except: pass
        try: p.delete_ws_btn.clicked.disconnect()
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
        p.set_active_btn.clicked.connect(lambda: self._on_set_active_workspace(ws))
        p.add_ws_btn.clicked.connect(self._on_add_workspace)
        p.delete_ws_btn.clicked.connect(lambda: self._on_delete_workspace(ws))

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

    def _active_workspace(self) -> Optional[Workspace]:
        if not self.project or not self.project.workspaces:
            return None
        idx = max(0, min(self.project.active_workspace_index, len(self.project.workspaces) - 1))
        return self.project.workspaces[idx]

    def _selected_elements_and_workspace(self, require_single_workspace: bool = False) -> tuple[list, Optional[Workspace]]:
        """Return selected elements and inferred source workspace.

        When require_single_workspace=True (used by copy/cut), mixed-workspace
        tree selections are rejected so clipboard semantics stay predictable.
        """
        if not self.project:
            return [], None

        items = self.tree.selectedItems()
        if items:
            elems = []
            source_ws = None
            seen = set()
            for item in items:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if not data or data[0] != ITEM_TYPE_ELEMENT:
                    continue
                elem = data[1]
                if id(elem) in seen:
                    continue
                seen.add(id(elem))
                ws = self._get_workspace_for_item(item)
                if source_ws is None:
                    source_ws = ws
                elif require_single_workspace and ws is not source_ws:
                    QMessageBox.warning(
                        self,
                        "Mixed Workspace Selection",
                        "Select elements from one workspace at a time for copy/cut.",
                    )
                    return [], None
                elems.append(elem)
            if elems:
                return elems, source_ws

        # Fallback: viewport selection uses active workspace context.
        vp = self._active_viewport()
        elems = list(getattr(vp, 'selected_elements', []) or [])
        if not elems and getattr(vp, 'selected_element', None):
            elems = [vp.selected_element]
        if not elems:
            # If focus/active viewport differs from where the selection was made,
            # fall back to whichever viewport actually has selected elements.
            for other_vp in (self.viewport, self.quad_viewport):
                elems = list(getattr(other_vp, 'selected_elements', []) or [])
                if not elems and getattr(other_vp, 'selected_element', None):
                    elems = [other_vp.selected_element]
                if elems:
                    break
        return elems, self._active_workspace()

    def _select_elements_in_tree(self, elements: list):
        if not elements:
            return
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        first_item = None
        for elem in elements:
            item = self._find_tree_item_for_element(elem)
            if item is None:
                continue
            item.setSelected(True)
            if first_item is None:
                first_item = item
        if first_item is not None:
            self.tree.scrollToItem(first_item)
        self.tree.blockSignals(False)
        self._on_selection_changed()
        self._sync_selection(elements if len(elements) > 1 else elements[0])

    def _on_copy_elements(self):
        if not self.project:
            return
        elements, source_ws = self._selected_elements_and_workspace(require_single_workspace=True)
        if not elements or not source_ws:
            QMessageBox.information(self, "Copy", "Select one or more elements to copy.")
            return
        self._workspace_clipboard = {
            "mode": "copy",
            "source_workspace_id": source_ws.unique_id,
            "element_ids": [e.unique_id for e in elements],
        }
        self.statusbar.showMessage(
            f"Copied {len(elements)} element(s). Switch workspace and press Ctrl+V to paste.",
            4000,
        )

    def _on_cut_elements(self):
        if not self.project:
            return
        elements, source_ws = self._selected_elements_and_workspace(require_single_workspace=True)
        if not elements or not source_ws:
            QMessageBox.information(self, "Cut", "Select one or more elements to cut.")
            return
        self._workspace_clipboard = {
            "mode": "cut",
            "source_workspace_id": source_ws.unique_id,
            "element_ids": [e.unique_id for e in elements],
        }
        self.statusbar.showMessage(
            f"Cut {len(elements)} element(s). Switch workspace and press Ctrl+V to move.",
            4000,
        )

    def _on_paste_elements(self):
        if not self.project:
            return
        if not self._workspace_clipboard:
            QMessageBox.information(self, "Paste", "Clipboard is empty.")
            return

        target_ws = self._active_workspace()
        if not target_ws:
            QMessageBox.warning(self, "Paste", "No active workspace selected.")
            return

        data = self._workspace_clipboard
        source_ws = next((w for w in self.project.workspaces
                          if w.unique_id == data.get("source_workspace_id")), None)
        element_ids = data.get("element_ids", [])
        if not element_ids:
            return

        pasted_elements = []
        if data.get("mode") == "copy":
            for eid in element_ids:
                elem = self.project.find_element(eid)
                if not elem:
                    continue
                pasted_elements.append(duplicate_element(self.project, elem, target_ws))
        else:  # cut: move element links between workspaces
            if source_ws is None:
                QMessageBox.warning(self, "Paste", "Source workspace no longer exists.")
                self._workspace_clipboard = None
                return
            for eid in element_ids:
                elem = self.project.find_element(eid)
                if not elem:
                    continue
                if eid in source_ws.element_ids:
                    source_ws.element_ids.remove(eid)
                if eid not in target_ws.element_ids:
                    target_ws.element_ids.append(eid)
                pasted_elements.append(elem)
            # Consume cut clipboard after move.
            self._workspace_clipboard = None

        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()
        self._select_elements_in_tree(pasted_elements)
        self.statusbar.showMessage(f"Pasted {len(pasted_elements)} element(s) to {target_ws.display_name}", 4000)

    def _cycle_active_workspace(self, delta: int):
        if not self.project or len(self.project.workspaces) < 2:
            return
        old_idx = self.project.active_workspace_index
        new_idx = (old_idx + delta) % len(self.project.workspaces)
        self._set_active_workspace_index(new_idx)
        ws = self.project.workspaces[new_idx]
        self.statusbar.showMessage(f"Active workspace: {ws.display_name}", 2500)

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
        elems_to_delete, _ = self._selected_elements_and_workspace()

        if not elems_to_delete:
            return

        if len(elems_to_delete) > 1:
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Delete {len(elems_to_delete)} elements?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

            cmd = BatchDeleteCommand(self.project, elems_to_delete, f"Delete {len(elems_to_delete)} elements")
            self.undo_stack.push(cmd)
        else:
            elem = elems_to_delete[0]
            ws_refs = []
            for ws in self.project.workspaces:
                if elem.unique_id in ws.element_ids:
                    idx = ws.element_ids.index(elem.unique_id)
                    ws_refs.append((ws, idx))
            cmd = DeleteElementCommand(self.project, elem, ws_refs, f"Delete {elem.unique_id}")
            self.undo_stack.push(cmd)

        # Keep tree + viewport selection in sync after keyboard delete.
        self._sync_selection(None)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

    def _get_workspace_for_item(self, item) -> Optional[Workspace]:
        parent = item.parent()
        if parent:
            pdata = parent.data(0, Qt.ItemDataRole.UserRole)
            if pdata and pdata[0] == ITEM_TYPE_WORKSPACE:
                return pdata[1]
        return None

    # -- Workspace management --

    def _on_add_workspace(self):
        if not self.project:
            return
        name, ok = QInputDialog.getText(self, "Add Workspace", "Workspace name:", text="New Workspace")
        if not ok or not name.strip():
            return
        ws = Workspace(
            unique_id=self.project.generate_id("Workspace"),
            display_name=name.strip(),
            enabled=True,
        )
        cmd = AddWorkspaceCommand(self.project, ws)
        self.undo_stack.push(cmd)
        self._rebuild_tree()

        ws_item = self._workspace_items.get(ws.unique_id)
        if ws_item is not None:
            self.tree.blockSignals(True)
            self.tree.clearSelection()
            ws_item.setSelected(True)
            self.tree.scrollToItem(ws_item)
            self.tree.blockSignals(False)
            self._on_selection_changed()

        self._update_statusbar()

    def _on_delete_workspace(self, ws: Workspace):
        if not self.project:
            return
        if len(self.project.workspaces) <= 1:
            QMessageBox.warning(self, "Cannot Delete", "A project must have at least one workspace.")
            return
        if ws.element_ids:
            reply = QMessageBox.question(
                self, "Confirm Delete",
                f"Workspace '{ws.display_name}' contains {len(ws.element_ids)} element(s).\n"
                "The elements will remain in the project but be unlinked from this workspace.\n"
                "Delete workspace anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        cmd = DeleteWorkspaceCommand(self.project, ws)
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._set_panel(self.empty_panel)
        self._update_statusbar()

    def _on_set_active_workspace(self, ws: Workspace):
        if not self.project or ws not in self.project.workspaces:
            return
        self._set_active_workspace_index(self.project.workspaces.index(ws))

    # -- Viewport callbacks --

    def _on_viewport_select(self, elem):
        """Viewport clicked an element — sync tree selection and open property panel."""
        logging.info(f"_on_viewport_select: {elem.unique_id if elem else 'None'}")
        if elem is None:
            self.tree.clearSelection()
            self._set_panel(self.empty_panel)
            self.performance_panel.set_selected_elements([], self._perf_send)
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
        # Always update performance panel (bypassed by blockSignals on tree)
        if isinstance(elem, (HitZone, MorphZone)):
            self.performance_panel.set_selected_elements([elem], self._perf_send)
            # If performance lock is active, jump straight to Performance Test tab
            if getattr(self, '_performance_lock', False):
                self._props_tabs.setCurrentIndex(1)

    def _on_perf_lock_toggled(self, checked: bool):
        """Toggle Performance Lock mode: disables viewport drag-to-move, auto-selects Performance Test tab."""
        self._performance_lock = checked
        # Apply to all viewports
        for vp in self._all_viewports():
            vp.lock_move = checked
        if checked:
            self._props_tabs.setCurrentIndex(1)  # jump to Performance Test tab
            self.statusbar.showMessage(
                "⚡ Performance Lock ON — click elements to test MIDI/OSC, drag is disabled", 4000
            )
        else:
            self.statusbar.showMessage("Performance Lock OFF — editing enabled", 3000)

    def _all_viewports(self):
        """Return all active SceneViewport instances."""
        viewports = []
        if hasattr(self, 'viewport') and self.viewport:
            viewports.append(self.viewport)
        if hasattr(self, 'quad_viewport') and self.quad_viewport:
            qv = self.quad_viewport
            for attr in ('persp', 'top_view', 'front_view', 'side_view'):
                vp = getattr(qv, attr, None)
                if vp:
                    viewports.append(vp)
        return viewports

    def _on_toggle_debug_mode(self, checked: bool):
        self.debug_mode = bool(checked)
        cfg = _load_config()
        cfg["debug_mode"] = self.debug_mode
        _save_config(cfg)
        self.statusbar.showMessage(
            "Debug mode enabled" if self.debug_mode else "Debug mode disabled",
            2500,
        )
        self._apply_roliblock_debug_visibility()
        self._sync_roliblock_mirror_from_panel()
        if getattr(self, "_play_mode_window", None) is not None:
            self._restart_desktop_play_midi_in()
        # Refresh panel for current selection so mode applies immediately.
        self._on_selection_changed()

    def _on_open_settings(self):
        if self._settings_dialog and not self._settings_dialog.isVisible():
            self._settings_dialog = None
        if self._settings_dialog is None:
            cfg = _load_config()
            self._settings_dialog = SettingsDialog(cfg, parent=self)
            self._settings_dialog.settings_changed.connect(self._on_settings_changed)
        self._settings_dialog.show()
        self._settings_dialog.raise_()

    def _on_settings_changed(self, new_cfg: dict):
        cfg = _load_config()
        cfg.update(new_cfg)
        _save_config(cfg)
        # Apply label font size to viewports
        self._label_font_size = int(new_cfg.get("label_font_size", 10))
        for vp in (self.viewport, self.quad_viewport):
            if hasattr(vp, '_label_font_size'):
                vp._label_font_size = self._label_font_size
        # Apply grid coordinate visibility
        show_coords = bool(new_cfg.get("show_grid_coords", True))
        self.viewport._show_grid_coords = show_coords
        self.quad_viewport._show_grid_coords = show_coords
        self._sync_viewports()

    def _on_open_midi_overview(self):
        if not self.project:
            QMessageBox.information(self, "MIDI Overview", "No project loaded.")
            return
        try:
            if self._midi_overview_dialog and not self._midi_overview_dialog.isVisible():
                self._midi_overview_dialog = None
            if self._midi_overview_dialog is None:
                self._midi_overview_dialog = MidiOverviewDialog(
                    self.project,
                    on_select_element=self._select_element_from_midi_overview,
                    on_element_edited=self._on_midi_overview_element_edited,
                    parent=self,
                )
            self._midi_overview_dialog.show()
            self._midi_overview_dialog.raise_()
        except Exception:
            self._midi_overview_dialog = None
            logging.exception("Failed to open MIDI overview dialog")
            QMessageBox.critical(
                self,
                "MIDI Overview",
                "MIDI overview failed to open. See editor.log for details.",
            )

    def _select_element_from_midi_overview(self, elem):
        """Jump tree/viewport selection to the element chosen in MIDI overview."""
        if not elem:
            return
        item = self._find_tree_item_for_element(elem)
        if not item:
            return
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        item.setSelected(True)
        self.tree.scrollToItem(item)
        self.tree.blockSignals(False)
        self._on_selection_changed()
        self._sync_selection(elem)
        vp = self._active_viewport()
        if hasattr(vp, '_focus_selected'):
            vp._focus_selected()

    def _on_midi_overview_element_edited(self, elem):
        """Refresh editor UI after quick MIDI overview edits."""
        self._rebuild_tree()
        self._sync_viewports()
        self._update_statusbar()
        # Keep details panel fresh if currently showing this element.
        widget = self.scroll.widget()
        if hasattr(widget, '_target') and widget._target is elem and hasattr(widget, 'load'):
            widget.load(elem)

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

    def _on_viewport_elements_scaled(self, elements, old_scales):
        """Batch scale from multi-select resize — single undo step."""
        new_scales = [(e.transform.scale.x, e.transform.scale.y, e.transform.scale.z)
                      for e in elements]
        # Restore old scales, then push command
        for elem, (sx, sy, sz) in zip(elements, old_scales):
            elem.transform.scale = Vec3(sx, sy, sz)
        cmd = BatchScaleCommand(elements, old_scales, new_scales, "Resize elements")
        self.undo_stack.push(cmd)
        self._sync_viewports()

    def _on_viewport_elements_rotated(self, elements, old_quats):
        """Batch rotate from keyboard or mouse drag — single undo step."""
        new_quats = [(e.transform.rotation.x, e.transform.rotation.y,
                      e.transform.rotation.z, e.transform.rotation.w)
                     for e in elements]
        # Restore old rotations, then push command
        for elem, (qx, qy, qz, qw) in zip(elements, old_quats):
            elem.transform.rotation = Quat(x=qx, y=qy, z=qz, w=qw)
        cmd = BatchRotateCommand(elements, old_quats, new_quats, "Rotate elements")
        self.undo_stack.push(cmd)
        self._sync_viewports()

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

    def _on_export_blend_script(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Blender Script", "",
            "Blender Python Script (*.py);;All Files (*)"
        )
        if not path:
            return
        from export_blend import export_blend_script
        try:
            export_blend_script(self.project, path)
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))
            return

        # Show usage tip
        tip = QMessageBox(self)
        tip.setWindowTitle("Blender Script Exported")
        tip.setIcon(QMessageBox.Icon.Information)
        tip.setText(f"<b>Script saved to:</b><br>{path}")
        tip.setInformativeText(
            "<b>How to use in Blender:</b><br>"
            "1. Open Blender → switch to the <b>Scripting</b> workspace (top tab).<br>"
            "2. Click <b>Open</b> in the Text Editor and select the .py file.<br>"
            "3. Click <b>Run Script</b> (▶ or <b>Alt+P</b>).<br><br>"
            "<b>Camera tracking:</b><br>"
            "A <i>VR_Camera</i> is placed at your saved MoveMusic head position "
            "and pointed at the scene centre. Load your footage in the "
            "Movie Clip Editor, run the Motion Tracker, then use "
            "<b>Clip → Setup Tracking Scene</b> to bind the solved camera "
            "to <i>VR_Camera</i> — the MoveMusic boxes serve as real-world "
            "reference geometry.<br><br>"
            "<b>Scale:</b> 1 unit = 1 cm → Blender metres (×0.01 applied automatically)."
        )
        tip.setStandardButtons(QMessageBox.StandardButton.Ok)
        tip.exec()
        self.statusbar.showMessage(f"Blender script exported to {path}", 5000)

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
        self._on_export_orbit_media("gif")

    def _on_export_mp4(self):
        self._on_export_orbit_media("mp4")

    def _on_export_touchosc(self):
        if not self.project:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export TouchOSC Layout Blueprint",
            "touchosc_layout_blueprint.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return

        cfg = _load_config()
        host = str(cfg.get("osc_bridge_host", "127.0.0.1"))
        port = int(cfg.get("osc_bridge_port", 57121))
        namespace = str(cfg.get("osc_bridge_namespace", "/mmc"))

        try:
            from export_touchosc import export_touchosc_layout

            summary = export_touchosc_layout(
                self.project,
                file_path,
                osc_host=host,
                osc_port=port,
                osc_namespace=namespace,
            )
            self.statusbar.showMessage(f"TouchOSC layout blueprint exported to {file_path}", 5000)
            QMessageBox.information(
                self,
                "TouchOSC Export",
                "TouchOSC layout blueprint exported successfully.\n\n"
                f"Pages: {summary['page_count']}\n"
                f"Pads/Notes: {summary['note_controls']}\n"
                f"CC Controls: {summary['cc_controls']}\n"
                f"XY Controls: {summary['morph_controls']}",
            )
        except Exception as exc:
            logging.exception("TouchOSC export failed")
            QMessageBox.critical(self, "Export Error", f"Failed to export TouchOSC layout: {exc}")

    def _on_export_orbit_media(self, initial_format: str = "gif"):
        """Export orbit animation with configurable options (GIF/MP4)."""
        if not self.project:
            return

        opts_dlg = OrbitExportOptionsDialog(default_format=initial_format, parent=self)
        if opts_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = opts_dlg.values()

        fmt = opts["format"]
        if fmt == "mp4":
            title = "Export Orbit MP4"
            file_filter = "MPEG-4 Video (*.mp4);;All Files (*.*)"
            default_ext = ".mp4"
        else:
            title = "Export Orbit GIF"
            file_filter = "Animated GIF (*.gif);;All Files (*.*)"
            default_ext = ".gif"

        path, _ = QFileDialog.getSaveFileName(self, title, "", file_filter)
        if not path:
            return
        if not path.lower().endswith(default_ext):
            path += default_ext

        try:
            from gif_export import export_orbit_gif, export_orbit_mp4, GifExportError

            progress = QProgressDialog(f"Preparing {fmt.upper()} export...", "Cancel", 0, 100, self)
            progress.setWindowTitle(f"Exporting {fmt.upper()}")
            progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)
            progress.show()

            def _on_progress(current: int, total: int, stage: str) -> bool:
                total = max(total, 1)
                pct = int((current / total) * 100)
                progress.setLabelText(f"{stage}... ({current}/{total})")
                progress.setValue(pct)
                QApplication.processEvents()
                return not progress.wasCanceled()

            common = dict(
                duration=opts["duration"],
                fps=opts["fps"],
                size=opts["size"],
                clockwise=opts["clockwise"],
                turns=opts["turns"],
                elevation_factor=opts["elevation_factor"],
                progress_callback=_on_progress,
            )

            if fmt == "mp4":
                success = export_orbit_mp4(self.project, path, self._active_viewport(), **common)
            else:
                success = export_orbit_gif(
                    self.project,
                    path,
                    self._active_viewport(),
                    palette_colors=opts["palette_colors"],
                    dither=opts["dither"],
                    **common,
                )

            progress.setValue(100)
            progress.close()

            if success:
                self.statusbar.showMessage(f"{fmt.upper()} exported to {path}", 5000)
                reply = QMessageBox.question(
                    self,
                    "Export Complete",
                    f"{fmt.upper()} exported successfully!\n\nWould you like to open the file?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    os.startfile(path)
            else:
                QMessageBox.warning(self, "Export Warning", f"{fmt.upper()} export completed with warnings.")

        except GifExportError as e:
            QMessageBox.critical(self, "Export Error", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export {fmt.upper()}: {str(e)}")

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

    def _set_template_orientation(self, label: str):
        if label.startswith("Flat"):
            self._template_orientation = "Flat"
        elif label.startswith("Vertical"):
            self._template_orientation = "Vertical"
        elif label.startswith("Side"):
            self._template_orientation = "Side"
        self.statusbar.showMessage(f"Template placement: {self._template_orientation}", 2500)

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
        elif element_type == "HitZone_NoteHold":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
            elem.behavior = "EHitZoneBehavior::Hold"
            elem.midi_message_type = "EMidiMessageType::Note"
            elem.fixed_midi_velocity_output = 127.0
        elif element_type == "HitZone_NoteToggle":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
            elem.behavior = "EHitZoneBehavior::Toggle"
            elem.midi_message_type = "EMidiMessageType::Note"
            elem.fixed_midi_velocity_output = 127.0
            elem.toggle_state = False
        elif element_type == "HitZone_CCHold":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
            elem.behavior = "EHitZoneBehavior::Hold"
            elem.midi_message_type = "EMidiMessageType::ControlChange"
            elem.midi_cc_mappings = [MidiCCMapping(channel=1, control=1, value=127)]
            elem.fixed_midi_velocity_output = 127.0
        elif element_type == "MorphZone":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
        elif element_type == "MorphZone_X":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
            elem.dimensions = "EDimensions::One"
            elem.is_x_axis_enabled = True
            elem.is_y_axis_enabled = False
            elem.is_z_axis_enabled = False
        elif element_type == "MorphZone_XY":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
            elem.dimensions = "EDimensions::Two"
            elem.is_x_axis_enabled = True
            elem.is_y_axis_enabled = True
            elem.is_z_axis_enabled = False
        elif element_type == "MorphZone_XYZ":
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))
            elem.dimensions = "EDimensions::Three"
            elem.is_x_axis_enabled = True
            elem.is_y_axis_enabled = True
            elem.is_z_axis_enabled = True
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
        """Handle batch deletion from context menu or viewport Delete key."""
        if not self.project or not elements:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {len(elements)} elements?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
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
        if not self.project:
            self._create_new_project()

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

        # Origin near camera target, but anchored to floor (human-scale VR default).
        target = self.viewport.camera.target
        origin = Vec3(target[0], target[1], 0.0)

        generator = TEMPLATES.get(template_name)
        if not generator:
            QMessageBox.warning(self, "Template Missing", f"Template '{template_name}' was not found.")
            return

        # --- Compute next-available MIDI CC and note values to avoid duplicates ---
        used_ccs = set()   # (channel, control) pairs
        used_notes = set()  # (channel, note) pairs
        for elem in self.project.elements:
            for attr in ('x_axis_cc_mappings', 'y_axis_cc_mappings', 'z_axis_cc_mappings', 'midi_cc_mappings'):
                for cc in getattr(elem, attr, None) or []:
                    used_ccs.add((cc.channel, cc.control))
            for note in getattr(elem, 'midi_note_mappings', None) or []:
                used_notes.add((note.channel, note.note))

        def _next_free_cc(channel: int, start: int, count: int) -> int:
            """Find the lowest base so that base..base+count-1 are all free on channel."""
            base = start
            while base + count - 1 <= 127:
                if all((channel, base + i) not in used_ccs for i in range(count)):
                    return base
                base += 1
            # Fallback: wrap around from 1
            base = 1
            while base + count - 1 <= 127:
                if all((channel, base + i) not in used_ccs for i in range(count)):
                    return base
                base += 1
            return start  # give up, use original

        def _next_free_note(channel: int, start: int, count: int, step: int = 1) -> int:
            """Find the lowest base so that base, base+step, ... are all free on channel."""
            base = start
            while base + (count - 1) * step <= 127:
                if all((channel, base + i * step) not in used_notes for i in range(count)):
                    return base
                base += 1
            return start  # give up

        # Patch well-known templates with auto-incremented MIDI values
        from template_generator import (
            generate_faders, generate_knobs, generate_xy_pads,
            generate_drum_pads, generate_buttons, generate_mixer,
            generate_keyboard, generate_debug_everything,
        )
        import re

        def _parse_template(name):
            """Extract (count, type, arrangement) from template name like '8 Faders (Row)'."""
            m = re.match(r'^(\d+)\s+(\w[\w\s]*?)\s*\(([^)]+)\)$', name)
            if m:
                return int(m.group(1)), m.group(2).strip(), m.group(3).strip()
            return None, None, None

        patched = False
        count, ttype, arr = _parse_template(template_name)

        if ttype and "Fader" in ttype:
            base = _next_free_cc(1, 1, count)
            elements = generate_faders(self.project, count, arr, 30, origin, base_cc=base)
            patched = True
        elif ttype and "Knob" in ttype:
            base = _next_free_cc(1, 16, count)
            elements = generate_knobs(self.project, count, arr, 30, origin, base_cc=base)
            patched = True
        elif ttype and "XY Pad" in ttype:
            base_x = _next_free_cc(1, 32, count)
            base_y = _next_free_cc(1, max(base_x + count, 48), count)
            elements = generate_xy_pads(self.project, count, arr, 40, origin, base_cc_x=base_x, base_cc_y=base_y)
            patched = True
        elif ttype and "Drum Pad" in ttype:
            base = _next_free_note(10, 36, count)
            elements = generate_drum_pads(self.project, count, arr, 30, origin, base_note=base, channel=10)
            patched = True
        elif ttype and "Button" in ttype:
            base = _next_free_cc(1, 64, count)
            elements = generate_buttons(self.project, count, arr, 25, origin, base_cc=base)
            patched = True
        elif template_name == "Mixer (8 Faders + 8 Knobs)":
            base_fader = _next_free_cc(1, 1, 8)
            base_knob = _next_free_cc(1, max(base_fader + 8, 9), 8)
            fader_origin = Vec3(origin.x, origin.y, origin.z)
            knob_origin = Vec3(origin.x, origin.y, origin.z + 60)
            elements = generate_faders(self.project, 8, "Row", 30, fader_origin, base_cc=base_fader, label_prefix="Vol")
            elements += generate_knobs(self.project, 8, "Row", 30, knob_origin, base_cc=base_knob, label_prefix="Pan")
            patched = True

        # --- Keyboard templates: auto-increment note ranges ---
        _KB_CONFIGS = {
            "Keyboard 1 Octave": (60, 71),
            "Keyboard 2 Octaves": (48, 71),
            "Keyboard 3 Octaves": (48, 83),
            "Keyboard 5 Octaves": (36, 95),
            "Keyboard Full": (0, 127),
        }
        if not patched and template_name.startswith("Keyboard"):
            # Parse arrangement from name
            if "Triangle" in template_name:
                arr = "Triangle"
            elif "Circle" in template_name:
                arr = "Circle"
            else:
                arr = "Row"

            # Find matching config
            default_base, default_max = 0, 127
            for prefix, (b, m) in _KB_CONFIGS.items():
                if template_name.startswith(prefix):
                    default_base, default_max = b, m
                    break

            note_count = default_max - default_base + 1
            base = _next_free_note(1, default_base, note_count)
            max_note = min(127, base + note_count - 1)
            elements = generate_keyboard(
                self.project, arrangement=arr, spacing=12, origin=origin,
                channel=1, base_note=base, max_note=max_note, label_prefix="Key"
            )
            patched = True

        if not patched:
            try:
                elements = generator(self.project, origin)
            except Exception as e:
                logging.exception("Template generation failed: %s", template_name)
                QMessageBox.critical(self, "Template Error", f"Failed to generate '{template_name}':\n{e}")
                return

        # Apply placement orientation (remap flat XY layout to vertical/side planes)
        if self._template_orientation == "Vertical":
            for elem in elements:
                p = elem.transform.translation
                dy = p.y - origin.y
                p.y = origin.y
                p.z = origin.z + dy
        elif self._template_orientation == "Side":
            for elem in elements:
                p = elem.transform.translation
                dx = p.x - origin.x
                p.x = origin.x
                p.y = origin.y + dx

        # Keep generated content on/above the floor plane by default.
        # Account for element scale extent: z_center - (scale.z / 2) should not go below 0
        if elements:
            min_z = min(
                elem.transform.translation.z - (elem.transform.scale.z / 2)
                for elem in elements
            )
            if min_z < 0.0:
                lift = -min_z
                for elem in elements:
                    elem.transform.translation.z += lift

        if not elements:
            self.statusbar.showMessage(f"Template '{template_name}' produced no elements.", 4000)
            return

        cmd = AddTemplateCommand(self.project, active_ws, elements, f"Add {template_name}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()
        self._sync_viewports()

        # Make the add operation obvious: select new elements and focus them.
        self._sync_selection(elements)
        self.statusbar.showMessage(f"Added template '{template_name}' ({len(elements)} elements)", 4000)
        self._active_viewport()._focus_selected()

    def _on_desktop_play_mode(self):
        self._enter_desktop_play_mode()

    def _enter_desktop_play_mode(self):
        if self._quad_mode:
            self.statusbar.showMessage("Exit Quad View to use Desktop Play Mode.", 5000)
            return
        if not self.project:
            self.statusbar.showMessage("Open a project first.", 3000)
            return
        if self._play_mode_window is not None:
            return
        idx = self.outer_splitter.indexOf(self.viewport)
        if idx < 0:
            self.statusbar.showMessage("Desktop Play: 3D viewport not in layout — restart the editor.", 8000)
            logging.error("Desktop Play: outer_splitter.indexOf(viewport) == %s", idx)
            return
        try:
            self._desktop_play_perf_lock_backup = self._performance_lock
            if not self._performance_lock:
                self.action_perf_lock.setChecked(True)
            else:
                for vp in self._all_viewports():
                    vp.lock_move = True

            self._play_viewport_split_index = idx
            self.viewport.setParent(None)
            self._play_viewport_placeholder = QWidget()
            self.outer_splitter.insertWidget(idx, self._play_viewport_placeholder)

            # Top-level window: child QMainWindow fullscreen often fails on Windows (no visible window).
            self._play_mode_window = PlayModeWindow(None)
            self._play_mode_window.attach_viewport(self.viewport)
            self._play_mode_window.transport_bar.load_from_runtime_dict(
                self.performance_panel.transport_bar.get_config()
            )
            self._play_mode_window.transport_bar.config_changed.connect(self._on_play_transport_changed)
            self._play_mode_window.exiting.connect(self._exit_desktop_play_mode)

            ws = self._active_workspace()
            wname = (ws.display_name or ws.unique_id) if ws else ""
            self._play_mode_window.set_roliblock_visible(self.debug_mode)
            self._play_mode_window.set_hud_text(wname, show_roliblock_tips=self.debug_mode)

            self._play_mode_window.refresh_navigator(
                self.project, self.project.active_workspace_index
            )
            prb = self._play_mode_window.roliblock_strip
            prb.load_from_dict(self.performance_panel.get_roliblock_config(), emit=False)
            prb.set_bind_callback(self._on_roliblock_bind)
            prb.config_changed.connect(self._on_play_roliblock_changed)
            self._play_mode_window.navigator_workspace_changed.connect(self._on_play_nav_workspace)
            self._play_mode_window.navigator_element_clicked.connect(self._on_play_nav_element)

            self.viewport.play_mode = True
            self.viewport.lock_move = True
            self.viewport.update()
            self._play_mode_window.showFullScreen()
            QApplication.processEvents()
            self._play_mode_window.raise_()
            self._play_mode_window.activateWindow()
            self.viewport.setFocus(Qt.FocusReason.OtherFocusReason)
            self.statusbar.showMessage("Desktop Play Mode — Esc to exit", 4000)
            self._start_desktop_play_midi_in()
        except Exception:
            logging.exception("Desktop Play Mode failed to start")
            self.statusbar.showMessage("Desktop Play Mode failed — see editor.log", 8000)
            self._play_mode_window = None
            if self._play_viewport_placeholder is not None:
                ph = self._play_viewport_placeholder
                self._play_viewport_placeholder = None
                ix = self.outer_splitter.indexOf(ph)
                if ix >= 0 and self.viewport.parent() is None:
                    self.outer_splitter.replaceWidget(ix, self.viewport)
            self.viewport.play_mode = False
            QMessageBox.warning(
                self,
                "Desktop Play Mode",
                "Could not start Desktop Play Mode. See editor.log for details.",
            )

    def _exit_desktop_play_mode(self):
        if self._play_mode_window is None:
            return
        self._stop_desktop_play_midi_in()
        pw = self._play_mode_window
        self._play_mode_window = None
        try:
            pw.transport_bar.config_changed.disconnect(self._on_play_transport_changed)
        except Exception:
            pass
        try:
            pw.exiting.disconnect(self._exit_desktop_play_mode)
        except Exception:
            pass
        try:
            pw.roliblock_strip.config_changed.disconnect(self._on_play_roliblock_changed)
        except Exception:
            pass
        try:
            pw.navigator_workspace_changed.disconnect(self._on_play_nav_workspace)
        except Exception:
            pass
        try:
            pw.navigator_element_clicked.disconnect(self._on_play_nav_element)
        except Exception:
            pass
        pw.roliblock_strip.set_bind_callback(None)

        self.viewport.play_mode = False
        backup = getattr(self, "_desktop_play_perf_lock_backup", False)
        self.action_perf_lock.blockSignals(True)
        self.action_perf_lock.setChecked(backup)
        self.action_perf_lock.blockSignals(False)
        self._on_perf_lock_toggled(backup)

        ph = self._play_viewport_placeholder
        if ph is not None:
            ix = self.outer_splitter.indexOf(ph)
            if ix >= 0:
                self.outer_splitter.replaceWidget(ix, self.viewport)
        self._play_viewport_placeholder = None

        self.viewport.show()
        self.viewport.update()
        pw.hide()
        pw.deleteLater()
        self.show()
        self.raise_()
        self.statusbar.showMessage("Exited Desktop Play Mode", 3000)

    def _stop_desktop_play_midi_in(self) -> None:
        threads = self._desktop_play_midi_in_threads
        self._desktop_play_midi_in_threads = []
        for t in threads:
            try:
                t.control_change.disconnect(self._on_desktop_play_midi_in_cc)
            except Exception:
                pass
            try:
                t.pitchwheel.disconnect(self._on_desktop_play_midi_in_pitchwheel)
            except Exception:
                pass
            t.stop_gracefully()
            t.wait(3000)

    def _start_desktop_play_midi_in(self) -> None:
        self._stop_desktop_play_midi_in()
        if not self.debug_mode:
            return
        if self._play_mode_window is None or not self.project:
            return
        d = self.performance_panel.get_roliblock_config()
        bid = d.get("roliblock_bound_id")
        if not bid:
            return
        elem = self.project.find_element(bid)
        if not isinstance(elem, MorphZone):
            return
        outs: List[str] = []
        for key in ("roliblock_pad_a", "roliblock_pad_b"):
            raw = d.get(key)
            if raw is not None and str(raw).strip():
                outs.append(str(raw).strip())
        if not outs:
            return
        seen_in: set = set()
        in_names: List[str] = []
        for out_port in outs:
            in_name = resolve_midi_input_port(out_port)
            if in_name and in_name not in seen_in:
                seen_in.add(in_name)
                in_names.append(in_name)
        if not in_names:
            self.statusbar.showMessage(
                "Desktop Play: no MIDI input matched Roliblock Pad A/B outputs — Block touch will not drive the MorphZone",
                8000,
            )
            logging.warning("Desktop play MIDI in: no input for outputs %r", outs)
            return
        started: List[DesktopPlayMidiInThread] = []
        for in_name in in_names:
            th = DesktopPlayMidiInThread(in_name)
            th.control_change.connect(self._on_desktop_play_midi_in_cc)
            th.pitchwheel.connect(self._on_desktop_play_midi_in_pitchwheel)
            started.append(th)
            th.start()
        self._desktop_play_midi_in_threads = started
        if len(in_names) == 1:
            msg = f"Desktop Play: MIDI in {in_names[0]} (bound MorphZone)"
        else:
            msg = "Desktop Play: MIDI in " + ", ".join(in_names) + " (bound MorphZone)"
        self.statusbar.showMessage(msg, 5000)

    def _restart_desktop_play_midi_in(self) -> None:
        if self._play_mode_window is not None:
            self._start_desktop_play_midi_in()

    def _on_desktop_play_midi_in_cc(self, ch0: int, control: int, value: int) -> None:
        if self._play_mode_window is None or not self.project:
            return
        d = self.performance_panel.get_roliblock_config()
        bid = d.get("roliblock_bound_id")
        if not bid:
            return
        elem = self.project.find_element(bid)
        if not isinstance(elem, MorphZone):
            return
        ch1 = ch0 + 1

        def _match_maps(maps) -> bool:
            for m in maps or []:
                if int(m.channel) == ch1 and int(m.control) == int(control):
                    return True
            return False

        axis = None
        ch_out = ch1
        if elem.is_x_axis_enabled and _match_maps(elem.x_axis_cc_mappings):
            elem.control_position_normalized.x = max(0.0, min(1.0, value / 127.0))
            axis = "X"
        elif elem.is_y_axis_enabled and _match_maps(elem.y_axis_cc_mappings):
            elem.control_position_normalized.y = max(0.0, min(1.0, value / 127.0))
            axis = "Y"
        elif elem.is_z_axis_enabled and _match_maps(elem.z_axis_cc_mappings):
            elem.control_position_normalized.z = max(0.0, min(1.0, value / 127.0))
            axis = "Z"
        if axis is None:
            loose_axes = []
            if elem.is_x_axis_enabled and any(
                int(m.control) == int(control) for m in (elem.x_axis_cc_mappings or [])
            ):
                loose_axes.append("X")
            if elem.is_y_axis_enabled and any(
                int(m.control) == int(control) for m in (elem.y_axis_cc_mappings or [])
            ):
                loose_axes.append("Y")
            if elem.is_z_axis_enabled and any(
                int(m.control) == int(control) for m in (elem.z_axis_cc_mappings or [])
            ):
                loose_axes.append("Z")
            if len(loose_axes) != 1:
                return
            axis = loose_axes[0]
            maps = (
                elem.x_axis_cc_mappings
                if axis == "X"
                else elem.y_axis_cc_mappings
                if axis == "Y"
                else elem.z_axis_cc_mappings
            )
            m0 = next((m for m in (maps or []) if int(m.control) == int(control)), None)
            if m0 is None:
                return
            ch_out = int(m0.channel)
            v = max(0.0, min(1.0, value / 127.0))
            if axis == "X":
                elem.control_position_normalized.x = v
            elif axis == "Y":
                elem.control_position_normalized.y = v
            else:
                elem.control_position_normalized.z = v
        self._modified = True
        self._perf_send(
            elem.unique_id,
            {
                "type": "cc",
                "cc": int(control),
                "channel": ch_out,
                "value": int(value),
                "axis": axis,
            },
        )
        self.viewport.update()

    def _on_desktop_play_midi_in_pitchwheel(self, ch0: int, value: int) -> None:
        """Roli MPE often sends X as per-channel pitch bend instead of CC."""
        if self._play_mode_window is None or not self.project:
            return
        d = self.performance_panel.get_roliblock_config()
        bid = d.get("roliblock_bound_id")
        if not bid:
            return
        elem = self.project.find_element(bid)
        if not isinstance(elem, MorphZone) or not elem.is_x_axis_enabled:
            return
        ch1 = ch0 + 1
        maps = elem.x_axis_cc_mappings or []
        if not maps:
            return
        chans = {int(m.channel) for m in maps}
        use = ch1 in chans
        if not use and chans and min(chans) == 1 and max(chans) == 1 and 2 <= ch1 <= 16:
            use = True
        if not use:
            return
        v = max(0.0, min(1.0, int(value) / 127.0))
        elem.control_position_normalized.x = v
        self._modified = True
        ch_out = min(chans) if chans else ch1
        self._perf_send(
            elem.unique_id,
            {
                "type": "cc",
                "cc": -1,
                "channel": ch_out,
                "value": int(value),
                "axis": "X",
            },
        )
        self.viewport.update()

    def _on_play_transport_changed(self, cfg: dict):
        self.performance_panel.transport_bar.load_from_runtime_dict(cfg)
        self.performance_panel._override_cfg = dict(cfg)

    def _on_play_nav_workspace(self, index: int) -> None:
        if not self.project:
            return
        if 0 <= index < len(self.project.workspaces):
            self._set_active_workspace_index(index)
        pw = getattr(self, "_play_mode_window", None)
        if pw is not None and self.project:
            pw.refresh_navigator(self.project, self.project.active_workspace_index)

    def _on_play_nav_element(self, elem) -> None:
        self.viewport.set_selected(elem)
        self.viewport._emit_selection()

    def _on_play_perf_send(self, element_id: str, payload: dict):
        self._perf_send(element_id, payload)

    def _on_play_morph_interaction(self, elem):
        self._modified = True
        self._update_statusbar()

    def _on_roliblock_bind(self):
        if not self.debug_mode:
            self.statusbar.showMessage(
                "Roliblock is hidden: enable Debug Mode in the View menu to use it.",
                5000,
            )
            return
        elem = None
        if self.viewport.selected_elements:
            cand = self.viewport.selected_elements[-1]
            if isinstance(cand, MorphZone):
                elem = cand
        if elem is None:
            items = self.tree.selectedItems()
            if not items:
                self.statusbar.showMessage("Select a MorphZone in the 3D view or tree.", 3000)
                return
            data = items[0].data(0, Qt.ItemDataRole.UserRole)
            if not data or data[0] != ITEM_TYPE_ELEMENT:
                self.statusbar.showMessage("Select an element in the tree.", 3000)
                return
            elem = data[1]
        if not isinstance(elem, MorphZone):
            self.statusbar.showMessage("Roliblock bind requires a MorphZone.", 3000)
            return
        self.performance_panel.set_bound_morphzone_id(elem.unique_id)
        self.statusbar.showMessage(f"Roliblock bound to {elem.display_name or elem.unique_id}", 3000)

    def _on_roliblock_config_changed(self, d: dict):
        cfg = _load_config()
        for k in (
            "roliblock_enabled",
            "roliblock_pad_a",
            "roliblock_pad_b",
            "roliblock_mode",
            "roliblock_bound_id",
            "roliblock_device_ids",
        ):
            if k in d:
                cfg[k] = d[k]
        _save_config(cfg)
        self._sync_roliblock_mirror_from_panel()
        pw = getattr(self, "_play_mode_window", None)
        if pw is not None:
            pw.roliblock_strip.load_from_dict(d, emit=False)
        self._restart_desktop_play_midi_in()

    def _on_play_roliblock_changed(self, d: dict):
        self.performance_panel.roliblock_strip.load_from_dict(d, emit=False)
        cfg = _load_config()
        for k in (
            "roliblock_enabled",
            "roliblock_pad_a",
            "roliblock_pad_b",
            "roliblock_mode",
            "roliblock_bound_id",
            "roliblock_device_ids",
        ):
            if k in d:
                cfg[k] = d[k]
        _save_config(cfg)
        self._sync_roliblock_mirror_from_panel()
        self._restart_desktop_play_midi_in()

    def _apply_roliblock_debug_visibility(self) -> None:
        self.performance_panel.set_roliblock_visible(self.debug_mode)
        pw = getattr(self, "_play_mode_window", None)
        if pw is not None:
            pw.set_roliblock_visible(self.debug_mode)
            ws = self._active_workspace()
            wname = (ws.display_name or ws.unique_id) if ws else ""
            pw.set_hud_text(wname, show_roliblock_tips=self.debug_mode)

    def _sync_roliblock_mirror_from_panel(self) -> None:
        if not self.debug_mode:
            self._roliblock_mirror.set_config(
                False, None, None, "off", None, None
            )
            return
        d = self.performance_panel.get_roliblock_config()
        mode = d.get("roliblock_mode") or "off"
        if not d.get("roliblock_enabled"):
            mode = "off"
        mm = "xy" if mode == "xy" else ("xyz_split" if mode == "xyz_split" else "off")
        self._roliblock_mirror.set_config(
            bool(d.get("roliblock_enabled", False)),
            d.get("roliblock_pad_a"),
            d.get("roliblock_pad_b"),
            mm,
            d.get("roliblock_bound_id"),
            d.get("roliblock_device_ids"),
        )

    def _perf_send(self, element_id: str, payload: dict):
        """Performance panel callback: send note/CC payload via OSC or MIDI.

        Priority order for transport config:
          1. Performance panel transport bar (user override, always wins)
          2. Saved config file
        Logs each sent message back to the panel's message log.
        """
        # 1. Get config from performance panel transport bar (live override)
        bar_cfg = self.performance_panel.get_transport_config()
        mode = bar_cfg.get("mode", "osc")
        osc_host = bar_cfg.get("osc_host", "127.0.0.1")
        osc_port = int(bar_cfg.get("osc_port", 57121))
        osc_ns = bar_cfg.get("osc_ns", "/mmc")
        midi_port_name = bar_cfg.get("midi_port")  # may be None

        cc_addr = f"{osc_ns}/midi/cc"
        note_addr = f"{osc_ns}/midi/note"

        msg_type = payload.get("type", "cc")
        channel_1 = int(payload.get("channel", 1))
        channel_0 = max(0, channel_1 - 1)  # mido uses 0-based channels

        def _log(transport: str, destination: str, detail: str):
            self.performance_panel.log_sent(msg_type, detail, transport, destination)
            pw = getattr(self, "_play_mode_window", None)
            if pw is not None:
                pw.log_sent(msg_type, detail, transport, destination)
            self.statusbar.showMessage(f"[{transport.upper()}] {detail} → {destination}", 1200)

        if msg_type in ("note_on", "note_off"):
            note = int(payload.get("note", 60))
            velocity = int(payload.get("velocity", 127)) if msg_type == "note_on" else 0
            detail = f"note={note} ch={channel_1} vel={velocity}"
            if mode in ("osc", "both") and SimpleUDPClient is not None:
                try:
                    SimpleUDPClient(osc_host, osc_port).send_message(
                        note_addr, [channel_1, note, velocity]
                    )
                    _log("osc", f"{osc_host}:{osc_port}{note_addr}", detail)
                except Exception as exc:
                    self.statusbar.showMessage(f"OSC error: {exc}", 4000)
            if mode in ("midi", "both") and mido is not None:
                if midi_port_name:
                    try:
                        with mido.open_output(midi_port_name) as port:
                            port.send(mido.Message(
                                msg_type, channel=channel_0, note=note, velocity=velocity
                            ))
                        _log("midi", midi_port_name, detail)
                    except Exception as exc:
                        self.statusbar.showMessage(f"MIDI error: {exc}", 4000)
                else:
                    self.statusbar.showMessage("No MIDI port selected in Performance panel.", 3000)

        elif msg_type == "cc":
            cc = int(payload.get("cc", 1))
            value = int(payload.get("value", 64))
            axis = payload.get("axis", "")
            detail = f"CC{cc}={value} ch={channel_1}{' [' + axis + ']' if axis else ''}"
            if cc >= 0:
                if mode in ("osc", "both") and SimpleUDPClient is not None:
                    try:
                        SimpleUDPClient(osc_host, osc_port).send_message(
                            cc_addr, [channel_1, cc, value]
                        )
                        _log("osc", f"{osc_host}:{osc_port}{cc_addr}", detail)
                    except Exception as exc:
                        self.statusbar.showMessage(f"OSC error: {exc}", 4000)
                if mode in ("midi", "both") and mido is not None:
                    if midi_port_name:
                        try:
                            with mido.open_output(midi_port_name) as port:
                                port.send(mido.Message(
                                    "control_change", channel=channel_0, control=cc, value=value
                                ))
                            _log("midi", midi_port_name, detail)
                        except Exception as exc:
                            self.statusbar.showMessage(f"MIDI error: {exc}", 4000)
                    else:
                        self.statusbar.showMessage("No MIDI port selected in Performance panel.", 3000)
            else:
                detail = f"pitchbend~{value} ch={channel_1}{' [' + axis + ']' if axis else ''} (local morph only)"
                self.performance_panel.log_sent(msg_type, detail, "local", "Desktop MIDI in")
                pw = getattr(self, "_play_mode_window", None)
                if pw is not None:
                    pw.log_sent(msg_type, detail, "local", "Desktop MIDI in")

            elem = self.project.find_element(element_id) if self.project else None
            if elem is not None and hasattr(elem, "dimensions"):
                self._roliblock_mirror.on_cc_perf(
                    element_id,
                    str(payload.get("axis", "")),
                    int(payload.get("value", 0)),
                    getattr(elem, "dimensions", ""),
                    getattr(elem, "is_z_axis_enabled", False),
                    is_x_enabled=getattr(elem, "is_x_axis_enabled", True),
                    is_y_enabled=getattr(elem, "is_y_axis_enabled", True),
                    is_z_axis_enabled=getattr(elem, "is_z_axis_enabled", True),
                )


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QWidget):
    """Persistent settings window (not modal)."""
    settings_changed = pyqtSignal(dict)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("MMC Editor — Settings")
        self.setMinimumWidth(400)
        self._cfg = dict(cfg)

        layout = QVBoxLayout(self)

        # ---- Viewport ----
        vp_group = QGroupBox("Viewport")
        vp_form = QFormLayout(vp_group)

        self._label_font_spin = QSpinBox()
        self._label_font_spin.setRange(6, 24)
        self._label_font_spin.setValue(int(cfg.get("label_font_size", 10)))
        vp_form.addRow("Element label font size:", self._label_font_spin)

        self._grid_check = QCheckBox()
        self._grid_check.setChecked(bool(cfg.get("show_grid_coords", True)))
        vp_form.addRow("Show grid measurements:", self._grid_check)

        layout.addWidget(vp_group)

        # ---- Buttons ----
        btn_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        close_btn = QPushButton("Close")
        apply_btn.clicked.connect(self._on_apply)
        close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(close_btn)
        button_w = QWidget()
        button_w.setLayout(btn_row)
        layout.addWidget(button_w)

    def _on_apply(self):
        self._cfg["label_font_size"] = self._label_font_spin.value()
        self._cfg["show_grid_coords"] = self._grid_check.isChecked()
        self.settings_changed.emit(dict(self._cfg))


# ---------------------------------------------------------------------------
# MIDI overview table dialog
# ---------------------------------------------------------------------------

class MidiOverviewDialog(QWidget):
    """Non-modal window listing all elements with constrained MIDI editors."""

    def __init__(self, project, on_select_element=None, on_element_edited=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("MIDI Overview — All Elements")
        self.setMinimumSize(1280, 560)
        self.project = project
        self._on_select_element = on_select_element
        self._on_element_edited = on_element_edited
        self._updating_table = False
        cfg = _load_config()
        self._osc_host = str(cfg.get("osc_bridge_host", "127.0.0.1"))
        self._osc_port = int(cfg.get("osc_bridge_port", 57121))
        self._test_transport = str(cfg.get("midi_test_transport", "osc"))
        self._osc_namespace = str(cfg.get("osc_bridge_namespace", "/mmc"))
        self._preferred_midi_output = str(cfg.get("preferred_midi_output", "Loopback Midi"))

        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by name, workspace, note, CC, or message type...")
        self._search_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(QLabel("Search:"))
        filter_row.addWidget(self._search_edit)

        filter_row.addWidget(QLabel("Test Transport:"))
        self._transport_combo = QComboBox()
        self._transport_combo.addItem("OSC Bridge", "osc")
        self._transport_combo.addItem("MIDI Output", "midi")
        self._transport_combo.addItem("OSC + MIDI", "both")
        self._set_combo_value(self._transport_combo, self._test_transport)
        self._transport_combo.currentIndexChanged.connect(self._on_transport_changed)
        filter_row.addWidget(self._transport_combo)

        filter_row.addWidget(QLabel("OSC Namespace:"))
        self._osc_ns_edit = QLineEdit(self._osc_namespace)
        self._osc_ns_edit.setMinimumWidth(130)
        self._osc_ns_edit.editingFinished.connect(self._persist_osc_settings)
        filter_row.addWidget(self._osc_ns_edit)

        filter_row.addWidget(QLabel("OSC Host:"))
        self._osc_host_edit = QLineEdit(self._osc_host)
        self._osc_host_edit.setMinimumWidth(120)
        self._osc_host_edit.editingFinished.connect(self._persist_osc_settings)
        filter_row.addWidget(self._osc_host_edit)

        filter_row.addWidget(QLabel("OSC Port:"))
        self._osc_port_spin = QSpinBox()
        self._osc_port_spin.setRange(1, 65535)
        self._osc_port_spin.setValue(self._osc_port)
        self._osc_port_spin.valueChanged.connect(self._persist_osc_settings)
        filter_row.addWidget(self._osc_port_spin)

        filter_row.addWidget(QLabel("MIDI Output:"))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(260)
        self._port_combo.currentIndexChanged.connect(self._on_midi_output_changed)
        filter_row.addWidget(self._port_combo)

        self._refresh_ports_btn = QPushButton("Refresh Ports")
        self._refresh_ports_btn.clicked.connect(self._refresh_midi_ports)
        filter_row.addWidget(self._refresh_ports_btn)

        self._export_btn = QPushButton("Export Mappings")
        self._export_btn.clicked.connect(self._export_mappings_text)
        filter_row.addWidget(self._export_btn)

        self._export_touchosc_btn = QPushButton("Export TouchOSC")
        self._export_touchosc_btn.clicked.connect(self._export_touchosc_layout)
        filter_row.addWidget(self._export_touchosc_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._populate)
        filter_row.addWidget(refresh_btn)

        self._select_btn = QPushButton("Select In Editor")
        self._select_btn.clicked.connect(self._select_current_row)
        filter_row.addWidget(self._select_btn)

        filter_w = QWidget()
        filter_w.setLayout(filter_row)
        layout.addWidget(filter_w)

        hint = QLabel(
            "Each row uses locked dropdowns instead of free-text parsing. "
            "The note and CC editors update the first mapping shown for that element; any additional mappings are preserved. "
            "Test sends either OSC bridge messages (/mmc/midi/cc or /mmc/midi/note) or direct MIDI output. "
            "Use Export TouchOSC to generate a multi-page OSC control layout blueprint from this project."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Element Name", "Type", "Workspace",
            "Note Summary", "CC Summary", "Message Type",
            "Note Mapping", "CC Mapping", "Test MIDI",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemDoubleClicked.connect(lambda _item: self._select_current_row())
        self._table.itemSelectionChanged.connect(self._update_select_button_state)
        layout.addWidget(self._table)

        self._loading_label = QLabel("Loading MIDI mappings...")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setStyleSheet(
            "background-color: rgba(20, 24, 36, 220); "
            "color: #E6EEF7; font-weight: 600; padding: 14px; "
            "border: 1px solid rgba(120, 150, 190, 180); border-radius: 6px;"
        )
        self._loading_label.hide()
        layout.addWidget(self._loading_label)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._port_combo.addItem("Click Refresh Ports", None)
        self._on_transport_changed()
        self._populate()
        self._update_select_button_state()

    def _set_loading(self, active: bool, message: str = "Loading MIDI mappings..."):
        self._loading_label.setText(message)
        self._loading_label.setVisible(active)
        self._table.setVisible(not active)
        QApplication.processEvents()

    def _on_midi_output_changed(self):
        port_name = self._port_combo.currentData() or self._port_combo.currentText()
        if not port_name or port_name in ("Click Refresh Ports", "No MIDI outputs found", "MIDI output discovery failed", "Install mido + python-rtmidi"):
            return
        cfg = _load_config()
        cfg["preferred_midi_output"] = port_name
        _save_config(cfg)

    def _persist_osc_settings(self):
        host = self._osc_host_edit.text().strip() or "127.0.0.1"
        port = int(self._osc_port_spin.value())
        namespace = self._osc_ns_edit.text().strip() or "/mmc"
        if not namespace.startswith("/"):
            namespace = "/" + namespace
        namespace = namespace.rstrip("/") or "/mmc"
        self._osc_ns_edit.setText(namespace)
        cfg = _load_config()
        cfg["osc_bridge_host"] = host
        cfg["osc_bridge_port"] = port
        cfg["osc_bridge_namespace"] = namespace
        _save_config(cfg)

    def _on_transport_changed(self):
        mode = self._transport_combo.currentData() or "osc"
        cfg = _load_config()
        cfg["midi_test_transport"] = mode
        _save_config(cfg)

        using_osc = mode in ("osc", "both")
        using_midi = mode in ("midi", "both")
        self._osc_host_edit.setEnabled(using_osc)
        self._osc_port_spin.setEnabled(using_osc)
        self._osc_ns_edit.setEnabled(using_osc)
        self._port_combo.setEnabled(using_midi)
        self._refresh_ports_btn.setEnabled(using_midi)
        if mode == "osc":
            self._mark_status("OSC bridge mode active. Test sends to configured OSC namespace.")
        elif mode == "midi":
            self._mark_status("MIDI output mode active. Click Refresh Ports to scan outputs.")
        else:
            self._mark_status("OSC + MIDI mode active. Test sends to both transports.")

        if using_midi and self._port_combo.count() <= 1:
            self._refresh_midi_ports()

    def _element_workspace(self, elem) -> str:
        for ws in self.project.workspaces:
            if elem.unique_id in ws.element_ids:
                return ws.display_name
        return "(none)"

    def _note_velocity_label(self, value: float) -> str:
        if value <= 1.0:
            return f"{value:.2f}"
        return str(int(round(value)))

    def _format_notes(self, elem) -> str:
        notes = getattr(elem, 'midi_note_mappings', [])
        if not notes:
            return ""
        return "  ".join(
            f"Ch{mapping.channel} N{mapping.note} ({_midi_note_label(mapping.note)}) V{self._note_velocity_label(mapping.velocity)}"
            for mapping in notes
        )

    def _format_ccs(self, elem) -> str:
        parts = []
        for attr in ('midi_cc_mappings', 'x_axis_cc_mappings', 'y_axis_cc_mappings', 'z_axis_cc_mappings'):
            ccs = getattr(elem, attr, [])
            prefix = {"x_axis_cc_mappings": "X:", "y_axis_cc_mappings": "Y:", "z_axis_cc_mappings": "Z:"}.get(attr, "")
            for mapping in ccs:
                parts.append(f"{prefix}Ch{mapping.channel} CC{mapping.control}={mapping.value}")
        return "  ".join(parts)

    def _make_read_only_item(self, text: str, elem) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, elem)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _set_combo_value(self, combo: QComboBox, value):
        for index in range(combo.count()):
            data = combo.itemData(index)
            if isinstance(data, float) or isinstance(value, float):
                if abs(float(data) - float(value)) < 1e-9:
                    combo.setCurrentIndex(index)
                    return
            elif data == value:
                combo.setCurrentIndex(index)
                return

    def _build_combo(self, options: list, current_value, *, enabled: bool = True, min_width: int = 0) -> QComboBox:
        combo = QComboBox()
        if min_width:
            combo.setMinimumWidth(min_width)
        for label, value in options:
            combo.addItem(label, value)
        self._set_combo_value(combo, current_value)
        combo.setEnabled(enabled)
        return combo

    def _default_note_mapping(self) -> MidiNoteMapping:
        return MidiNoteMapping(channel=1, note=60, velocity=1.0)

    def _default_cc_mapping(self, axis: str = "") -> MidiCCMapping:
        control_defaults = {"": 69, "X": 70, "Y": 71, "Z": 72}
        value_defaults = {"": 127, "X": 0, "Y": 0, "Z": 0}
        return MidiCCMapping(channel=1, control=control_defaults.get(axis, 0), value=value_defaults.get(axis, 0))

    def _find_row_for_element(self, elem) -> int:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) is elem:
                return row
        return -1

    def _refresh_row_summary(self, elem):
        row = self._find_row_for_element(elem)
        if row < 0:
            return
        self._table.item(row, 3).setText(self._format_notes(elem))
        self._table.item(row, 4).setText(self._format_ccs(elem))

    def _mark_status(self, message: str):
        self._status_label.setText(message)

    def _notify_element_edited(self, elem):
        self._refresh_row_summary(elem)
        if callable(self._on_element_edited):
            self._on_element_edited(elem)

    def _morph_axis_options(self, elem) -> list:
        if hasattr(elem, 'x_axis_cc_mappings'):
            return [("X Axis", "X"), ("Y Axis", "Y"), ("Z Axis", "Z")]
        if hasattr(elem, 'midi_cc_mappings'):
            return [("Main", "")]
        return []

    def _get_cc_mapping_list(self, elem, axis: str) -> list:
        attr = {
            "": "midi_cc_mappings",
            "X": "x_axis_cc_mappings",
            "Y": "y_axis_cc_mappings",
            "Z": "z_axis_cc_mappings",
        }.get(axis, "midi_cc_mappings")
        return copy.deepcopy(getattr(elem, attr, []))

    def _set_cc_mapping_list(self, elem, axis: str, mappings: list):
        attr = {
            "": "midi_cc_mappings",
            "X": "x_axis_cc_mappings",
            "Y": "y_axis_cc_mappings",
            "Z": "z_axis_cc_mappings",
        }.get(axis, "midi_cc_mappings")
        setattr(elem, attr, mappings)

    def _first_cc_mapping(self, elem, axis: str) -> MidiCCMapping:
        attr = {
            "": "midi_cc_mappings",
            "X": "x_axis_cc_mappings",
            "Y": "y_axis_cc_mappings",
            "Z": "z_axis_cc_mappings",
        }.get(axis, "midi_cc_mappings")
        mappings = getattr(elem, attr, [])
        if mappings:
            return mappings[0]
        return self._default_cc_mapping(axis)

    def _first_note_mapping(self, elem) -> MidiNoteMapping:
        mappings = getattr(elem, 'midi_note_mappings', [])
        if mappings:
            return mappings[0]
        return self._default_note_mapping()

    def _selected_morph_axis(self, elem) -> str:
        for axis in ("X", "Y", "Z"):
            if self._get_cc_mapping_list(elem, axis):
                return axis
        return "X"

    def _apply_message_type_change(self, elem, combo: QComboBox):
        if self._updating_table or not hasattr(elem, 'midi_message_type'):
            return
        elem.midi_message_type = combo.currentData() or MIDI_MESSAGE_TYPES[0]
        self._notify_element_edited(elem)

    def _apply_note_mapping_change(self, elem, channel_combo: QComboBox, note_combo: QComboBox, velocity_combo: QComboBox):
        if self._updating_table or not hasattr(elem, 'midi_note_mappings'):
            return
        mappings = copy.deepcopy(getattr(elem, 'midi_note_mappings', []))
        if mappings:
            mapping = mappings[0]
        else:
            mapping = self._default_note_mapping()
            mappings = [mapping]
        mapping.channel = int(channel_combo.currentData())
        mapping.note = int(note_combo.currentData())
        mapping.velocity = float(velocity_combo.currentData())
        elem.midi_note_mappings = mappings
        self._notify_element_edited(elem)

    def _load_cc_editor_values(self, elem, axis_combo: QComboBox, channel_combo: QComboBox, control_combo: QComboBox, value_combo: QComboBox):
        axis = axis_combo.currentData() or ""
        mapping = self._first_cc_mapping(elem, axis)
        for combo, value in ((channel_combo, mapping.channel), (control_combo, mapping.control), (value_combo, mapping.value)):
            combo.blockSignals(True)
            self._set_combo_value(combo, value)
            combo.blockSignals(False)

    def _apply_cc_mapping_change(self, elem, axis_combo: QComboBox, channel_combo: QComboBox, control_combo: QComboBox, value_combo: QComboBox):
        if self._updating_table:
            return
        axis = axis_combo.currentData() or ""
        if not self._morph_axis_options(elem):
            return
        mappings = self._get_cc_mapping_list(elem, axis)
        if mappings:
            mapping = mappings[0]
        else:
            mapping = self._default_cc_mapping(axis)
            mappings = [mapping]
        mapping.channel = int(channel_combo.currentData())
        mapping.control = int(control_combo.currentData())
        mapping.value = int(value_combo.currentData())
        self._set_cc_mapping_list(elem, axis, mappings)
        self._notify_element_edited(elem)

    def _note_editor_widget(self, elem) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        if not hasattr(elem, 'midi_note_mappings'):
            label = QLabel("n/a")
            label.setEnabled(False)
            row.addWidget(label)
            return wrapper

        mapping = self._first_note_mapping(elem)
        channel_combo = self._build_combo(MIDI_CHANNEL_OPTIONS, mapping.channel, min_width=72)
        note_combo = self._build_combo(MIDI_NOTE_OPTIONS, mapping.note, min_width=118)
        velocity_combo = self._build_combo(MIDI_VELOCITY_OPTIONS, float(mapping.velocity), min_width=70)
        channel_combo.setToolTip("Note mapping channel")
        note_combo.setToolTip("Note mapping note value")
        velocity_combo.setToolTip("Note mapping velocity")
        for combo in (channel_combo, note_combo, velocity_combo):
            combo.currentIndexChanged.connect(
                lambda _index, e=elem, ch=channel_combo, nt=note_combo, vel=velocity_combo:
                self._apply_note_mapping_change(e, ch, nt, vel)
            )
            row.addWidget(combo)
        if len(getattr(elem, 'midi_note_mappings', [])) > 1:
            wrapper.setToolTip("Editing the first note mapping only. Additional note mappings are preserved.")
        return wrapper

    def _cc_editor_widget(self, elem) -> tuple[QWidget, Optional[QComboBox]]:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        axis_options = self._morph_axis_options(elem)
        if not axis_options:
            label = QLabel("n/a")
            label.setEnabled(False)
            row.addWidget(label)
            return wrapper, None

        current_axis = self._selected_morph_axis(elem) if hasattr(elem, 'x_axis_cc_mappings') else ""
        axis_combo = self._build_combo(axis_options, current_axis, min_width=78)
        axis_combo.setEnabled(len(axis_options) > 1)
        channel_combo = self._build_combo(MIDI_CHANNEL_OPTIONS, 1, min_width=72)
        control_combo = self._build_combo(MIDI_CC_OPTIONS, 0, min_width=64)
        value_combo = self._build_combo(MIDI_CC_OPTIONS, 0, min_width=64)

        axis_combo.currentIndexChanged.connect(
            lambda _index, e=elem, a=axis_combo, ch=channel_combo, ctrl=control_combo, val=value_combo:
            self._load_cc_editor_values(e, a, ch, ctrl, val)
        )
        for combo in (channel_combo, control_combo, value_combo):
            combo.currentIndexChanged.connect(
                lambda _index, e=elem, a=axis_combo, ch=channel_combo, ctrl=control_combo, val=value_combo:
                self._apply_cc_mapping_change(e, a, ch, ctrl, val)
            )

        row.addWidget(axis_combo)
        row.addWidget(channel_combo)
        row.addWidget(control_combo)
        row.addWidget(value_combo)
        self._load_cc_editor_values(elem, axis_combo, channel_combo, control_combo, value_combo)

        axis_counts = [len(self._get_cc_mapping_list(elem, axis_value)) for _, axis_value in axis_options]
        if any(count > 1 for count in axis_counts):
            wrapper.setToolTip("Editing the first CC mapping for the selected group only. Additional CC mappings are preserved.")
        return wrapper, axis_combo

    def _message_type_widget(self, elem) -> QWidget:
        combo = self._build_combo([(item, item) for item in MIDI_MESSAGE_TYPES], getattr(elem, 'midi_message_type', MIDI_MESSAGE_TYPES[0]), min_width=146)
        if not hasattr(elem, 'midi_message_type'):
            combo.clear()
            combo.addItem("n/a", "")
            combo.setEnabled(False)
            return combo
        combo.currentIndexChanged.connect(lambda _index, e=elem, c=combo: self._apply_message_type_change(e, c))
        return combo

    def _velocity_to_midi(self, value: float) -> int:
        if value <= 1.0:
            return max(0, min(127, int(round(value * 127))))
        return max(0, min(127, int(round(value))))

    def _refresh_midi_ports(self):
        current_port = self._port_combo.currentData()
        current_text = self._port_combo.currentText()
        self._port_combo.blockSignals(True)
        self._port_combo.clear()
        self._port_combo.setEnabled(False)
        self._mark_status("Scanning MIDI outputs...")
        QApplication.processEvents()
        if mido is None:
            self._port_combo.addItem("Install mido + python-rtmidi", None)
            self._port_combo.blockSignals(False)
            self._mark_status("MIDI testing is unavailable until mido and python-rtmidi are installed.")
            return
        try:
            port_names = mido.get_output_names()
        except Exception as exc:
            self._port_combo.addItem("MIDI output discovery failed", None)
            self._port_combo.blockSignals(False)
            self._mark_status(f"MIDI output discovery failed: {exc}")
            return
        if not port_names:
            self._port_combo.addItem("No MIDI outputs found", None)
            self._port_combo.blockSignals(False)
            self._mark_status("No MIDI output ports are currently available.")
            return
        for port_name in port_names:
            self._port_combo.addItem(port_name, port_name)

        preferred = _load_config().get("preferred_midi_output", "Loopback Midi")
        selected = None
        if current_port in port_names:
            selected = current_port
        elif current_text in port_names:
            selected = current_text
        elif preferred in port_names:
            selected = preferred
        else:
            loopback = _pick_loopback_midi_port(port_names)
            selected = loopback or port_names[0]
        self._set_combo_value(self._port_combo, selected)

        if (self._transport_combo.currentData() or "osc") in ("midi", "both"):
            self._port_combo.setEnabled(True)
        self._port_combo.blockSignals(False)
        self._on_midi_output_changed()
        self._mark_status(f"Ready to send test MIDI to {self._port_combo.currentText()}.")

    def _osc_addresses(self) -> tuple[str, str]:
        namespace = self._osc_ns_edit.text().strip() or "/mmc"
        if not namespace.startswith("/"):
            namespace = "/" + namespace
        namespace = namespace.rstrip("/")
        return f"{namespace}/cc", f"{namespace}/note"

    def _send_test_osc(self, elem, axis_combo: Optional[QComboBox] = None) -> bool:
        host = self._osc_host_edit.text().strip() or "127.0.0.1"
        port = int(self._osc_port_spin.value())
        if SimpleUDPClient is None:
            return False

        try:
            client = SimpleUDPClient(host, port)
            cc_addr, note_addr = self._osc_addresses()
            if hasattr(elem, 'x_axis_cc_mappings'):
                sent = 0
                for axis_attr in ("x_axis_cc_mappings", "y_axis_cc_mappings", "z_axis_cc_mappings"):
                    for mapping in getattr(elem, axis_attr, [])[:1]:
                        client.send_message(cc_addr, [int(mapping.channel), int(mapping.control), int(mapping.value)])
                        sent += 1
                if sent:
                    self._mark_status(f"OSC -> {cc_addr} sent {sent} MorphZone CC message(s) to {host}:{port}")
                    return True
            if hasattr(elem, 'midi_message_type') and getattr(elem, 'midi_message_type', MIDI_MESSAGE_TYPES[0]) == "EMidiMessageType::CC":
                mapping = self._first_cc_mapping(elem, "")
                client.send_message(cc_addr, [int(mapping.channel), int(mapping.control), int(mapping.value)])
                self._mark_status(
                    f"OSC -> {cc_addr} [{mapping.channel}, {mapping.control}, {mapping.value}] to {host}:{port}"
                )
                return True

            if hasattr(elem, 'midi_note_mappings') and getattr(elem, 'midi_note_mappings', []):
                mapping = self._first_note_mapping(elem)
                velocity = self._velocity_to_midi(float(mapping.velocity))
                client.send_message(note_addr, [int(mapping.channel), int(mapping.note), int(velocity)])
                QTimer.singleShot(
                    250,
                    lambda h=host, p=port, ch=int(mapping.channel), nt=int(mapping.note):
                    SimpleUDPClient(h, p).send_message(note_addr, [ch, nt, 0]),
                )
                self._mark_status(
                    f"OSC -> {note_addr} [{mapping.channel}, {mapping.note}, {velocity}] to {host}:{port}"
                )
                return True

            axis = axis_combo.currentData() if axis_combo is not None else "X"
            mapping = self._first_cc_mapping(elem, axis or "X")
            client.send_message(cc_addr, [int(mapping.channel), int(mapping.control), int(mapping.value)])
            self._mark_status(
                f"OSC -> {cc_addr} [{mapping.channel}, {mapping.control}, {mapping.value}] to {host}:{port}"
            )
            return True
        except Exception as exc:
            logging.exception("OSC test send failed")
            self._mark_status(f"OSC send failed: {exc}")
            return False

    def _send_test_midi_only(self, elem, axis_combo: Optional[QComboBox] = None) -> bool:
        if mido is None:
            return False

        port_name = self._port_combo.currentData() or self._port_combo.currentText()
        if port_name in ("Click Refresh Ports", "No MIDI outputs found", "MIDI output discovery failed", "Install mido + python-rtmidi"):
            port_name = None
        if not port_name:
            self._mark_status("No valid MIDI output selected.")
            return False

        try:
            port = mido.open_output(port_name)
        except Exception as exc:
            logging.exception("MIDI output open failed")
            self._mark_status(f"MIDI output open failed: {exc}")
            return False

        try:
            if hasattr(elem, 'x_axis_cc_mappings'):
                sent = 0
                for axis_attr in ("x_axis_cc_mappings", "y_axis_cc_mappings", "z_axis_cc_mappings"):
                    for mapping in getattr(elem, axis_attr, [])[:1]:
                        message = mido.Message(
                            'control_change',
                            channel=max(0, int(mapping.channel) - 1),
                            control=int(mapping.control),
                            value=int(mapping.value),
                        )
                        port.send(message)
                        sent += 1
                if sent:
                    port.close()
                    self._mark_status(f"Sent {sent} MorphZone CC message(s) to {port_name}.")
                    return True

            if hasattr(elem, 'midi_message_type') and getattr(elem, 'midi_message_type', MIDI_MESSAGE_TYPES[0]) == "EMidiMessageType::CC":
                mapping = self._first_cc_mapping(elem, "")
                message = mido.Message('control_change', channel=max(0, int(mapping.channel) - 1), control=int(mapping.control), value=int(mapping.value))
                port.send(message)
                port.close()
                self._mark_status(f"Sent CC{mapping.control}={mapping.value} on channel {mapping.channel} to {port_name}.")
                return True

            if hasattr(elem, 'midi_note_mappings') and getattr(elem, 'midi_note_mappings', []):
                mapping = self._first_note_mapping(elem)
                note_on = mido.Message(
                    'note_on',
                    channel=max(0, int(mapping.channel) - 1),
                    note=int(mapping.note),
                    velocity=self._velocity_to_midi(float(mapping.velocity)),
                )
                note_off = mido.Message('note_off', channel=max(0, int(mapping.channel) - 1), note=int(mapping.note), velocity=0)
                port.send(note_on)
                QTimer.singleShot(250, lambda p=port, msg=note_off: (p.send(msg), p.close()))
                self._mark_status(f"Sent note {_midi_note_label(mapping.note)} ({mapping.note}) on channel {mapping.channel} to {port_name}.")
                return True

            axis = axis_combo.currentData() if axis_combo is not None else "X"
            mapping = self._first_cc_mapping(elem, axis or "X")
            message = mido.Message('control_change', channel=max(0, int(mapping.channel) - 1), control=int(mapping.control), value=int(mapping.value))
            port.send(message)
            port.close()
            axis_label = axis or "Main"
            self._mark_status(f"Sent {axis_label} CC{mapping.control}={mapping.value} on channel {mapping.channel} to {port_name}.")
            return True
        except Exception as exc:
            port.close()
            logging.exception("MIDI test send failed")
            self._mark_status(f"MIDI send failed: {exc}")
            return False

    def _send_test_midi(self, elem, axis_combo: Optional[QComboBox] = None):
        mode = self._transport_combo.currentData() or "osc"
        osc_ok = False
        midi_ok = False

        if mode in ("osc", "both"):
            osc_ok = self._send_test_osc(elem, axis_combo)

        if mode in ("midi", "both"):
            midi_ok = self._send_test_midi_only(elem, axis_combo)

        if mode == "osc" and not osc_ok:
            QMessageBox.warning(self, "Test MIDI", "OSC test send failed. Check host/port, namespace, and bridge status.")
        elif mode == "midi" and not midi_ok:
            QMessageBox.warning(self, "Test MIDI", "MIDI test send failed. Check output selection and MIDI device state.")
        elif mode == "both" and (not osc_ok or not midi_ok):
            QMessageBox.warning(self, "Test MIDI", "One or more transports failed. Check status text for details.")

    def _perf_send(self, element_id: str, payload: dict):
        """Performance panel callback: sends exact note/CC specified in payload dict.

        Reads transport config from saved config (same source MidiOverviewDialog uses).
        payload keys:
          type: "note_on" | "note_off" | "cc"
          note / cc: int
          channel: int (1-based)
          value: int (0-127, for cc)
          velocity: int (0-127, for notes)
        """
        cfg = _load_config()
        mode = str(cfg.get("midi_test_transport", "osc"))
        osc_host = str(cfg.get("osc_bridge_host", "127.0.0.1"))
        osc_port = int(cfg.get("osc_bridge_port", 57121))
        osc_ns = str(cfg.get("osc_namespace", "/mmc"))
        cc_addr = f"{osc_ns}/midi/cc"
        note_addr = f"{osc_ns}/midi/note"

        # Pull live values from dialog if it's open
        if self._midi_overview_dialog and self._midi_overview_dialog.isVisible():
            dlg = self._midi_overview_dialog
            if hasattr(dlg, '_osc_host_edit'):
                osc_host = dlg._osc_host_edit.text().strip() or osc_host
            if hasattr(dlg, '_osc_port_spin'):
                osc_port = int(dlg._osc_port_spin.value())
            if hasattr(dlg, '_transport_combo'):
                mode = dlg._transport_combo.currentData() or mode

        msg_type = payload.get("type", "cc")
        channel_1 = int(payload.get("channel", 1))
        channel_0 = max(0, channel_1 - 1)  # mido is 0-based

        if msg_type in ("note_on", "note_off"):
            note = int(payload.get("note", 60))
            velocity = int(payload.get("velocity", 127)) if msg_type == "note_on" else 0
            if mode in ("osc", "both") and SimpleUDPClient is not None:
                try:
                    SimpleUDPClient(osc_host, osc_port).send_message(note_addr, [channel_1, note, velocity])
                    self._mark_status(f"Perf OSC -> {note_addr} [{channel_1},{note},{velocity}]")
                except Exception as exc:
                    self._mark_status(f"OSC send error: {exc}")
            if mode in ("midi", "both") and mido is not None:
                port_name = None
                if self._midi_overview_dialog and hasattr(self._midi_overview_dialog, '_port_combo'):
                    port_name = self._midi_overview_dialog._port_combo.currentData()
                if port_name:
                    try:
                        with mido.open_output(port_name) as port:
                            port.send(mido.Message(msg_type, channel=channel_0, note=note, velocity=velocity))
                        self._mark_status(f"Perf MIDI {msg_type} note={note} ch={channel_1}")
                    except Exception as exc:
                        self._mark_status(f"MIDI send error: {exc}")
                else:
                    self._mark_status("No MIDI port selected — open MIDI Overview to configure.")
        elif msg_type == "cc":
            cc = int(payload.get("cc", 1))
            value = int(payload.get("value", 64))
            axis = payload.get("axis", "")
            if mode in ("osc", "both") and SimpleUDPClient is not None:
                try:
                    SimpleUDPClient(osc_host, osc_port).send_message(cc_addr, [channel_1, cc, value])
                    self._mark_status(f"Perf OSC -> {cc_addr} [{channel_1},{cc},{value}] {axis}")
                except Exception as exc:
                    self._mark_status(f"OSC send error: {exc}")
            if mode in ("midi", "both") and mido is not None:
                port_name = None
                if self._midi_overview_dialog and hasattr(self._midi_overview_dialog, '_port_combo'):
                    port_name = self._midi_overview_dialog._port_combo.currentData()
                if port_name:
                    try:
                        with mido.open_output(port_name) as port:
                            port.send(mido.Message("control_change", channel=channel_0, control=cc, value=value))
                        self._mark_status(f"Perf MIDI CC{cc}={value} ch={channel_1} {axis}")
                    except Exception as exc:
                        self._mark_status(f"MIDI send error: {exc}")
                else:
                    self._mark_status("No MIDI port selected — open MIDI Overview to configure.")

    def _insert_row(self, row: int, elem):
        row_data = [
            elem.display_name or elem.unique_id,
            type(elem).__name__,
            self._element_workspace(elem),
            self._format_notes(elem),
            self._format_ccs(elem),
        ]
        for column, value in enumerate(row_data):
            self._table.setItem(row, column, self._make_read_only_item(value, elem))

        msg_widget = self._message_type_widget(elem)
        note_widget = self._note_editor_widget(elem)
        cc_widget, axis_combo = self._cc_editor_widget(elem)
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(lambda _checked=False, e=elem, a=axis_combo: self._send_test_midi(e, a))

        self._table.setCellWidget(row, 5, msg_widget)
        self._table.setCellWidget(row, 6, note_widget)
        self._table.setCellWidget(row, 7, cc_widget)
        self._table.setCellWidget(row, 8, test_btn)

    def _export_mappings_text(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export MIDI/OSC Mappings",
            "midi_osc_mappings.txt",
            "Text Files (*.txt);;All Files (*)",
        )
        if not file_path:
            return

        cc_addr, note_addr = self._osc_addresses()
        lines = [
            "MoveMusic MIDI/OSC Mapping Export",
            "",
            f"OSC Host: {self._osc_host_edit.text().strip() or '127.0.0.1'}",
            f"OSC Port: {int(self._osc_port_spin.value())}",
            f"OSC CC Address: {cc_addr}",
            f"OSC Note Address: {note_addr}",
            "",
        ]

        for elem in self.project.elements:
            lines.append(f"Element: {elem.display_name or elem.unique_id}")
            lines.append(f"Type: {type(elem).__name__}")
            lines.append(f"Workspace: {self._element_workspace(elem)}")
            msg_type = getattr(elem, 'midi_message_type', 'N/A')
            lines.append(f"Message Type: {msg_type}")

            note_mappings = getattr(elem, 'midi_note_mappings', [])
            if note_mappings:
                lines.append("Note Mappings:")
                for mapping in note_mappings:
                    velocity = self._velocity_to_midi(float(mapping.velocity))
                    lines.append(
                        f"  Ch {mapping.channel} Note {mapping.note} Vel {velocity} | OSC: {note_addr} [{mapping.channel}, {mapping.note}, {velocity}]"
                    )

            has_cc = False
            for label, attr in (
                ("Main CC", "midi_cc_mappings"),
                ("X Axis CC", "x_axis_cc_mappings"),
                ("Y Axis CC", "y_axis_cc_mappings"),
                ("Z Axis CC", "z_axis_cc_mappings"),
            ):
                cc_mappings = getattr(elem, attr, [])
                if not cc_mappings:
                    continue
                has_cc = True
                lines.append(f"{label}:")
                for mapping in cc_mappings:
                    lines.append(
                        f"  Ch {mapping.channel} CC {mapping.control} Value {mapping.value} | OSC: {cc_addr} [{mapping.channel}, {mapping.control}, {mapping.value}]"
                    )

            if not note_mappings and not has_cc:
                lines.append("No MIDI mappings")

            lines.append("")

        try:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
            self._mark_status(f"Exported mappings to {file_path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export Mappings", f"Failed to export mappings: {exc}")

    def _export_touchosc_layout(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export TouchOSC Layout Blueprint",
            "touchosc_layout_blueprint.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return

        try:
            from export_touchosc import export_touchosc_layout

            summary = export_touchosc_layout(
                self.project,
                file_path,
                osc_host=self._osc_host_edit.text().strip() or "127.0.0.1",
                osc_port=int(self._osc_port_spin.value()),
                osc_namespace=self._osc_ns_edit.text().strip() or "/mmc",
            )
            self._mark_status(
                f"TouchOSC export complete: {summary['page_count']} page(s), "
                f"{summary['note_controls']} pads, {summary['cc_controls']} CC controls, "
                f"{summary['morph_controls']} XY controls."
            )
            QMessageBox.information(
                self,
                "TouchOSC Export",
                "TouchOSC layout blueprint exported successfully.\n\n"
                f"Pages: {summary['page_count']}\n"
                f"Pads/Notes: {summary['note_controls']}\n"
                f"CC Controls: {summary['cc_controls']}\n"
                f"XY Controls: {summary['morph_controls']}",
            )
        except Exception as exc:
            logging.exception("TouchOSC export failed")
            QMessageBox.warning(self, "TouchOSC Export", f"Failed to export TouchOSC layout: {exc}")

    def _populate(self):
        self._set_loading(True, "Loading MIDI mappings... 0/0")
        self._updating_table = True
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(0)
        elements = list(self.project.elements)
        total = len(elements)
        self._set_loading(True, f"Loading MIDI mappings... 0/{total}")
        self._table.setRowCount(len(elements))
        for row, elem in enumerate(elements):
            self._insert_row(row, elem)
            if (row + 1) % 25 == 0 or (row + 1) == total:
                self._set_loading(True, f"Loading MIDI mappings... {row + 1}/{total}")
        self._table.setUpdatesEnabled(True)
        self._updating_table = False
        self._apply_filter(self._search_edit.text())
        self._set_loading(False)

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        for row in range(self._table.rowCount()):
            if not text:
                self._table.setRowHidden(row, False)
                continue
            row_parts = []
            for col in range(5):
                item = self._table.item(row, col)
                if item:
                    row_parts.append(item.text())
            for col in (5, 6, 7):
                widget = self._table.cellWidget(row, col)
                if widget:
                    row_parts.extend(combo.currentText() for combo in widget.findChildren(QComboBox))
            row_text = " ".join(row_parts).lower()
            self._table.setRowHidden(row, text not in row_text)

    def _current_element(self):
        row = self._table.currentRow()
        if row < 0:
            return None, -1
        item = self._table.item(row, 0)
        if not item:
            return None, row
        return item.data(Qt.ItemDataRole.UserRole), row

    def _select_current_row(self):
        elem, _ = self._current_element()
        if elem is not None and callable(self._on_select_element):
            self._on_select_element(elem)

    def _update_select_button_state(self):
        elem, _ = self._current_element()
        self._select_btn.setEnabled(elem is not None)


class OrbitExportOptionsDialog(QDialog):
    """Dialog for orbit media export settings (GIF/MP4)."""

    def __init__(self, default_format: str = "gif", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Orbit Export Options")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.format_combo = QComboBox()
        self.format_combo.addItem("GIF", "gif")
        self.format_combo.addItem("MP4", "mp4")
        self.format_combo.setCurrentIndex(0 if default_format.lower() == "gif" else 1)
        form.addRow("Format:", self.format_combo)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1.0, 60.0)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(4.0)
        form.addRow("Duration (sec):", self.duration_spin)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 120)
        self.fps_spin.setValue(15 if default_format.lower() == "gif" else 30)
        form.addRow("FPS:", self.fps_spin)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(320, 3840)
        self.width_spin.setSingleStep(16)
        self.width_spin.setValue(800 if default_format.lower() == "gif" else 1280)
        form.addRow("Width:", self.width_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(240, 2160)
        self.height_spin.setSingleStep(16)
        self.height_spin.setValue(600 if default_format.lower() == "gif" else 720)
        form.addRow("Height:", self.height_spin)

        self.clockwise_check = QCheckBox()
        self.clockwise_check.setChecked(True)
        form.addRow("Clockwise orbit:", self.clockwise_check)

        self.turns_spin = QDoubleSpinBox()
        self.turns_spin.setRange(0.1, 8.0)
        self.turns_spin.setDecimals(2)
        self.turns_spin.setSingleStep(0.1)
        self.turns_spin.setValue(1.0)
        form.addRow("Orbit turns:", self.turns_spin)

        self.elevation_spin = QDoubleSpinBox()
        self.elevation_spin.setRange(-1.5, 1.5)
        self.elevation_spin.setDecimals(2)
        self.elevation_spin.setSingleStep(0.05)
        self.elevation_spin.setValue(0.3)
        form.addRow("Elevation factor:", self.elevation_spin)

        self.palette_spin = QSpinBox()
        self.palette_spin.setRange(2, 256)
        self.palette_spin.setValue(256)
        form.addRow("GIF colors:", self.palette_spin)

        self.dither_check = QCheckBox()
        self.dither_check.setChecked(True)
        form.addRow("GIF dithering:", self.dither_check)

        layout.addLayout(form)

        self.tip = QLabel("Tip: GIF colors controls color richness. MP4 preserves full color.")
        self.tip.setWordWrap(True)
        layout.addWidget(self.tip)

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("Export")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._on_format_changed(self.format_combo.currentIndex())

    def _on_format_changed(self, _index: int):
        is_gif = self.format_combo.currentData() == "gif"
        self.palette_spin.setEnabled(is_gif)
        self.dither_check.setEnabled(is_gif)
        if is_gif and self.fps_spin.value() > 30:
            self.fps_spin.setValue(20)
        if (not is_gif) and self.fps_spin.value() < 24:
            self.fps_spin.setValue(30)

    def values(self) -> dict:
        return {
            "format": self.format_combo.currentData(),
            "duration": self.duration_spin.value(),
            "fps": self.fps_spin.value(),
            "size": (self.width_spin.value(), self.height_spin.value()),
            "clockwise": self.clockwise_check.isChecked(),
            "turns": self.turns_spin.value(),
            "elevation_factor": self.elevation_spin.value(),
            "palette_colors": self.palette_spin.value(),
            "dither": self.dither_check.isChecked(),
        }


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
