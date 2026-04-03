"""
3D viewport widget for the MoveMusic editor.

Renders HitZones as solid colored boxes and MorphZones as wireframe cubes.
Provides orbit/pan/zoom camera, multi-select (Shift+click, marquee),
clickable HUD toolbar, grid snapping, and overlays.
"""

from __future__ import annotations

import copy
import logging
import math
from typing import Optional, List

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QRect
from PyQt6.QtGui import (
    QMouseEvent, QWheelEvent, QPainter, QFont, QFontMetrics,
    QColor, QPen, QBrush, QPainterPath, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import QMenu, QSplitter, QWidget, QVBoxLayout
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from OpenGL.GL import *
from OpenGL.GLU import *

from model import Project, HitZone, MorphZone, TextLabel, GroupIE, Vec3, Quat


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

class OrbitCamera:
    def __init__(self):
        self.target = [750.0, 220.0, 105.0]
        self.distance = 120.0
        self.yaw = -45.0
        self.pitch = 30.0
        self.fov = 50.0
        self.near = 1.0
        self.far = 10000.0
        self.ortho = False  # True for orthographic (2D) views

    def eye(self) -> tuple:
        yr = math.radians(self.yaw)
        pr = math.radians(self.pitch)
        x = self.target[0] + self.distance * math.cos(pr) * math.cos(yr)
        y = self.target[1] + self.distance * math.cos(pr) * math.sin(yr)
        z = self.target[2] + self.distance * math.sin(pr)
        return (x, y, z)

    def right_vector(self):
        yr = math.radians(self.yaw)
        return (-math.sin(yr), math.cos(yr), 0)

    def up_vector(self):
        yr = math.radians(self.yaw)
        pr = math.radians(self.pitch)
        return (
            -math.cos(yr) * math.sin(pr),
            -math.sin(yr) * math.sin(pr),
            math.cos(pr),
        )

    def forward_vector(self):
        yr = math.radians(self.yaw)
        pr = math.radians(self.pitch)
        return (
            -math.cos(pr) * math.cos(yr),
            -math.cos(pr) * math.sin(yr),
            -math.sin(pr),
        )

    def orbit(self, dx: float, dy: float):
        self.yaw -= dx * 0.3
        self.pitch += dy * 0.3
        self.pitch = max(-89.0, min(89.0, self.pitch))

    def pan(self, dx: float, dy: float):
        speed = self.distance * 0.002
        rx, ry, rz = self.right_vector()
        ux, uy, uz = self.up_vector()
        self.target[0] += (-dx * rx + dy * ux) * speed
        self.target[1] += (-dx * ry + dy * uy) * speed
        self.target[2] += (-dx * rz + dy * uz) * speed

    def zoom(self, delta: float):
        factor = 1.0 - delta * 0.001
        self.distance = max(5.0, min(5000.0, self.distance * factor))

    def fit_to_bounds(self, min_pt: list, max_pt: list):
        cx = (min_pt[0] + max_pt[0]) / 2
        cy = (min_pt[1] + max_pt[1]) / 2
        cz = (min_pt[2] + max_pt[2]) / 2
        self.target = [cx, cy, cz]
        dx = max_pt[0] - min_pt[0]
        dy = max_pt[1] - min_pt[1]
        dz = max_pt[2] - min_pt[2]
        extent = max(dx, dy, dz, 50.0)
        self.distance = extent * 1.5

    def apply(self, width: int, height: int):
        aspect = width / max(height, 1)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        if self.ortho:
            half_h = self.distance * 0.5
            half_w = half_h * aspect
            glOrtho(-half_w, half_w, -half_h, half_h, self.near, self.far)
        else:
            gluPerspective(self.fov, aspect, self.near, self.far)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        ex, ey, ez = self.eye()
        gluLookAt(ex, ey, ez,
                  self.target[0], self.target[1], self.target[2],
                  0, 0, 1)  # Z-up

    def world_to_screen(self, wx, wy, wz, viewport_w, viewport_h):
        """Project a world-space point to screen coordinates using camera math."""
        eye = self.eye()
        dx = wx - eye[0]
        dy = wy - eye[1]
        dz = wz - eye[2]

        rx, ry, rz = self.right_vector()
        ux, uy, uz = self.up_vector()
        fx, fy, fz = self.forward_vector()

        # Camera-space coordinates
        cam_x = dx * rx + dy * ry + dz * rz
        cam_y = dx * ux + dy * uy + dz * uz
        cam_z = dx * fx + dy * fy + dz * fz  # positive = in front of camera

        if cam_z <= 0:
            return None  # Behind camera

        if self.ortho:
            aspect = viewport_w / max(viewport_h, 1)
            half_h = self.distance * 0.5
            half_w = half_h * aspect
            ndc_x = cam_x / half_w if half_w else 0
            ndc_y = cam_y / half_h if half_h else 0
        else:
            aspect = viewport_w / max(viewport_h, 1)
            fov_scale = math.tan(math.radians(self.fov) * 0.5)
            ndc_x = cam_x / (cam_z * fov_scale * aspect)
            ndc_y = cam_y / (cam_z * fov_scale)

        sx = (ndc_x + 1.0) * 0.5 * viewport_w
        sy = (1.0 - ndc_y) * 0.5 * viewport_h

        return (sx, sy, cam_z)


# ---------------------------------------------------------------------------
# Bounding box helpers
# ---------------------------------------------------------------------------

def _get_element_bbox(elem, half_size=15.0) -> tuple:
    p = elem.transform.translation
    s = elem.transform.scale
    if isinstance(elem, MorphZone):
        ext = elem.mesh_extent
        hx, hy, hz = ext.x * s.x * 0.5, ext.y * s.y * 0.5, ext.z * s.z * 0.5
    elif isinstance(elem, TextLabel):
        hx, hy, hz = 10.0 * s.x, 8.0 * s.y, 0.5
    elif isinstance(elem, GroupIE):
        bb = elem.bounding_box
        hx = (bb.max.x - bb.min.x) * s.x * 0.5
        hy = (bb.max.y - bb.min.y) * s.y * 0.5
        hz = (bb.max.z - bb.min.z) * s.z * 0.5
    else:
        hx = half_size * s.x
        hy = half_size * s.y
        hz = 2.5 * s.z
    return (
        (p.x - hx, p.y - hy, p.z - hz),
        (p.x + hx, p.y + hy, p.z + hz),
    )


# ---------------------------------------------------------------------------
# 3D Viewport Widget
# ---------------------------------------------------------------------------

class SceneViewport(QOpenGLWidget):
    # Signals — backward-compatible single-element signals
    element_selected = pyqtSignal(object)
    element_moved = pyqtSignal(object, float, float, float)
    element_scaled = pyqtSignal(object, float, float, float)
    add_element_requested = pyqtSignal(str)
    duplicate_element_requested = pyqtSignal(object)
    delete_element_requested = pyqtSignal(object)
    delete_elements_requested = pyqtSignal(list)  # batch delete
    # New multi-select signals
    selection_changed = pyqtSignal(list)
    elements_moved = pyqtSignal(list, list)   # [elements], [(old_x, old_y, old_z), ...]
    elements_scaled = pyqtSignal(list, list)  # [elements], [(old_sx, old_sy, old_sz), ...]
    elements_rotated = pyqtSignal(list, list) # [elements], [(old_qx, old_qy, old_qz, old_qw), ...]
    midi_mappings_nudged = pyqtSignal(list, str)  # [(obj, attr, old_val, new_val)], description
    auto_layout_requested = pyqtSignal(str)   # "Row" / "Grid" / "Circle"
    maximize_requested = pyqtSignal()
    # Group management signals
    add_to_group_requested = pyqtSignal(list, str)  # [elements], group_id
    remove_from_group_requested = pyqtSignal(list, str)  # [elements], group_id
    create_group_requested = pyqtSignal(list)  # [elements]
    # Quick edit signals
    edit_text_requested = pyqtSignal(object)  # element
    change_color_requested = pyqtSignal(list)  # [elements]
    # Lock/unlock signals
    toggle_lock_requested = pyqtSignal(list)  # [elements]
    # Workspace transfer signals
    move_to_workspace_requested = pyqtSignal(list, str)  # [elements], workspace_id
    status_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = OrbitCamera()
        self.project: Optional[Project] = None
        self.selected_elements: List = []  # ordered list; last = primary
        self.setMinimumHeight(100)

        # View configuration
        self.view_label = "Perspective"
        self.lock_orbit = False

        # Mouse state
        self._last_mouse = QPoint()
        self._dragging_element = False
        self._drag_start_positions = {}  # id(elem) -> (x, y, z)
        self._mouse_moved = False
        self._resizing = False
        self._resize_axis = None

        # Rotation drag
        self._rotating = False
        self._rotate_drag_axis = None
        self._rotation_start_quats = {}  # id(elem) -> (qx, qy, qz, qw)

        # Marquee (box) select
        self._marquee_active = False
        self._marquee_start = QPoint()
        self._marquee_rect = None  # QRect or None

        # Snap grid
        self._snap_grid_enabled = False
        self._snap_grid_size = 5.0

        # UI state
        self._show_shortcuts = True  # Always show shortcuts by default
        self._show_grid_coords = True  # Show grid coordinate numbers
        self._status_message = ""  # Current action feedback
        self._status_timer = 0  # Timer for status message fadeout
        self._grid_coords = []  # Grid coordinate positions for labeling
        self._hover_element = None  # Element currently under cursor

        # Overlay buttons (populated during paintEvent)
        self._overlay_buttons = []  # [(QRectF, label_str)]

        # Cached screen positions for picking & labels
        self._screen_positions = {}  # id(elem) -> (sx, sy, depth, elem)
        self._handle_screen_pos = {}  # axis -> (sx, sy)
        self._rot_handle_screen_pos = {}  # axis -> (sx, sy)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Explicit shortcuts keep Alt+Arrow MIDI nudges working on Windows,
        # where the menu bar can otherwise consume Alt key combos.
        self._shortcut_cc_up = QShortcut(QKeySequence("Alt+Up"), self)
        self._shortcut_cc_up.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_cc_up.activated.connect(lambda: self._on_shortcut_midi_cc(1))

        self._shortcut_cc_down = QShortcut(QKeySequence("Alt+Down"), self)
        self._shortcut_cc_down.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_cc_down.activated.connect(lambda: self._on_shortcut_midi_cc(-1))

        self._shortcut_note_up = QShortcut(QKeySequence("Alt+Shift+Up"), self)
        self._shortcut_note_up.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_note_up.activated.connect(lambda: self._on_shortcut_midi_note(1))

        self._shortcut_note_down = QShortcut(QKeySequence("Alt+Shift+Down"), self)
        self._shortcut_note_down.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_note_down.activated.connect(lambda: self._on_shortcut_midi_note(-1))

    # -- Backward-compat property --

    @property
    def selected_element(self):
        """Returns the primary (last-clicked) element or None."""
        return self.selected_elements[-1] if self.selected_elements else None

    def set_project(self, project: Optional[Project]):
        self.project = project
        if project and project.elements:
            self._fit_all()
        self.update()

    def set_selected(self, elem_or_list):
        """Accept a single element, a list, or None."""
        if isinstance(elem_or_list, list):
            self.selected_elements = list(elem_or_list)
        elif elem_or_list is None:
            self.selected_elements = []
        else:
            self.selected_elements = [elem_or_list]
        self.update()

    def _active_workspace_elements(self) -> List:
        """Return elements visible/editable in the current active workspace."""
        if not self.project:
            return []
        if not self.project.workspaces:
            return list(self.project.elements)

        idx = self.project.active_workspace_index
        if not (0 <= idx < len(self.project.workspaces)):
            return []

        active_ws = self.project.workspaces[idx]
        return [e for e in self.project.elements if e.unique_id in active_ws.element_ids]

    def _fit_all(self):
        if not self.project:
            return
        visible = self._active_workspace_elements()
        if not visible:
            return
        min_pt = [1e30, 1e30, 1e30]
        max_pt = [-1e30, -1e30, -1e30]
        for e in visible:
            p = e.transform.translation
            for i, v in enumerate([p.x, p.y, p.z]):
                min_pt[i] = min(min_pt[i], v - 30)
                max_pt[i] = max(max_pt[i], v + 30)
        self.camera.fit_to_bounds(min_pt, max_pt)
        self.update()

    def _emit_selection(self):
        """Emit both selection signals."""
        self.selection_changed.emit(list(self.selected_elements))
        self.element_selected.emit(self.selected_element)

    def _on_shortcut_midi_cc(self, delta: int):
        """Handle Alt+Up/Down MIDI CC nudge shortcuts."""
        logging.info(f"_on_shortcut_midi_cc fired: delta={delta}, selected={len(self.selected_elements)}")
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._nudge_selected_midi_cc(delta)
        self.update()

    def _on_shortcut_midi_note(self, delta: int):
        """Handle Alt+Shift+Up/Down MIDI note nudge shortcuts."""
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._nudge_selected_midi_note(delta)
        self.update()

    def _midi_summary(self, elem, limit: int = 4) -> list:
        """Return short human-readable MIDI mapping summaries for an element."""
        summary = []
        if hasattr(elem, 'x_axis_cc_mappings') and elem.x_axis_cc_mappings:
            for cc in elem.x_axis_cc_mappings:
                summary.append(f"X CC{cc.control} -> {cc.value}")
        if hasattr(elem, 'y_axis_cc_mappings') and elem.y_axis_cc_mappings:
            for cc in elem.y_axis_cc_mappings:
                summary.append(f"Y CC{cc.control} -> {cc.value}")
        if hasattr(elem, 'z_axis_cc_mappings') and elem.z_axis_cc_mappings:
            for cc in elem.z_axis_cc_mappings:
                summary.append(f"Z CC{cc.control} -> {cc.value}")
        if hasattr(elem, 'midi_cc_mappings') and elem.midi_cc_mappings:
            for cc in elem.midi_cc_mappings:
                summary.append(f"CC{cc.control} -> {cc.value}")
        if hasattr(elem, 'midi_note_mappings') and elem.midi_note_mappings:
            for note in elem.midi_note_mappings:
                summary.append(f"Ch{note.channel} Note{note.note} -> Vel{int(note.velocity)}")
        return summary[:limit]

    def _update_hover_target(self, mx: int, my: int):
        """Track hovered element and show quick MIDI summary on hover change."""
        if not self.project:
            return

        positions = self._compute_screen_positions_now()
        best_elem = None
        best_dist = 80.0
        for _, (sx, sy, _, elem) in positions.items():
            dist = math.sqrt((mx - sx) ** 2 + (my - sy) ** 2)
            threshold = 150.0 if isinstance(elem, GroupIE) else 80.0
            if dist < threshold and dist < best_dist:
                best_dist = dist
                best_elem = elem

        if best_elem is self._hover_element:
            return

        self._hover_element = best_elem
        if best_elem is not None:
            midi = self._midi_summary(best_elem, limit=2)
            if midi:
                self._show_status(f"Hover: {best_elem.display_name or best_elem.unique_id} -> {', '.join(midi)}")

    def _update_gizmo_hover_cursor(self, mx: int, my: int):
        """Change cursor when hovering over gizmo handles."""
        if not self.selected_elements:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        # Check resize handles
        for axis, (hx, hy) in self._handle_screen_pos.items():
            if (mx - hx) ** 2 + (my - hy) ** 2 < 900:  # 30px
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self._hover_gizmo = f"resize_{axis}"
                self.update()
                return

        # Check rotation ring sample points
        for axis, pts in getattr(self, '_rot_ring_screen_points', {}).items():
            for (hx, hy) in pts:
                if (mx - hx) ** 2 + (my - hy) ** 2 < 900:  # 30px
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                    self._hover_gizmo = f"rotate_{axis}"
                    self.update()
                    return

        if getattr(self, '_hover_gizmo', None):
            self._hover_gizmo = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    # -- OpenGL callbacks --

    def initializeGL(self):
        try:
            glClearColor(0.15, 0.15, 0.18, 1.0)
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_LINE_SMOOTH)
            glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
            logging.info("OpenGL initialized")
        except Exception:
            logging.exception("initializeGL failed")

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)

    def paintGL(self):
        try:
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self.camera.apply(self.width(), self.height())

            self._draw_grid()
            self._draw_world_axes()

            if self.project:
                visible_elements = self._active_workspace_elements()
                self._screen_positions.clear()
                w, h = self.width(), self.height()

                sel_set = set(id(e) for e in self.selected_elements)

                # Keep viewport selection scoped to visible workspace content.
                self.selected_elements = [e for e in self.selected_elements if e in visible_elements]

                for elem in visible_elements:
                    is_selected = id(elem) in sel_set
                    self._draw_element(elem, is_selected, True)

                    p = elem.transform.translation
                    sp = self.camera.world_to_screen(p.x, p.y, p.z, w, h)
                    if sp:
                        self._screen_positions[id(elem)] = (sp[0], sp[1], sp[2], elem)

                # Draw gizmo on primary selected element only
                if self.selected_elements:
                    primary = self.selected_elements[-1]
                    if len(self.selected_elements) == 1:
                        self._draw_selection_gizmo(primary)
                    else:
                        # Just draw a simple crosshair at each selected element
                        self._draw_multi_select_indicator()
                        self._draw_selection_gizmo(primary)

        except Exception:
            logging.exception("paintGL failed")

    def _draw_grid(self):
        glLineWidth(1.0)

        # Dynamic grid spacing based on camera distance
        distance = self.camera.distance
        if distance < 50:
            base_step = 5
            coord_interval = 25
        elif distance < 200:
            base_step = 25
            coord_interval = 50
        elif distance < 800:
            base_step = 25
            coord_interval = 100
        else:
            base_step = 50
            coord_interval = 200

        cx = round(self.camera.target[0] / base_step) * base_step
        cy = round(self.camera.target[1] / base_step) * base_step
        half = min(1000, int(distance * 8))  # Dynamic grid extent
        step = base_step

        snap_on = self._snap_grid_enabled
        snap_sz = self._snap_grid_size

        # Store grid coordinate positions for text rendering later
        self._grid_coords = []

        glBegin(GL_LINES)
        for i in range(-half, half + 1, step):
            if snap_on and snap_sz > 0:
                # Enhanced snap grid visual feedback
                if (i % int(snap_sz * 10)) == 0:
                    a = 0.80  # Bright snap lines
                    glColor4f(0.0, 1.0, 0.3, a)
                elif (i % coord_interval) == 0:
                    a = 0.55   # Major grid lines — green
                    glColor4f(0.0, 0.85, 0.25, a)
                    self._grid_coords.append((cx + i, cy + i))
                else:
                    a = 0.18  # Minor grid lines — dark green
                    glColor4f(0.0, 0.55, 0.15, a)
            else:
                is_major = (i % coord_interval) == 0
                if is_major:
                    glColor4f(0.0, 0.85, 0.25, 0.55)   # bright green major
                    self._grid_coords.append((cx + i, cy + i))
                else:
                    glColor4f(0.0, 0.45, 0.12, 0.22)   # dim green minor

            glVertex3f(cx + i, cy - half, 0)
            glVertex3f(cx + i, cy + half, 0)
            glVertex3f(cx - half, cy + i, 0)
            glVertex3f(cx + half, cy + i, 0)
        glEnd()

        # Add grid cell count overlay in corner
        self._grid_cell_count = len([coord for coord in self._grid_coords if abs(coord[0] - cx) <= half and abs(coord[1] - cy) <= half])

    def _draw_world_axes(self):
        glLineWidth(2.0)
        cx = round(self.camera.target[0] / 50) * 50
        cy = round(self.camera.target[1] / 50) * 50
        length = 200

        glBegin(GL_LINES)
        glColor4f(1.0, 0.2, 0.2, 0.6)
        glVertex3f(cx - length, cy, 0)
        glVertex3f(cx + length, cy, 0)
        glColor4f(0.2, 1.0, 0.2, 0.6)
        glVertex3f(cx, cy - length, 0)
        glVertex3f(cx, cy + length, 0)
        glColor4f(0.2, 0.2, 1.0, 0.6)
        glVertex3f(cx, cy, -length)
        glVertex3f(cx, cy, length)
        glEnd()

    def _draw_multi_select_indicator(self):
        """Draw a subtle marker at each selected element's position."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_DEPTH_TEST)
        glLineWidth(2.0)
        sz = 5.0
        for elem in self.selected_elements:
            p = elem.transform.translation
            glColor4f(1.0, 1.0, 0.2, 0.8)
            glBegin(GL_LINES)
            glVertex3f(p.x - sz, p.y, p.z); glVertex3f(p.x + sz, p.y, p.z)
            glVertex3f(p.x, p.y - sz, p.z); glVertex3f(p.x, p.y + sz, p.z)
            glVertex3f(p.x, p.y, p.z - sz); glVertex3f(p.x, p.y, p.z + sz)
            glEnd()
        glPopAttrib()

    def _draw_selection_gizmo(self, elem):
        p = elem.transform.translation
        size = 30.0
        handle_size = 4.0

        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_DEPTH_TEST)
        glLineWidth(4.0)

        glBegin(GL_LINES)
        glColor4f(1.0, 0.2, 0.2, 1.0)
        glVertex3f(p.x, p.y, p.z)
        glVertex3f(p.x + size, p.y, p.z)
        glColor4f(0.2, 1.0, 0.2, 1.0)
        glVertex3f(p.x, p.y, p.z)
        glVertex3f(p.x, p.y + size, p.z)
        glColor4f(0.2, 0.2, 1.0, 1.0)
        glVertex3f(p.x, p.y, p.z)
        glVertex3f(p.x, p.y, p.z + size)
        glEnd()

        # Arrow heads
        glBegin(GL_LINES)
        ah = 5.0
        glColor4f(1.0, 0.2, 0.2, 1.0)
        glVertex3f(p.x + size, p.y, p.z)
        glVertex3f(p.x + size - ah, p.y + ah * 0.5, p.z)
        glVertex3f(p.x + size, p.y, p.z)
        glVertex3f(p.x + size - ah, p.y - ah * 0.5, p.z)
        glColor4f(0.2, 1.0, 0.2, 1.0)
        glVertex3f(p.x, p.y + size, p.z)
        glVertex3f(p.x + ah * 0.5, p.y + size - ah, p.z)
        glVertex3f(p.x, p.y + size, p.z)
        glVertex3f(p.x - ah * 0.5, p.y + size - ah, p.z)
        glColor4f(0.2, 0.2, 1.0, 1.0)
        glVertex3f(p.x, p.y, p.z + size)
        glVertex3f(p.x + ah * 0.5, p.y, p.z + size - ah)
        glVertex3f(p.x, p.y, p.z + size)
        glVertex3f(p.x - ah * 0.5, p.y, p.z + size - ah)
        glEnd()

        # Resize handle cubes
        hs = handle_size
        glColor4f(1.0, 0.3, 0.3, 0.9)
        _draw_solid_box(p.x + size - hs, p.y - hs, p.z - hs,
                        p.x + size + hs, p.y + hs, p.z + hs)
        glColor4f(0.3, 1.0, 0.3, 0.9)
        _draw_solid_box(p.x - hs, p.y + size - hs, p.z - hs,
                        p.x + hs, p.y + size + hs, p.z + hs)
        glColor4f(0.3, 0.3, 1.0, 0.9)
        _draw_solid_box(p.x - hs, p.y - hs, p.z + size - hs,
                        p.x + hs, p.y + hs, p.z + size + hs)

        # Rotation ring handles — single thick ring per axis, highlight on hover
        rot_dist = size * 0.5
        ring_r = 8.0
        ring_segs = 32
        hover = getattr(self, '_hover_gizmo', None)
        for axis, color_normal, color_hover, ring_fn in [
            ('x', (1.0, 0.45, 0.45, 0.95), (1.0, 0.8, 0.8, 1.0),
             lambda i, r=ring_r: (p.x + rot_dist, p.y + r * math.cos(2*math.pi*i/ring_segs), p.z + r * math.sin(2*math.pi*i/ring_segs))),
            ('y', (0.45, 1.0, 0.45, 0.95), (0.8, 1.0, 0.8, 1.0),
             lambda i, r=ring_r: (p.x + r * math.cos(2*math.pi*i/ring_segs), p.y + rot_dist, p.z + r * math.sin(2*math.pi*i/ring_segs))),
            ('z', (0.45, 0.45, 1.0, 0.95), (0.8, 0.8, 1.0, 1.0),
             lambda i, r=ring_r: (p.x + r * math.cos(2*math.pi*i/ring_segs), p.y + r * math.sin(2*math.pi*i/ring_segs), p.z + rot_dist)),
        ]:
            is_hovered = hover == f"rotate_{axis}"
            glLineWidth(8.0 if is_hovered else 6.0)
            glColor4f(*(color_hover if is_hovered else color_normal))
            glBegin(GL_LINE_LOOP)
            for i in range(ring_segs):
                glVertex3f(*ring_fn(i))
            glEnd()

        glPopAttrib()

        # Cache handle screen positions
        w, h = self.width(), self.height()
        self._handle_screen_pos = {}
        for axis, wx, wy, wz in [
            ('x', p.x + size, p.y, p.z),
            ('y', p.x, p.y + size, p.z),
            ('z', p.x, p.y, p.z + size),
        ]:
            sp = self.camera.world_to_screen(wx, wy, wz, w, h)
            if sp:
                self._handle_screen_pos[axis] = (sp[0], sp[1])

        # Cache rotation ring screen points (sample 8 points around each ring)
        self._rot_handle_screen_pos = {}
        self._rot_ring_screen_points = {}  # axis -> list of (sx, sy)
        ring_samples = 8
        ring_world_points = {
            'x': [(p.x + rot_dist, p.y + ring_r * math.cos(2*math.pi*i/ring_samples),
                    p.z + ring_r * math.sin(2*math.pi*i/ring_samples)) for i in range(ring_samples)],
            'y': [(p.x + ring_r * math.cos(2*math.pi*i/ring_samples), p.y + rot_dist,
                    p.z + ring_r * math.sin(2*math.pi*i/ring_samples)) for i in range(ring_samples)],
            'z': [(p.x + ring_r * math.cos(2*math.pi*i/ring_samples),
                    p.y + ring_r * math.sin(2*math.pi*i/ring_samples), p.z + rot_dist) for i in range(ring_samples)],
        }
        for axis, world_pts in ring_world_points.items():
            screen_pts = []
            for wx, wy, wz in world_pts:
                sp = self.camera.world_to_screen(wx, wy, wz, w, h)
                if sp:
                    screen_pts.append((sp[0], sp[1]))
            if screen_pts:
                self._rot_ring_screen_points[axis] = screen_pts
                # Also keep center for backward compat
                csp = self.camera.world_to_screen(*world_pts[0][:3], w, h)
                if csp:
                    self._rot_handle_screen_pos[axis] = (csp[0], csp[1])

    def _draw_element(self, elem, is_selected: bool, in_active_workspace: bool):
        p = elem.transform.translation
        s = elem.transform.scale
        c = elem.color

        alpha = 0.85 if in_active_workspace else 0.3

        glPushMatrix()
        glTranslatef(p.x, p.y, p.z)

        q = elem.transform.rotation
        angle, ax, ay, az = _quat_to_axis_angle(q)
        if abs(angle) > 0.001:
            glRotatef(angle, ax, ay, az)

        if isinstance(elem, HitZone):
            self._draw_hit_zone_box(s, c, alpha, is_selected)
        elif isinstance(elem, MorphZone):
            self._draw_morph_zone(elem, s, c, alpha, is_selected)
        elif isinstance(elem, TextLabel):
            self._draw_text_label(elem, s, c, alpha, is_selected)
        elif isinstance(elem, GroupIE):
            self._draw_group_ie(elem, s, c, alpha, is_selected)

        glPopMatrix()

    def _draw_hit_zone_box(self, s, c, alpha, is_selected):
        hx, hy, hz = max(15.0 * s.x, 3.0), max(15.0 * s.y, 3.0), max(2.5 * s.z, 1.0)

        if is_selected:
            glLineWidth(3.0)
            glColor4f(1.0, 1.0, 0.2, 1.0)
            _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glColor4f(c.r, c.g, c.b, alpha)
        _draw_solid_box(-hx, -hy, -hz, hx, hy, hz)

        glLineWidth(1.5)
        glColor4f(min(c.r + 0.3, 1), min(c.g + 0.3, 1), min(c.b + 0.3, 1), alpha)
        _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

    def _draw_morph_zone(self, mz, s, c, alpha, is_selected):
        ext = mz.mesh_extent
        hx = max(ext.x * s.x * 0.5, 3.0)
        hy = max(ext.y * s.y * 0.5, 3.0)
        hz = max(ext.z * s.z * 0.5, 1.0)

        if is_selected:
            glLineWidth(3.0)
            glColor4f(1.0, 1.0, 0.2, 1.0)
            _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glLineWidth(2.0)
        glColor4f(c.r, c.g, c.b, alpha)
        _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glColor4f(c.r, c.g, c.b, alpha * 0.15)
        _draw_solid_box(-hx, -hy, -hz, hx, hy, hz)

        cp = mz.control_position_normalized
        cpx = -hx + cp.x * 2 * hx
        cpy = -hy + cp.y * 2 * hy
        cpz = -hz + cp.z * 2 * hz
        glColor4f(1.0, 1.0, 1.0, 0.9)
        glPushMatrix()
        glTranslatef(cpx, cpy, cpz)
        _draw_sphere(1.5, 8, 8)
        glPopMatrix()

        glLineWidth(1.0)
        if mz.is_x_axis_enabled:
            glColor4f(1.0, 0.3, 0.3, 0.6)
            glBegin(GL_LINES)
            glVertex3f(-hx, cpy, cpz); glVertex3f(hx, cpy, cpz)
            glEnd()
        if mz.is_y_axis_enabled:
            glColor4f(0.3, 1.0, 0.3, 0.6)
            glBegin(GL_LINES)
            glVertex3f(cpx, -hy, cpz); glVertex3f(cpx, hy, cpz)
            glEnd()
        if mz.is_z_axis_enabled:
            glColor4f(0.3, 0.3, 1.0, 0.6)
            glBegin(GL_LINES)
            glVertex3f(cpx, cpy, -hz); glVertex3f(cpx, cpy, hz)
            glEnd()

    def _draw_text_label(self, tl, s, c, alpha, is_selected):
        hx, hy = max(10.0 * s.x, 3.0), max(8.0 * s.y, 3.0)

        if is_selected:
            glLineWidth(3.0)
            glColor4f(1.0, 1.0, 0.2, 1.0)
            _draw_wire_box(-hx, -hy, -0.5, hx, hy, 0.5)

        glColor4f(c.r, c.g, c.b, alpha * 0.7)
        _draw_solid_box(-hx, -hy, -0.5, hx, hy, 0.5)

        glLineWidth(1.5)
        glColor4f(c.r, c.g, c.b, alpha)
        _draw_wire_box(-hx, -hy, -0.5, hx, hy, 0.5)

        glLineWidth(2.0)
        glColor4f(c.r, c.g, c.b, alpha * 0.9)
        tx = hx * 0.4
        ty = hy * 0.4
        glBegin(GL_LINES)
        glVertex3f(-tx, 0, ty)
        glVertex3f(tx, 0, ty)
        glVertex3f(0, 0, ty)
        glVertex3f(0, 0, -ty)
        glEnd()

    def _draw_group_ie(self, grp, s, c, alpha, is_selected):
        bb = grp.bounding_box
        hx = max((bb.max.x - bb.min.x) * s.x * 0.5, 3.0)
        hy = max((bb.max.y - bb.min.y) * s.y * 0.5, 3.0)
        hz = max((bb.max.z - bb.min.z) * s.z * 0.5, 1.0)

        if is_selected:
            glLineWidth(3.0)
            glColor4f(1.0, 1.0, 0.2, 1.0)
            _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glLineWidth(2.0)
        glColor4f(c.r, c.g, c.b, alpha * 0.7)
        glEnable(GL_LINE_STIPPLE)
        glLineStipple(2, 0xAAAA)
        _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)
        glDisable(GL_LINE_STIPPLE)

        # Don't draw solid box - it creates massive overlays that block interaction
        # glColor4f(c.r, c.g, c.b, alpha * 0.06)
        # _draw_solid_box(-hx, -hy, -hz, hx, hy, hz)

    # -- QPainter overlay (labels + HUD + toolbar + marquee) --

    def paintEvent(self, event):
        super().paintEvent(event)

        # Update status timer
        if self._status_timer > 0:
            self._status_timer -= 1

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_grid_coordinates(painter)  # Grid coordinate numbers
            self._draw_element_labels(painter)
            self._draw_axis_hud(painter)
            self._draw_info_hud(painter)
            self._draw_overlay_toolbar(painter)
            self._draw_shortcuts_overlay(painter)  # Always visible shortcuts
            self._draw_status_message(painter)     # Action feedback
            if self._marquee_rect:
                self._draw_marquee(painter)
        except Exception:
            logging.exception("paintEvent overlay failed")
        finally:
            painter.end()

    def _draw_element_labels(self, painter: QPainter):
        if not self.project:
            return

        sel_set = set(id(e) for e in self.selected_elements)

        for elem_id, (sx, sy, depth, elem) in self._screen_positions.items():
            if sx < 0 or sx > self.width() or sy < 0 or sy > self.height():
                continue

            is_text = isinstance(elem, TextLabel)
            font_size = 13 if is_text else 10
            font = QFont("Segoe UI", font_size, QFont.Weight.Bold if is_text else QFont.Weight.Normal)
            painter.setFont(font)
            fm = QFontMetrics(font)

            label = elem.display_name or elem.unique_id
            if is_text and elem.display_name:
                label = f'T: "{elem.display_name}"'
            is_sel = id(elem) in sel_set
            is_hover = elem is self._hover_element

            tw = fm.horizontalAdvance(label)
            rx = int(sx - tw / 2)
            ry = int(sy - 22)

            if is_sel:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 50, 200))
                painter.drawRoundedRect(rx - 4, ry - fm.ascent() - 2, tw + 8, fm.height() + 4, 4, 4)
                painter.setPen(QColor(0, 0, 0))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(30, 30, 35, 180))
                painter.drawRoundedRect(rx - 4, ry - fm.ascent() - 2, tw + 8, fm.height() + 4, 4, 4)
                painter.setPen(QColor(200, 200, 200))

            painter.drawText(rx, ry, label)

            if is_sel or is_hover:
                badge_font = QFont("Segoe UI", 8)
                painter.setFont(badge_font)
                bfm = QFontMetrics(badge_font)
                midi_lines = self._midi_summary(elem, limit=3)
                if midi_lines:
                    badge_text = " | ".join(midi_lines)
                    badge_w = bfm.horizontalAdvance(badge_text)
                    badge_x = int(sx - badge_w / 2)
                    badge_y = ry + fm.height() + 6
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(20, 24, 30, 210) if is_sel else QColor(32, 40, 48, 190))
                    painter.drawRoundedRect(
                        badge_x - 5, badge_y - bfm.ascent() - 2, badge_w + 10, bfm.height() + 4, 4, 4
                    )
                    painter.setPen(QColor(255, 230, 140) if is_sel else QColor(190, 220, 255))
                    painter.drawText(badge_x, badge_y, badge_text)

    def _draw_grid_coordinates(self, painter: QPainter):
        """Draw grid coordinate numbers at major intersections with real-world measurements."""
        if not self._show_grid_coords or not hasattr(self, '_grid_coords'):
            return

        font = QFont("Consolas", 7)
        painter.setFont(font)

        for world_x, world_y in self._grid_coords:
            screen_pos = self.camera.world_to_screen(world_x, world_y, 0, self.width(), self.height())
            if not screen_pos:
                continue
            sx, sy = screen_pos[0], screen_pos[1]
            if not (20 < sx < self.width() - 100 and 20 < sy < self.height() - 30):
                continue

            # Convert units to real-world (1 unit = 1 cm)
            dist = self.camera.distance
            if dist < 50:
                # Very close: show cm values
                lx = f"X{world_x:.0f}cm"
                ly = f"Y{world_y:.0f}cm"
            elif world_x % 100 == 0 and world_y % 100 == 0:
                # On-metre lines: show both units
                lx = f"X{world_x/100:.1f}m"
                ly = f"Y{world_y/100:.1f}m"
            else:
                lx = f"X{world_x:.0f}"
                ly = f"Y{world_y:.0f}"

            coord_text = f"{lx} {ly}"
            painter.setPen(QColor(0, 210, 80, 200))
            painter.drawText(int(sx + 4), int(sy - 4), coord_text)

        # Grid info in top-right
        if hasattr(self, '_grid_cell_count'):
            distance = self.camera.distance
            step_cm = (
                5 if distance < 50
                else 25 if distance < 200
                else 25 if distance < 800
                else 50
            )
            grid_info = f"Grid {step_cm}cm | Dist {distance:.0f}cm"

            info_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(info_font)
            painter.setPen(QColor(0, 200, 70, 220))

            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(grid_info)
            bg_rect = text_rect.adjusted(-8, -4, 8, 4)
            top_right = self.rect().topRight()
            bg_rect.moveTopRight(QPoint(top_right.x() - 10, top_right.y() + 10))

            painter.fillRect(bg_rect, QColor(0, 20, 5, 200))
            text_r = bg_rect.adjusted(8, 4, -8, -4)
            painter.drawText(text_r.x(), text_r.y() + metrics.ascent(), grid_info)

    def _draw_axis_hud(self, painter: QPainter):
        ox, oy = 50, self.height() - 50
        length = 30

        yr = math.radians(self.camera.yaw)
        pr = math.radians(self.camera.pitch)

        def project_axis(wx, wy, wz):
            sx = -wx * math.sin(yr) + wy * math.cos(yr)
            sy = -(-wx * math.cos(yr) * math.sin(pr) - wy * math.sin(yr) * math.sin(pr) + wz * math.cos(pr))
            return sx * length, sy * length

        axes = [
            ("X", (1, 0, 0), QColor(230, 70, 70)),
            ("Y", (0, 1, 0), QColor(70, 230, 70)),
            ("Z", (0, 0, 1), QColor(70, 70, 230)),
        ]

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)

        for name, (wx, wy, wz), color in axes:
            ex, ey = project_axis(wx, wy, wz)
            pen = QPen(color, 2.5)
            painter.setPen(pen)
            painter.drawLine(int(ox), int(oy), int(ox + ex), int(oy + ey))
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(ox + ex - 5), int(oy + ey - 5), 10, 10)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(int(ox + ex - 4), int(oy + ey + 4), name)

    def _draw_info_hud(self, painter: QPainter):
        # View label in top-right with background pill
        font_label = QFont("Segoe UI", 12, QFont.Weight.Bold)
        painter.setFont(font_label)
        fm_label = QFontMetrics(font_label)
        lw = fm_label.horizontalAdvance(self.view_label)
        lx = self.width() - lw - 16
        ly = 10

        # Axis-colored underline
        if "Top" in self.view_label:
            accent = QColor(70, 70, 230)
        elif "Front" in self.view_label:
            accent = QColor(70, 230, 70)
        elif "Right" in self.view_label or "Side" in self.view_label:
            accent = QColor(230, 70, 70)
        else:
            accent = QColor(200, 200, 200)

        # Background pill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 30, 40, 200))
        painter.drawRoundedRect(lx - 8, ly, lw + 16, fm_label.height() + 8, 6, 6)
        # Accent underline
        painter.setPen(QPen(accent, 2))
        painter.drawLine(lx - 4, ly + fm_label.height() + 6, lx + lw + 8, ly + fm_label.height() + 6)
        # Label text
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(lx, ly + fm_label.ascent() + 4, self.view_label)

        # Info in top-left
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        painter.setPen(QColor(160, 160, 160))

        lines = []
        if len(self.selected_elements) > 1:
            n = len(self.selected_elements)
            cx = sum(e.transform.translation.x for e in self.selected_elements) / n
            cy = sum(e.transform.translation.y for e in self.selected_elements) / n
            cz = sum(e.transform.translation.z for e in self.selected_elements) / n
            lines.append(f"🎯 EDITING: {n} elements")
            lines.append(f"Centroid: X={cx:.1f}  Y={cy:.1f}  Z={cz:.1f}")

            # Show grid position of centroid
            grid_cx = round(cx / 25) * 25
            grid_cy = round(cy / 25) * 25
            grid_cz = round(cz / 25) * 25
            lines.append(f"📐 Grid center: ({grid_cx:.0f},{grid_cy:.0f},{grid_cz:.0f})")

            # Show bounding box in grid units
            min_x = min(e.transform.translation.x for e in self.selected_elements)
            max_x = max(e.transform.translation.x for e in self.selected_elements)
            min_y = min(e.transform.translation.y for e in self.selected_elements)
            max_y = max(e.transform.translation.y for e in self.selected_elements)
            span_x = (max_x - min_x) / 25
            span_y = (max_y - min_y) / 25
            lines.append(f"📦 Span: {span_x:.1f} x {span_y:.1f} grid units")
        elif len(self.selected_elements) == 1:
            e = self.selected_elements[0]
            p = e.transform.translation
            s = e.transform.scale
            lines.append(f"🎯 EDITING: {e.display_name or e.unique_id}")
            lines.append(f"Pos: X={p.x:.1f}  Y={p.y:.1f}  Z={p.z:.1f}")

            # Show grid coordinates and snapped position
            grid_x = round(p.x / 25) * 25  # 25-unit grid
            grid_y = round(p.y / 25) * 25
            grid_z = round(p.z / 25) * 25
            lines.append(f"📐 Grid: ({grid_x:.0f},{grid_y:.0f},{grid_z:.0f})")

            # Show offset from grid
            offset_x = p.x - grid_x
            offset_y = p.y - grid_y
            offset_z = p.z - grid_z
            if abs(offset_x) > 0.1 or abs(offset_y) > 0.1 or abs(offset_z) > 0.1:
                lines.append(f"📏 Offset: X{offset_x:+.1f} Y{offset_y:+.1f} Z{offset_z:+.1f}")

            lines.append(f"Scale: {s.x:.2f} x {s.y:.2f} x {s.z:.2f}")

            # Show physical size in grid units
            phys_x = s.x * 50  # 50 = base cube size
            phys_y = s.y * 50
            phys_z = s.z * 50
            grid_size_x = phys_x / 25  # How many grid squares
            grid_size_y = phys_y / 25
            grid_size_z = phys_z / 25
            lines.append(f"📦 Size: {grid_size_x:.1f} x {grid_size_y:.1f} x {grid_size_z:.1f} grid units")

            # Show MIDI mappings if present
            midi_info = self._midi_summary(e, limit=4)

            if midi_info:
                lines.append(f"🎵 MIDI: {', '.join(midi_info[:4])}")  # Show first 4 mappings
        elif self._hover_element is not None:
            h = self._hover_element
            lines.append(f"👆 Hover: {h.display_name or h.unique_id}")
            hover_midi = self._midi_summary(h, limit=3)
            if hover_midi:
                lines.append(f"🎵 MIDI: {', '.join(hover_midi)}")
        else:
            lines.append("❌ No selection - Click to select")

        lines.append("")
        lines.append("LMB: Select | Shift+LMB: Multi-select | MMB: Pan | RMB: Orbit")
        if self._snap_grid_enabled:
            lines.append(f"📐 Snap grid: ON ({self._snap_grid_size:.0f} units)")
        if self._show_grid_coords:
            lines.append("📍 Grid coordinates: VISIBLE")
        lines.append("Press H for shortcuts • Press N for grid numbers")

        y = 15
        for line in lines:
            painter.drawText(10, y, line)
            y += 15

    def _draw_overlay_toolbar(self, painter: QPainter):
        """Draw clickable toolbar buttons at top-center of viewport."""
        buttons = [
            ("Grid", self._snap_grid_enabled),
            ("Ortho" if not self.camera.ortho else "Persp", self.camera.ortho),
            ("Top", False),
            ("Front", False),
            ("Side", False),
        ]

        btn_w, btn_h = 52, 24
        gap = 4
        total_w = len(buttons) * (btn_w + gap) - gap
        start_x = (self.width() - total_w) / 2
        start_y = 8

        self._overlay_buttons = []
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)

        for i, (label, active) in enumerate(buttons):
            x = start_x + i * (btn_w + gap)
            rect = QRectF(x, start_y, btn_w, btn_h)

            if active:
                bg = QColor(60, 130, 200, 220)
                border = QColor(100, 170, 240)
            else:
                bg = QColor(40, 40, 50, 200)
                border = QColor(80, 80, 90)

            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 4, 4)

            painter.setPen(QColor(240, 240, 240) if active else QColor(180, 180, 190))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

            self._overlay_buttons.append((rect, label))

    def _draw_marquee(self, painter: QPainter):
        """Draw the selection marquee rectangle."""
        r = self._marquee_rect
        painter.setPen(QPen(QColor(100, 180, 255), 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(100, 180, 255, 40))
        painter.drawRect(r)

    def _draw_shortcuts_overlay(self, painter: QPainter):
        """Draw context-sensitive shortcuts overlay based on current selection."""
        # Build context-sensitive shortcuts based on selection
        shortcuts = []

        if len(self.selected_elements) == 0:
            shortcuts = [
                "🔍 NO SELECTION",
                "Click any element to select it",
                "Shift+Click: Add to selection",
                "Ctrl+A: Select all elements",
                "Drag empty space: Marquee select",
                "",
                "📂 TEMPLATES:",
                "Templates menu → Add elements",
                "",
                "📷 CAMERA CONTROLS:",
                "RMB drag: Orbit camera",
                "MMB/Shift+RMB: Pan camera",
                "Mouse wheel: Zoom",
                "1/3/7: Front/Side/Top views",
                "5: Ortho toggle  |  Home: Fit all",
            ]
        elif len(self.selected_elements) == 1:
            elem = self.selected_elements[0]
            elem_type = type(elem).__name__

            shortcuts = [f"🎯 EDITING: {elem.display_name or elem.unique_id}"]
            shortcuts.append(f"Type: {elem_type}")

            # Position info
            p = elem.transform.translation
            shortcuts.append(f"Position: X={p.x:.1f}, Y={p.y:.1f}, Z={p.z:.1f}")

            # Show available MIDI controls
            midi_controls = []
            if hasattr(elem, 'y_axis_cc_mappings') and elem.y_axis_cc_mappings:
                for cc in elem.y_axis_cc_mappings:
                    midi_controls.append(f"Y-axis: CC{cc.control} value={cc.value}")
            if hasattr(elem, 'x_axis_cc_mappings') and elem.x_axis_cc_mappings:
                for cc in elem.x_axis_cc_mappings:
                    midi_controls.append(f"X-axis: CC{cc.control} value={cc.value}")
            if hasattr(elem, 'z_axis_cc_mappings') and elem.z_axis_cc_mappings:
                for cc in elem.z_axis_cc_mappings:
                    midi_controls.append(f"Z-axis: CC{cc.control} value={cc.value}")
            if hasattr(elem, 'midi_cc_mappings') and elem.midi_cc_mappings:
                for cc in elem.midi_cc_mappings:
                    midi_controls.append(f"Button: CC{cc.control} value={cc.value}")
            if hasattr(elem, 'midi_note_mappings') and elem.midi_note_mappings:
                for note in elem.midi_note_mappings:
                    midi_controls.append(f"Drum: Note{note.note} Ch{note.channel}")

            if midi_controls:
                shortcuts.append("")
                shortcuts.extend(["🎵 MIDI MAPPINGS:"] + midi_controls[:4])
                if elem_type == "MorphZone":
                    shortcuts.append("Alt+↑↓: Adjust CC output values ±1")
                elif elem_type == "HitZone" and midi_controls[0].startswith("Drum"):
                    shortcuts.append("Alt+Shift+↑↓: Adjust note values ±1")
                elif elem_type == "HitZone":
                    shortcuts.append("Alt+↑↓: Adjust CC output values ±1")

            shortcuts.extend([
                "",
                "ℹ️ MIDI SHORTCUT RULES:",
                "Alt+↑↓ needs CC mappings on the selected object(s)",
                "If you see 'No MIDI CC mappings', use Alt+Shift+↑↓ for note mappings",
                "CC values clamp at 0..127, so values at limits will not change",
            ])

            shortcuts.extend([
                "",
                "⚡ MOVEMENT (Selected Element):",
                "Drag: Move freely",
                "Ctrl+←→: Nudge X-axis ±1 unit",
                "Ctrl+↑↓: Nudge Y-axis ±1 unit",
                "Ctrl+PgUp/Dn: Nudge Z-axis ±1 unit",
                "",
                "📐 GRID INFO:",
                f"Grid position: ({p.x//25*25:.0f},{p.y//25*25:.0f},{p.z//25*25:.0f})",
                f"Grid offset: {p.x%25:+.1f},{p.y%25:+.1f},{p.z%25:+.1f}",
                "G: Toggle snap to grid",
                "N: Toggle grid coordinate numbers",
                "",
                "🔄 ROTATION:",
                "R/T: Rotate Z-axis ±15°",
                "Shift+R/T: Rotate Y-axis ±15°",
                "Ctrl+R/T: Rotate X-axis ±15°",
                "Drag ring handle: Free rotate",
                "",
                "⚙️ ACTIONS:",
                "Ctrl+D: Duplicate element",
                "Delete: Remove element",
                "F: Focus camera on element",
            ])
        else:
            # Multiple selection
            n = len(self.selected_elements)
            shortcuts = [
                f"🎯 EDITING: {n} Elements",
                "Multiple elements selected",
                "",
                "⚡ BULK MOVEMENT:",
                "Drag: Move all together",
                "Ctrl+←→: Nudge all X-axis ±1",
                "Ctrl+↑↓: Nudge all Y-axis ±1",
                "Ctrl+PgUp/Dn: Nudge all Z-axis ±1",
                "",
                "🔄 BULK ROTATION:",
                "R/T: Rotate all Z-axis ±15°",
                "Shift+R/T: Rotate all Y-axis ±15°",
                "Ctrl+R/T: Rotate all X-axis ±15°",
                "",
                "🎵 BULK MIDI EDITING:",
                "Alt+↑↓: Adjust all CC output values ±1",
                "Alt+Shift+↑↓: Adjust all note values ±1",
                "",
                "⚙️ BULK ACTIONS:",
                "Ctrl+D: Duplicate all selected",
                "Delete: Remove all selected",
                "Ctrl+L: Arrange in row layout",
                "Esc: Deselect all",
            ]

        # Always show toggle option
        shortcuts.extend([
            "",
            "💡 DISPLAY CONTROLS:",
            "Shortcut help is pinned visible",
            "N: Toggle grid coordinate numbers",
            "G: Toggle grid snap",
            "Right-click: Context menu",
        ])

        font_sm = QFont("Segoe UI", 9)
        painter.setFont(font_sm)
        fm_sm = QFontMetrics(font_sm)
        line_h = fm_sm.height() + 3

        # Filter out empty lines for width calculation
        non_empty = [s for s in shortcuts if s.strip()]
        block_w = min(450, max(fm_sm.horizontalAdvance(s) for s in non_empty) + 20)
        block_h = len(shortcuts) * line_h + 20

        # Position on right side, but not at the very edge
        bx = self.width() - block_w - 15
        by = 50  # Below toolbar

        # Semi-transparent dark background
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(bx, by, block_w, block_h, 8, 8)

        # Border
        painter.setPen(QPen(QColor(100, 150, 200), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bx, by, block_w, block_h, 8, 8)

        # Content
        painter.setPen(QColor(220, 220, 230))
        ty = by + line_h

        for s in shortcuts:
            if s.strip():  # Non-empty lines
                if s.startswith('🎯') or s.startswith('🔍'):
                    # Main header - larger and brighter
                    font_header = QFont("Segoe UI", 11, QFont.Weight.Bold)
                    painter.setFont(font_header)
                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(bx + 10, ty, s)
                    painter.setFont(font_sm)  # Reset font
                elif s.startswith('🎵') or s.startswith('⚡') or s.startswith('🔄') or s.startswith('⚙️') or s.startswith('📷') or s.startswith('📂') or s.startswith('💡'):
                    # Section headers
                    painter.setPen(QColor(150, 200, 255))
                    painter.drawText(bx + 10, ty, s)
                elif s.startswith('Type:') or s.startswith('Position:') or s.startswith('Y-axis:') or s.startswith('X-axis:') or s.startswith('Z-axis:') or s.startswith('Button:') or s.startswith('Drum:'):
                    # Data lines
                    painter.setPen(QColor(180, 255, 180))
                    painter.drawText(bx + 10, ty, s)
                else:
                    # Regular shortcuts
                    painter.setPen(QColor(220, 220, 230))
                    painter.drawText(bx + 10, ty, s)
            ty += line_h

    def _draw_status_message(self, painter: QPainter):
        """Draw temporary status message in center of screen."""
        if not self._status_message or self._status_timer <= 0:
            return

        # Fade out effect
        alpha = min(255, self._status_timer * 2)

        font = QFont("Segoe UI", 14, QFont.Weight.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        text_w = fm.horizontalAdvance(self._status_message)
        text_h = fm.height()

        # Center on screen
        x = (self.width() - text_w) // 2
        y = (self.height() // 2) + 100  # Below center

        # Background
        padding = 15
        bg_rect = QRectF(x - padding, y - text_h - padding//2, text_w + padding*2, text_h + padding)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 40, 50, alpha))
        painter.drawRoundedRect(bg_rect, 8, 8)

        # Border
        painter.setPen(QPen(QColor(100, 180, 255, alpha), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bg_rect, 8, 8)

        # Text
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(x, y, self._status_message)

    # -- Mouse interaction --

    def _check_overlay_click(self, mx, my) -> bool:
        """Check if click is on an overlay button. Returns True if handled."""
        for rect, label in self._overlay_buttons:
            if rect.contains(float(mx), float(my)):
                if label == "Grid":
                    self._snap_grid_enabled = not self._snap_grid_enabled
                elif label in ("Ortho", "Persp"):
                    self.camera.ortho = not self.camera.ortho
                elif label == "Top":
                    self.camera.yaw, self.camera.pitch = 0.0, 89.0
                    self.camera.ortho = True
                elif label == "Front":
                    self.camera.yaw, self.camera.pitch = 0.0, 0.0
                    self.camera.ortho = True
                elif label == "Side":
                    self.camera.yaw, self.camera.pitch = 90.0, 0.0
                    self.camera.ortho = True
                self.update()
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent):
        self._last_mouse = event.pos()
        self._mouse_moved = False

        if event.button() == Qt.MouseButton.LeftButton:
            try:
                mx, my = event.pos().x(), event.pos().y()

                # Check overlay buttons first
                if self._check_overlay_click(mx, my):
                    return

                # Check resize handles (works for single and multi-selection)
                if self.selected_elements and hasattr(self, '_handle_screen_pos'):
                    for axis, (hx, hy) in self._handle_screen_pos.items():
                        if math.sqrt((mx - hx) ** 2 + (my - hy) ** 2) < 30:
                            self._resizing = True
                            self._resize_axis = axis
                            self._drag_start_positions = {}
                            for elem in self.selected_elements:
                                s = elem.transform.scale
                                self._drag_start_positions[id(elem)] = (s.x, s.y, s.z)
                            return

                # Check rotation handles — test against ring sample points
                if self.selected_elements and hasattr(self, '_rot_ring_screen_points'):
                    for axis, pts in self._rot_ring_screen_points.items():
                        for (hx, hy) in pts:
                            if (mx - hx) ** 2 + (my - hy) ** 2 < 900:  # 30px radius
                                self._rotating = True
                                self._rotate_drag_axis = axis
                                self._rotation_start_quats = {}
                                for elem in self.selected_elements:
                                    q = elem.transform.rotation
                                    self._rotation_start_quats[id(elem)] = (q.x, q.y, q.z, q.w)
                                return

                self._handle_pick(mx, my, event.modifiers())
            except Exception:
                logging.exception("mousePressEvent pick failed")

    def mouseMoveEvent(self, event: QMouseEvent):
        dx = event.pos().x() - self._last_mouse.x()
        dy = event.pos().y() - self._last_mouse.y()
        self._mouse_moved = True

        if event.buttons() & Qt.MouseButton.RightButton:
            if not self.lock_orbit:
                self.camera.orbit(dx, dy)
            else:
                self.camera.pan(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.MiddleButton:
            self.camera.pan(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton and self._marquee_active:
            # Update marquee rectangle
            self._marquee_rect = QRect(self._marquee_start, event.pos()).normalized()
            self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton and self._resizing and self.selected_elements:
            self._handle_resize_drag(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton and self._rotating and self.selected_elements:
            self._handle_rotate_drag(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton and self._dragging_element and self.selected_elements:
            self._handle_drag(dx, dy, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self.update()
        elif event.buttons() == Qt.MouseButton.NoButton:
            self._update_hover_target(event.pos().x(), event.pos().y())
            self._update_gizmo_hover_cursor(event.pos().x(), event.pos().y())
            self.update()

        self._last_mouse = event.pos()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._marquee_active:
            # Finish marquee select
            if self._marquee_rect and self._mouse_moved:
                self._finish_marquee_select(event.modifiers())
            elif not self._mouse_moved:
                # Click on empty space — deselect
                self.selected_elements = []
                self._emit_selection()
            self._marquee_active = False
            self._marquee_rect = None
            self.update()
        elif event.button() == Qt.MouseButton.LeftButton and self._resizing:
            if self.selected_elements and self._drag_start_positions and self._mouse_moved:
                elems = []
                old_scales = []
                for elem in self.selected_elements:
                    old = self._drag_start_positions.get(id(elem))
                    if old:
                        elems.append(elem)
                        old_scales.append(old)
                if elems:
                    self.elements_scaled.emit(elems, old_scales)
            self._resizing = False
            self._resize_axis = None
            self._drag_start_positions = {}
        elif event.button() == Qt.MouseButton.LeftButton and self._rotating:
            if self.selected_elements and self._rotation_start_quats and self._mouse_moved:
                old_quats = []
                elems = []
                for elem in self.selected_elements:
                    old = self._rotation_start_quats.get(id(elem))
                    if old:
                        elems.append(elem)
                        old_quats.append(old)
                if elems:
                    self.elements_rotated.emit(elems, old_quats)
            self._rotating = False
            self._rotate_drag_axis = None
            self._rotation_start_quats = {}
        elif event.button() == Qt.MouseButton.LeftButton and self._dragging_element:
            if self.selected_elements and self._drag_start_positions and self._mouse_moved:
                if len(self.selected_elements) == 1:
                    elem = self.selected_elements[0]
                    old = self._drag_start_positions.get(id(elem))
                    if old:
                        self.element_moved.emit(elem, old[0], old[1], old[2])
                elif len(self.selected_elements) > 1:
                    old_positions = []
                    elems = []
                    for elem in self.selected_elements:
                        old = self._drag_start_positions.get(id(elem))
                        if old:
                            elems.append(elem)
                            old_positions.append(old)
                    if elems:
                        self.elements_moved.emit(elems, old_positions)
            self._dragging_element = False
            self._drag_start_positions = {}
        elif event.button() == Qt.MouseButton.RightButton and not self._mouse_moved:
            self._show_context_menu(event.pos())

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        self.camera.zoom(event.angleDelta().y())
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Home:
            self._fit_all()
        elif key == Qt.Key.Key_Escape:
            self.selected_elements = []
            self._emit_selection()
            self.update()
        elif key == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            if self.project:
                self.selected_elements = list(self.project.elements)
                self._emit_selection()
                self.update()
        elif key == Qt.Key.Key_Delete:
            if self.selected_elements:
                if len(self.selected_elements) == 1:
                    self.delete_element_requested.emit(self.selected_elements[0])
                else:
                    self.delete_elements_requested.emit(list(self.selected_elements))
        elif key == Qt.Key.Key_D and mods & Qt.KeyboardModifier.ControlModifier:
            if self.selected_elements:
                for elem in list(self.selected_elements):
                    self.duplicate_element_requested.emit(elem)
        elif key == Qt.Key.Key_L and mods & Qt.KeyboardModifier.ControlModifier:
            if len(self.selected_elements) > 1:
                self.auto_layout_requested.emit("Row")
        elif key == Qt.Key.Key_F:
            self._focus_selected()
        elif key == Qt.Key.Key_Tab:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self._cycle_selection(-1)
            else:
                self._cycle_selection(1)
        elif key == Qt.Key.Key_Left:
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Left: Nudge selected elements left (X-)
                self._nudge_selected_position(-1.0, 0.0, 0.0)
            else:
                self.camera.pan(30, 0)
            self.update()
        elif key == Qt.Key.Key_Right:
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Right: Nudge selected elements right (X+)
                self._nudge_selected_position(1.0, 0.0, 0.0)
            else:
                self.camera.pan(-30, 0)
            self.update()
        elif key == Qt.Key.Key_Up:
            if mods & Qt.KeyboardModifier.AltModifier and mods & Qt.KeyboardModifier.ShiftModifier:
                # Alt+Shift+Up: Nudge MIDI note values up
                self._nudge_selected_midi_note(1)
            elif mods & Qt.KeyboardModifier.AltModifier:
                # Alt+Up: Nudge MIDI CC values up
                self._nudge_selected_midi_cc(1)
            elif mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Up: Nudge selected elements forward (Y+)
                self._nudge_selected_position(0.0, 1.0, 0.0)
            else:
                self.camera.pan(0, 30)
            self.update()
        elif key == Qt.Key.Key_Down:
            if mods & Qt.KeyboardModifier.AltModifier and mods & Qt.KeyboardModifier.ShiftModifier:
                # Alt+Shift+Down: Nudge MIDI note values down
                self._nudge_selected_midi_note(-1)
            elif mods & Qt.KeyboardModifier.AltModifier:
                # Alt+Down: Nudge MIDI CC values down
                self._nudge_selected_midi_cc(-1)
            elif mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Down: Nudge selected elements back (Y-)
                self._nudge_selected_position(0.0, -1.0, 0.0)
            else:
                self.camera.pan(0, -30)
            self.update()
        elif key == Qt.Key.Key_PageUp:
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+PageUp: Nudge selected elements up (Z+)
                self._nudge_selected_position(0.0, 0.0, 1.0)
                self.update()
        elif key == Qt.Key.Key_PageDown:
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+PageDown: Nudge selected elements down (Z-)
                self._nudge_selected_position(0.0, 0.0, -1.0)
                self.update()
        elif key == Qt.Key.Key_1:
            self.camera.ortho = False
            self.camera.yaw = 0.0
            self.camera.pitch = 0.0
            self.update()
        elif key == Qt.Key.Key_3:
            self.camera.ortho = False
            self.camera.yaw = 90.0
            self.camera.pitch = 0.0
            self.update()
        elif key == Qt.Key.Key_7:
            self.camera.ortho = False
            self.camera.yaw = 0.0
            self.camera.pitch = 89.0
            self.update()
        elif key == Qt.Key.Key_5:
            self.camera.ortho = not self.camera.ortho
            self.update()
        elif key == Qt.Key.Key_0:
            self.camera.ortho = False
            self.camera.yaw = -45.0
            self.camera.pitch = 30.0
            self.update()
        elif key == Qt.Key.Key_G:
            # Toggle grid snap
            self._snap_grid_enabled = not self._snap_grid_enabled
            self._show_status(f"Grid snap: {'ON' if self._snap_grid_enabled else 'OFF'} ({self._snap_grid_size:.0f} units)")
            self.update()
        elif key == Qt.Key.Key_H:
            # Keep shortcut help pinned visible to avoid losing the on-screen guide.
            self._show_shortcuts = True
            self._show_status("Shortcut help is pinned visible")
            self.update()
        elif key == Qt.Key.Key_N:
            # Toggle grid coordinate numbers
            self._show_grid_coords = not self._show_grid_coords
            self._show_status(f"Grid coordinates: {'VISIBLE' if self._show_grid_coords else 'HIDDEN'} (Press N to toggle)")
            self.update()
        elif key == Qt.Key.Key_R:
            # Rotate selected elements
            if self.selected_elements:
                old_quats = [(e.transform.rotation.x, e.transform.rotation.y,
                              e.transform.rotation.z, e.transform.rotation.w)
                             for e in self.selected_elements]
                angle = 15.0  # degrees
                if mods & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl+R: Rotate around X-axis
                    self._rotate_selected_elements(angle, 'x')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around X-axis (+{angle}°)")
                elif mods & Qt.KeyboardModifier.ShiftModifier:
                    # Shift+R: Rotate around Y-axis
                    self._rotate_selected_elements(angle, 'y')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around Y-axis (+{angle}°)")
                else:
                    # R: Rotate around Z-axis (most common for groups)
                    self._rotate_selected_elements(angle, 'z')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around Z-axis (+{angle}°)")
                self.elements_rotated.emit(list(self.selected_elements), old_quats)
                self.update()
            else:
                self._show_status("No elements selected to rotate!")
        elif key == Qt.Key.Key_T:
            # Rotate selected elements counter-clockwise (opposite of R)
            if self.selected_elements:
                old_quats = [(e.transform.rotation.x, e.transform.rotation.y,
                              e.transform.rotation.z, e.transform.rotation.w)
                             for e in self.selected_elements]
                angle = -15.0  # degrees
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self._rotate_selected_elements(angle, 'x')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around X-axis ({angle}°)")
                elif mods & Qt.KeyboardModifier.ShiftModifier:
                    self._rotate_selected_elements(angle, 'y')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around Y-axis ({angle}°)")
                else:
                    self._rotate_selected_elements(angle, 'z')
                    self._show_status(f"Rotated {len(self.selected_elements)} element(s) around Z-axis ({angle}°)")
                self.elements_rotated.emit(list(self.selected_elements), old_quats)
                self.update()
            else:
                self._show_status("No elements selected to rotate!")
        else:
            super().keyPressEvent(event)

    def _focus_selected(self):
        if not self.selected_elements:
            return
        if len(self.selected_elements) == 1:
            p = self.selected_elements[0].transform.translation
            self.camera.target = [p.x, p.y, p.z]
        else:
            n = len(self.selected_elements)
            cx = sum(e.transform.translation.x for e in self.selected_elements) / n
            cy = sum(e.transform.translation.y for e in self.selected_elements) / n
            cz = sum(e.transform.translation.z for e in self.selected_elements) / n
            self.camera.target = [cx, cy, cz]
        self.camera.distance = min(self.camera.distance, 80.0)
        self.update()

    def _cycle_selection(self, direction: int):
        if not self.project:
            return
        elems = self._active_workspace_elements()
        if not elems:
            self.selected_elements = []
            self._emit_selection()
            self.update()
            return
        if self.selected_elements and self.selected_elements[-1] in elems:
            idx = elems.index(self.selected_elements[-1])
            idx = (idx + direction) % len(elems)
        else:
            idx = 0
        self.selected_elements = [elems[idx]]
        self._emit_selection()
        self.update()

    def _compute_screen_positions_now(self):
        positions = {}
        if not self.project:
            return positions
        w, h = self.width(), self.height()
        for elem in self._active_workspace_elements():
            p = elem.transform.translation
            sp = self.camera.world_to_screen(p.x, p.y, p.z, w, h)
            if sp:
                positions[id(elem)] = (sp[0], sp[1], sp[2], elem)
        return positions

    def _handle_pick(self, mx, my, modifiers=Qt.KeyboardModifier(0)):
        """Screen-space picking with Shift/Ctrl multi-select support."""
        if not self.project:
            return

        positions = self._compute_screen_positions_now()
        if not positions:
            return

        best_elem = None
        best_dist = 80.0

        for elem_id, (sx, sy, depth, elem) in positions.items():
            dist = math.sqrt((mx - sx) ** 2 + (my - sy) ** 2)

            # Use larger hit radius for groups to make them easier to select
            threshold = 150.0 if isinstance(elem, GroupIE) else 80.0

            if dist < threshold and dist < best_dist:
                best_dist = dist
                best_elem = elem

        shift_or_ctrl = bool(modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))

        if shift_or_ctrl:
            # Toggle element in/out of selection
            if best_elem is not None:
                if best_elem in self.selected_elements:
                    self.selected_elements.remove(best_elem)
                else:
                    self.selected_elements.append(best_elem)
            # If miss with modifier, do nothing
        else:
            # Replace selection
            if best_elem is not None:
                self.selected_elements = [best_elem]
                midi = self._midi_summary(best_elem, limit=2)
                if midi:
                    self._show_status(f"Selected: {best_elem.display_name or best_elem.unique_id} -> {', '.join(midi)}")
                else:
                    self._show_status(f"Selected: {best_elem.display_name or best_elem.unique_id}")
            else:
                # Miss on empty — start marquee select
                self._marquee_active = True
                self._marquee_start = QPoint(mx, my)
                self._marquee_rect = None
                self.selected_elements = []
                self._show_status("Click and drag to marquee select")

        # Set up drag if we have a selection
        if self.selected_elements and best_elem is not None:
            self._dragging_element = True
            self._drag_start_positions = {}
            for elem in self.selected_elements:
                p = elem.transform.translation
                self._drag_start_positions[id(elem)] = (p.x, p.y, p.z)

        self._emit_selection()
        self.update()

    def _finish_marquee_select(self, modifiers):
        """Select all elements whose screen positions fall inside the marquee rect."""
        positions = self._compute_screen_positions_now()
        rect = self._marquee_rect
        new_sel = []
        for _, (sx, sy, depth, elem) in positions.items():
            if rect.contains(int(sx), int(sy)):
                new_sel.append(elem)

        shift_or_ctrl = bool(modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if shift_or_ctrl:
            # Add to existing selection
            for elem in new_sel:
                if elem not in self.selected_elements:
                    self.selected_elements.append(elem)
        else:
            self.selected_elements = new_sel

        self._emit_selection()

    def _show_context_menu(self, pos):
        menu = QMenu(self)

        add_menu = menu.addMenu("Add Element")
        add_hitzone = add_menu.addAction("Add HitZone")
        add_morphzone = add_menu.addAction("Add MorphZone")
        add_textlabel = add_menu.addAction("Add TextLabel")
        add_group = add_menu.addAction("Add Group")

        dup_action = None
        del_action = None
        layout_row = None
        layout_grid = None
        layout_circle = None
        create_group_action = None
        add_to_group_menu = None
        remove_from_group_menu = None
        edit_text_action = None
        change_color_action = None
        toggle_lock_action = None
        workspace_menu = None

        if len(self.selected_elements) > 1:
            menu.addSeparator()
            n = len(self.selected_elements)
            dup_action = menu.addAction(f"Duplicate {n} elements")
            del_action = menu.addAction(f"Delete {n} elements")

            # Lock/unlock toggle
            locked_count = sum(1 for e in self.selected_elements if e.is_locked)
            if locked_count == n:
                toggle_lock_action = menu.addAction(f"Unlock {n} elements")
            elif locked_count == 0:
                toggle_lock_action = menu.addAction(f"Lock {n} elements")
            else:
                toggle_lock_action = menu.addAction(f"Toggle Lock ({locked_count}/{n} locked)")

            # Quick edit options for multi-selection
            menu.addSeparator()
            change_color_action = menu.addAction(f"Change Color of {n} elements")

            # Workspace transfer
            if self.project and len(self.project.workspaces) > 1:
                menu.addSeparator()
                workspace_menu = menu.addMenu(f"Move {n} elements to Workspace")
                for workspace in self.project.workspaces:
                    ws_display = workspace.display_name or workspace.unique_id
                    ws_action = workspace_menu.addAction(ws_display)
                    ws_action.setData(("move_to_workspace", workspace.unique_id))

            menu.addSeparator()
            layout_menu = menu.addMenu(f"Auto Layout ({n} elements)")
            layout_row = layout_menu.addAction("Row")
            layout_grid = layout_menu.addAction("Grid")
            layout_circle = layout_menu.addAction("Circle")

            # Group operations for multi-selection
            menu.addSeparator()
            create_group_action = menu.addAction(f"Create Group from {n} elements")

            # Add to existing groups (only show if there are groups)
            if self.project:
                existing_groups = [e for e in self.project.elements if hasattr(e, 'group_items')]
                if existing_groups:
                    add_to_group_menu = menu.addMenu(f"Add {n} elements to Group")
                    for group in existing_groups:
                        display_name = group.display_name or group.unique_id
                        group_action = add_to_group_menu.addAction(display_name)
                        group_action.setData(("add_to_group", group.unique_id))

        elif len(self.selected_elements) == 1:
            elem = self.selected_elements[0]
            menu.addSeparator()
            dup_action = menu.addAction(f"Duplicate {elem.unique_id}")
            del_action = menu.addAction(f"Delete {elem.unique_id}")

            # Quick edit options for single element
            menu.addSeparator()
            # Text editing for TextLabels or display names for others
            from model import TextLabel
            if isinstance(elem, TextLabel):
                edit_text_action = menu.addAction(f"Edit Text")
            else:
                edit_text_action = menu.addAction(f"Edit Display Name")

            change_color_action = menu.addAction(f"Change Color")

            # Group operations for single element
            if self.project:
                menu.addSeparator()

                # Find groups this element is in
                element_groups = []
                for group in self.project.elements:
                    if hasattr(group, 'group_items') and elem.unique_id in group.group_items:
                        element_groups.append(group)

                # Create group from single element
                create_group_action = menu.addAction(f"Create Group from {elem.unique_id}")

                # Add to existing groups (exclude groups it's already in)
                other_groups = [g for g in self.project.elements
                               if hasattr(g, 'group_items') and elem.unique_id not in g.group_items]
                if other_groups:
                    add_to_group_menu = menu.addMenu("Add to Group")
                    for group in other_groups:
                        display_name = group.display_name or group.unique_id
                        group_action = add_to_group_menu.addAction(display_name)
                        group_action.setData(("add_to_group", group.unique_id))

                # Remove from groups it's currently in
                if element_groups:
                    remove_from_group_menu = menu.addMenu("Remove from Group")
                    for group in element_groups:
                        display_name = group.display_name or group.unique_id
                        group_action = remove_from_group_menu.addAction(display_name)
                        group_action.setData(("remove_from_group", group.unique_id))

        # Execute the menu and get the selected action
        action = menu.exec(self.mapToGlobal(pos))

        # Handle basic actions
        if action == add_hitzone:
            self.add_element_requested.emit("HitZone")
        elif action == add_morphzone:
            self.add_element_requested.emit("MorphZone")
        elif action == add_textlabel:
            self.add_element_requested.emit("TextLabel")
        elif action == add_group:
            self.add_element_requested.emit("GroupIE")
        elif action is not None and action == dup_action:
            for elem in list(self.selected_elements):
                self.duplicate_element_requested.emit(elem)
        elif action is not None and action == del_action:
            # Use batch delete for efficiency
            self.delete_elements_requested.emit(list(self.selected_elements))
        elif action is not None and action == layout_row:
            self.auto_layout_requested.emit("Row")
        elif action is not None and action == layout_grid:
            self.auto_layout_requested.emit("Grid")
        elif action is not None and action == layout_circle:
            self.auto_layout_requested.emit("Circle")
        elif action is not None and action == create_group_action:
            self.create_group_requested.emit(list(self.selected_elements))
        elif action is not None and action == edit_text_action:
            # Only emit for single element
            if len(self.selected_elements) == 1:
                self.edit_text_requested.emit(self.selected_elements[0])
        elif action is not None and action == change_color_action:
            self.change_color_requested.emit(list(self.selected_elements))
        elif action is not None and action == toggle_lock_action:
            self.toggle_lock_requested.emit(list(self.selected_elements))

        # Use action data to identify group operations and workspace transfers
        if action is not None and hasattr(action, 'data') and action.data():
            action_type, target_id = action.data()
            if action_type == "add_to_group":
                self.add_to_group_requested.emit(list(self.selected_elements), target_id)
            elif action_type == "remove_from_group":
                self.remove_from_group_requested.emit(list(self.selected_elements), target_id)
            elif action_type == "move_to_workspace":
                self.move_to_workspace_requested.emit(list(self.selected_elements), target_id)

    def _handle_drag(self, dx, dy, shift_held):
        if not self.selected_elements:
            return

        speed = self.camera.distance * 0.002
        rx, ry, rz = self.camera.right_vector()
        fx, fy, fz = self.camera.forward_vector()

        for elem in self.selected_elements:
            p = elem.transform.translation
            if shift_held:
                p.z -= dy * speed
            else:
                p.x += (dx * rx - dy * fx) * speed
                p.y += (dx * ry - dy * fy) * speed

            if self._snap_grid_enabled:
                gs = self._snap_grid_size
                p.x = round(p.x / gs) * gs
                p.y = round(p.y / gs) * gs
                p.z = round(p.z / gs) * gs

    def _handle_resize_drag(self, dx, dy):
        if not self.selected_elements or not self._resize_axis:
            return
        speed = self.camera.distance * 0.0005
        delta = dx * speed
        for elem in self.selected_elements:
            s = elem.transform.scale
            if self._resize_axis == 'x':
                s.x = max(0.05, s.x + delta)
            elif self._resize_axis == 'y':
                s.y = max(0.05, s.y + delta)
            elif self._resize_axis == 'z':
                s.z = max(0.05, s.z + delta)

    def _handle_rotate_drag(self, dx, dy):
        """Rotate all selected elements based on mouse drag."""
        if not self.selected_elements or not self._rotate_drag_axis:
            return
        angle = dx * 0.5  # degrees per pixel
        self._rotate_selected_elements(angle, self._rotate_drag_axis)

    def _rotate_selected_elements(self, angle_degrees: float, axis: str):
        """Rotate selected elements around specified axis."""
        if not self.selected_elements:
            return

        # Create rotation quaternion for the specified axis
        angle_rad = math.radians(angle_degrees)
        cos_half = math.cos(angle_rad * 0.5)
        sin_half = math.sin(angle_rad * 0.5)

        if axis == 'x':
            rot_quat = Quat(x=sin_half, y=0.0, z=0.0, w=cos_half)
        elif axis == 'y':
            rot_quat = Quat(x=0.0, y=sin_half, z=0.0, w=cos_half)
        else:  # 'z'
            rot_quat = Quat(x=0.0, y=0.0, z=sin_half, w=cos_half)

        # Apply rotation to each selected element
        for elem in self.selected_elements:
            # Combine with existing rotation: new_rotation = rotation * existing_rotation
            existing = elem.transform.rotation
            new_quat = _multiply_quaternions(rot_quat, existing)
            elem.transform.rotation = new_quat

    def _nudge_selected_position(self, dx: float, dy: float, dz: float):
        """Nudge selected elements by small amounts in X/Y/Z."""
        if not self.selected_elements:
            self._show_status("No elements selected to nudge!")
            return

        nudge_amount = 1.0  # units
        if self._snap_grid_enabled:
            nudge_amount = self._snap_grid_size

        for elem in self.selected_elements:
            p = elem.transform.translation
            p.x += dx * nudge_amount
            p.y += dy * nudge_amount
            p.z += dz * nudge_amount

        # Show status feedback
        direction = ""
        if dx > 0: direction = "RIGHT"
        elif dx < 0: direction = "LEFT"
        elif dy > 0: direction = "FORWARD"
        elif dy < 0: direction = "BACK"
        elif dz > 0: direction = "UP"
        elif dz < 0: direction = "DOWN"

        count = len(self.selected_elements)
        elem_text = "element" if count == 1 else f"{count} elements"
        self._show_status(f"Nudged {elem_text} {direction} by {nudge_amount:.1f} units")

    def _nudge_selected_midi_cc(self, delta: int):
        """Nudge MIDI CC values of selected elements."""
        if not self.selected_elements:
            self._show_status("No elements selected to nudge MIDI CC!")
            return

        logging.info(f"_nudge_selected_midi_cc: delta={delta}, {len(self.selected_elements)} elements selected")
        for elem in self.selected_elements:
            logging.info(f"  elem={elem.unique_id}, type={type(elem).__name__}, "
                         f"has x_cc={hasattr(elem, 'x_axis_cc_mappings') and bool(getattr(elem, 'x_axis_cc_mappings', None))}, "
                         f"has y_cc={hasattr(elem, 'y_axis_cc_mappings') and bool(getattr(elem, 'y_axis_cc_mappings', None))}, "
                         f"has z_cc={hasattr(elem, 'z_axis_cc_mappings') and bool(getattr(elem, 'z_axis_cc_mappings', None))}, "
                         f"has midi_cc={hasattr(elem, 'midi_cc_mappings') and bool(getattr(elem, 'midi_cc_mappings', None))}")

        changed_count = 0
        updated_values = []
        undo_changes = []
        total_cc_mappings = 0
        for elem in self.selected_elements:
            # Handle MorphZones with CC mappings
            if hasattr(elem, 'x_axis_cc_mappings') and elem.x_axis_cc_mappings:
                old_maps = copy.deepcopy(elem.x_axis_cc_mappings)
                field_changed = False
                for cc_map in elem.x_axis_cc_mappings:
                    total_cc_mappings += 1
                    old_val = cc_map.value
                    cc_map.value = max(0, min(127, cc_map.value + delta))
                    if cc_map.value != old_val:
                        changed_count += 1
                        field_changed = True
                        updated_values.append(cc_map.value)
                if field_changed:
                    undo_changes.append((elem, 'x_axis_cc_mappings', old_maps, copy.deepcopy(elem.x_axis_cc_mappings)))
            if hasattr(elem, 'y_axis_cc_mappings') and elem.y_axis_cc_mappings:
                old_maps = copy.deepcopy(elem.y_axis_cc_mappings)
                field_changed = False
                for cc_map in elem.y_axis_cc_mappings:
                    total_cc_mappings += 1
                    old_val = cc_map.value
                    cc_map.value = max(0, min(127, cc_map.value + delta))
                    if cc_map.value != old_val:
                        changed_count += 1
                        field_changed = True
                        updated_values.append(cc_map.value)
                if field_changed:
                    undo_changes.append((elem, 'y_axis_cc_mappings', old_maps, copy.deepcopy(elem.y_axis_cc_mappings)))
            if hasattr(elem, 'z_axis_cc_mappings') and elem.z_axis_cc_mappings:
                old_maps = copy.deepcopy(elem.z_axis_cc_mappings)
                field_changed = False
                for cc_map in elem.z_axis_cc_mappings:
                    total_cc_mappings += 1
                    old_val = cc_map.value
                    cc_map.value = max(0, min(127, cc_map.value + delta))
                    if cc_map.value != old_val:
                        changed_count += 1
                        field_changed = True
                        updated_values.append(cc_map.value)
                if field_changed:
                    undo_changes.append((elem, 'z_axis_cc_mappings', old_maps, copy.deepcopy(elem.z_axis_cc_mappings)))

            # Handle HitZones with CC mappings
            if hasattr(elem, 'midi_cc_mappings') and elem.midi_cc_mappings:
                old_maps = copy.deepcopy(elem.midi_cc_mappings)
                field_changed = False
                for cc_map in elem.midi_cc_mappings:
                    total_cc_mappings += 1
                    old_val = cc_map.value
                    cc_map.value = max(0, min(127, cc_map.value + delta))
                    if cc_map.value != old_val:
                        changed_count += 1
                        field_changed = True
                        updated_values.append(cc_map.value)
                if field_changed:
                    undo_changes.append((elem, 'midi_cc_mappings', old_maps, copy.deepcopy(elem.midi_cc_mappings)))

        if changed_count:
            direction = "UP" if delta > 0 else "DOWN"
            # Re-emit current selection so external property panels refresh to the saved values.
            self._emit_selection()
            if undo_changes:
                self.midi_mappings_nudged.emit(undo_changes, f"Nudge MIDI CC {direction}")

            preview_values = ", ".join(str(v) for v in updated_values[:4])
            if len(updated_values) > 4:
                preview_values += ", ..."
            selected_preview = ""
            if self.selected_element is not None:
                selected_midi = self._midi_summary(self.selected_element, limit=2)
                if selected_midi:
                    selected_preview = f" | Selected: {', '.join(selected_midi)}"
            self._show_status(
                f"Nudged {changed_count} MIDI CC values {direction} by {abs(delta)} -> value {preview_values}{selected_preview}"
            )
        elif total_cc_mappings > 0:
            limit_text = "127 (max)" if delta > 0 else "0 (min)"
            self._show_status(
                f"MIDI CC mappings found, but all selected CC values are already at {limit_text}."
            )
        else:
            self._show_status(
                "No MIDI CC mappings on selected elements (note-only objects use Alt+Shift+Up/Down)."
            )

    def _nudge_selected_midi_note(self, delta: int):
        """Nudge MIDI note values of selected elements."""
        if not self.selected_elements:
            self._show_status("No elements selected to nudge MIDI notes!")
            return

        changed_count = 0
        updated_notes = []
        undo_changes = []
        for elem in self.selected_elements:
            # Handle HitZones with note mappings
            if hasattr(elem, 'midi_note_mappings') and elem.midi_note_mappings:
                old_maps = copy.deepcopy(elem.midi_note_mappings)
                field_changed = False
                for note_map in elem.midi_note_mappings:
                    old_val = note_map.note
                    note_map.note = max(0, min(127, note_map.note + delta))
                    if note_map.note != old_val:
                        changed_count += 1
                        field_changed = True
                        updated_notes.append(note_map.note)
                if field_changed:
                    undo_changes.append((elem, 'midi_note_mappings', old_maps, copy.deepcopy(elem.midi_note_mappings)))

        if changed_count:
            direction = "UP" if delta > 0 else "DOWN"
            # Re-emit current selection so external property panels refresh to the saved values.
            self._emit_selection()
            if undo_changes:
                self.midi_mappings_nudged.emit(undo_changes, f"Nudge MIDI note {direction}")

            preview_notes = ", ".join(str(v) for v in updated_notes[:4])
            if len(updated_notes) > 4:
                preview_notes += ", ..."
            self._show_status(
                f"Nudged {changed_count} MIDI notes {direction} by {abs(delta)} -> Note {preview_notes}"
            )
        else:
            self._show_status("No MIDI note mappings found on selected elements!")

    def _show_status(self, message: str):
        """Show a status message that fades out after a few seconds."""
        self._status_message = message
        self._status_timer = 120  # frames (about 2 seconds at 60fps)
        self.status_message.emit(message)


def _multiply_quaternions(q1: Quat, q2: Quat) -> Quat:
    """Multiply two quaternions (q1 * q2)."""
    return Quat(
        w=q1.w*q2.w - q1.x*q2.x - q1.y*q2.y - q1.z*q2.z,
        x=q1.w*q2.x + q1.x*q2.w + q1.y*q2.z - q1.z*q2.y,
        y=q1.w*q2.y - q1.x*q2.z + q1.y*q2.w + q1.z*q2.x,
        z=q1.w*q2.z + q1.x*q2.y - q1.y*q2.x + q1.z*q2.w
    )


# ---------------------------------------------------------------------------
# GL drawing helpers
# ---------------------------------------------------------------------------

def _quat_to_axis_angle(q: Quat) -> tuple:
    w = max(-1.0, min(1.0, q.w))
    angle = 2.0 * math.acos(abs(w)) * 180.0 / math.pi
    s = math.sqrt(max(0.0, 1.0 - w * w))
    if s < 0.001:
        return (angle, 1.0, 0.0, 0.0)
    return (angle, q.x / s, q.y / s, q.z / s)


def _draw_wire_box(x0, y0, z0, x1, y1, z1):
    glBegin(GL_LINES)
    glVertex3f(x0, y0, z0); glVertex3f(x1, y0, z0)
    glVertex3f(x1, y0, z0); glVertex3f(x1, y1, z0)
    glVertex3f(x1, y1, z0); glVertex3f(x0, y1, z0)
    glVertex3f(x0, y1, z0); glVertex3f(x0, y0, z0)
    glVertex3f(x0, y0, z1); glVertex3f(x1, y0, z1)
    glVertex3f(x1, y0, z1); glVertex3f(x1, y1, z1)
    glVertex3f(x1, y1, z1); glVertex3f(x0, y1, z1)
    glVertex3f(x0, y1, z1); glVertex3f(x0, y0, z1)
    glVertex3f(x0, y0, z0); glVertex3f(x0, y0, z1)
    glVertex3f(x1, y0, z0); glVertex3f(x1, y0, z1)
    glVertex3f(x1, y1, z0); glVertex3f(x1, y1, z1)
    glVertex3f(x0, y1, z0); glVertex3f(x0, y1, z1)
    glEnd()


def _draw_solid_box(x0, y0, z0, x1, y1, z1):
    glBegin(GL_QUADS)
    glNormal3f(0, -1, 0)
    glVertex3f(x0, y0, z0); glVertex3f(x1, y0, z0)
    glVertex3f(x1, y0, z1); glVertex3f(x0, y0, z1)
    glNormal3f(0, 1, 0)
    glVertex3f(x0, y1, z0); glVertex3f(x0, y1, z1)
    glVertex3f(x1, y1, z1); glVertex3f(x1, y1, z0)
    glNormal3f(-1, 0, 0)
    glVertex3f(x0, y0, z0); glVertex3f(x0, y0, z1)
    glVertex3f(x0, y1, z1); glVertex3f(x0, y1, z0)
    glNormal3f(1, 0, 0)
    glVertex3f(x1, y0, z0); glVertex3f(x1, y1, z0)
    glVertex3f(x1, y1, z1); glVertex3f(x1, y0, z1)
    glNormal3f(0, 0, -1)
    glVertex3f(x0, y0, z0); glVertex3f(x0, y1, z0)
    glVertex3f(x1, y1, z0); glVertex3f(x1, y0, z0)
    glNormal3f(0, 0, 1)
    glVertex3f(x0, y0, z1); glVertex3f(x1, y0, z1)
    glVertex3f(x1, y1, z1); glVertex3f(x0, y1, z1)
    glEnd()


def _draw_sphere(radius, slices, stacks):
    quad = gluNewQuadric()
    gluSphere(quad, radius, slices, stacks)
    gluDeleteQuadric(quad)


# ---------------------------------------------------------------------------
# Quad-view container (Perspective + Top + Front + Side)
# ---------------------------------------------------------------------------

def _make_ortho_viewport(label: str, yaw: float, pitch: float, parent=None) -> SceneViewport:
    vp = SceneViewport(parent)
    vp.view_label = label
    vp.lock_orbit = True
    vp.camera.ortho = True
    vp.camera.yaw = yaw
    vp.camera.pitch = pitch
    return vp


class QuadViewport(QWidget):
    """Container widget that holds 4 viewports in a 2x2 grid.

    Layout:
        +-------------+-------------+
        | Perspective  |     Top     |
        +-------------+-------------+
        |    Front     |    Side     |
        +-------------+-------------+

    All viewports share the same project and selection state.
    """
    element_selected = pyqtSignal(object)
    element_moved = pyqtSignal(object, float, float, float)
    element_scaled = pyqtSignal(object, float, float, float)
    add_element_requested = pyqtSignal(str)
    duplicate_element_requested = pyqtSignal(object)
    delete_element_requested = pyqtSignal(object)
    delete_elements_requested = pyqtSignal(list)  # batch delete
    selection_changed = pyqtSignal(list)
    elements_moved = pyqtSignal(list, list)
    elements_scaled = pyqtSignal(list, list)
    elements_rotated = pyqtSignal(list, list)
    midi_mappings_nudged = pyqtSignal(list, str)  # [(obj, attr, old_val, new_val)], description
    auto_layout_requested = pyqtSignal(str)
    add_to_group_requested = pyqtSignal(list, str)  # [elements], group_id
    remove_from_group_requested = pyqtSignal(list, str)  # [elements], group_id
    create_group_requested = pyqtSignal(list)  # [elements]
    edit_text_requested = pyqtSignal(object)  # element
    change_color_requested = pyqtSignal(list)  # [elements]
    toggle_lock_requested = pyqtSignal(list)  # [elements]
    move_to_workspace_requested = pyqtSignal(list, str)  # [elements], workspace_id
    status_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.persp = SceneViewport()
        self.persp.view_label = "Perspective"

        self.top_view = _make_ortho_viewport("Top", 0.0, 89.0)
        self.front_view = _make_ortho_viewport("Front", 0.0, 0.0)
        self.side_view = _make_ortho_viewport("Right", 90.0, 0.0)

        self._viewports = [self.persp, self.top_view, self.front_view, self.side_view]
        self._maximized_pane = None

        # Layout
        self._top_split = QSplitter(Qt.Orientation.Horizontal)
        self._top_split.addWidget(self.persp)
        self._top_split.addWidget(self.top_view)

        self._bottom_split = QSplitter(Qt.Orientation.Horizontal)
        self._bottom_split.addWidget(self.front_view)
        self._bottom_split.addWidget(self.side_view)

        self._outer = QSplitter(Qt.Orientation.Vertical)
        self._outer.addWidget(self._top_split)
        self._outer.addWidget(self._bottom_split)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._outer)

        # Forward signals
        for vp in self._viewports:
            vp.selection_changed.connect(self._on_child_selection_changed)
            vp.element_selected.connect(self._on_child_selected_compat)
            vp.element_moved.connect(self.element_moved)
            vp.element_scaled.connect(self.element_scaled)
            vp.elements_moved.connect(self.elements_moved)
            vp.elements_scaled.connect(self.elements_scaled)
            vp.elements_rotated.connect(self.elements_rotated)
            vp.midi_mappings_nudged.connect(self.midi_mappings_nudged)
            vp.add_element_requested.connect(self.add_element_requested)
            vp.duplicate_element_requested.connect(self.duplicate_element_requested)
            vp.delete_element_requested.connect(self.delete_element_requested)
            vp.delete_elements_requested.connect(self.delete_elements_requested)
            vp.auto_layout_requested.connect(self.auto_layout_requested)
            vp.add_to_group_requested.connect(self.add_to_group_requested)
            vp.remove_from_group_requested.connect(self.remove_from_group_requested)
            vp.create_group_requested.connect(self.create_group_requested)
            vp.edit_text_requested.connect(self.edit_text_requested)
            vp.change_color_requested.connect(self.change_color_requested)
            vp.toggle_lock_requested.connect(self.toggle_lock_requested)
            vp.move_to_workspace_requested.connect(self.move_to_workspace_requested)
            vp.status_message.connect(self.status_message)
            vp.maximize_requested.connect(lambda v=vp: self._toggle_maximize(v))

    def _on_child_selection_changed(self, elements):
        for vp in self._viewports:
            vp.selected_elements = list(elements)
            vp.update()
        self.selection_changed.emit(elements)

    def _on_child_selected_compat(self, elem):
        """Backward compat: forward single-element signal."""
        self.element_selected.emit(elem)

    def _toggle_maximize(self, viewport):
        if self._maximized_pane is viewport:
            # Restore all
            for vp in self._viewports:
                vp.show()
            self._top_split.show()
            self._bottom_split.show()
            self._maximized_pane = None
        else:
            # Hide all except this one
            for vp in self._viewports:
                vp.setVisible(vp is viewport)
            # Show the splitter that contains the viewport
            self._top_split.setVisible(viewport in (self.persp, self.top_view))
            self._bottom_split.setVisible(viewport in (self.front_view, self.side_view))
            self._maximized_pane = viewport

    def set_project(self, project):
        for vp in self._viewports:
            vp.set_project(project)

    def set_selected(self, elem_or_list):
        for vp in self._viewports:
            vp.set_selected(elem_or_list)

    def _fit_all(self):
        for vp in self._viewports:
            vp._fit_all()

    @property
    def camera(self):
        return self.persp.camera

    @property
    def selected_elements(self):
        return self.persp.selected_elements

    def refresh(self):
        for vp in self._viewports:
            vp.update()
