"""
3D Model Import for MoveMusic Save Editor

Imports GLB/OBJ files using trimesh and converts them to internal element types.
Provides smart element type assignment based on mesh characteristics.
"""

from __future__ import annotations

import os
import logging
from typing import List, Optional, Tuple
from dataclasses import replace

try:
    import trimesh
    import numpy as np
    TRIMESH_AVAILABLE = True
except ImportError:
    TRIMESH_AVAILABLE = False

from model import (
    HitZone, MorphZone, TextLabel, GroupIE, Transform, Vec3, Color, BoundingBox,
    MidiNoteMapping, MidiCCMapping
)

logger = logging.getLogger(__name__)


class Import3DError(Exception):
    """Base exception for 3D import operations."""
    pass


def check_dependencies():
    """Check if required dependencies are available."""
    if not TRIMESH_AVAILABLE:
        raise Import3DError(
            "Required dependencies not installed. Please install:\n"
            "pip install trimesh pillow imageio numpy"
        )


def load_glb(filepath: str, project) -> List[object]:
    """
    Load GLB file using trimesh, convert to project elements.

    Args:
        filepath: Path to GLB file
        project: Project instance for generating unique IDs

    Returns:
        List of element instances (HitZone, MorphZone, TextLabel, GroupIE)

    Raises:
        Import3DError: If file cannot be loaded or converted
    """
    check_dependencies()

    try:
        # Load GLB scene using trimesh
        scene = trimesh.load(filepath)

        if isinstance(scene, trimesh.Scene):
            # Multi-mesh scene - convert to elements
            elements = []
            group_items = []

            for name, mesh in scene.geometry.items():
                if isinstance(mesh, trimesh.Trimesh):
                    # Extract material color if available
                    material_color = _extract_material_color(scene, name)

                    # Convert mesh to appropriate element type
                    element = _mesh_to_element(
                        mesh, material_color, project, name
                    )
                    elements.append(element)
                    group_items.append(element.unique_id)

            # Create group if multiple meshes
            if len(elements) > 1:
                group = _create_group_for_elements(elements, project, filepath)
                group.group_items = group_items
                elements.append(group)

            return elements

        elif isinstance(scene, trimesh.Trimesh):
            # Single mesh - convert directly
            element = _mesh_to_element(scene, None, project, os.path.basename(filepath))
            return [element]

        else:
            raise Import3DError(f"Unsupported geometry type in {filepath}")

    except Exception as e:
        raise Import3DError(f"Failed to load GLB file {filepath}: {str(e)}")


def load_obj(filepath: str, project) -> List[object]:
    """
    Load OBJ file (with optional MTL) using trimesh, convert to project elements.

    Args:
        filepath: Path to OBJ file
        project: Project instance for generating unique IDs

    Returns:
        List of element instances

    Raises:
        Import3DError: If file cannot be loaded or converted
    """
    check_dependencies()

    try:
        # Load OBJ (with materials if MTL exists)
        scene = trimesh.load(filepath)

        if isinstance(scene, trimesh.Scene):
            # Multi-mesh OBJ file
            elements = []
            group_items = []

            for name, mesh in scene.geometry.items():
                if isinstance(mesh, trimesh.Trimesh):
                    # Extract material color from MTL if available
                    material_color = _extract_obj_material_color(mesh)

                    # Convert mesh to element
                    element = _mesh_to_element(
                        mesh, material_color, project, name
                    )
                    elements.append(element)
                    group_items.append(element.unique_id)

            # Create group if multiple objects
            if len(elements) > 1:
                group = _create_group_for_elements(elements, project, filepath)
                group.group_items = group_items
                elements.append(group)

            return elements

        elif isinstance(scene, trimesh.Trimesh):
            # Single mesh OBJ
            material_color = _extract_obj_material_color(scene)
            element = _mesh_to_element(scene, material_color, project, os.path.basename(filepath))
            return [element]

        else:
            raise Import3DError(f"Unsupported geometry type in {filepath}")

    except Exception as e:
        raise Import3DError(f"Failed to load OBJ file {filepath}: {str(e)}")


def _mesh_to_element(mesh: 'trimesh.Trimesh', material_color: Optional[Color],
                    project, name: str) -> object:
    """
    Convert trimesh mesh to appropriate MoveMusic element type based on size.

    Element Type Mapping Strategy:
    - Large meshes (>100 units): Convert to MorphZone with mesh_extent
    - Medium meshes (20-100 units): Convert to HitZone
    - Small meshes (<20 units): Convert to TextLabel
    """
    # Calculate mesh bounding box
    bounds = mesh.bounds
    size = bounds[1] - bounds[0]  # max - min
    max_dimension = max(size)

    # Calculate transform from mesh
    center = (bounds[1] + bounds[0]) / 2.0
    transform = Transform(
        translation=Vec3(float(center[0]), float(center[1]), float(center[2])),
        scale=Vec3(1.0, 1.0, 1.0)  # Use original scale
    )

    # Set default color or use material color
    color = material_color or Color(0.6, 0.6, 0.6, 1.0)  # Default gray

    # Clean name for display
    display_name = _clean_name(name)

    if max_dimension >= 100.0:
        # Large mesh -> MorphZone with mesh extent
        extent = Vec3(float(size[0]), float(size[1]), float(size[2]))
        mesh_min = Vec3(float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2]))
        mesh_max = Vec3(float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2]))

        return MorphZone(
            unique_id=project.generate_id("MorphZone"),
            display_name=f"Imported_{display_name}",
            transform=transform,
            color=color,
            mesh_extent=extent,
            mesh_min_local_position=mesh_min,
            mesh_max_local_position=mesh_max,
        )

    elif max_dimension >= 20.0:
        # Medium mesh -> HitZone
        # Scale transform to represent size properly
        scaled_transform = replace(transform, scale=Vec3(
            max(0.1, size[0] / 30.0),  # HitZones default to 30x30x5
            max(0.1, size[1] / 30.0),
            max(0.1, size[2] / 5.0)
        ))

        return HitZone(
            unique_id=project.generate_id("HitZone"),
            display_name=f"Imported_{display_name}",
            transform=scaled_transform,
            color=color,
        )

    else:
        # Small mesh -> TextLabel
        return TextLabel(
            unique_id=project.generate_id("TextLabel_C"),
            display_name=display_name,  # TextLabel uses display_name as the text
            transform=transform,
            color=color,
        )


