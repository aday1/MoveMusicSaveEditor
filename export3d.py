"""
3D scene export for the MoveMusic editor.

Exports project elements as colored boxes to:
- Wavefront OBJ + MTL (universal, no dependencies)
- glTF 2.0 Binary (.glb) for AR/WebXR (no dependencies beyond stdlib)
"""

from __future__ import annotations

import json
import math
import struct
from typing import List, Tuple

from model import Project, HitZone, MorphZone, TextLabel, GroupIE


# ---------------------------------------------------------------------------
# Shared geometry: unit box centered at origin
# ---------------------------------------------------------------------------

# 8 vertices of a unit cube [-0.5, 0.5]
_BOX_VERTICES = [
    (-0.5, -0.5, -0.5), ( 0.5, -0.5, -0.5), ( 0.5,  0.5, -0.5), (-0.5,  0.5, -0.5),
    (-0.5, -0.5,  0.5), ( 0.5, -0.5,  0.5), ( 0.5,  0.5,  0.5), (-0.5,  0.5,  0.5),
]

# 12 triangles (36 indices)
_BOX_INDICES = [
    0,1,2, 2,3,0,  # -Z face
    4,6,5, 4,7,6,  # +Z face
    0,4,5, 5,1,0,  # -Y face
    2,6,7, 7,3,2,  # +Y face
    0,3,7, 7,4,0,  # -X face
    1,5,6, 6,2,1,  # +X face
]

# Per-face normals (6 faces, 2 triangles each)
_BOX_NORMALS_PER_TRIANGLE = [
    (0,0,-1), (0,0,-1),
    (0,0,1),  (0,0,1),
    (0,-1,0), (0,-1,0),
    (0,1,0),  (0,1,0),
    (-1,0,0), (-1,0,0),
    (1,0,0),  (1,0,0),
]


def _element_box_scale(elem) -> Tuple[float, float, float]:
    """Return the world-space box dimensions for an element."""
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


# ---------------------------------------------------------------------------
# Wavefront OBJ export
# ---------------------------------------------------------------------------

def export_obj(project: Project, filepath: str):
    """Export scene as Wavefront OBJ + MTL files.

    Each element becomes a colored box at its world position and scale.
    """
    mtl_path = filepath.rsplit('.', 1)[0] + '.mtl'
    mtl_name = mtl_path.replace('\\', '/').rsplit('/', 1)[-1]

    vertex_offset = 1  # OBJ is 1-indexed
    obj_lines = [f"mtllib {mtl_name}\n"]
    mtl_lines = []

    for i, elem in enumerate(project.elements):
        p = elem.transform.translation
        sx, sy, sz = _element_box_scale(elem)
        c = elem.color
        mat_name = f"mat_{i}"

        # Material
        mtl_lines.append(f"newmtl {mat_name}\n")
        mtl_lines.append(f"Kd {c.r:.4f} {c.g:.4f} {c.b:.4f}\n")
        mtl_lines.append(f"d {c.a:.2f}\n")
        mtl_lines.append("\n")

        # Object
        name = (elem.display_name or elem.unique_id).replace(' ', '_')
        obj_lines.append(f"o {name}\n")
        obj_lines.append(f"usemtl {mat_name}\n")

        # Vertices (scaled and translated)
        for vx, vy, vz in _BOX_VERTICES:
            obj_lines.append(f"v {p.x + vx * sx:.4f} {p.y + vy * sy:.4f} {p.z + vz * sz:.4f}\n")

        # Faces (quads, 6 faces)
        face_quads = [
            (1,2,3,4), (5,8,7,6), (1,5,6,2),
            (3,7,8,4), (1,4,8,5), (2,6,7,3),
        ]
        for a, b, c_idx, d in face_quads:
            obj_lines.append(f"f {vertex_offset+a-1} {vertex_offset+b-1} "
                             f"{vertex_offset+c_idx-1} {vertex_offset+d-1}\n")

        vertex_offset += 8

    with open(filepath, 'w') as f:
        f.writelines(obj_lines)
    with open(mtl_path, 'w') as f:
        f.writelines(mtl_lines)


# ---------------------------------------------------------------------------
# glTF 2.0 Binary (.glb) export
# ---------------------------------------------------------------------------

