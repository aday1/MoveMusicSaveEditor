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

import bpy
import math

# ---------- setup ----------
# Remove default objects so we start clean
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for col in list(bpy.data.collections):
    bpy.data.collections.remove(col)

# Set units to metric / millimetres → metres handled by 0.01 scale
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.scale_length = 1.0

# ---- Collections (one per Workspace) ----
col_ws_0 = bpy.data.collections.new("Main [ACTIVE]")
bpy.context.scene.collection.children.link(col_ws_0)

col_unassigned = bpy.data.collections.new("(Unassigned)")
bpy.context.scene.collection.children.link(col_unassigned)

# ---- Material helper ----
def _make_mat(name, r, g, b, a=1.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes['Principled BSDF']
    bsdf.inputs['Base Color'].default_value = (r, g, b, a)
    bsdf.inputs['Alpha'].default_value = a
    if a < 1.0:
        mat.blend_method = 'BLEND'
    return mat

# ---- Scene elements ----
# [HitZone] HZ1
m_0 = _make_mat("mat_HZ1", 0.0000, 0.3700, 0.8000, 1.0000)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0.00000, 0.00000, 0.00000))
obj_0 = bpy.context.active_object
obj_0.name = "HZ1"
obj_0.scale = (0.15000, 0.15000, 0.02500)
obj_0.rotation_euler = (0.000000, 0.000000, 0.000000)
obj_0.data.materials.append(m_0)
# Link to workspace collection
bpy.context.scene.collection.objects.unlink(obj_0)
col_ws_0.objects.link(obj_0)

# ---- VR Camera (placed at saved user head position) ----
cam_data = bpy.data.cameras.new(name='VR_Camera')
cam_data.lens = 24  # ~80° FOV — typical VR headset
cam_data.clip_start = 0.01
cam_data.clip_end = 500.0
cam_obj = bpy.data.objects.new('VR_Camera', cam_data)
cam_obj.location = (0.00000, 0.00000, 0.00000)
bpy.context.scene.collection.objects.link(cam_obj)
bpy.context.scene.camera = cam_obj

# Point camera at scene centre using a Track-To constraint
empty = bpy.data.objects.new('Scene_Centre', None)
empty.location = (0.00000, 0.00000, 0.00000)
bpy.context.scene.collection.objects.link(empty)
con = cam_obj.constraints.new(type='TRACK_TO')
con.target = empty
con.track_axis = 'TRACK_NEGATIVE_Z'
con.up_axis = 'UP_Y'

# ---- Switch to Material Preview so colours are visible immediately ----
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                space.shading.type = 'MATERIAL'
                space.shading.use_scene_lights = True
        break

print('MoveMusic scene imported successfully.')
print(f'1 elements, 1 workspace(s).')