def _create_group_for_elements(elements: List[object], project, filepath: str) -> GroupIE:
    """Create a GroupIE element to contain multiple imported elements."""
    # Calculate overall bounding box
    min_vals = [float('inf')] * 3
    max_vals = [float('-inf')] * 3

    for element in elements:
        pos = element.transform.translation
        scale = element.transform.scale

        # Rough bounding box calculation
        for i in range(3):
            coord = getattr(pos, ['x', 'y', 'z'][i])
            size = getattr(scale, ['x', 'y', 'z'][i]) * 15  # Approximate element size
            min_vals[i] = min(min_vals[i], coord - size)
            max_vals[i] = max(max_vals[i], coord + size)

    # Create bounding box
    bbox = BoundingBox(
        min=Vec3(min_vals[0], min_vals[1], min_vals[2]),
        max=Vec3(max_vals[0], max_vals[1], max_vals[2]),
        is_valid=1
    )

    # Group transform at center of bounding box
    center = Vec3(
        (min_vals[0] + max_vals[0]) / 2,
        (min_vals[1] + max_vals[1]) / 2,
        (min_vals[2] + max_vals[2]) / 2
    )

    filename = os.path.splitext(os.path.basename(filepath))[0]

    return GroupIE(
        unique_id=project.generate_id("GroupIE"),
        display_name=f"Imported_{_clean_name(filename)}",
        transform=Transform(translation=center),
        color=Color(0.8, 0.8, 0.2, 1.0),  # Default group color
        bounding_box=bbox,
        group_items=[]  # Will be set by caller
    )


def _extract_material_color(scene, mesh_name: str) -> Optional[Color]:
    """Extract material color from GLB scene for a specific mesh."""
    if not hasattr(scene, 'materials') or not scene.materials:
        return None

    try:
        # Find material for this mesh
        material = None
        if hasattr(scene, 'graph') and scene.graph:
            for node_name in scene.graph.nodes:
                if mesh_name in node_name:
                    node = scene.graph[node_name]
                    if hasattr(node, 'material') and node.material:
                        material = scene.materials[node.material]
                        break

        if material and hasattr(material, 'baseColorFactor'):
            color_factor = material.baseColorFactor
            return Color(
                float(color_factor[0]),
                float(color_factor[1]),
                float(color_factor[2]),
                float(color_factor[3]) if len(color_factor) > 3 else 1.0
            )

    except Exception as e:
        logger.warning(f"Could not extract material color from {mesh_name}: {e}")

    return None


def _extract_obj_material_color(mesh: 'trimesh.Trimesh') -> Optional[Color]:
    """Extract material color from OBJ mesh (via MTL file)."""
    try:
        if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'material'):
            material = mesh.visual.material

            # Check for diffuse color
            if hasattr(material, 'diffuse') and material.diffuse is not None:
                diffuse = material.diffuse
                if len(diffuse) >= 3:
                    return Color(
                        float(diffuse[0]) / 255.0 if diffuse[0] > 1 else float(diffuse[0]),
                        float(diffuse[1]) / 255.0 if diffuse[1] > 1 else float(diffuse[1]),
                        float(diffuse[2]) / 255.0 if diffuse[2] > 1 else float(diffuse[2]),
                        float(diffuse[3]) / 255.0 if len(diffuse) > 3 and diffuse[3] > 1 else 1.0 if len(diffuse) <= 3 else float(diffuse[3])
                    )

            # Fallback to base color
            if hasattr(material, 'baseColorFactor') and material.baseColorFactor is not None:
                color = material.baseColorFactor
                return Color(
                    float(color[0]),
                    float(color[1]),
                    float(color[2]),
                    float(color[3]) if len(color) > 3 else 1.0
                )

    except Exception as e:
        logger.warning(f"Could not extract OBJ material color: {e}")

    return None


def _clean_name(name: str) -> str:
    """Clean imported name for use as display name."""
    # Remove file extension
    name = os.path.splitext(name)[0]

    # Replace invalid characters with underscores
    import re
    name = re.sub(r'[^\w\s-]', '_', name)

    # Limit length
    if len(name) > 30:
        name = name[:30]

    return name or "ImportedMesh"