def export_glb(project: Project, filepath: str, include_camera_orbit: bool = False):
    """Export scene as a glTF Binary (.glb) file.

    Each element is a box mesh node with its own material color.
    Optionally includes a camera that orbits the scene.
    """
    if not project.elements:
        return

    # Build shared box mesh data
    # We need per-vertex normals, so we expand vertices per-face (24 verts for a box)
    positions = []  # flat list of floats
    normals = []
    indices = []

    # 6 faces, 4 vertices each = 24 vertices
    face_verts = [
        # -Z
        [0,1,2,3],
        # +Z
        [4,5,6,7],
        # -Y
        [0,1,5,4],
        # +Y
        [2,3,7,6],
        # -X
        [0,3,7,4],
        # +X
        [1,2,6,5],
    ]
    face_normals = [
        (0,0,-1), (0,0,1), (0,-1,0), (0,1,0), (-1,0,0), (1,0,0)
    ]

    vert_idx = 0
    for fi, (verts, normal) in enumerate(zip(face_verts, face_normals)):
        for vi in verts:
            v = _BOX_VERTICES[vi]
            positions.extend(v)
            normals.extend(normal)
        # Two triangles per face
        indices.extend([vert_idx, vert_idx+1, vert_idx+2])
        indices.extend([vert_idx+2, vert_idx+3, vert_idx])
        vert_idx += 4

    # Pack binary buffer
    pos_bytes = struct.pack(f'<{len(positions)}f', *positions)
    norm_bytes = struct.pack(f'<{len(normals)}f', *normals)
    idx_bytes = struct.pack(f'<{len(indices)}H', *indices)

    # Pad each section to 4-byte boundary
    def _pad4(data: bytes) -> bytes:
        remainder = len(data) % 4
        if remainder:
            data += b'\x00' * (4 - remainder)
        return data

    pos_bytes = _pad4(pos_bytes)
    norm_bytes = _pad4(norm_bytes)
    idx_bytes = _pad4(idx_bytes)

    bin_data = pos_bytes + norm_bytes + idx_bytes

    pos_offset = 0
    norm_offset = len(pos_bytes)
    idx_offset = norm_offset + len(norm_bytes)

    num_verts = 24
    num_indices = len(indices)

    # Build glTF JSON
    nodes = []
    materials = []
    meshes = []

    for i, elem in enumerate(project.elements):
        p = elem.transform.translation
        sx, sy, sz = _element_box_scale(elem)
        c = elem.color

        nodes.append({
            "name": elem.display_name or elem.unique_id,
            "mesh": i,
            "translation": [p.x, p.z, -p.y],  # glTF is Y-up, MoveMusic is Z-up
            "scale": [sx, sz, sy],
        })

        materials.append({
            "name": f"mat_{i}",
            "pbrMetallicRoughness": {
                "baseColorFactor": [c.r, c.g, c.b, c.a],
                "metallicFactor": 0.0,  # Non-metallic for better Blender display
                "roughnessFactor": 0.9,  # More realistic for UI elements
            },
            "alphaMode": "OPAQUE" if c.a >= 0.99 else "BLEND",
            "extras": {
                "generator": "MoveMusicSaveEditor",
                "intended_for": "blender"
            }
        })

        meshes.append({
            "primitives": [{
                "attributes": {
                    "POSITION": 0,
                    "NORMAL": 1,
                },
                "indices": 2,
                "material": i,
            }]
        })

    # Compute position bounds
    min_pos = [min(positions[j] for j in range(0, len(positions), 3)),
               min(positions[j] for j in range(1, len(positions), 3)),
               min(positions[j] for j in range(2, len(positions), 3))]
    max_pos = [max(positions[j] for j in range(0, len(positions), 3)),
               max(positions[j] for j in range(1, len(positions), 3)),
               max(positions[j] for j in range(2, len(positions), 3))]

    scene_nodes = list(range(len(nodes)))

    # Camera orbit
    if include_camera_orbit:
        # Compute scene centroid
        cx = sum(e.transform.translation.x for e in project.elements) / len(project.elements)
        cy = sum(e.transform.translation.y for e in project.elements) / len(project.elements)
        cz = sum(e.transform.translation.z for e in project.elements) / len(project.elements)

        # Find scene extent for orbit distance
        max_dist = 0
        for e in project.elements:
            p = e.transform.translation
            d = math.sqrt((p.x - cx)**2 + (p.y - cy)**2 + (p.z - cz)**2)
            max_dist = max(max_dist, d)
        orbit_radius = max(max_dist * 2, 100)

        # Add camera node
        cam_node_idx = len(nodes)
        nodes.append({
            "name": "OrbitCamera",
            "camera": 0,
            "translation": [cx, cz + orbit_radius * 0.3, -cy + orbit_radius],
            "rotation": [0, 0, 0, 1],
        })
        scene_nodes.append(cam_node_idx)

        # Generate orbit animation: 60 keyframes over 10 seconds
        num_frames = 60
        duration = 10.0
        time_data = []
        translation_data = []
        for f in range(num_frames):
            t = f / (num_frames - 1) * duration
            angle = 2 * math.pi * f / (num_frames - 1)
            cam_x = cx + orbit_radius * math.cos(angle)
            cam_y = cy + orbit_radius * math.sin(angle)
            cam_z_pos = cz + orbit_radius * 0.3
            time_data.append(t)
            # glTF Y-up
            translation_data.extend([cam_x, cam_z_pos, -cam_y])

        time_bytes = _pad4(struct.pack(f'<{len(time_data)}f', *time_data))
        trans_bytes = _pad4(struct.pack(f'<{len(translation_data)}f', *translation_data))

        anim_offset = len(bin_data)
        bin_data += time_bytes + trans_bytes

        time_acc_idx = 3
        trans_acc_idx = 4

        gltf = _build_gltf_json(
            nodes, materials, meshes, scene_nodes,
            pos_offset, norm_offset, idx_offset,
            len(pos_bytes), len(norm_bytes), len(idx_bytes),
            num_verts, num_indices, min_pos, max_pos,
            len(bin_data),
            camera={
                "type": "perspective",
                "perspective": {
                    "aspectRatio": 1.778,
                    "yfov": 0.8,
                    "znear": 1.0,
                    "zfar": 10000.0,
                }
            },
            animation={
                "time_offset": anim_offset,
                "time_bytes": len(time_bytes),
                "trans_offset": anim_offset + len(time_bytes),
                "trans_bytes": len(trans_bytes),
                "num_frames": num_frames,
                "duration": duration,
                "cam_node": cam_node_idx,
            }
        )
    else:
        gltf = _build_gltf_json(
            nodes, materials, meshes, scene_nodes,
            pos_offset, norm_offset, idx_offset,
            len(pos_bytes), len(norm_bytes), len(idx_bytes),
            num_verts, num_indices, min_pos, max_pos,
            len(bin_data),
        )

    _write_glb(filepath, gltf, bin_data)


