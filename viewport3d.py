"""
3D viewport widget for the MoveMusic editor.

Renders HitZones by role (note pad vs CC strip) and MorphZones by dimension
(1D axis rod, 2D plane slab, 3D box). Provides orbit/pan/zoom camera,
multi-select (Shift+click, marquee), clickable HUD toolbar, grid snapping, and overlays.
"""

from __future__ import annotations

import copy
import logging
import math
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QRectF, QRect
from PyQt6.QtGui import (
    QMouseEvent, QWheelEvent, QPainter, QFont, QFontMetrics,
    QColor, QPen, QBrush, QPainterPath, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu, QSplitter, QWidget, QVBoxLayout
from PyQt6.QtOpenGLWidgets import QOpenGLWidget

from OpenGL.GL import *
from OpenGL.GLU import *

from model import (
    Project,
    HitZone,
    MorphZone,
    TextLabel,
    GroupIE,
    Vec3,
    Quat,
    MidiCCMapping,
)


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
        ex, ey, ez = self.eye()
        dx = wx - ex
        dy = wy - ey
        dz = wz - ez

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

    def screen_to_world_ray(self, mx: float, my: float, viewport_w: int, viewport_h: int):
        """Return ray origin (ox,oy,oz) and unit direction (dx,dy,dz) through screen pixel."""
        w = max(int(viewport_w), 1)
        h = max(int(viewport_h), 1)
        ndc_x = (mx / w) * 2.0 - 1.0
        ndc_y = 1.0 - (my / h) * 2.0
        aspect = w / max(h, 1)
        rx, ry, rz = self.right_vector()
        ux, uy, uz = self.up_vector()
        fx, fy, fz = self.forward_vector()

        ox, oy, oz = self.eye()

        if self.ortho:
            half_h = self.distance * 0.5
            half_w = half_h * aspect
            ox += ndc_x * half_w * rx + ndc_y * half_h * ux
            oy += ndc_x * half_w * ry + ndc_y * half_h * uy
            oz += ndc_x * half_w * rz + ndc_y * half_h * uz
            vx, vy, vz = fx, fy, fz
        else:
            fov_scale = math.tan(math.radians(self.fov) * 0.5)
            dx = ndc_x * fov_scale * aspect * rx + ndc_y * fov_scale * ux + fx
            dy = ndc_x * fov_scale * aspect * ry + ndc_y * fov_scale * uy + fy
            dz = ndc_x * fov_scale * aspect * rz + ndc_y * fov_scale * uz + fz
            ln = math.sqrt(dx * dx + dy * dy + dz * dz)
            if ln < 1e-12:
                return None
            vx, vy, vz = dx / ln, dy / ln, dz / ln
            return ((ox, oy, oz), (vx, vy, vz))

        ln = math.sqrt(vx * vx + vy * vy + vz * vz)
        if ln < 1e-12:
            return None
        return ((ox, oy, oz), (vx / ln, vy / ln, vz / ln))


# ---------------------------------------------------------------------------
# Bounding box helpers
# ---------------------------------------------------------------------------

_MORPH_SLAB_HALF_T = 1.35


def _morph_dimensions_rank(mz: MorphZone) -> int:
    d = getattr(mz, "dimensions", "") or ""
    if "::Three" in d or d.endswith("Three"):
        return 3
    if "::Two" in d or d.endswith("Two"):
        return 2
    if "::One" in d or d.endswith("One"):
        return 1
    return 3


def _morph_enabled_axes(mz: MorphZone) -> Tuple[str, ...]:
    axes = []
    if mz.is_x_axis_enabled:
        axes.append("x")
    if mz.is_y_axis_enabled:
        axes.append("y")
    if mz.is_z_axis_enabled:
        axes.append("z")
    return tuple(axes)


def _morph_visual_half_extents(mz: MorphZone, s) -> Tuple[float, float, float]:
    """Half-extents matching on-screen shape: 1D rod, 2D slab, or 3D box."""
    ext = mz.mesh_extent
    hx = max(ext.x * s.x * 0.5, 3.0)
    hy = max(ext.y * s.y * 0.5, 3.0)
    hz = max(ext.z * s.z * 0.5, 1.0)
    t = _MORPH_SLAB_HALF_T
    rank = _morph_dimensions_rank(mz)
    axes = _morph_enabled_axes(mz)
    n = len(axes)
    if n == 0:
        if rank <= 1:
            return (hx, t, t)
        if rank == 2:
            return (hx, hy, t)
        return (hx, hy, hz)
    cap = min(rank, n)
    if cap >= 3:
        return (hx, hy, hz)
    if cap <= 1:
        if axes == ("y",):
            return (t, hy, t)
        if axes == ("z",):
            return (t, t, hz)
        return (hx, t, t)
    if "x" in axes and "y" in axes:
        return (hx, hy, t)
    if "x" in axes and "z" in axes:
        return (hx, t, hz)
    if "y" in axes and "z" in axes:
        return (t, hy, hz)
    return (hx, hy, t)


def _hitzone_is_cc(hz: HitZone) -> bool:
    """True when this HitZone sends MIDI CC (Unreal may use ::ControlChange or editor ::CC)."""
    mt = getattr(hz, "midi_message_type", "") or ""
    return (
        "ControlChange" in mt
        or mt == "EMidiMessageType::CC"
        or mt.endswith("::CC")
    )


def _hitzone_is_note(hz: HitZone) -> bool:
    if _hitzone_is_cc(hz):
        return False
    mt = getattr(hz, "midi_message_type", "") or ""
    return "Note" in mt


def _hitzone_is_toggle(hz: HitZone) -> bool:
    b = getattr(hz, "behavior", "") or ""
    return "Toggle" in b


def _hitzone_visual_half_extents(hz: HitZone, s) -> Tuple[float, float, float]:
    thin = 1.15
    if _hitzone_is_note(hz):
        r = max(13.0 * max(s.x, s.y, 0.25), 4.0)
        return (r, r, thin)
    hx = max(24.0 * s.x, 6.0)
    hy = max(9.0 * s.y, 3.5)
    return (hx, hy, thin)


def _element_role_label(elem) -> str:
    if isinstance(elem, MorphZone):
        rank = _morph_dimensions_rank(elem)
        axes = _morph_enabled_axes(elem)
        al = "".join(a.upper() for a in axes)
        if rank == 1:
            return f"Morph 1D ({al or 'X'})"
        if rank == 2:
            return f"Morph 2D ({al or 'XY'})"
        return f"Morph 3D ({al or 'XYZ'})"
    if isinstance(elem, HitZone):
        if _hitzone_is_note(elem):
            return "Note pad (toggle)" if _hitzone_is_toggle(elem) else "Note pad (hold)"
        return "CC control"
    if isinstance(elem, GroupIE):
        return "Group"
    return type(elem).__name__


def _get_element_bbox(elem, half_size=15.0) -> tuple:
    p = elem.transform.translation
    s = elem.transform.scale
    if isinstance(elem, MorphZone):
        hx, hy, hz = _morph_visual_half_extents(elem, s)
    elif isinstance(elem, HitZone):
        hx, hy, hz = _hitzone_visual_half_extents(elem, s)
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
    play_mode_exit_requested = pyqtSignal()
    play_perf_send = pyqtSignal(str, dict)
    play_morph_interaction = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = OrbitCamera()
        self.project: Optional[Project] = None
        self.selected_elements: List = []  # ordered list; last = primary
        self.setMinimumHeight(100)

        # View configuration
        self.view_label = "Perspective"
        self.lock_orbit = False
        self.lock_move = False  # Performance mode: block pick-drag and scale/rotate; move handle still works
        self.play_mode = False  # Desktop Play: fullscreen game-like session

        # Mouse state
        self._last_mouse = QPoint()
        self._dragging_element = False
        self._gizmo_move_screen_pos = None  # (sx, sy) cached in paintGL for hit test
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

        # Desktop Play: 3D morph drag / hitzone notes
        self._play_morph_drag = False
        self._play_morph_elem: Optional[MorphZone] = None
        self._play_hitzone_elem: Optional[HitZone] = None
        self._midi_quick_panel_rect: Optional[QRect] = None

        # Snap grid
        self._snap_grid_enabled = False
        self._snap_grid_size = 5.0
        self.SNAP_GRID_PRESETS = (5.0, 10.0, 25.0, 50.0, 100.0)

        # UI state
        self._show_shortcuts = True  # Always show shortcuts by default
        self._show_grid_coords = True  # Show grid coordinate numbers
        self._status_message = ""  # Current action feedback
        self._status_timer = 0  # Timer for status message fadeout
        self._grid_coords = []  # Grid coordinate positions for labeling
        self._hover_element = None  # Element currently under cursor

        # Overlay buttons (populated during paintEvent)
        self._overlay_buttons = []  # [(QRectF, label_str)]

        # Auto-fly camera system
        self._autofly_mode = ""       # "", "orbit", "flythrough", "tour"
        self._autofly_timer = QTimer(self)
        self._autofly_timer.setInterval(33)  # ~30 fps
        self._autofly_timer.timeout.connect(self._autofly_tick)
        self._autofly_t = 0.0         # progress [0..1] or angle accumulator
        self._autofly_speed = 1.0     # multiplier
        self._autofly_waypoints: List[tuple] = []  # [(cx, cy, cz, dist, yaw, pitch), ...]
        self._autofly_wp_index = 0
        self._autofly_wp_blend = 0.0
        self._autofly_paused = False

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

    def set_snap_grid_size(self, size: float) -> None:
        """Set snap distance in world units (used when grid snap is enabled)."""
        self._snap_grid_size = max(0.1, float(size))
        self.update()

    def cycle_snap_grid_preset(self, direction: int) -> None:
        """Cycle through SNAP_GRID_PRESETS (direction +1 or -1)."""
        presets = list(self.SNAP_GRID_PRESETS)
        cur = self._snap_grid_size
        closest = min(range(len(presets)), key=lambda j: abs(presets[j] - cur))
        nxt = (closest + direction) % len(presets)
        self._snap_grid_size = presets[nxt]
        self._show_status(f"Snap grid preset: {self._snap_grid_size:g} units (G toggles snap)")
        self.update()

    def fit_workspace_bounds(self, ws_index: int) -> bool:
        """Frame camera to the bounding region of all elements in workspace ws_index."""
        if not self.project or not self.project.workspaces:
            return False
        if not (0 <= ws_index < len(self.project.workspaces)):
            return False
        ws = self.project.workspaces[ws_index]
        id_to_el = {e.unique_id: e for e in self.project.elements}
        visible = [id_to_el[uid] for uid in ws.element_ids if uid in id_to_el]
        if not visible:
            return False
        min_pt = [1e30, 1e30, 1e30]
        max_pt = [-1e30, -1e30, -1e30]
        for e in visible:
            p = e.transform.translation
            for i, v in enumerate([p.x, p.y, p.z]):
                min_pt[i] = min(min_pt[i], v - 30)
                max_pt[i] = max(max_pt[i], v + 30)
        self.camera.fit_to_bounds(min_pt, max_pt)
        self.camera.ortho = False
        self.camera.yaw = 48.0
        self.camera.pitch = 32.0
        self.update()
        return True

    # ---- Auto-fly camera ----

    def autofly_start(self, mode: str, speed: float = 1.0) -> None:
        """Start an auto-fly camera mode: 'orbit', 'flythrough', or 'tour'."""
        self.autofly_stop()
        elems = self._active_workspace_elements()
        if not elems and self.project:
            elems = list(self.project.elements)
        if not elems:
            self._show_status("No elements to fly around")
            return

        self._autofly_mode = mode
        self._autofly_speed = max(0.1, float(speed))
        self._autofly_t = 0.0
        self._autofly_paused = False

        if mode == "orbit":
            self._autofly_setup_orbit(elems)
        elif mode == "flythrough":
            self._autofly_setup_flythrough(elems)
        elif mode == "tour":
            self._autofly_setup_tour()
        else:
            self._autofly_mode = ""
            return

        self._autofly_timer.start()
        self._show_status(f"Auto-fly: {mode} (any mouse drag or Esc to stop)")

    def autofly_stop(self) -> None:
        if self._autofly_timer.isActive():
            self._autofly_timer.stop()
        self._autofly_mode = ""
        self._autofly_waypoints = []

    def autofly_toggle_pause(self) -> None:
        if not self._autofly_mode:
            return
        self._autofly_paused = not self._autofly_paused
        self._show_status(
            f"Auto-fly {'paused' if self._autofly_paused else 'resumed'} -- Esc or drag to stop"
        )

    @property
    def autofly_active(self) -> bool:
        return bool(self._autofly_mode)

    def _autofly_bounds(self, elems) -> tuple:
        min_pt = [1e30, 1e30, 1e30]
        max_pt = [-1e30, -1e30, -1e30]
        for e in elems:
            p = e.transform.translation
            for i, v in enumerate([p.x, p.y, p.z]):
                min_pt[i] = min(min_pt[i], v - 20)
                max_pt[i] = max(max_pt[i], v + 20)
        cx = (min_pt[0] + max_pt[0]) / 2
        cy = (min_pt[1] + max_pt[1]) / 2
        cz = (min_pt[2] + max_pt[2]) / 2
        extent = max(max_pt[0] - min_pt[0], max_pt[1] - min_pt[1], max_pt[2] - min_pt[2], 60.0)
        return cx, cy, cz, extent

    def _autofly_setup_orbit(self, elems) -> None:
        cx, cy, cz, extent = self._autofly_bounds(elems)
        self.camera.target = [cx, cy, cz]
        self.camera.distance = extent * 1.4
        self.camera.pitch = 28.0
        self.camera.ortho = False

    def _autofly_setup_flythrough(self, elems) -> None:
        clusters = self._autofly_cluster_elements(elems, max_clusters=12)
        if len(clusters) < 2:
            cx, cy, cz, extent = self._autofly_bounds(elems)
            clusters = [
                (cx - extent * 0.6, cy, cz),
                (cx, cy + extent * 0.6, cz),
                (cx + extent * 0.6, cy, cz),
                (cx, cy - extent * 0.6, cz),
            ]
        wps = []
        for (px, py, pz) in clusters:
            dist = 120.0
            yaw = math.degrees(math.atan2(py - self.camera.target[1], px - self.camera.target[0]))
            wps.append((px, py, pz, dist, yaw, 22.0))
        wps.append(wps[0])
        self._autofly_waypoints = wps
        self._autofly_wp_index = 0
        self._autofly_wp_blend = 0.0
        self.camera.ortho = False

    def _autofly_setup_tour(self) -> None:
        if not self.project or not self.project.workspaces:
            self._autofly_mode = ""
            return
        wps = []
        for ws in self.project.workspaces:
            id_to_el = {e.unique_id: e for e in self.project.elements}
            ws_elems = [id_to_el[uid] for uid in ws.element_ids if uid in id_to_el]
            if not ws_elems:
                continue
            cx, cy, cz, extent = self._autofly_bounds(ws_elems)
            dist = extent * 1.3
            yaw = 45.0 + len(wps) * 60.0
            wps.append((cx, cy, cz, dist, yaw, 30.0))
        if len(wps) < 2:
            self._autofly_mode = ""
            self._show_status("Need at least 2 workspaces with elements for tour")
            return
        wps.append(wps[0])
        self._autofly_waypoints = wps
        self._autofly_wp_index = 0
        self._autofly_wp_blend = 0.0
        self.camera.ortho = False

    def _autofly_cluster_elements(self, elems, max_clusters: int = 12) -> List[tuple]:
        """Simple spatial clustering: sort by angle from centroid, sample evenly."""
        if not elems:
            return []
        xs = [e.transform.translation.x for e in elems]
        ys = [e.transform.translation.y for e in elems]
        zs = [e.transform.translation.z for e in elems]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        cz = sum(zs) / len(zs)
        angled = []
        for e in elems:
            p = e.transform.translation
            a = math.atan2(p.y - cy, p.x - cx)
            angled.append((a, p.x, p.y, p.z))
        angled.sort()
        step = max(1, len(angled) // max_clusters)
        pts = []
        for i in range(0, len(angled), step):
            _, px, py, pz = angled[i]
            pts.append((px, py, pz))
        if len(pts) > max_clusters:
            pts = pts[:max_clusters]
        return pts

    def _autofly_tick(self) -> None:
        if self._autofly_paused:
            return
        dt = 0.033 * self._autofly_speed

        if self._autofly_mode == "orbit":
            self.camera.yaw += dt * 18.0
            if self.camera.yaw > 360.0:
                self.camera.yaw -= 360.0
            bobble = math.sin(self._autofly_t * 0.4) * 4.0
            self.camera.pitch = 28.0 + bobble
            self._autofly_t += dt
            self.update()
            return

        if self._autofly_mode in ("flythrough", "tour"):
            wps = self._autofly_waypoints
            if len(wps) < 2:
                self.autofly_stop()
                return
            idx = self._autofly_wp_index
            nxt = (idx + 1) % len(wps)
            a = wps[idx]
            b = wps[nxt]

            seg_speed = 0.25 if self._autofly_mode == "tour" else 0.4
            self._autofly_wp_blend += dt * seg_speed
            t = min(1.0, self._autofly_wp_blend)
            st = t * t * (3 - 2 * t)

            self.camera.target[0] = a[0] + (b[0] - a[0]) * st
            self.camera.target[1] = a[1] + (b[1] - a[1]) * st
            self.camera.target[2] = a[2] + (b[2] - a[2]) * st
            self.camera.distance = a[3] + (b[3] - a[3]) * st
            yaw_diff = b[4] - a[4]
            if yaw_diff > 180:
                yaw_diff -= 360
            elif yaw_diff < -180:
                yaw_diff += 360
            self.camera.yaw = a[4] + yaw_diff * st
            self.camera.pitch = a[5] + (b[5] - a[5]) * st

            if t >= 1.0:
                self._autofly_wp_blend = 0.0
                self._autofly_wp_index = nxt
                if nxt == 0:
                    pass
            self.update()
            return

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

    def _show_scale_rotate_gizmo(self) -> bool:
        """Scale cubes and rotation rings — layout tools; off in Performance Lock and Desktop Play."""
        if self.lock_move:
            return False
        if self.play_mode:
            return False
        return True

    def _hit_move_gizmo(self, mx: int, my: int) -> bool:
        pos = self._gizmo_move_screen_pos
        if pos is None and self.selected_elements:
            p = self.selected_elements[-1].transform.translation
            sp = self.camera.world_to_screen(p.x, p.y, p.z, self.width(), self.height())
            if sp:
                pos = (sp[0], sp[1])
        if pos is None:
            return False
        sx, sy = pos
        r = 34 if self.play_mode else 30
        return (mx - sx) ** 2 + (my - sy) ** 2 <= r * r

    def _start_move_drag_from_gizmo(self) -> None:
        self._dragging_element = True
        self._drag_start_positions = {}
        for elem in self.selected_elements:
            p = elem.transform.translation
            self._drag_start_positions[id(elem)] = (p.x, p.y, p.z)

    def _update_gizmo_hover_cursor(self, mx: int, my: int):
        """Change cursor when hovering over gizmo handles."""
        if not self.selected_elements:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._hit_move_gizmo(mx, my):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            if getattr(self, "_hover_gizmo", None) != "move":
                self._hover_gizmo = "move"
                self.update()
            return

        if not self._show_scale_rotate_gizmo():
            if getattr(self, "_hover_gizmo", None):
                self._hover_gizmo = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.update()
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
            glClearColor(0.06, 0.04, 0.1, 1.0)
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
            if self.play_mode:
                glClearColor(0.05, 0.05, 0.08, 1.0)
            else:
                glClearColor(0.06, 0.04, 0.1, 1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self.camera.apply(self.width(), self.height())

            if not self.play_mode:
                self._draw_grid()
                self._draw_world_axes()
            else:
                self._draw_play_floor_hologrid()

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

                # Transform gizmo: yellow move handle always when selected; scale/rotate only when unlocked
                if self.selected_elements:
                    primary = self.selected_elements[-1]
                    if len(self.selected_elements) == 1:
                        self._draw_selection_gizmo(primary)
                    else:
                        self._draw_multi_select_indicator()
                        self._draw_selection_gizmo(primary)
                else:
                    self._handle_screen_pos = {}
                    self._rot_ring_screen_points = {}
                    self._rot_handle_screen_pos = {}
                    self._gizmo_move_screen_pos = None

        except Exception:
            logging.exception("paintGL failed")

    def _draw_grid(self):
        """Holodeck / synthwave floor: neon green + cyan/magenta accents on Z=0."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

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
        half = min(1000, int(distance * 8))
        step = base_step

        snap_on = self._snap_grid_enabled
        snap_sz = self._snap_grid_size
        self._grid_coords = []

        zf = -0.4
        big = float(half) * 1.15
        glDisable(GL_DEPTH_TEST)
        glBegin(GL_QUADS)
        glColor4f(0.0, 0.14, 0.08, 0.45)
        glVertex3f(cx - big, cy - big, zf)
        glVertex3f(cx + big, cy - big, zf)
        glVertex3f(cx + big, cy + big, zf)
        glVertex3f(cx - big, cy + big, zf)
        glEnd()
        glEnable(GL_DEPTH_TEST)

        glLineWidth(1.15)
        glBegin(GL_LINES)
        line_idx = 0
        for i in range(-half, half + 1, step):
            dist_f = abs(i) / float(half + 1)
            fade = 0.35 + 0.55 * (1.0 - dist_f)

            if snap_on and snap_sz > 0:
                if (i % int(snap_sz * 10)) == 0:
                    glColor4f(0.2, 1.0, 0.55, 0.88 * fade)
                elif (i % coord_interval) == 0:
                    if line_idx % 2 == 0:
                        glColor4f(0.0, 0.95, 0.85, 0.72 * fade)
                    else:
                        glColor4f(0.85, 0.25, 1.0, 0.55 * fade)
                    self._grid_coords.append((cx + i, cy + i))
                else:
                    glColor4f(0.0, 0.75, 0.35, 0.22 * fade)
            else:
                is_major = (i % coord_interval) == 0
                if is_major:
                    if line_idx % 2 == 0:
                        glColor4f(0.0, 1.0, 0.72, 0.62 * fade)
                    else:
                        glColor4f(0.95, 0.15, 0.85, 0.45 * fade)
                    self._grid_coords.append((cx + i, cy + i))
                else:
                    glColor4f(0.05, 0.55, 0.28, 0.28 * fade)

            glVertex3f(cx + i, cy - half, 0.0)
            glVertex3f(cx + i, cy + half, 0.0)
            glVertex3f(cx - half, cy + i, 0.0)
            glVertex3f(cx + half, cy + i, 0.0)
            line_idx += 1
        glEnd()
        glPopAttrib()

        self._grid_cell_count = len(
            [c for c in self._grid_coords if abs(c[0] - cx) <= half and abs(c[1] - cy) <= half]
        )

    def _draw_play_floor_hologrid(self):
        """Neon floor grid on Z=0 for Desktop Play (subtle holographic look)."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glLineWidth(1.2)

        distance = self.camera.distance
        if distance < 200:
            base_step = 25
        elif distance < 800:
            base_step = 50
        else:
            base_step = 100

        cx = round(self.camera.target[0] / base_step) * base_step
        cy = round(self.camera.target[1] / base_step) * base_step
        half = min(900, max(200, int(distance * 8)))
        step = base_step

        glBegin(GL_LINES)
        n = 0
        for i in range(-half, half + 1, step):
            t = abs(i) / float(half + 1)
            a = 0.12 + 0.28 * (1.0 - t)
            if n % 2 == 0:
                glColor4f(0.0, 0.95, 1.0, a)
            else:
                glColor4f(0.75, 0.0, 1.0, a * 0.9)
            glVertex3f(cx + i, cy - half, 0.0)
            glVertex3f(cx + i, cy + half, 0.0)
            glColor4f(0.35, 0.75, 1.0, a * 0.85)
            glVertex3f(cx - half, cy + i, 0.0)
            glVertex3f(cx + half, cy + i, 0.0)
            n += 1
        glEnd()
        glPopAttrib()

    def _draw_world_axes(self):
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glLineWidth(2.4)
        cx = round(self.camera.target[0] / 50) * 50
        cy = round(self.camera.target[1] / 50) * 50
        length = 200

        glBegin(GL_LINES)
        glColor4f(1.0, 0.35, 0.45, 0.75)
        glVertex3f(cx - length, cy, 0)
        glVertex3f(cx + length, cy, 0)
        glColor4f(0.35, 1.0, 0.55, 0.75)
        glVertex3f(cx, cy - length, 0)
        glVertex3f(cx, cy + length, 0)
        glColor4f(0.45, 0.65, 1.0, 0.75)
        glVertex3f(cx, cy, -length)
        glVertex3f(cx, cy, length)
        glEnd()
        glPopAttrib()

    def _draw_multi_select_indicator(self):
        """Draw a subtle marker at each selected element's position."""
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_DEPTH_TEST)
        if self.play_mode:
            glLineWidth(1.0)
            sz = 2.2
            ca = 0.22
        else:
            glLineWidth(1.4)
            sz = 4.0
            ca = 0.5
        for elem in self.selected_elements:
            p = elem.transform.translation
            glColor4f(0.45, 0.95, 1.0, ca)
            glBegin(GL_LINES)
            glVertex3f(p.x - sz, p.y, p.z); glVertex3f(p.x + sz, p.y, p.z)
            glVertex3f(p.x, p.y - sz, p.z); glVertex3f(p.x, p.y + sz, p.z)
            glVertex3f(p.x, p.y, p.z - sz); glVertex3f(p.x, p.y, p.z + sz)
            glEnd()
        glPopAttrib()

    def _draw_selection_gizmo(self, elem):
        p = elem.transform.translation
        w, h = self.width(), self.height()
        self._handle_screen_pos = {}
        self._rot_handle_screen_pos = {}
        self._rot_ring_screen_points = {}

        full = self._show_scale_rotate_gizmo()
        play_lite = self.play_mode
        size = 22.0 if play_lite else 30.0
        handle_size = 4.0
        move_hs = 3.2 if play_lite else 3.5
        hover = getattr(self, "_hover_gizmo", None)

        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glDisable(GL_DEPTH_TEST)

        sp0 = self.camera.world_to_screen(p.x, p.y, p.z, w, h)
        self._gizmo_move_screen_pos = (sp0[0], sp0[1]) if sp0 else None

        if not full and play_lite:
            # Desktop Play: tiny pivot hint only (no box, no long axes over the control)
            stub = 5.5
            glLineWidth(1.05 if hover != "move" else 1.35)
            ha = 0.42 if hover == "move" else 0.28
            glBegin(GL_LINES)
            glColor4f(0.35, 0.95, 1.0, ha)
            glVertex3f(p.x - stub, p.y, p.z); glVertex3f(p.x + stub, p.y, p.z)
            glColor4f(0.35, 1.0, 0.75, ha * 0.92)
            glVertex3f(p.x, p.y - stub, p.z); glVertex3f(p.x, p.y + stub, p.z)
            glColor4f(0.55, 0.75, 1.0, ha * 0.88)
            glVertex3f(p.x, p.y, p.z - stub); glVertex3f(p.x, p.y, p.z + stub)
            glEnd()
            glPopAttrib()
            return

        # --- Move handle (compact wire box at pivot): edit mode / perf lock ---
        glLineWidth(1.6 if play_lite else 1.8)
        if hover == "move":
            glColor4f(1.0, 0.92, 0.4, 0.95)
        else:
            glColor4f(1.0, 0.78, 0.2, 0.72 if play_lite else 0.78)
        mx0, mx1 = p.x - move_hs, p.x + move_hs
        my0, my1 = p.y - move_hs, p.y + move_hs
        mz0, mz1 = p.z - move_hs, p.z + move_hs
        _draw_wire_box(mx0, my0, mz0, mx1, my1, mz1)
        glColor4f(1.0, 0.8, 0.15, 0.08 if play_lite else 0.1)
        _draw_solid_box(mx0, my0, mz0, mx1, my1, mz1)

        if not full:
            glLineWidth(1.8 if play_lite else 2.2)
            a = 0.38 if play_lite else 0.55
            glBegin(GL_LINES)
            glColor4f(1.0, 0.25, 0.25, a)
            glVertex3f(p.x, p.y, p.z)
            glVertex3f(p.x + size, p.y, p.z)
            glColor4f(0.25, 1.0, 0.25, a)
            glVertex3f(p.x, p.y, p.z)
            glVertex3f(p.x, p.y + size, p.z)
            glColor4f(0.25, 0.25, 1.0, a)
            glVertex3f(p.x, p.y, p.z)
            glVertex3f(p.x, p.y, p.z + size)
            glEnd()
            glPopAttrib()
            return

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

        rot_dist = size * 0.5
        ring_r = 8.0
        ring_segs = 32
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

        for axis, wx, wy, wz in [
            ('x', p.x + size, p.y, p.z),
            ('y', p.x, p.y + size, p.z),
            ('z', p.x, p.y, p.z + size),
        ]:
            sp = self.camera.world_to_screen(wx, wy, wz, w, h)
            if sp:
                self._handle_screen_pos[axis] = (sp[0], sp[1])

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
            self._draw_hit_zone(elem, s, c, alpha, is_selected)
        elif isinstance(elem, MorphZone):
            self._draw_morph_zone(elem, s, c, alpha, is_selected)
        elif isinstance(elem, TextLabel):
            self._draw_text_label(elem, s, c, alpha, is_selected)
        elif isinstance(elem, GroupIE):
            self._draw_group_ie(elem, s, c, alpha, is_selected)

        glPopMatrix()

    def _draw_hit_zone(self, hz: HitZone, s, c, alpha, is_selected):
        hx, hy, hzz = _hitzone_visual_half_extents(hz, s)
        if _hitzone_is_note(hz):
            self._draw_hit_zone_note_shape(hx, hzz, c, alpha, is_selected, _hitzone_is_toggle(hz))
        else:
            self._draw_hit_zone_cc_shape(hx, hy, hzz, c, alpha, is_selected)

    def _draw_hit_zone_note_shape(self, r: float, hz: float, c, alpha, is_selected, is_toggle: bool):
        """Drum-pad style short cylinder (note targets)."""
        body_r = max(r * 0.92, 2.5)
        h = hz
        quad = gluNewQuadric()
        gluQuadricNormals(quad, GLU_SMOOTH)

        if is_selected and not self.play_mode:
            glLineWidth(1.8)
            glColor4f(1.0, 0.88, 0.35, 0.55)
            if is_toggle:
                glEnable(GL_LINE_STIPPLE)
                glLineStipple(2, 0xAAAA)
            glBegin(GL_LINE_LOOP)
            for i in range(36):
                ang = 2.0 * math.pi * i / 36
                glVertex3f(body_r * math.cos(ang), body_r * math.sin(ang), h + 0.02)
            glEnd()
            glBegin(GL_LINE_LOOP)
            for i in range(36):
                ang = 2.0 * math.pi * i / 36
                glVertex3f(body_r * math.cos(ang), body_r * math.sin(ang), -h - 0.02)
            glEnd()
            if is_toggle:
                glDisable(GL_LINE_STIPPLE)

        glColor4f(c.r * 0.55, c.g * 0.55, c.b * 0.55, alpha * 0.9)
        glPushMatrix()
        glTranslatef(0.0, 0.0, -h)
        gluDisk(quad, 0.0, body_r, 28, 1)
        glColor4f(c.r, c.g, c.b, alpha)
        gluCylinder(quad, body_r, body_r, 2.0 * h, 28, 1)
        glTranslatef(0.0, 0.0, 2.0 * h)
        gluDisk(quad, 0.0, body_r, 28, 1)
        glPopMatrix()

        glLineWidth(2.0 if is_toggle else 1.4)
        glColor4f(min(c.r + 0.35, 1.0), min(c.g + 0.35, 1.0), min(c.b + 0.35, 1.0), alpha)
        if is_toggle:
            glEnable(GL_LINE_STIPPLE)
            glLineStipple(2, 0xCCCC)
        glBegin(GL_LINE_LOOP)
        for i in range(36):
            ang = 2.0 * math.pi * i / 36
            glVertex3f(body_r * math.cos(ang), body_r * math.sin(ang), h + 0.03)
        glEnd()
        if is_toggle:
            glDisable(GL_LINE_STIPPLE)
        ir = body_r * 0.42
        glLineWidth(1.0)
        glColor4f(c.r * 0.9, c.g * 0.9, c.b * 0.9, alpha * 0.65)
        glBegin(GL_LINE_LOOP)
        for i in range(24):
            ang = 2.0 * math.pi * i / 24
            glVertex3f(ir * math.cos(ang), ir * math.sin(ang), h + 0.04)
        glEnd()

        gluDeleteQuadric(quad)

    def _draw_hit_zone_cc_shape(self, hx: float, hy: float, hz: float, c, alpha, is_selected):
        """Elongated strip suggesting a CC / fader target."""
        if is_selected and not self.play_mode:
            glLineWidth(1.8)
            glColor4f(1.0, 0.88, 0.35, 0.55)
            _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glColor4f(c.r, c.g, c.b, alpha)
        _draw_solid_box(-hx, -hy, -hz, hx, hy, hz)

        glLineWidth(1.2)
        glColor4f(min(c.r + 0.28, 1.0), min(c.g + 0.28, 1.0), min(c.b + 0.28, 1.0), alpha * 0.95)
        zt = hz + 0.04
        glBegin(GL_LINES)
        for k in range(5):
            t = -1.0 + 0.5 * k
            x = t * hx * 0.82
            glVertex3f(x, -hy * 0.88, zt)
            glVertex3f(x, hy * 0.88, zt)
        glEnd()
        glLineWidth(1.5)
        glColor4f(min(c.r + 0.15, 1.0), min(c.g + 0.15, 1.0), min(c.b + 0.15, 1.0), alpha)
        _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

    def _draw_morph_zone(self, mz, s, c, alpha, is_selected):
        hx, hy, hz = _morph_visual_half_extents(mz, s)

        if is_selected and not self.play_mode:
            glLineWidth(1.8)
            glColor4f(1.0, 0.88, 0.35, 0.55)
            _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        glLineWidth(1.6 if not self.play_mode else 1.35)
        glColor4f(c.r, c.g, c.b, alpha)
        _draw_wire_box(-hx, -hy, -hz, hx, hy, hz)

        fill_a = alpha * (0.12 if self.play_mode else 0.15)
        glColor4f(c.r, c.g, c.b, fill_a)
        _draw_solid_box(-hx, -hy, -hz, hx, hy, hz)

        cp = mz.control_position_normalized
        cpx = -hx + cp.x * 2 * hx
        cpy = -hy + cp.y * 2 * hy
        cpz = -hz + cp.z * 2 * hz
        knob_a = 0.55 if self.play_mode else 0.9
        knob_r = 1.15 if self.play_mode else 1.5
        glColor4f(1.0, 1.0, 1.0, knob_a)
        glPushMatrix()
        glTranslatef(cpx, cpy, cpz)
        _draw_sphere(knob_r, 8 if not self.play_mode else 6, 8 if not self.play_mode else 6)
        glPopMatrix()

        glLineWidth(1.0 if not self.play_mode else 0.9)
        guide_a = 0.28 if self.play_mode else 0.6
        if mz.is_x_axis_enabled:
            glColor4f(1.0, 0.3, 0.3, guide_a)
            glBegin(GL_LINES)
            glVertex3f(-hx, cpy, cpz); glVertex3f(hx, cpy, cpz)
            glEnd()
        if mz.is_y_axis_enabled:
            glColor4f(0.3, 1.0, 0.3, guide_a)
            glBegin(GL_LINES)
            glVertex3f(cpx, -hy, cpz); glVertex3f(cpx, hy, cpz)
            glEnd()
        if mz.is_z_axis_enabled:
            glColor4f(0.3, 0.3, 1.0, guide_a)
            glBegin(GL_LINES)
            glVertex3f(cpx, cpy, -hz); glVertex3f(cpx, cpy, hz)
            glEnd()

    def _draw_text_label(self, tl, s, c, alpha, is_selected):
        hx, hy = max(10.0 * s.x, 3.0), max(8.0 * s.y, 3.0)

        if is_selected and not self.play_mode:
            glLineWidth(1.8)
            glColor4f(1.0, 0.88, 0.35, 0.55)
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

        if is_selected and not self.play_mode:
            glLineWidth(1.8)
            glColor4f(1.0, 0.88, 0.35, 0.55)
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
            if self.play_mode:
                self._draw_midi_quick_panel(painter)
                self._draw_status_message(painter)
            else:
                self._draw_grid_coordinates(painter)  # Grid coordinate numbers
                self._draw_element_labels(painter)
                self._draw_axis_hud(painter)
                self._draw_info_hud(painter)
                self._draw_overlay_toolbar(painter)
                self._draw_shortcuts_overlay(painter)  # Always visible shortcuts
                self._draw_midi_quick_panel(painter)
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

            base_name = elem.display_name or elem.unique_id
            if is_text:
                label = f'T: "{elem.display_name}"' if elem.display_name else base_name
            else:
                role = _element_role_label(elem)
                label = f"{role} — {base_name}"
            is_sel = id(elem) in sel_set
            is_hover = elem is self._hover_element

            tw = fm.horizontalAdvance(label)
            rx = int(sx - tw / 2)
            ry = int(sy - 22)

            if is_sel:
                painter.setPen(QColor(255, 210, 80, 140))
                painter.setBrush(QColor(40, 48, 58, 120))
                painter.drawRoundedRect(rx - 4, ry - fm.ascent() - 2, tw + 8, fm.height() + 4, 4, 4)
                painter.setPen(QColor(235, 240, 250))
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
            painter.setPen(QColor(0, 255, 210, 220))
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
            painter.setPen(QColor(0, 255, 180, 235))

            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(grid_info)
            bg_rect = text_rect.adjusted(-8, -4, 8, 4)
            top_right = self.rect().topRight()
            bg_rect.moveTopRight(QPoint(top_right.x() - 10, top_right.y() + 10))

            painter.fillRect(bg_rect, QColor(0, 20, 5, 200))
            text_r = bg_rect.adjusted(8, 4, -8, -4)
            painter.drawText(text_r.x(), text_r.y() + metrics.ascent(), grid_info)

            if distance < 20:
                zoom_hint = "VERY CLOSE -- scroll out to see more"
                hint_color = QColor(255, 160, 60, 240)
            elif distance < 60:
                zoom_hint = "Close up -- good for detail editing"
                hint_color = QColor(120, 220, 180, 200)
            elif distance > 3000:
                zoom_hint = "VERY FAR -- scroll in or press Home"
                hint_color = QColor(255, 160, 60, 240)
            elif distance > 1200:
                zoom_hint = "Zoomed out -- overview mode"
                hint_color = QColor(150, 200, 230, 200)
            else:
                zoom_hint = ""
                hint_color = QColor(0, 0, 0, 0)

            if zoom_hint:
                hint_rect = metrics.boundingRect(zoom_hint).adjusted(-8, -4, 8, 4)
                hint_rect.moveTopRight(QPoint(top_right.x() - 10, bg_rect.bottom() + 4))
                painter.fillRect(hint_rect, QColor(20, 10, 0, 180))
                painter.setPen(hint_color)
                hr = hint_rect.adjusted(8, 4, -8, -4)
                painter.drawText(hr.x(), hr.y() + metrics.ascent(), zoom_hint)

    def _draw_axis_hud(self, painter: QPainter):
        """Blender-style clickable axis gizmo: click an axis ball to snap the camera."""
        ox, oy = 60, self.height() - 60
        length = 36
        hit_radius = 14

        yr = math.radians(self.camera.yaw)
        pr = math.radians(self.camera.pitch)

        def project_axis(wx, wy, wz):
            sx = -wx * math.sin(yr) + wy * math.cos(yr)
            sy = -(-wx * math.cos(yr) * math.sin(pr) - wy * math.sin(yr) * math.sin(pr) + wz * math.cos(pr))
            return sx * length, sy * length

        axes = [
            ("X", (1, 0, 0), QColor(230, 70, 70)),
            ("Y", (0, 1, 0), QColor(70, 230, 70)),
            ("Z", (0, 0, 1), QColor(70, 100, 230)),
            ("-X", (-1, 0, 0), QColor(140, 50, 50)),
            ("-Y", (0, -1, 0), QColor(50, 140, 50)),
            ("-Z", (0, 0, -1), QColor(50, 50, 140)),
        ]

        painter.setPen(QPen(QColor(60, 60, 70, 120), 1))
        painter.setBrush(QColor(20, 24, 32, 160))
        painter.drawEllipse(int(ox - length - 12), int(oy - length - 12),
                            int((length + 12) * 2), int((length + 12) * 2))

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)

        self._axis_gizmo_hits = []

        sorted_axes = sorted(axes, key=lambda a: project_axis(*a[1])[1], reverse=True)

        for name, (wx, wy, wz), color in sorted_axes:
            ex, ey = project_axis(wx, wy, wz)
            is_front = (ex * ex + ey * ey) > (length * 0.3) ** 2
            alpha = 220 if is_front else 90
            lc = QColor(color.red(), color.green(), color.blue(), alpha)

            painter.setPen(QPen(lc, 2.0))
            painter.drawLine(int(ox), int(oy), int(ox + ex), int(oy + ey))

            ball_r = hit_radius if not name.startswith("-") else 8
            bc = QColor(color.red(), color.green(), color.blue(), alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bc)
            bx, by = int(ox + ex), int(oy + ey)
            painter.drawEllipse(bx - ball_r, by - ball_r, ball_r * 2, ball_r * 2)

            if not name.startswith("-"):
                painter.setPen(QColor(255, 255, 255, alpha))
                painter.drawText(bx - 4, by + 5, name)

            self._axis_gizmo_hits.append((bx, by, ball_r + 4, name))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 200, 210, 180))
        painter.drawEllipse(int(ox - 5), int(oy - 5), 10, 10)

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
            ("Home", False),
            ("Fit", False),
            ("Z+", False),
            ("Z-", False),
        ]

        btn_w, btn_h = 48, 28
        gap = 3
        total_w = len(buttons) * (btn_w + gap) - gap
        start_x = (self.width() - total_w) / 2
        start_y = 6

        self._overlay_buttons = []
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)

        for i, (label, active) in enumerate(buttons):
            x = start_x + i * (btn_w + gap)
            rect = QRectF(x, start_y, btn_w, btn_h)

            if active:
                bg = QColor(50, 120, 190, 220)
                border = QColor(90, 160, 230)
            else:
                bg = QColor(30, 34, 44, 210)
                border = QColor(70, 75, 85)

            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 5, 5)

            painter.setPen(QColor(240, 240, 240) if active else QColor(180, 185, 195))
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
                "⚡ F5 — Performance Lock",
                "Toggle to test MIDI/OSC without",
                "accidentally moving elements!",
                "",
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
                    midi_controls.append(f"Note pad: n{note.note} ch{note.channel}")

            if midi_controls:
                shortcuts.append("")
                shortcuts.extend(["🎵 MIDI MAPPINGS:"] + midi_controls[:4])
                if elem_type == "MorphZone":
                    shortcuts.append("Alt+↑↓: Adjust CC output values ±1")
                elif elem_type == "HitZone" and midi_controls[0].startswith("Note pad"):
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
                "Pivot cross / cube: drag to move (full cube in Performance Lock; tiny cross in Desktop Play)",
                "Drag element body: Move freely (when Performance Lock is off)",
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
                "Ctrl+Delete: Remove element",
                "  (Delete alone won't work here)",
                "F: Focus camera on element",
                "",
                "⚡ PERFORMANCE TEST:",
                "F5: Toggle Performance Lock",
                "  Locks drag → click safely to",
                "  select & test MIDI/OSC output",
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
                "Ctrl+Delete: Remove all selected",
                "  (Delete alone won't work here)",
                "Ctrl+L: Arrange in row layout",
                "Esc: Deselect all",
                "",
                "⚡ PERFORMANCE TEST:",
                "F5: Toggle Performance Lock",
                "  Locks drag → click safely to",
                "  select & test MIDI/OSC output",
            ]

        # Always show toggle option
        shortcuts.extend([
            "",
            "💡 DISPLAY CONTROLS:",
            "N: Toggle grid coordinate numbers",
            "G: Toggle grid snap",
            "[ / ]: Cycle snap grid preset (5/10/25/50/100)",
            "Right-click: Context menu",
            "",
            "⚡ F5: Performance Lock ON/OFF",
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
                elif s.startswith('Type:') or s.startswith('Position:') or s.startswith('Y-axis:') or s.startswith('X-axis:') or s.startswith('Z-axis:') or s.startswith('Button:') or s.startswith('Note pad:'):
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

    def _check_axis_gizmo_click(self, mx, my) -> bool:
        """Check if click landed on a clickable axis ball. Snap camera like Blender."""
        for bx, by, r, name in getattr(self, "_axis_gizmo_hits", []):
            dx = mx - bx
            dy = my - by
            if dx * dx + dy * dy <= r * r:
                if name == "X":
                    self.camera.yaw, self.camera.pitch = 90.0, 0.0
                elif name == "-X":
                    self.camera.yaw, self.camera.pitch = -90.0, 0.0
                elif name == "Y":
                    self.camera.yaw, self.camera.pitch = 0.0, 0.0
                elif name == "-Y":
                    self.camera.yaw, self.camera.pitch = 180.0, 0.0
                elif name == "Z":
                    self.camera.yaw, self.camera.pitch = 0.0, 89.0
                elif name == "-Z":
                    self.camera.yaw, self.camera.pitch = 0.0, -89.0
                self.camera.ortho = True
                self._show_status(f"View: {name} axis (ortho)")
                self.update()
                return True
        # Centre dot -> perspective home
        gx, gy = 60, self.height() - 60
        if (mx - gx) ** 2 + (my - gy) ** 2 <= 8 * 8:
            self.camera.ortho = False
            self.camera.yaw = -45.0
            self.camera.pitch = 30.0
            self._show_status("View: Perspective home")
            self.update()
            return True
        return False

    def _check_overlay_click(self, mx, my) -> bool:
        """Check if click is on an overlay button. Returns True if handled."""
        if self._check_axis_gizmo_click(mx, my):
            return True
        for rect, label in self._overlay_buttons:
            if rect.contains(float(mx), float(my)):
                if label == "Grid":
                    self._snap_grid_enabled = not self._snap_grid_enabled
                    self._show_status(f"Grid snap: {'ON' if self._snap_grid_enabled else 'OFF'}")
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
                elif label == "Home":
                    self.camera.ortho = False
                    self.camera.yaw = -45.0
                    self.camera.pitch = 30.0
                elif label == "Fit":
                    self._fit_all()
                elif label == "Z+":
                    self.camera.zoom(300)
                elif label == "Z-":
                    self.camera.zoom(-300)
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

                if (
                    not self.play_mode
                    and self._midi_quick_panel_rect is not None
                    and self._midi_quick_panel_rect.contains(mx, my)
                    and len(self.selected_elements) == 1
                    and isinstance(self.selected_elements[0], (MorphZone, HitZone))
                ):
                    self._run_midi_edit_dialog(self.selected_elements[0])
                    return

                if self.selected_elements and self._hit_move_gizmo(mx, my):
                    use_move = True
                    if self.play_mode and self.lock_move:
                        tgt = self._pick_play_ray_target(float(mx), float(my))
                        if isinstance(tgt, (MorphZone, HitZone)):
                            use_move = False
                    if use_move:
                        self._start_move_drag_from_gizmo()
                        return

                if self.play_mode and self.lock_move:
                    if self._try_play_mode_press(mx, my):
                        return

                # Check resize handles (works for single and multi-selection)
                if self.selected_elements and self._handle_screen_pos and self._show_scale_rotate_gizmo():
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
                if self.selected_elements and self._rot_ring_screen_points and self._show_scale_rotate_gizmo():
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
            if self.autofly_active:
                self.autofly_stop()
                self._show_status("Auto-fly stopped (manual camera)")
            if not self.lock_orbit:
                self.camera.orbit(dx, dy)
            else:
                self.camera.pan(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.MiddleButton:
            if self.autofly_active:
                self.autofly_stop()
                self._show_status("Auto-fly stopped (manual camera)")
            self.camera.pan(dx, dy)
            self.update()
        elif event.buttons() & Qt.MouseButton.LeftButton and self._play_morph_drag and self._play_morph_elem:
            self._update_morph_drag_from_screen(self._play_morph_elem, event.pos().x(), event.pos().y())
            self.update()
        elif (
            self.play_mode
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._play_hitzone_elem is not None
        ):
            hz = self._play_hitzone_elem
            mt = getattr(hz, "midi_message_type", "") or ""
            bh = getattr(hz, "behavior", "") or ""
            if _hitzone_is_cc(hz) and "Toggle" not in bh and hz.midi_cc_mappings:
                self._update_hitzone_cc_drag_from_screen(hz, event.pos().x(), event.pos().y())
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
        if event.button() == Qt.MouseButton.LeftButton and self._play_morph_drag:
            self._play_morph_drag = False
            self._play_morph_elem = None
            self.update()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._play_hitzone_elem is not None:
            hz = self._play_hitzone_elem
            self._play_hitzone_elem = None
            if _hitzone_is_note(hz) and hz.midi_note_mappings:
                m = hz.midi_note_mappings[0]
                self.play_perf_send.emit(
                    hz.unique_id,
                    {"type": "note_off", "note": m.note, "channel": m.channel, "velocity": 0},
                )
            elif _hitzone_is_cc(hz) and hz.midi_cc_mappings:
                m = hz.midi_cc_mappings[0]
                self.play_perf_send.emit(
                    hz.unique_id,
                    {
                        "type": "cc",
                        "cc": m.control,
                        "channel": m.channel,
                        "value": 0,
                        "axis": "hitzone",
                    },
                )
            self.update()
            return
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
            mx, my = event.pos().x(), event.pos().y()
            if (
                self.play_mode
                and self._midi_quick_panel_rect is not None
                and self._midi_quick_panel_rect.contains(mx, my)
                and len(self.selected_elements) == 1
                and isinstance(self.selected_elements[0], (MorphZone, HitZone))
            ):
                self._run_midi_edit_dialog(self.selected_elements[0])
                return
            self._show_context_menu(event.pos())

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if self.play_mode:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if self.autofly_active:
            self.autofly_stop()
            self._show_status("Auto-fly stopped (manual camera)")
        self.camera.zoom(event.angleDelta().y())
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if self.autofly_active and key == Qt.Key.Key_Escape:
            self.autofly_stop()
            self._show_status("Auto-fly stopped")
            self.update()
            return

        if key == Qt.Key.Key_Home:
            self._fit_all()
        elif key == Qt.Key.Key_Escape:
            if self.play_mode:
                self.play_mode_exit_requested.emit()
                return
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
        elif key == Qt.Key.Key_BracketLeft:
            self.cycle_snap_grid_preset(-1)
        elif key == Qt.Key.Key_BracketRight:
            self.cycle_snap_grid_preset(1)
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
                # Miss on empty — marquee (disabled in Desktop Play)
                self.selected_elements = []
                if self.play_mode:
                    self._show_status(
                        "Click a control to select — drag MorphZones to play (Desktop Play)"
                    )
                else:
                    self._marquee_active = True
                    self._marquee_start = QPoint(mx, my)
                    self._marquee_rect = None
                    self._show_status("Click and drag to marquee select")

        # Set up drag if we have a selection (unless move is locked)
        if self.selected_elements and best_elem is not None and not self.lock_move:
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

    def _run_midi_edit_dialog(self, elem) -> None:
        """Set morph MIDI targets (ch/CC/value) or output levels; HitZone note/CC routing."""
        changed = False
        if isinstance(elem, MorphZone):
            mz = elem
            mode_labels = (
                "MIDI targets (channel, CC number, value per axis)",
                "Morph output level only (0-127 position)",
            )
            mode, ok_mode = QInputDialog.getItem(
                self,
                "MorphZone MIDI",
                "What to edit:",
                mode_labels,
                0,
                False,
            )
            if not ok_mode:
                self.update()
                return
            if mode == mode_labels[0]:
                undo_changes = []
                axis_cfgs = []
                if mz.is_x_axis_enabled:
                    axis_cfgs.append(("X", "x_axis_cc_mappings"))
                if mz.is_y_axis_enabled:
                    axis_cfgs.append(("Y", "y_axis_cc_mappings"))
                if mz.is_z_axis_enabled:
                    axis_cfgs.append(("Z", "z_axis_cc_mappings"))
                if not axis_cfgs:
                    self._show_status("No morph axes enabled — enable X/Y/Z in properties first")
                    self.update()
                    return
                for axis_label, attr in axis_cfgs:
                    maps = getattr(mz, attr)
                    if not maps:
                        setattr(mz, attr, [MidiCCMapping()])
                        maps = getattr(mz, attr)
                    cm = maps[0]
                    old_maps = copy.deepcopy(maps)
                    ch0 = max(1, min(16, int(getattr(cm, "channel", 1) or 1)))
                    ch, ok_ch = QInputDialog.getInt(
                        self,
                        f"Morph {axis_label} — where MIDI goes",
                        "MIDI channel (1-16)",
                        ch0,
                        1,
                        16,
                    )
                    if not ok_ch:
                        self.update()
                        return
                    cc0 = max(0, min(127, int(cm.control)))
                    cc_num, ok_cc = QInputDialog.getInt(
                        self,
                        f"Morph {axis_label} — where MIDI goes",
                        "Controller number CC 0-127",
                        cc0,
                        0,
                        127,
                    )
                    if not ok_cc:
                        self.update()
                        return
                    val0 = max(0, min(127, int(cm.value)))
                    nv, ok_v = QInputDialog.getInt(
                        self,
                        f"Morph {axis_label} — where MIDI goes",
                        "CC value sent at this axis position (0-127)",
                        val0,
                        0,
                        127,
                    )
                    if not ok_v:
                        self.update()
                        return
                    if ch != cm.channel or cc_num != cm.control or nv != cm.value:
                        cm.channel = ch
                        cm.control = cc_num
                        cm.value = nv
                        changed = True
                        undo_changes.append(
                            (mz, attr, old_maps, copy.deepcopy(maps))
                        )
                if undo_changes:
                    self.midi_mappings_nudged.emit(undo_changes, "Set morph MIDI targets")
                if changed:
                    self._emit_morph_play_payloads(mz, None)
                    self.play_morph_interaction.emit(mz)
            else:
                cp = mz.control_position_normalized
                if mz.is_x_axis_enabled:
                    cur = int(round(max(0.0, min(1.0, cp.x)) * 127.0))
                    nv, ok = QInputDialog.getInt(self, "Morph X", "Output 0-127", cur, 0, 127)
                    if ok:
                        cp.x = nv / 127.0
                        changed = True
                if mz.is_y_axis_enabled:
                    cur = int(round(max(0.0, min(1.0, cp.y)) * 127.0))
                    nv, ok = QInputDialog.getInt(self, "Morph Y", "Output 0-127", cur, 0, 127)
                    if ok:
                        cp.y = nv / 127.0
                        changed = True
                if mz.is_z_axis_enabled:
                    cur = int(round(max(0.0, min(1.0, cp.z)) * 127.0))
                    nv, ok = QInputDialog.getInt(self, "Morph Z", "Output 0-127", cur, 0, 127)
                    if ok:
                        cp.z = nv / 127.0
                        changed = True
                if changed:
                    self._emit_morph_play_payloads(mz, None)
                    self.play_morph_interaction.emit(mz)
        elif isinstance(elem, HitZone):
            hz = elem
            if _hitzone_is_cc(hz) and hz.midi_cc_mappings:
                old = copy.deepcopy(hz.midi_cc_mappings)
                cm = hz.midi_cc_mappings[0]
                ch0 = max(1, min(16, int(getattr(cm, "channel", 1) or 1)))
                cc0, val0 = int(cm.control), int(cm.value)
                ch, ok_ch = QInputDialog.getInt(
                    self, "MIDI channel", "Channel 1-16", ch0, 1, 16
                )
                if not ok_ch:
                    self.update()
                    return
                cc_num, ok_cc = QInputDialog.getInt(
                    self, "CC number", "Controller 0-127", max(0, min(127, cc0)), 0, 127
                )
                if not ok_cc:
                    self.update()
                    return
                nv, ok_v = QInputDialog.getInt(
                    self, "CC value", "Value 0-127", max(0, min(127, val0)), 0, 127
                )
                if not ok_v:
                    self.update()
                    return
                if ch != cm.channel or cc_num != cm.control or nv != cm.value:
                    cm.channel = ch
                    cm.control = cc_num
                    cm.value = nv
                    changed = True
                    self.midi_mappings_nudged.emit(
                        [(hz, "midi_cc_mappings", old, copy.deepcopy(hz.midi_cc_mappings))],
                        "Set MIDI CC mapping",
                    )
                    if self.play_mode:
                        self.play_perf_send.emit(
                            hz.unique_id,
                            {
                                "type": "cc",
                                "cc": cm.control,
                                "channel": cm.channel,
                                "value": nv,
                                "axis": "hitzone",
                            },
                        )
            elif _hitzone_is_note(hz) and hz.midi_note_mappings:
                old = copy.deepcopy(hz.midi_note_mappings)
                nm = hz.midi_note_mappings[0]
                ch0 = max(1, min(16, int(getattr(nm, "channel", 1) or 1)))
                ch, ok_ch = QInputDialog.getInt(
                    self, "MIDI channel", "Channel 1-16", ch0, 1, 16
                )
                if not ok_ch:
                    self.update()
                    return
                nv, ok_n = QInputDialog.getInt(
                    self, "MIDI note", "Note number 0-127", int(nm.note), 0, 127
                )
                if not ok_n:
                    self.update()
                    return
                if ch != nm.channel or nv != nm.note:
                    nm.channel = ch
                    nm.note = nv
                    changed = True
                    self.midi_mappings_nudged.emit(
                        [(hz, "midi_note_mappings", old, copy.deepcopy(hz.midi_note_mappings))],
                        "Set MIDI note",
                    )
        if changed:
            self._emit_selection()
            self._show_status("Updated MIDI routing / output")
        self.update()

    def _draw_midi_quick_panel(self, painter: QPainter) -> None:
        self._midi_quick_panel_rect = None
        if getattr(self, "view_label", "Perspective") != "Perspective":
            return
        if len(self.selected_elements) != 1:
            return
        elem = self.selected_elements[0]
        if not isinstance(elem, (MorphZone, HitZone)):
            return

        w = self.width()
        h = self.height()
        margin = 12
        if self.play_mode:
            lines = ["MIDI / output — right-click to edit"]
        else:
            lines = ["MIDI / output — click to edit"]
        if isinstance(elem, MorphZone):
            cp = elem.control_position_normalized
            if elem.is_x_axis_enabled:
                if elem.x_axis_cc_mappings:
                    m = elem.x_axis_cc_mappings[0]
                    lines.append(
                        f"X: ch{m.channel} CC{m.control} val{m.value}  pos {int(round(cp.x * 127))}"
                    )
                else:
                    lines.append(f"X out: {int(round(cp.x * 127))}")
            if elem.is_y_axis_enabled:
                if elem.y_axis_cc_mappings:
                    m = elem.y_axis_cc_mappings[0]
                    lines.append(
                        f"Y: ch{m.channel} CC{m.control} val{m.value}  pos {int(round(cp.y * 127))}"
                    )
                else:
                    lines.append(f"Y out: {int(round(cp.y * 127))}")
            if elem.is_z_axis_enabled:
                if elem.z_axis_cc_mappings:
                    m = elem.z_axis_cc_mappings[0]
                    lines.append(
                        f"Z: ch{m.channel} CC{m.control} val{m.value}  pos {int(round(cp.z * 127))}"
                    )
                else:
                    lines.append(f"Z out: {int(round(cp.z * 127))}")
        else:
            hz = elem
            if hz.midi_note_mappings:
                m = hz.midi_note_mappings[0]
                lines.append(f"Note: {m.note}  ch {m.channel}")
            if hz.midi_cc_mappings:
                m = hz.midi_cc_mappings[0]
                lines.append(f"CC{m.control}: {m.value}  ch {m.channel}")

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = max(fm.horizontalAdvance(s) for s in lines)
        text_h = fm.height()
        pad = 10
        box_w = text_w + pad * 2
        box_h = len(lines) * text_h + pad * 2
        x = margin
        y = h - margin - box_h
        self._midi_quick_panel_rect = QRect(x, y, box_w, box_h)

        path = QPainterPath()
        path.addRoundedRect(float(x), float(y), float(box_w), float(box_h), 8, 8)
        painter.fillPath(path, QColor(8, 22, 18, 220))
        painter.setPen(QPen(QColor(0, 255, 200, 200), 1.5))
        painter.drawPath(path)
        painter.setPen(QColor(200, 255, 240, 255))
        ty = y + pad + fm.ascent()
        for line in lines:
            painter.drawText(x + pad, ty, line)
            ty += text_h

    def _show_play_context_menu(self, pos):
        """Lightweight add / selection actions while in Desktop Play."""
        menu = QMenu(self)
        qm = menu.addMenu("Quick add MorphZone")
        for label, key in (
            ("Morph X (1 axis)", "MorphZone_X"),
            ("Morph XY", "MorphZone_XY"),
            ("Morph XYZ", "MorphZone_XYZ"),
        ):
            act = qm.addAction(label)
            act.setData(key)
        qh = menu.addMenu("Quick add HitZone")
        for label, key in (
            ("Note + Hold", "HitZone_NoteHold"),
            ("Note + Toggle", "HitZone_NoteToggle"),
            ("CC + Hold", "HitZone_CCHold"),
        ):
            act = qh.addAction(label)
            act.setData(key)
        menu.addSeparator()
        dup_action = None
        del_action = None
        midi_edit_action = None
        if len(self.selected_elements) == 1:
            e0 = self.selected_elements[0]
            if isinstance(e0, (MorphZone, HitZone)):
                midi_edit_action = menu.addAction("Edit MIDI / output levels…")
                menu.addSeparator()
        if len(self.selected_elements) > 1:
            n = len(self.selected_elements)
            dup_action = menu.addAction(f"Duplicate {n} elements")
            del_action = menu.addAction(f"Delete {n} elements")
        elif len(self.selected_elements) == 1:
            elem = self.selected_elements[0]
            dup_action = menu.addAction(f"Duplicate {elem.unique_id}")
            del_action = menu.addAction(f"Delete {elem.unique_id}")
        menu.addSeparator()
        exit_a = menu.addAction("Exit Desktop Play (Esc)")
        action = menu.exec(self.mapToGlobal(pos))
        if action is None:
            return
        if action == midi_edit_action and len(self.selected_elements) == 1:
            self._run_midi_edit_dialog(self.selected_elements[0])
            return
        if action == exit_a:
            self.play_mode_exit_requested.emit()
            return
        data = action.data()
        if isinstance(data, str) and (
            data.startswith("MorphZone_") or data.startswith("HitZone_")
        ):
            self.add_element_requested.emit(data)
            return
        if action == dup_action:
            for elem in list(self.selected_elements):
                self.duplicate_element_requested.emit(elem)
        elif action == del_action:
            if len(self.selected_elements) == 1:
                self.delete_element_requested.emit(self.selected_elements[0])
            else:
                self.delete_elements_requested.emit(list(self.selected_elements))

    def _show_context_menu(self, pos):
        if self.play_mode:
            self._show_play_context_menu(pos)
            return
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
        midi_edit_action = None

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
            if isinstance(elem, (MorphZone, HitZone)):
                midi_edit_action = menu.addAction("Edit MIDI / output levels…")

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
        elif action is not None and action == midi_edit_action:
            if len(self.selected_elements) == 1:
                self._run_midi_edit_dialog(self.selected_elements[0])

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

    def _morph_local_hit_point(self, mz: MorphZone, origin_w: tuple, dir_w: tuple):
        """Return (px,py,pz, hx,hy,hz) local hit on morph box, or None."""
        t = mz.transform.translation
        q = mz.transform.rotation
        s = mz.transform.scale
        hx, hy, hz = _morph_local_half_extents(mz, s)
        tr = _ray_obb_intersect(mz, hx, hy, hz, origin_w, dir_w)
        if tr is None:
            return None
        vx = origin_w[0] - t.x
        vy = origin_w[1] - t.y
        vz = origin_w[2] - t.z
        lo = _quat_rotate_vec_inv(q, vx, vy, vz)
        ld = _quat_rotate_vec_inv(q, dir_w[0], dir_w[1], dir_w[2])
        ln = math.sqrt(ld[0] * ld[0] + ld[1] * ld[1] + ld[2] * ld[2])
        if ln < 1e-12:
            return None
        ld = (ld[0] / ln, ld[1] / ln, ld[2] / ln)
        px = lo[0] + tr * ld[0]
        py = lo[1] + tr * ld[1]
        pz = lo[2] + tr * ld[2]
        return (px, py, pz, hx, hy, hz)

    def _pick_play_ray_target(self, mx: float, my: float):
        """Return closest MorphZone or HitZone under ray, or None."""
        ray = self.camera.screen_to_world_ray(mx, my, self.width(), self.height())
        if not ray:
            return None
        ow, dw = ray
        best = None
        best_t = None
        for elem in self._active_workspace_elements():
            if isinstance(elem, MorphZone):
                s = elem.transform.scale
                hx, hy, hz = _morph_local_half_extents(elem, s)
                tr = _ray_obb_intersect(elem, hx, hy, hz, ow, dw)
            elif isinstance(elem, HitZone):
                s = elem.transform.scale
                hx, hy, hz = _hitzone_visual_half_extents(elem, s)
                tr = _ray_obb_intersect(elem, hx, hy, hz, ow, dw)
            else:
                continue
            if tr is None:
                continue
            if best_t is None or tr < best_t:
                best_t = tr
                best = elem
        return best

    def _play_drag_axis_mask(self):
        """Desktop Play: optional subset of axes while dragging (Alt=X, Ctrl=Y, Shift=Z)."""
        if not self.play_mode:
            return None
        m = QApplication.keyboardModifiers()
        axes = set()
        if m & Qt.KeyboardModifier.AltModifier:
            axes.add("X")
        if m & Qt.KeyboardModifier.ControlModifier:
            axes.add("Y")
        if m & Qt.KeyboardModifier.ShiftModifier:
            axes.add("Z")
        return axes if axes else None

    def _emit_morph_play_payloads(self, mz: MorphZone, axes_filter=None):
        cp = mz.control_position_normalized
        uid = mz.unique_id
        active = axes_filter

        def _emit_axis(axis_letter: str, enabled: bool, mappings, norm_val: float) -> None:
            if not enabled or not mappings:
                return
            if active is not None and axis_letter not in active:
                return
            m = mappings[0]
            val = int(round(max(0.0, min(1.0, norm_val)) * 127.0))
            self.play_perf_send.emit(
                uid,
                {"type": "cc", "cc": m.control, "channel": m.channel, "value": val, "axis": axis_letter},
            )

        _emit_axis("X", mz.is_x_axis_enabled, mz.x_axis_cc_mappings, cp.x)
        _emit_axis("Y", mz.is_y_axis_enabled, mz.y_axis_cc_mappings, cp.y)
        _emit_axis("Z", mz.is_z_axis_enabled, mz.z_axis_cc_mappings, cp.z)

    def _camera_forward_unit(self) -> tuple:
        fx, fy, fz = self.camera.forward_vector()
        ln = math.sqrt(fx * fx + fy * fy + fz * fz)
        if ln < 1e-12:
            return (0.0, 0.0, -1.0)
        return (fx / ln, fy / ln, fz / ln)

    def _update_morph_drag_from_screen(self, mz: MorphZone, mx: float, my: float):
        ray = self.camera.screen_to_world_ray(mx, my, self.width(), self.height())
        if not ray:
            return
        ow, dw = ray
        s = mz.transform.scale
        hx, hy, hz = _morph_local_half_extents(mz, s)
        hit = self._morph_local_hit_point(mz, ow, dw)
        if hit:
            px, py, pz, hx, hy, hz = hit
        else:
            t = mz.transform.translation
            pw = (t.x, t.y, t.z)
            n = self._camera_forward_unit()
            wp = _ray_plane_intersect_point(ow, dw, pw, n)
            if wp is None:
                return
            q = mz.transform.rotation
            vx, vy, vz = wp[0] - t.x, wp[1] - t.y, wp[2] - t.z
            lo = _quat_rotate_vec_inv(q, vx, vy, vz)
            px = max(-hx, min(hx, lo[0]))
            py = max(-hy, min(hy, lo[1]))
            pz = max(-hz, min(hz, lo[2]))
        cp = mz.control_position_normalized
        mask = self._play_drag_axis_mask()
        move_x = mz.is_x_axis_enabled and (mask is None or "X" in mask)
        move_y = mz.is_y_axis_enabled and (mask is None or "Y" in mask)
        move_z = mz.is_z_axis_enabled and (mask is None or "Z" in mask)
        if move_x:
            cp.x = max(0.0, min(1.0, (px + hx) / (2.0 * hx)))
        if move_y:
            cp.y = max(0.0, min(1.0, (py + hy) / (2.0 * hy)))
        if move_z:
            cp.z = max(0.0, min(1.0, (pz + hz) / (2.0 * hz)))
        self._emit_morph_play_payloads(mz, mask)
        self.play_morph_interaction.emit(mz)
        self.update()

    def _update_hitzone_cc_drag_from_screen(self, hz: HitZone, mx: float, my: float):
        """Sticky CC fader: ray-box hit, else plane through pad so cursor need not stay on mesh."""
        if not hz.midi_cc_mappings:
            return
        ray = self.camera.screen_to_world_ray(mx, my, self.width(), self.height())
        if not ray:
            return
        ow, dw = ray
        s = hz.transform.scale
        hx, hy, hzz = _hitzone_visual_half_extents(hz, s)
        tr = _ray_obb_intersect(hz, hx, hy, hzz, ow, dw)
        t = hz.transform.translation
        q = hz.transform.rotation
        if tr is not None:
            hw = (ow[0] + tr * dw[0], ow[1] + tr * dw[1], ow[2] + tr * dw[2])
            vx = hw[0] - t.x
            vy = hw[1] - t.y
            vz = hw[2] - t.z
            lo = _quat_rotate_vec_inv(q, vx, vy, vz)
            px, py, pz = lo[0], lo[1], lo[2]
        else:
            n = self._camera_forward_unit()
            wp = _ray_plane_intersect_point(ow, dw, (t.x, t.y, t.z), n)
            if wp is None:
                return
            vx = wp[0] - t.x
            vy = wp[1] - t.y
            vz = wp[2] - t.z
            lo = _quat_rotate_vec_inv(q, vx, vy, vz)
            px = max(-hx, min(hx, lo[0]))
            py = max(-hy, min(hy, lo[1]))
            pz = max(-hzz, min(hzz, lo[2]))
        norm = max(0.0, min(1.0, (px + hx) / (2.0 * hx)))
        val = int(round(norm * 127.0))
        m = hz.midi_cc_mappings[0]
        m.value = val
        self.play_perf_send.emit(
            hz.unique_id,
            {"type": "cc", "cc": m.control, "channel": m.channel, "value": val, "axis": "hitzone"},
        )
        self.update()

    def _try_play_mode_press(self, mx: int, my: int) -> bool:
        """Handle Desktop Play left-click: morph drag or hitzone note. Returns True if handled."""
        if not self.play_mode or not self.lock_move:
            return False
        target = self._pick_play_ray_target(float(mx), float(my))
        if isinstance(target, MorphZone):
            self.selected_elements = [target]
            self._play_morph_drag = True
            self._play_morph_elem = target
            self._play_hitzone_elem = None
            self._update_morph_drag_from_screen(target, mx, my)
            self._emit_selection()
            self._show_status(
                f"Play: {target.display_name or target.unique_id}  |  "
                "Alt=X only, Ctrl=Y, Shift=Z (combine keys for 2D/1D); no keys = all axes"
            )
            return True
        if isinstance(target, HitZone):
            hz = target
            self.selected_elements = [hz]
            self._play_morph_drag = False
            self._play_morph_elem = None

            if _hitzone_is_note(hz) and hz.behavior == "EHitZoneBehavior::Toggle":
                hz.toggle_state = not hz.toggle_state
                if hz.midi_note_mappings:
                    m = hz.midi_note_mappings[0]
                    vel = int(hz.fixed_midi_velocity_output or 127)
                    if hz.toggle_state:
                        self.play_perf_send.emit(
                            hz.unique_id,
                            {"type": "note_on", "note": m.note, "channel": m.channel, "velocity": vel},
                        )
                    else:
                        self.play_perf_send.emit(
                            hz.unique_id,
                            {"type": "note_off", "note": m.note, "channel": m.channel, "velocity": 0},
                        )
                self._play_hitzone_elem = None
                self._emit_selection()
                self._show_status(f"Play toggle: {hz.display_name or hz.unique_id}")
                return True

            if _hitzone_is_cc(hz) and hz.midi_cc_mappings:
                m = hz.midi_cc_mappings[0]
                if hz.behavior == "EHitZoneBehavior::Toggle":
                    hz.toggle_state = not hz.toggle_state
                    val = 127 if hz.toggle_state else 0
                    self.play_perf_send.emit(
                        hz.unique_id,
                        {
                            "type": "cc",
                            "cc": m.control,
                            "channel": m.channel,
                            "value": val,
                            "axis": "hitzone",
                        },
                    )
                    self._play_hitzone_elem = None
                else:
                    self._play_hitzone_elem = hz
                    self.play_perf_send.emit(
                        hz.unique_id,
                        {
                            "type": "cc",
                            "cc": m.control,
                            "channel": m.channel,
                            "value": 127,
                            "axis": "hitzone",
                        },
                    )
                self._emit_selection()
                self._show_status(f"Play: {hz.display_name or hz.unique_id}")
                return True

            self._play_hitzone_elem = hz
            if hz.midi_note_mappings:
                m = hz.midi_note_mappings[0]
                vel = int(hz.fixed_midi_velocity_output or 127)
                self.play_perf_send.emit(
                    hz.unique_id,
                    {"type": "note_on", "note": m.note, "channel": m.channel, "velocity": vel},
                )
            self._emit_selection()
            self._show_status(f"Play: {hz.display_name or hz.unique_id}")
            return True
        self.selected_elements = []
        self._emit_selection()
        self._show_status("Desktop Play — click a MorphZone or HitZone")
        self.update()
        return True

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


def _quat_rotate_vec(q: Quat, vx: float, vy: float, vz: float) -> tuple:
    """Rotate vector v by quaternion q."""
    qx, qy, qz, qw = q.x, q.y, q.z, q.w
    tx = 2 * (qy * vz - qz * vy)
    ty = 2 * (qz * vx - qx * vz)
    tz = 2 * (qx * vy - qy * vx)
    cx = qy * tz - qz * ty
    cy = qz * tx - qx * tz
    cz = qx * ty - qy * tx
    return (vx + qw * tx + cx, vy + qw * ty + cy, vz + qw * tz + cz)


def _quat_rotate_vec_inv(q: Quat, vx: float, vy: float, vz: float) -> tuple:
    """Rotate by inverse quaternion (world offset/direction -> local)."""
    qi = Quat(-q.x, -q.y, -q.z, q.w)
    return _quat_rotate_vec(qi, vx, vy, vz)


def _ray_aabb_intersect(lo, ld, bmin, bmax) -> Optional[float]:
    """Ray vs axis-aligned box in local space. lo, ld: 3-tuples; returns t or None."""
    tmin = -1e30
    tmax = 1e30
    for i in range(3):
        if abs(ld[i]) < 1e-12:
            if lo[i] < bmin[i] or lo[i] > bmax[i]:
                return None
            continue
        inv = 1.0 / ld[i]
        t0 = (bmin[i] - lo[i]) * inv
        t1 = (bmax[i] - lo[i]) * inv
        if t0 > t1:
            t0, t1 = t1, t0
        tmin = max(tmin, t0)
        tmax = min(tmax, t1)
        if tmin > tmax:
            return None
    if tmax < 0:
        return None
    if tmin >= 0:
        return tmin
    return 0.0


def _morph_local_half_extents(mz: MorphZone, s) -> tuple:
    return _morph_visual_half_extents(mz, s)


def _ray_obb_intersect(elem, hx: float, hy: float, hz: float, origin_w: tuple, dir_w: tuple) -> Optional[float]:
    """Ray vs OBB; box centered at element origin in local space with half-extents hx,hy,hz."""
    t = elem.transform.translation
    q = elem.transform.rotation
    vx = origin_w[0] - t.x
    vy = origin_w[1] - t.y
    vz = origin_w[2] - t.z
    lo = _quat_rotate_vec_inv(q, vx, vy, vz)
    ld = _quat_rotate_vec_inv(q, dir_w[0], dir_w[1], dir_w[2])
    ln = math.sqrt(ld[0] * ld[0] + ld[1] * ld[1] + ld[2] * ld[2])
    if ln < 1e-12:
        return None
    ld = (ld[0] / ln, ld[1] / ln, ld[2] / ln)
    return _ray_aabb_intersect(lo, ld, (-hx, -hy, -hz), (hx, hy, hz))


def _ray_plane_intersect_point(
    ow: tuple, dw: tuple, plane_point: tuple, plane_normal_unit: tuple
) -> Optional[tuple]:
    """Return world hit point of ray origin+dir with plane, or None."""
    nx, ny, nz = plane_normal_unit
    denom = dw[0] * nx + dw[1] * ny + dw[2] * nz
    if abs(denom) < 1e-10:
        return None
    t = (
        (plane_point[0] - ow[0]) * nx
        + (plane_point[1] - ow[1]) * ny
        + (plane_point[2] - ow[2]) * nz
    ) / denom
    if t < 0:
        return None
    return (ow[0] + t * dw[0], ow[1] + t * dw[1], ow[2] + t * dw[2])


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
