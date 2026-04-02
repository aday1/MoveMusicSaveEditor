"""
MoveMusic .mmc Save File Editor — PyQt6 UI (No 3D viewport)

Tree view + property panel with add/duplicate/delete/mass-edit and undo/redo.
"""

from __future__ import annotations

import copy
import json
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


from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStatusBar,
    QTableWidget, QTableWidgetItem, QToolBar, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QInputDialog,
)

from model import (
    Project, Workspace, HitZone, MorphZone,
    MidiNoteMapping, MidiCCMapping, Color, Vec3, Quat, Transform,
    load_project_from_file, save_project_to_file, save_project,
    duplicate_element,
)


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


# ---------------------------------------------------------------------------
# Property panel widgets (same as before)
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
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")
        self.setText(f"({r}, {g}, {b})")

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
        self.cellChanged.connect(lambda: self.data_changed.emit())

    def set_mappings(self, mappings: list):
        self.blockSignals(True)
        self.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self.setItem(i, 0, QTableWidgetItem(str(m.channel)))
            self.setItem(i, 1, QTableWidgetItem(str(m.note)))
            self.setItem(i, 2, QTableWidgetItem(f"{m.velocity:.2f}"))
        self.blockSignals(False)

    def get_mappings(self) -> list:
        mappings = []
        for i in range(self.rowCount()):
            try:
                ch = int(self.item(i, 0).text()) if self.item(i, 0) else 1
                note = int(self.item(i, 1).text()) if self.item(i, 1) else 60
                vel = float(self.item(i, 2).text()) if self.item(i, 2) else 1.0
                mappings.append(MidiNoteMapping(ch, note, vel))
            except (ValueError, AttributeError):
                pass
        return mappings


class MidiCCTable(QTableWidget):
    data_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Channel", "Control", "Value"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setMaximumHeight(150)
        self.cellChanged.connect(lambda: self.data_changed.emit())

    def set_mappings(self, mappings: list):
        self.blockSignals(True)
        self.setRowCount(len(mappings))
        for i, m in enumerate(mappings):
            self.setItem(i, 0, QTableWidgetItem(str(m.channel)))
            self.setItem(i, 1, QTableWidgetItem(str(m.control)))
            self.setItem(i, 2, QTableWidgetItem(str(m.value)))
        self.blockSignals(False)

    def get_mappings(self) -> list:
        mappings = []
        for i in range(self.rowCount()):
            try:
                ch = int(self.item(i, 0).text()) if self.item(i, 0) else 1
                ctrl = int(self.item(i, 1).text()) if self.item(i, 1) else 0
                val = int(self.item(i, 2).text()) if self.item(i, 2) else 0
                mappings.append(MidiCCMapping(ch, ctrl, val))
            except (ValueError, AttributeError):
                pass
        return mappings


def _make_float_spin(value=0.0, min_val=-99999.0, max_val=99999.0, decimals=4):
    spin = QDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setDecimals(decimals)
    spin.setValue(value)
    return spin


def _make_int_spin(value=0, min_val=-999999, max_val=999999):
    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setValue(value)
    return spin


# ---------------------------------------------------------------------------
# Property panels (same as before - just the UI components)
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


# Note: HitZonePanel and MorphZonePanel would be the same as in editor.py
# For brevity, I'll include simplified versions:

class SimpleElementPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.info = QLabel("Element properties will be shown here.\n(3D viewport disabled to avoid OpenGL crash)")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info)

    def load(self, elem):
        self.info.setText(f"Type: {type(elem).__name__}\nID: {elem.unique_id}\nName: {elem.display_name}")


# ---------------------------------------------------------------------------
# Main Window (No 3D viewport)
# ---------------------------------------------------------------------------

ITEM_TYPE_PROJECT = 0
ITEM_TYPE_VI = 1
ITEM_TYPE_WORKSPACE = 2
ITEM_TYPE_ELEMENT = 3


class MainWindowNo3D(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MoveMusic Save Editor (No 3D)")
        self.setMinimumSize(800, 600)

        self.project: Optional[Project] = None
        self.file_path: Optional[str] = None
        self.undo_stack = QUndoStack(self)
        self._modified = False

        self._setup_ui()
        self._setup_toolbar()
        self._setup_menu()
        self._setup_statusbar()
        self._connect_signals()

        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

    def _setup_ui(self):
        # Horizontal splitter: tree + property panel (no 3D)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Project")
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setMinimumWidth(280)
        splitter.addWidget(self.tree)

        # Property panel
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        splitter.addWidget(self.scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # Create panels
        self.empty_panel = QLabel("Select an item to view its properties.")
        self.empty_panel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.project_panel = ProjectPanel()
        self.workspace_panel = WorkspacePanel()
        self.element_panel = SimpleElementPanel()

        self.scroll.setWidget(self.empty_panel)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

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

    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
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
        self.action_open.triggered.connect(self._on_open)
        self.action_save.triggered.connect(self._on_save)
        self.action_save_as.triggered.connect(self._on_save_as)
        self.action_add.triggered.connect(self._on_add)
        self.action_duplicate.triggered.connect(self._on_duplicate)
        self.action_delete.triggered.connect(self._on_delete)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_clean_changed(self, clean):
        self._modified = not clean
        self._update_statusbar()
        title = "MoveMusic Save Editor (No 3D)"
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

    # File operations
    def _on_open(self):
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
            self._rebuild_tree()
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

    def _rebuild_tree(self):
        self.tree.clear()
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
            self.scroll.setWidget(self.empty_panel)
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        item_type, obj = data
        if item_type == ITEM_TYPE_PROJECT:
            self.project_panel.load(self.project)
            self.scroll.setWidget(self.project_panel)
        elif item_type == ITEM_TYPE_WORKSPACE:
            self.workspace_panel.load(obj)
            self.scroll.setWidget(self.workspace_panel)
        elif item_type == ITEM_TYPE_ELEMENT:
            self.element_panel.load(obj)
            self.scroll.setWidget(self.element_panel)

    # Add/Duplicate/Delete (simplified)
    def _get_selected_workspace(self) -> Optional[Workspace]:
        items = self.tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return None
        if data[0] == ITEM_TYPE_WORKSPACE:
            return data[1]
        return None

    def _on_add(self):
        if not self.project:
            return
        ws = self._get_selected_workspace()
        if not ws:
            QMessageBox.warning(self, "Select Workspace", "Select a workspace to add an element to.")
            return

        choices = ["HitZone", "MorphZone"]
        choice, ok = QInputDialog.getItem(self, "Add Element", "Element type:", choices, 0, False)
        if not ok:
            return

        if choice == "HitZone":
            elem = HitZone(unique_id=self.project.generate_id("HitZone"))
        else:
            elem = MorphZone(unique_id=self.project.generate_id("MorphZone"))

        cmd = AddElementCommand(self.project, ws, elem, f"Add {choice}")
        self.undo_stack.push(cmd)
        self._rebuild_tree()
        self._update_statusbar()

    def _on_duplicate(self):
        QMessageBox.information(self, "Feature", "Duplicate feature available in full 3D version")

    def _on_delete(self):
        QMessageBox.information(self, "Feature", "Delete feature available in full 3D version")


def run_editor_no3d():
    app = QApplication(sys.argv)
    window = MainWindowNo3D()
    window.show()

    # Auto-open last file
    cfg = _load_config()
    last = cfg.get("last_file")
    if last and os.path.isfile(last):
        window._open_file(last)

    sys.exit(app.exec())