def _build_gltf_json(
    nodes, materials, meshes, scene_nodes,
    pos_offset, norm_offset, idx_offset,
    pos_size, norm_size, idx_size,
    num_verts, num_indices, min_pos, max_pos,
    buffer_length,
    camera=None,
    animation=None,
):
    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "MoveMusicSaveEditor v1.0",
            "extras": {
                "source_application": "MoveMusic Save Editor",
                "export_target": "blender",
                "coordinate_system": "Z_UP_CONVERTED_TO_Y_UP",
                "units": "metric",
                "scale": 0.01,  # Blender units (elements are in game units, ~100x larger)
                "recommended_import_settings": {
                    "scale": 0.01,
                    "up_axis": "Y",
                    "forward_axis": "Z"
                }
            }
        },
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": [
            {  # 0: positions
                "bufferView": 0,
                "componentType": 5126,  # FLOAT
                "count": num_verts,
                "type": "VEC3",
                "min": min_pos,
                "max": max_pos,
            },
            {  # 1: normals
                "bufferView": 1,
                "componentType": 5126,
                "count": num_verts,
                "type": "VEC3",
            },
            {  # 2: indices
                "bufferView": 2,
                "componentType": 5123,  # UNSIGNED_SHORT
                "count": num_indices,
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": pos_offset, "byteLength": pos_size, "target": 34962},
            {"buffer": 0, "byteOffset": norm_offset, "byteLength": norm_size, "target": 34962},
            {"buffer": 0, "byteOffset": idx_offset, "byteLength": idx_size, "target": 34963},
        ],
        "buffers": [{"byteLength": buffer_length}],
    }

    if camera:
        gltf["cameras"] = [camera]

    if animation:
        a = animation
        # Add accessors for time and translation
        time_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "bufferView": len(gltf["bufferViews"]),
            "componentType": 5126,
            "count": a["num_frames"],
            "type": "SCALAR",
            "min": [0.0],
            "max": [a["duration"]],
        })
        gltf["bufferViews"].append({
            "buffer": 0,
            "byteOffset": a["time_offset"],
            "byteLength": a["time_bytes"],
        })

        trans_acc = len(gltf["accessors"])
        gltf["accessors"].append({
            "bufferView": len(gltf["bufferViews"]),
            "componentType": 5126,
            "count": a["num_frames"],
            "type": "VEC3",
        })
        gltf["bufferViews"].append({
            "buffer": 0,
            "byteOffset": a["trans_offset"],
            "byteLength": a["trans_bytes"],
        })

        gltf["animations"] = [{
            "name": "CameraOrbit",
            "channels": [{
                "sampler": 0,
                "target": {"node": a["cam_node"], "path": "translation"},
            }],
            "samplers": [{
                "input": time_acc,
                "output": trans_acc,
                "interpolation": "LINEAR",
            }],
        }]

    return gltf


def _write_glb(filepath: str, gltf_json: dict, bin_data: bytes):
    """Write a GLB file (glTF Binary container)."""
    json_str = json.dumps(gltf_json, separators=(',', ':'))
    json_bytes = json_str.encode('utf-8')
    # Pad JSON to 4-byte boundary with spaces
    remainder = len(json_bytes) % 4
    if remainder:
        json_bytes += b' ' * (4 - remainder)

    # GLB header: magic, version, total length
    json_chunk_length = len(json_bytes)
    bin_chunk_length = len(bin_data)

    total_length = (
        12 +  # header
        8 + json_chunk_length +  # JSON chunk
        8 + bin_chunk_length    # BIN chunk
    )

    with open(filepath, 'wb') as f:
        # Header
        f.write(struct.pack('<I', 0x46546C67))  # magic: "glTF"
        f.write(struct.pack('<I', 2))            # version
        f.write(struct.pack('<I', total_length))

        # JSON chunk
        f.write(struct.pack('<I', json_chunk_length))
        f.write(struct.pack('<I', 0x4E4F534A))  # "JSON"
        f.write(json_bytes)

        # BIN chunk
        f.write(struct.pack('<I', bin_chunk_length))
        f.write(struct.pack('<I', 0x004E4942))  # "BIN\0"
        f.write(bin_data)
