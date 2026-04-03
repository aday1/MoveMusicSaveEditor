"""
Blender Python Script Export for MoveMusic Save Editor.

Generates a .py file that, when run inside Blender's Scripting workspace,
recreates the full MoveMusic scene as coloured box meshes organised into
Collections (one per Workspace).  A VR-perspective camera is placed at the
saved user_location and pointed at the scene centre so you can immediately
start camera-tracking or rendering.

No external dependencies — pure stdlib + model only.
"""

from __future__ import annotations

import math
import re
from typing import Tuple

from model import Project, HitZone, MorphZone, TextLabel, GroupIE


# ---------------------------------------------------------------------------
# Helpers (same logic as export3d.py)
# ---------------------------------------------------------------------------

def _element_box_scale(elem) -> Tuple[float, float, float]:
    s = elem.transform.scale
    if isinstance(elem, HitZone):
        return (30.0 * s.x, 30.0 * s.y, 5.0 * s.z)
    elif isinstance(elem, MorphZone):
        ext = elem.mesh_extent
        return (ext.x * s.x, ext.y * s.y, ext.z * s.z)
    elif isinstance(elem, TextLabel):
        return (20.0 * s.x, 16.0 * s.y, 1.0)
    elif isinstance(elem, GroupIE):
        bb = elem.bounding_box
        return (
            (bb.max.x - bb.min.x) * s.x,
            (bb.max.y - bb.min.y) * s.y,
            (bb.max.z - bb.min.z) * s.z,
        )
    return (10.0 * s.x, 10.0 * s.y, 10.0 * s.z)


def _safe_name(name: str) -> str:
    """Sanitise a string for use as a Blender object/material name."""
    name = re.sub(r'[^\w\s\-.]', '_', name)
    return name[:63] or "Element"


def _quat_to_euler_z_up(qx: float, qy: float, qz: float, qw: float) -> Tuple[float, float, float]:
    """Convert a quaternion to ZYX Euler angles (radians, Z-up)."""
    # Roll (X)
    sinr_cosp = 2.0 * (qw * qx + qy * qz)
    cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    # Pitch (Y)
    sinp = 2.0 * (qw * qy - qz * qx)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)
    # Yaw (Z)
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return (roll, pitch, yaw)


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------

BLENDER_SCALE = 0.01   # 1 MoveMusic unit = 1 cm → 0.01 m in Blender metres

TIP_COMMENT = '''\
# =============================================================================
#  HOW TO USE THIS SCRIPT IN BLENDER
# =============================================================================
#
#  1. Open Blender and switch to the "Scripting" workspace (top tab bar).
#  2. Click "Open" in the Text Editor and select this .py file,
#     OR paste the entire contents into a new text block.
#  3. Click "Run Script" (▶ button, or Alt+P).
#  4. Your MoveMusic scene will appear in the 3D viewport.
#
#  CAMERA TRACKING WORKFLOW
#  ------------------------
#  • A camera named "VR_Camera" is created at your saved head position in
#    MoveMusic, looking towards the centre of the scene.
#  • To use it for camera tracking / matchmoving:
#      a. Go to the "Movie Clip Editor", load your video footage.
#      b. Run the motion tracker (Tracking > Solve Camera).
#      c. In the 3D viewport, select the VR_Camera and apply the solved
#         camera motion: Clip > Setup Tracking Scene.
#      d. The MoveMusic boxes act as reference geometry when you align the
#         solved camera to the real-world VR stage.
#
#  SCALE NOTE
#  ----------
#  All coordinates are converted from MoveMusic units (1 unit = 1 cm) to
#  Blender metres (1 m = 100 cm) by multiplying by 0.01.
#  The scene uses Z-up orientation to match Blender's default.
#
#  COLLECTIONS
#  -----------
#  Each MoveMusic Workspace becomes a Blender Collection so you can toggle
#  workspace visibility independently.
#
#  MATERIALS
#  ---------
#  Each element gets a Principled BSDF material with its MoveMusic colour.
#  Alpha < 1.0 uses "Alpha Blend" mode so transparency renders correctly.
# =============================================================================
'''


def export_blend_script(project: Project, filepath: str) -> None:
    """
    Write a Blender Python script (.py) to *filepath* that recreates the
    MoveMusic project as coloured boxes with a positioned VR camera.
    """
    sc = BLENDER_SCALE
    lines: list[str] = []

    def w(*args):
        lines.append("".join(str(a) for a in args) + "\n")

    # --- Header / tip ---
    w(TIP_COMMENT)
    w("import bpy")
    w("import math")
    w()
    w("# ---------- setup ----------")
    w("# Remove default objects so we start clean")
    w("bpy.ops.object.select_all(action='SELECT')")
    w("bpy.ops.object.delete(use_global=False)")
    w("for col in list(bpy.data.collections):")
    w("    bpy.data.collections.remove(col)")
    w()

    # --- Unit scale ---
    w("# Set units to metric / millimetres → metres handled by 0.01 scale")
    w("bpy.context.scene.unit_settings.system = 'METRIC'")
    w("bpy.context.scene.unit_settings.scale_length = 1.0")
    w()

    # --- Workspace collections ---
    w("# ---- Collections (one per Workspace) ----")
    ws_collection_vars: dict[str, str] = {}
    for wi, ws in enumerate(project.workspaces):
        var = f"col_ws_{wi}"
        ws_collection_vars[ws.unique_id] = var
        safe = _safe_name(ws.display_name or ws.unique_id)
        active = " [ACTIVE]" if wi == project.active_workspace_index else ""
        w(f'{var} = bpy.data.collections.new("{safe}{active}")')
        w(f"bpy.context.scene.collection.children.link({var})")
    w()

    # Fallback collection for elements not in any workspace
    w('col_unassigned = bpy.data.collections.new("(Unassigned)")')
    w("bpy.context.scene.collection.children.link(col_unassigned)")
    w()

    # Build element → collection map
    elem_ws_map: dict[str, str] = {}
    for ws in project.workspaces:
        for eid in ws.element_ids:
            elem_ws_map[eid] = ws.unique_id

    # --- Material helper ---
    w("# ---- Material helper ----")
    w("def _make_mat(name, r, g, b, a=1.0):")
    w("    mat = bpy.data.materials.new(name=name)")
    w("    mat.use_nodes = True")
    w("    bsdf = mat.node_tree.nodes['Principled BSDF']")
    w("    bsdf.inputs['Base Color'].default_value = (r, g, b, a)")
    w("    bsdf.inputs['Alpha'].default_value = a")
    w("    if a < 1.0:")
    w("        mat.blend_method = 'BLEND'")
    w("    return mat")
    w()

    # --- Elements ---
    w("# ---- Scene elements ----")
    for i, elem in enumerate(project.elements):
        p = elem.transform.translation
        sx, sy, sz = _element_box_scale(elem)
        rot = elem.transform.rotation
        rx, ry, rz = _quat_to_euler_z_up(rot.x, rot.y, rot.z, rot.w)
        c = elem.color
        etype = type(elem).__name__
        display = elem.display_name or elem.unique_id
        safe_display = _safe_name(display)

        # Blender coordinates (cm → m, Z-up preserved)
        bx = p.x * sc
        by = p.y * sc
        bz = p.z * sc
        bsx = sx * sc
        bsy = sy * sc
        bsz = sz * sc

        w(f"# [{etype}] {display}")
        mat_name = _safe_name(f"mat_{safe_display}"[:60])
        w(f'm_{i} = _make_mat("{mat_name}", {c.r:.4f}, {c.g:.4f}, {c.b:.4f}, {c.a:.4f})')
        w(f"bpy.ops.mesh.primitive_cube_add(size=1, location=({bx:.5f}, {by:.5f}, {bz:.5f}))")
        w(f"obj_{i} = bpy.context.active_object")
        w(f'obj_{i}.name = "{safe_display}"')
        w(f"obj_{i}.scale = ({bsx:.5f}, {bsy:.5f}, {bsz:.5f})")
        w(f"obj_{i}.rotation_euler = ({rx:.6f}, {ry:.6f}, {rz:.6f})")
        w(f"obj_{i}.data.materials.append(m_{i})")

        col_var = ws_collection_vars.get(elem_ws_map.get(elem.unique_id, ""), "col_unassigned")
        w(f"# Link to workspace collection")
        w(f"bpy.context.scene.collection.objects.unlink(obj_{i})")
        w(f"{col_var}.objects.link(obj_{i})")
        w()

    # --- VR Camera ---
    w("# ---- VR Camera (placed at saved user head position) ----")
    ul = project.user_location
    cam_x, cam_y, cam_z = ul.x * sc, ul.y * sc, ul.z * sc

    # Scene centre (average of all element positions)
    if project.elements:
        cx = sum(e.transform.translation.x for e in project.elements) / len(project.elements) * sc
        cy = sum(e.transform.translation.y for e in project.elements) / len(project.elements) * sc
        cz = sum(e.transform.translation.z for e in project.elements) / len(project.elements) * sc
    else:
        cx, cy, cz = 0.0, 0.0, 0.0

    w("cam_data = bpy.data.cameras.new(name='VR_Camera')")
    w("cam_data.lens = 24  # ~80° FOV — typical VR headset")
    w("cam_data.clip_start = 0.01")
    w("cam_data.clip_end = 500.0")
    w(f"cam_obj = bpy.data.objects.new('VR_Camera', cam_data)")
    w(f"cam_obj.location = ({cam_x:.5f}, {cam_y:.5f}, {cam_z:.5f})")
    w(f"bpy.context.scene.collection.objects.link(cam_obj)")
    w(f"bpy.context.scene.camera = cam_obj")
    w()
    w("# Point camera at scene centre using a Track-To constraint")
    w("empty = bpy.data.objects.new('Scene_Centre', None)")
    w(f"empty.location = ({cx:.5f}, {cy:.5f}, {cz:.5f})")
    w("bpy.context.scene.collection.objects.link(empty)")
    w("con = cam_obj.constraints.new(type='TRACK_TO')")
    w("con.target = empty")
    w("con.track_axis = 'TRACK_NEGATIVE_Z'")
    w("con.up_axis = 'UP_Y'")
    w()

    # --- Viewport shading ---
    w("# ---- Switch to Material Preview so colours are visible immediately ----")
    w("for area in bpy.context.screen.areas:")
    w("    if area.type == 'VIEW_3D':")
    w("        for space in area.spaces:")
    w("            if space.type == 'VIEW_3D':")
    w("                space.shading.type = 'MATERIAL'")
    w("                space.shading.use_scene_lights = True")
    w("        break")
    w()
    w("print('MoveMusic scene imported successfully.')")
    w(f"print(f'{len(project.elements)} elements, {len(project.workspaces)} workspace(s).')")

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
