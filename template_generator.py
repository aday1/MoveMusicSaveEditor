"""
Template generator for common MoveMusic instrument layouts.

Generates groups of elements (faders, knobs, XY pads, drum pads, buttons)
arranged in rows, grids, or circles, with text labels and a containing GroupIE.
"""

from __future__ import annotations

import math
from dataclasses import field
from typing import List, Tuple

from model import (
    Project, Workspace, HitZone, MorphZone, TextLabel, GroupIE,
    BoundingBox, MidiNoteMapping, MidiCCMapping, Color, Vec3, Quat, Transform,
)


# ---------------------------------------------------------------------------
# Colour palettes and management
# ---------------------------------------------------------------------------

# Rainbow-ish palette for differentiating elements
PALETTE = [
    Color(0.2, 0.6, 1.0, 1.0),   # blue
    Color(0.2, 0.8, 0.4, 1.0),   # green
    Color(1.0, 0.7, 0.1, 1.0),   # amber
    Color(1.0, 0.3, 0.3, 1.0),   # red
    Color(0.7, 0.3, 1.0, 1.0),   # purple
    Color(0.1, 0.9, 0.9, 1.0),   # cyan
    Color(1.0, 0.5, 0.0, 1.0),   # orange
    Color(0.9, 0.2, 0.7, 1.0),   # pink
]

# Additional color palettes
WARM_PALETTE = [
    Color(1.0, 0.2, 0.2, 1.0),   # red
    Color(1.0, 0.4, 0.1, 1.0),   # red-orange
    Color(1.0, 0.6, 0.1, 1.0),   # orange
    Color(1.0, 0.8, 0.1, 1.0),   # yellow-orange
    Color(1.0, 0.9, 0.2, 1.0),   # yellow
    Color(0.9, 0.7, 0.2, 1.0),   # gold
    Color(0.8, 0.5, 0.2, 1.0),   # amber
    Color(0.7, 0.3, 0.2, 1.0),   # brown
]

COOL_PALETTE = [
    Color(0.1, 0.3, 1.0, 1.0),   # blue
    Color(0.2, 0.5, 0.9, 1.0),   # light blue
    Color(0.1, 0.7, 0.8, 1.0),   # cyan
    Color(0.2, 0.8, 0.6, 1.0),   # teal
    Color(0.2, 0.9, 0.4, 1.0),   # green
    Color(0.3, 0.8, 0.8, 1.0),   # aqua
    Color(0.4, 0.6, 0.9, 1.0),   # periwinkle
    Color(0.2, 0.4, 0.8, 1.0),   # royal blue
]

MONO_PALETTE = [
    Color(0.9, 0.9, 0.9, 1.0),   # light gray
    Color(0.8, 0.8, 0.8, 1.0),
    Color(0.7, 0.7, 0.7, 1.0),
    Color(0.6, 0.6, 0.6, 1.0),
    Color(0.5, 0.5, 0.5, 1.0),   # medium gray
    Color(0.4, 0.4, 0.4, 1.0),
    Color(0.3, 0.3, 0.3, 1.0),
    Color(0.2, 0.2, 0.2, 1.0),   # dark gray
]

NEON_PALETTE = [
    Color(1.0, 0.0, 1.0, 1.0),   # magenta
    Color(0.0, 1.0, 1.0, 1.0),   # cyan
    Color(0.0, 1.0, 0.0, 1.0),   # lime
    Color(1.0, 1.0, 0.0, 1.0),   # yellow
    Color(1.0, 0.0, 0.5, 1.0),   # hot pink
    Color(0.5, 0.0, 1.0, 1.0),   # violet
    Color(0.0, 0.5, 1.0, 1.0),   # electric blue
    Color(1.0, 0.5, 0.0, 1.0),   # electric orange
]

COLOR_PALETTES = {
    "Rainbow": PALETTE,
    "Warm": WARM_PALETTE,
    "Cool": COOL_PALETTE,
    "Monochrome": MONO_PALETTE,
    "Neon": NEON_PALETTE,
}

LABEL_COLOR = Color(1.0, 1.0, 1.0, 1.0)
GROUP_COLOR = Color(0.8, 0.8, 0.2, 1.0)

def _get_colors(count: int, color_mode: str = "Rainbow") -> List[Color]:
    """Get colors for elements based on mode."""
    import random

    if color_mode == "Random":
        colors = []
        for _ in range(count):
            colors.append(Color(random.random(), random.random(), random.random(), 1.0))
        return colors
    elif color_mode == "Gradient":
        # Blue to red gradient
        colors = []
        for i in range(count):
            t = i / max(1, count - 1)
            r = t
            g = 0.3 * (1 - abs(2 * t - 1))  # peak in middle
            b = 1 - t
            colors.append(Color(r, g, b, 1.0))
        return colors
    elif color_mode == "Single":
        # All same color (blue)
        return [Color(0.2, 0.6, 1.0, 1.0)] * count
    else:
        # Use palette
        palette = COLOR_PALETTES.get(color_mode, PALETTE)
        return [palette[i % len(palette)] for i in range(count)]


# ---------------------------------------------------------------------------
# Enhanced layout helpers
# ---------------------------------------------------------------------------

def _row_positions(count: int, spacing: float) -> List[Tuple[float, float]]:
    """Evenly spaced positions along X, centered at origin. Returns (x, y)."""
    total = (count - 1) * spacing
    start = -total / 2
    return [(start + i * spacing, 0.0) for i in range(count)]

def _column_positions(count: int, spacing: float) -> List[Tuple[float, float]]:
    """Evenly spaced positions along Y, centered at origin. Returns (x, y)."""
    total = (count - 1) * spacing
    start = -total / 2
    return [(0.0, start + i * spacing) for i in range(count)]

def _grid_positions(count: int, cols: int, spacing: float) -> List[Tuple[float, float]]:
    """Grid layout, filling rows left-to-right. Returns (x, y)."""
    rows = math.ceil(count / cols)
    total_x = (cols - 1) * spacing
    total_y = (rows - 1) * spacing
    positions = []
    for i in range(count):
        r, c = divmod(i, cols)
        x = -total_x / 2 + c * spacing
        y = total_y / 2 - r * spacing
        positions.append((x, y))
    return positions

def _square_positions(count: int, spacing: float) -> List[Tuple[float, float]]:
    """Square grid layout - automatically determine best square dimensions."""
    side = math.ceil(math.sqrt(count))
    return _grid_positions(count, side, spacing)

def _circle_positions(count: int, radius: float) -> List[Tuple[float, float]]:
    """Positions evenly spaced around a circle. Returns (x, y)."""
    positions = []
    for i in range(count):
        angle = 2 * math.pi * i / count - math.pi / 2  # start at top
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        positions.append((x, y))
    return positions

def _spiral_positions(count: int, spacing: float, turns: float = 2.0) -> List[Tuple[float, float]]:
    """Spiral layout starting from center."""
    positions = []
    for i in range(count):
        t = turns * 2 * math.pi * i / max(1, count - 1)
        r = spacing * i / max(1, count - 1)
        x = r * math.cos(t)
        y = r * math.sin(t)
        positions.append((x, y))
    return positions

def _arc_positions(count: int, radius: float, arc_degrees: float = 180) -> List[Tuple[float, float]]:
    """Arc layout - partial circle."""
    positions = []
    arc_radians = math.radians(arc_degrees)
    start_angle = -arc_radians / 2  # center the arc
    for i in range(count):
        if count == 1:
            angle = 0
        else:
            angle = start_angle + arc_radians * i / (count - 1)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        positions.append((x, y))
    return positions

def _diamond_positions(count: int, spacing: float) -> List[Tuple[float, float]]:
    """Diamond/rhombus pattern."""
    if count <= 4:
        # Simple diamond for 4 or fewer
        basic = [(0, spacing), (spacing, 0), (0, -spacing), (-spacing, 0)]
        return basic[:count]

    # Larger diamond with multiple rings
    positions = [(0, 0)]  # center
    ring_size = 1
    while len(positions) < count:
        # Add points around current ring
        for side in range(4):  # 4 sides of diamond
            if side == 0:  # top-right edge
                for i in range(ring_size):
                    if len(positions) >= count: break
                    x = i * spacing
                    y = (ring_size - i) * spacing
                    positions.append((x, y))
            elif side == 1:  # bottom-right edge
                for i in range(ring_size):
                    if len(positions) >= count: break
                    x = ring_size * spacing - i * spacing
                    y = -i * spacing
                    positions.append((x, y))
            elif side == 2:  # bottom-left edge
                for i in range(ring_size):
                    if len(positions) >= count: break
                    x = -i * spacing
                    y = -(ring_size - i) * spacing
                    positions.append((x, y))
            elif side == 3:  # top-left edge
                for i in range(ring_size):
                    if len(positions) >= count: break
                    x = -ring_size * spacing + i * spacing
                    y = i * spacing
                    positions.append((x, y))
        ring_size += 1

    return positions[:count]

def _diagonal_positions(count: int, spacing: float, angle_degrees: float = 45) -> List[Tuple[float, float]]:
    """Diagonal line at specified angle."""
    angle = math.radians(angle_degrees)
    total = (count - 1) * spacing
    start = -total / 2
    positions = []
    for i in range(count):
        dist = start + i * spacing
        x = dist * math.cos(angle)
        y = dist * math.sin(angle)
        positions.append((x, y))
    return positions

def _zigzag_positions(count: int, spacing: float, amplitude: float = 20) -> List[Tuple[float, float]]:
    """Zigzag pattern."""
    positions = []
    x_spacing = spacing * 0.7
    total_x = (count - 1) * x_spacing
    start_x = -total_x / 2

    for i in range(count):
        x = start_x + i * x_spacing
        y = amplitude * math.sin(2 * math.pi * i / 8)  # 8-element period
        positions.append((x, y))
    return positions

def _random_positions(count: int, area_size: float) -> List[Tuple[float, float]]:
    """Random scatter within square area."""
    import random
    positions = []
    for _ in range(count):
        x = random.uniform(-area_size/2, area_size/2)
        y = random.uniform(-area_size/2, area_size/2)
        positions.append((x, y))
    return positions

def _triangle_positions(count: int, spacing: float) -> List[Tuple[float, float]]:
    """Arrange elements along the three edges of an equilateral triangle.

    Elements are distributed evenly around the perimeter, starting from the
    bottom-left vertex and going clockwise.
    """
    if count <= 0:
        return []
    if count == 1:
        return [(0.0, 0.0)]
    if count == 2:
        return [(-spacing / 2, 0.0), (spacing / 2, 0.0)]

    # Equilateral triangle vertices (centered, flat bottom)
    h = spacing * count / 3  # side length proportional to count
    side = h
    tri_h = side * math.sqrt(3) / 2
    v0 = (-side / 2, -tri_h / 3)      # bottom-left
    v1 = (side / 2, -tri_h / 3)       # bottom-right
    v2 = (0.0, tri_h * 2 / 3)         # top

    edges = [(v0, v1), (v1, v2), (v2, v0)]
    perimeter = side * 3
    step = perimeter / count

    positions = []
    edge_idx = 0
    edge_progress = 0.0
    edge_lengths = [side, side, side]

    for _ in range(count):
        # Interpolate along current edge
        ex, ey = edges[edge_idx][0]
        fx, fy = edges[edge_idx][1]
        t = edge_progress / edge_lengths[edge_idx] if edge_lengths[edge_idx] > 0 else 0
        t = min(t, 1.0)
        x = ex + (fx - ex) * t
        y = ey + (fy - ey) * t
        positions.append((x, y))

        # Advance along perimeter
        edge_progress += step
        while edge_idx < 2 and edge_progress >= edge_lengths[edge_idx]:
            edge_progress -= edge_lengths[edge_idx]
            edge_idx += 1

    return positions
    return positions

def _get_layout_positions(count: int, arrangement: str, spacing: float = 30.0) -> List[Tuple[float, float]]:
    """Get positions based on arrangement type."""
    if arrangement == "Row":
        return _row_positions(count, spacing)
    elif arrangement == "Column":
        return _column_positions(count, spacing)
    elif arrangement == "Grid 2x4" or (arrangement == "Grid" and count == 8):
        return _grid_positions(count, 4, spacing)
    elif arrangement == "Grid 3x3":
        return _grid_positions(count, 3, spacing)
    elif arrangement == "Grid 4x4":
        return _grid_positions(count, 4, spacing)
    elif arrangement == "Square":
        return _square_positions(count, spacing)
    elif arrangement == "Circle":
        radius = spacing * count / (2 * math.pi) * 1.2  # scale for nice spacing
        return _circle_positions(count, radius)
    elif arrangement == "Spiral":
        return _spiral_positions(count, spacing)
    elif arrangement == "Arc":
        return _arc_positions(count, spacing * 2, 180)
    elif arrangement == "Diamond":
        return _diamond_positions(count, spacing)
    elif arrangement == "Diagonal":
        return _diagonal_positions(count, spacing)
    elif arrangement == "Zigzag":
        return _zigzag_positions(count, spacing)
    elif arrangement == "Triangle":
        return _triangle_positions(count, spacing)
    elif arrangement == "Random":
        area = spacing * count * 0.8  # area scales with count
        return _random_positions(count, area)
    else:
        # Default to row
        return _row_positions(count, spacing)
        x = radius * math.cos(angle)
        y = radius * math.sin(angle)
        positions.append((x, y))
    return positions


ARRANGEMENTS = {
    "Row": _row_positions,
    "Circle": _circle_positions,
}


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------

def generate_faders(
    project: Project,
    count: int = 8,
    arrangement: str = "Row",
    spacing: float = 30.0,
    origin: Vec3 = None,
    base_cc: int = 1,
    channel: int = 1,
    label_prefix: str = "Fader",
    color_mode: str = "Rainbow",
) -> List:
    """Generate fader MorphZones (1D Y-axis) with enhanced layouts and colors.

    Args:
        color_mode: "Rainbow", "Warm", "Cool", "Monochrome", "Neon", "Random", "Gradient", "Single"
        arrangement: "Row", "Column", "Grid", "Square", "Circle", "Spiral", "Arc", "Diamond", "Diagonal", "Zigzag", "Random"
    """
    if origin is None:
        origin = Vec3(0, 0, 0)

    positions = _get_layout_positions(count, arrangement, spacing)
    colors = _get_colors(count, color_mode)

    elements = []
    member_ids = []

    for i, (px, py) in enumerate(positions):
        # MorphZone: 1D fader on Y axis
        mz_id = project.generate_id("MorphZone")
        name = f"{label_prefix} {i + 1}"
        mz = MorphZone(
            unique_id=mz_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.15, 0.8, 0.15),
            ),
            color=colors[i],
            is_x_axis_enabled=False,
            x_axis_cc_mappings=[],
            is_y_axis_enabled=True,
            y_axis_cc_mappings=[MidiCCMapping(channel=channel, control=base_cc + i, value=0)],
            is_z_axis_enabled=False,
            z_axis_cc_mappings=[],
            dimensions="EDimensions::One",
            soloed_axis="EAxis::Y",
        )
        elements.append(mz)
        member_ids.append(mz_id)

        # Text label above the fader with clear MIDI info
        tl_id = project.generate_id("TextLabel_C")
        tl = TextLabel(
            unique_id=tl_id,
            display_name=f"{name}\nCC{base_cc + i}\nCh{channel}",
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z + 30),
                scale=Vec3(0.3, 0.3, 0.3),
            ),
            color=LABEL_COLOR,
        )
        elements.append(tl)
        member_ids.append(tl_id)

    # Don't create a group - users find them annoying to manage
    # group = _make_group(project, f"{label_prefix}s ({arrangement}, {color_mode})", origin, member_ids, elements)
    # elements.append(group)

    return elements


def generate_knobs(
    project: Project,
    count: int = 8,
    arrangement: str = "Row",
    spacing: float = 30.0,
    origin: Vec3 = None,
    base_cc: int = 16,
    channel: int = 1,
    label_prefix: str = "Knob",
    color_mode: str = "Cool",
) -> List:
    """Generate rotary knob MorphZones (1D X-axis, small cube)."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    positions = _get_layout_positions(count, arrangement, spacing)
    colors = _get_colors(count, color_mode)

    elements = []
    member_ids = []

    for i, (px, py) in enumerate(positions):
        mz_id = project.generate_id("MorphZone")
        name = f"{label_prefix} {i + 1}"
        mz = MorphZone(
            unique_id=mz_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.4, 0.4, 0.15),
            ),
            color=colors[i],
            is_x_axis_enabled=True,
            x_axis_cc_mappings=[MidiCCMapping(channel=channel, control=base_cc + i, value=0)],
            is_y_axis_enabled=False,
            y_axis_cc_mappings=[],
            is_z_axis_enabled=False,
            z_axis_cc_mappings=[],
            dimensions="EDimensions::One",
            soloed_axis="EAxis::X",
        )
        elements.append(mz)
        member_ids.append(mz_id)

        tl_id = project.generate_id("TextLabel_C")
        tl = TextLabel(
            unique_id=tl_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z + 20),
                scale=Vec3(0.3, 0.3, 0.3),
            ),
            color=LABEL_COLOR,
        )
        elements.append(tl)
        member_ids.append(tl_id)

    # Don't auto-group knobs
    # group = _make_group(project, f"{label_prefix}s", origin, member_ids, elements)
    # elements.append(group)
    return elements


def generate_xy_pads(
    project: Project,
    count: int = 8,
    arrangement: str = "Row",
    spacing: float = 40.0,
    origin: Vec3 = None,
    base_cc_x: int = 32,
    base_cc_y: int = 48,
    channel: int = 1,
    label_prefix: str = "XY Pad",
) -> List:
    """Generate 2D XY MorphZone pads."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    positions = _get_layout_positions(count, arrangement, spacing)
    colors = _get_colors(count, "Rainbow")

    elements = []
    member_ids = []

    for i, (px, py) in enumerate(positions):
        mz_id = project.generate_id("MorphZone")
        name = f"{label_prefix} {i + 1}"
        mz = MorphZone(
            unique_id=mz_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.5, 0.5, 0.15),
            ),
            color=colors[i],
            is_x_axis_enabled=True,
            x_axis_cc_mappings=[MidiCCMapping(channel=channel, control=base_cc_x + i, value=0)],
            is_y_axis_enabled=True,
            y_axis_cc_mappings=[MidiCCMapping(channel=channel, control=base_cc_y + i, value=0)],
            is_z_axis_enabled=False,
            z_axis_cc_mappings=[],
            dimensions="EDimensions::Two",
            soloed_axis="EAxis::None",
        )
        elements.append(mz)
        member_ids.append(mz_id)

        tl_id = project.generate_id("TextLabel_C")
        tl = TextLabel(
            unique_id=tl_id,
            display_name=f"{name}\nX:CC{base_cc_x + i}\nY:CC{base_cc_y + i}",
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z + 25),
                scale=Vec3(0.3, 0.3, 0.3),
            ),
            color=LABEL_COLOR,
        )
        elements.append(tl)
        member_ids.append(tl_id)

    # Don't auto-group knobs
    # group = _make_group(project, f"{label_prefix}s", origin, member_ids, elements)
    # elements.append(group)
    return elements


def generate_drum_pads(
    project: Project,
    count: int = 8,
    arrangement: str = "Row",
    spacing: float = 30.0,
    origin: Vec3 = None,
    base_note: int = 36,
    channel: int = 10,
    label_prefix: str = "Pad",
) -> List:
    """Generate drum-pad HitZones (velocity sensitive, MIDI note)."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    positions = _get_layout_positions(count, arrangement, spacing)
    colors = _get_colors(count, "Rainbow")

    elements = []
    member_ids = []

    for i, (px, py) in enumerate(positions):
        hz_id = project.generate_id("HitZone")
        name = f"{label_prefix} {i + 1}"
        hz = HitZone(
            unique_id=hz_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.4, 0.4, 0.15),
            ),
            color=colors[i],
            should_use_velocity_sensitivity=True,
            midi_note_mappings=[MidiNoteMapping(channel=channel, note=base_note + i, velocity=1.0)],
            midi_cc_mappings=[],
            behavior="EHitZoneBehavior::Hold",
            midi_message_type="EMidiMessageType::Note",
        )
        elements.append(hz)
        member_ids.append(hz_id)

        tl_id = project.generate_id("TextLabel_C")
        tl = TextLabel(
            unique_id=tl_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z + 20),
                scale=Vec3(0.3, 0.3, 0.3),
            ),
            color=LABEL_COLOR,
        )
        elements.append(tl)
        member_ids.append(tl_id)

    # Don't auto-group knobs
    # group = _make_group(project, f"{label_prefix}s", origin, member_ids, elements)
    # elements.append(group)
    return elements


def generate_buttons(
    project: Project,
    count: int = 8,
    arrangement: str = "Row",
    spacing: float = 25.0,
    origin: Vec3 = None,
    base_cc: int = 64,
    channel: int = 1,
    label_prefix: str = "Btn",
) -> List:
    """Generate toggle button HitZones (CC toggle mode)."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    positions = _get_layout_positions(count, arrangement, spacing)
    colors = _get_colors(count, "Rainbow")

    elements = []
    member_ids = []

    for i, (px, py) in enumerate(positions):
        hz_id = project.generate_id("HitZone")
        name = f"{label_prefix} {i + 1}"
        hz = HitZone(
            unique_id=hz_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.3, 0.15, 0.3),
            ),
            color=colors[i],
            should_use_velocity_sensitivity=False,
            midi_note_mappings=[],
            midi_cc_mappings=[MidiCCMapping(channel=channel, control=base_cc + i, value=127)],
            behavior="EHitZoneBehavior::Toggle",
            midi_message_type="EMidiMessageType::CC",
        )
        elements.append(hz)
        member_ids.append(hz_id)

        tl_id = project.generate_id("TextLabel_C")
        tl = TextLabel(
            unique_id=tl_id,
            display_name=name,
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z + 18),
                scale=Vec3(0.25, 0.25, 0.25),
            ),
            color=LABEL_COLOR,
        )
        elements.append(tl)
        member_ids.append(tl_id)

    # Don't auto-group knobs
    # group = _make_group(project, f"{label_prefix}s", origin, member_ids, elements)
    # elements.append(group)
    return elements


def _note_name(note: int) -> str:
    """Convert MIDI note number (0-127) to a human-readable name, e.g. C4."""
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    octave = note // 12 - 1
    return f"{names[note % 12]}{octave}"


def _octave_color(note: int, is_black: bool) -> Color:
    """Color-code keys by octave while keeping black keys visually dark."""
    octave_palette = [
        Color(0.95, 0.35, 0.35, 1.0),  # red
        Color(0.98, 0.55, 0.25, 1.0),  # orange
        Color(0.95, 0.82, 0.25, 1.0),  # yellow
        Color(0.45, 0.82, 0.35, 1.0),  # green
        Color(0.25, 0.85, 0.75, 1.0),  # mint
        Color(0.25, 0.65, 0.95, 1.0),  # blue
        Color(0.45, 0.45, 0.95, 1.0),  # indigo
        Color(0.72, 0.45, 0.95, 1.0),  # violet
        Color(0.93, 0.45, 0.82, 1.0),  # magenta
        Color(0.92, 0.50, 0.65, 1.0),  # rose
        Color(0.75, 0.75, 0.75, 1.0),  # high octave gray
    ]
    octave = max(0, min(10, note // 12))
    c = octave_palette[octave]
    if not is_black:
        return c
    return Color(c.r * 0.35, c.g * 0.35, c.b * 0.35, 1.0)


def generate_keyboard(
    project: Project,
    arrangement: str = "Row",
    spacing: float = 12.0,
    origin: Vec3 = None,
    channel: int = 1,
    base_note: int = 0,
    max_note: int = 127,
    label_prefix: str = "Key",
) -> List:
    """Generate a full MIDI keyboard as HitZones across the available note range.

    Each key is set to Hold behavior so press/release produces Note On/Note Off behavior.
    We also include a CC mapping per key (CC number == note number) so each key carries
    explicit CC metadata for users who want both note and CC reference information.
    """
    if origin is None:
        origin = Vec3(0, 0, 0)

    base_note = max(0, min(127, int(base_note)))
    max_note = max(0, min(127, int(max_note)))
    if max_note < base_note:
        base_note, max_note = max_note, base_note

    notes = list(range(base_note, max_note + 1))
    count = len(notes)

    if arrangement == "Circle":
        radius = max(40.0, count * 2.6)
        positions = _circle_positions(count, radius)
    elif arrangement == "Triangle":
        positions = _triangle_positions(count, spacing)
    else:
        positions = _row_positions(count, spacing)

    elements = []

    black_keys = {1, 3, 6, 8, 10}
    for i, (px, py) in enumerate(positions):
        note = notes[i]
        note_name = _note_name(note)
        is_black = (note % 12) in black_keys

        hz_id = project.generate_id("HitZone")
        hz = HitZone(
            unique_id=hz_id,
            display_name=f"{label_prefix} {note_name}",
            transform=Transform(
                translation=Vec3(origin.x + px, origin.y + py, origin.z),
                scale=Vec3(0.22 if not is_black else 0.18, 0.7 if not is_black else 0.45, 0.12),
            ),
            color=_octave_color(note, is_black),
            should_use_velocity_sensitivity=False,
            fixed_midi_velocity_output=127.0,
            midi_note_mappings=[MidiNoteMapping(channel=channel, note=note, velocity=127.0)],
            midi_cc_mappings=[MidiCCMapping(channel=channel, control=note, value=127)],
            behavior="EHitZoneBehavior::Hold",
            midi_message_type="EMidiMessageType::Note",
        )
        elements.append(hz)

        # Add labels for octave starts (C notes) plus min/max note for quick orientation.
        if note % 12 == 0 or note == base_note or note == max_note:
            tl_id = project.generate_id("TextLabel_C")
            tl = TextLabel(
                unique_id=tl_id,
                display_name=(
                    f"{note_name} #{note} Ch{channel}\n"
                    f"CC{note}=127\n"
                    "Note On/Off (Hold)"
                ),
                transform=Transform(
                    translation=Vec3(origin.x + px, origin.y + py, origin.z + 20),
                    scale=Vec3(0.22, 0.22, 0.22),
                ),
                color=_octave_color(note, False),
            )
            elements.append(tl)

    return elements


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_group(
    project: Project,
    name: str,
    origin: Vec3,
    member_ids: List[str],
    member_elements: List,
) -> GroupIE:
    """Create a GroupIE that encloses all member elements."""
    # Compute bounding box from member transforms
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

    grp_id = project.generate_id("GroupIE")
    return GroupIE(
        unique_id=grp_id,
        display_name=name,
        transform=Transform(
            translation=Vec3(cx, cy, cz),
            scale=Vec3(1.0, 1.0, 1.0),
        ),
        color=GROUP_COLOR,
        bounding_box=BoundingBox(
            min=Vec3(-hx, -hy, -hz),
            max=Vec3(hx, hy, hz),
            is_valid=1,
        ),
        group_items=list(member_ids),
    )


# ---------------------------------------------------------------------------
# Template catalogue (used by the editor UI)
# ---------------------------------------------------------------------------

TEMPLATES = {
    # --- Faders (8) ---
    "8 Faders (Row)":       lambda p, o: generate_faders(p, 8, "Row", 30, o, base_cc=1),
    "8 Faders (Circle)":    lambda p, o: generate_faders(p, 8, "Circle", 30, o, base_cc=1),
    "8 Faders (Triangle)":  lambda p, o: generate_faders(p, 8, "Triangle", 30, o, base_cc=1),
    "8 Faders (Grid)":      lambda p, o: generate_faders(p, 8, "Grid", 30, o, base_cc=1),
    "8 Faders (Arc)":       lambda p, o: generate_faders(p, 8, "Arc", 30, o, base_cc=1),
    "8 Faders (Diamond)":   lambda p, o: generate_faders(p, 8, "Diamond", 30, o, base_cc=1),
    "8 Faders (Spiral)":    lambda p, o: generate_faders(p, 8, "Spiral", 80, o, base_cc=1),
    "8 Faders (Zigzag)":    lambda p, o: generate_faders(p, 8, "Zigzag", 30, o, base_cc=1),
    "16 Faders (Row)":      lambda p, o: generate_faders(p, 16, "Row", 30, o, base_cc=1),
    "16 Faders (Grid)":     lambda p, o: generate_faders(p, 16, "Grid", 30, o, base_cc=1),
    "16 Faders (Circle)":   lambda p, o: generate_faders(p, 16, "Circle", 30, o, base_cc=1),

    # --- Knobs (8) ---
    "8 Knobs (Row)":        lambda p, o: generate_knobs(p, 8, "Row", 30, o, base_cc=20),
    "8 Knobs (Circle)":     lambda p, o: generate_knobs(p, 8, "Circle", 30, o, base_cc=20),
    "8 Knobs (Triangle)":   lambda p, o: generate_knobs(p, 8, "Triangle", 30, o, base_cc=20),
    "8 Knobs (Grid)":       lambda p, o: generate_knobs(p, 8, "Grid", 30, o, base_cc=20),
    "8 Knobs (Arc)":        lambda p, o: generate_knobs(p, 8, "Arc", 30, o, base_cc=20),
    "8 Knobs (Diamond)":    lambda p, o: generate_knobs(p, 8, "Diamond", 30, o, base_cc=20),
    "8 Knobs (Spiral)":     lambda p, o: generate_knobs(p, 8, "Spiral", 80, o, base_cc=20),
    "16 Knobs (Row)":       lambda p, o: generate_knobs(p, 16, "Row", 30, o, base_cc=20),
    "16 Knobs (Grid)":      lambda p, o: generate_knobs(p, 16, "Grid", 30, o, base_cc=20),
    "16 Knobs (Circle)":    lambda p, o: generate_knobs(p, 16, "Circle", 30, o, base_cc=20),

    # --- XY Pads (4/8) ---
    "4 XY Pads (Row)":      lambda p, o: generate_xy_pads(p, 4, "Row", 40, o, base_cc_x=40, base_cc_y=50),
    "4 XY Pads (Grid)":     lambda p, o: generate_xy_pads(p, 4, "Grid", 40, o, base_cc_x=40, base_cc_y=50),
    "4 XY Pads (Diamond)":  lambda p, o: generate_xy_pads(p, 4, "Diamond", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Row)":      lambda p, o: generate_xy_pads(p, 8, "Row", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Circle)":   lambda p, o: generate_xy_pads(p, 8, "Circle", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Triangle)": lambda p, o: generate_xy_pads(p, 8, "Triangle", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Grid)":     lambda p, o: generate_xy_pads(p, 8, "Grid", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Arc)":      lambda p, o: generate_xy_pads(p, 8, "Arc", 40, o, base_cc_x=40, base_cc_y=50),

    # --- Drum Pads (8/16) ---
    "8 Drum Pads (Row)":       lambda p, o: generate_drum_pads(p, 8, "Row", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Circle)":    lambda p, o: generate_drum_pads(p, 8, "Circle", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Triangle)":  lambda p, o: generate_drum_pads(p, 8, "Triangle", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Grid)":      lambda p, o: generate_drum_pads(p, 8, "Grid", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Diamond)":   lambda p, o: generate_drum_pads(p, 8, "Diamond", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Arc)":       lambda p, o: generate_drum_pads(p, 8, "Arc", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Row)":      lambda p, o: generate_drum_pads(p, 16, "Row", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Grid)":     lambda p, o: generate_drum_pads(p, 16, "Grid", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Circle)":   lambda p, o: generate_drum_pads(p, 16, "Circle", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Triangle)": lambda p, o: generate_drum_pads(p, 16, "Triangle", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Diamond)":  lambda p, o: generate_drum_pads(p, 16, "Diamond", 30, o, base_note=36, channel=10),
    "16 Drum Pads (Spiral)":   lambda p, o: generate_drum_pads(p, 16, "Spiral", 80, o, base_note=36, channel=10),

    # --- Buttons (8/16) ---
    "8 Buttons (Row)":       lambda p, o: generate_buttons(p, 8, "Row", 25, o, base_cc=70),
    "8 Buttons (Circle)":    lambda p, o: generate_buttons(p, 8, "Circle", 25, o, base_cc=70),
    "8 Buttons (Triangle)":  lambda p, o: generate_buttons(p, 8, "Triangle", 25, o, base_cc=70),
    "8 Buttons (Grid)":      lambda p, o: generate_buttons(p, 8, "Grid", 25, o, base_cc=70),
    "8 Buttons (Arc)":       lambda p, o: generate_buttons(p, 8, "Arc", 25, o, base_cc=70),
    "8 Buttons (Diamond)":   lambda p, o: generate_buttons(p, 8, "Diamond", 25, o, base_cc=70),
    "16 Buttons (Row)":      lambda p, o: generate_buttons(p, 16, "Row", 25, o, base_cc=70),
    "16 Buttons (Grid)":     lambda p, o: generate_buttons(p, 16, "Grid", 25, o, base_cc=70),
    "16 Buttons (Circle)":   lambda p, o: generate_buttons(p, 16, "Circle", 25, o, base_cc=70),
    "16 Buttons (Triangle)": lambda p, o: generate_buttons(p, 16, "Triangle", 25, o, base_cc=70),

    # --- Mixer ---
    "Mixer (8 Faders + 8 Knobs)": None,  # special composite — handled in editor

    # --- Keyboards: 1 Octave (12 keys) ---
    "Keyboard 1 Octave (Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=60, max_note=71, label_prefix="Key"
    ),
    "Keyboard 1 Octave (Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=60, max_note=71, label_prefix="Key"
    ),
    "Keyboard 1 Octave (Triangle)": lambda p, o: generate_keyboard(
        p, arrangement="Triangle", spacing=12, origin=o, channel=1, base_note=60, max_note=71, label_prefix="Key"
    ),

    # --- Keyboards: 2 Octaves (24 keys) ---
    "Keyboard 2 Octaves (Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=48, max_note=71, label_prefix="Key"
    ),
    "Keyboard 2 Octaves (Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=48, max_note=71, label_prefix="Key"
    ),
    "Keyboard 2 Octaves (Triangle)": lambda p, o: generate_keyboard(
        p, arrangement="Triangle", spacing=12, origin=o, channel=1, base_note=48, max_note=71, label_prefix="Key"
    ),

    # --- Keyboards: 3 Octaves (36 keys) ---
    "Keyboard 3 Octaves (Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=48, max_note=83, label_prefix="Key"
    ),
    "Keyboard 3 Octaves (Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=48, max_note=83, label_prefix="Key"
    ),
    "Keyboard 3 Octaves (Triangle)": lambda p, o: generate_keyboard(
        p, arrangement="Triangle", spacing=12, origin=o, channel=1, base_note=48, max_note=83, label_prefix="Key"
    ),

    # --- Keyboards: 5 Octaves (60 keys) ---
    "Keyboard 5 Octaves (Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=36, max_note=95, label_prefix="Key"
    ),
    "Keyboard 5 Octaves (Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=36, max_note=95, label_prefix="Key"
    ),
    "Keyboard 5 Octaves (Triangle)": lambda p, o: generate_keyboard(
        p, arrangement="Triangle", spacing=12, origin=o, channel=1, base_note=36, max_note=95, label_prefix="Key"
    ),

    # --- Keyboards: Full 128 ---
    "Keyboard Full (Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=0, max_note=127, label_prefix="Key"
    ),
    "Keyboard Full (Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=0, max_note=127, label_prefix="Key"
    ),
    "Keyboard Full (Triangle)": lambda p, o: generate_keyboard(
        p, arrangement="Triangle", spacing=12, origin=o, channel=1, base_note=0, max_note=127, label_prefix="Key"
    ),

    "DEBUG: Calibrator Overlay": lambda p, o: generate_calibrator(p, o),
    "DEBUG: Everything Kitchen Sink": lambda p, o: generate_debug_everything(p, o),
}


def generate_mixer(project: Project, origin: Vec3 = None) -> List:
    """Composite: 8 faders in a row with 8 knobs above them."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []

    # Faders at base (CC 1-8)
    fader_origin = Vec3(origin.x, origin.y, origin.z)
    faders = generate_faders(project, 8, "Row", 30, fader_origin, base_cc=1, label_prefix="Vol")
    all_elements.extend(faders)

    # Knobs above (CC 9-16, instead of 20-27 to keep mixer compact)
    knob_origin = Vec3(origin.x, origin.y, origin.z + 60)
    knobs = generate_knobs(project, 8, "Row", 30, knob_origin, base_cc=9, label_prefix="Pan")
    all_elements.extend(knobs)

    return all_elements


def generate_acid_banger_starter(project: Project, origin: Vec3 = None) -> List:
    """Acid-banger starter mapped to Channel 1 CC16-39."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []

    all_elements.extend(
        generate_faders(
            project,
            count=8,
            arrangement="Row",
            spacing=28,
            origin=Vec3(origin.x, origin.y, origin.z),
            base_cc=16,
            channel=1,
            label_prefix="Acid Fader",
            color_mode="Gradient",
        )
    )

    all_elements.extend(
        generate_knobs(
            project,
            count=8,
            arrangement="Row",
            spacing=28,
            origin=Vec3(origin.x, origin.y + 45, origin.z),
            base_cc=24,
            channel=1,
            label_prefix="Acid Knob",
            color_mode="Cool",
        )
    )

    all_elements.extend(
        generate_buttons(
            project,
            count=8,
            arrangement="Row",
            spacing=24,
            origin=Vec3(origin.x, origin.y + 80, origin.z),
            base_cc=32,
            channel=1,
            label_prefix="Acid Trig",
        )
    )

    title_id = project.generate_id("TextLabel_C")
    title = TextLabel(
        unique_id=title_id,
        display_name="Acid Banger Starter Ch1 CC16-39",
        transform=Transform(
            translation=Vec3(origin.x, origin.y + 112, origin.z),
            scale=Vec3(0.35, 0.35, 0.35),
        ),
        color=LABEL_COLOR,
    )
    all_elements.append(title)

    return all_elements


def generate_bitwig_performance(project: Project, origin: Vec3 = None) -> List:
    """Bitwig-style performance layout: XY pads, macro knobs, scene selectors, layer mixers."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    # Row 1: Foundation XY touch pads
    all_elements.extend(generate_xy_pads(
        project, count=2, arrangement="Row", spacing=80, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc_x=20, base_cc_y=28, channel=1, label_prefix="Bitwig XY"
    ))
    # Row 2: Layer mixer faders
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 35, origin.z),
        base_cc=1, channel=1, label_prefix="Layer Mix", color_mode="Gradient"
    ))
    # Row 3: Macro knobs (macro control)
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 65, origin.z),
        base_cc=40, channel=1, label_prefix="Macro", color_mode="Cool"
    ))
    # Row 4: Macro knobs (second bank)
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 90, origin.z),
        base_cc=48, channel=1, label_prefix="Macro 2", color_mode="Neon"
    ))
    # Row 5: Scene selectors (first bank)
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 115, origin.z),
        base_cc=80, channel=1, label_prefix="Scene 1"
    ))
    # Row 6: Scene selectors (second bank)
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 140, origin.z),
        base_cc=88, channel=1, label_prefix="Scene 2"
    ))
    return all_elements


def generate_reaper_mix_transport(project: Project, origin: Vec3 = None) -> List:
    """Reaper-oriented mixer + transport controls."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=1, channel=1, label_prefix="Track", color_mode="Gradient"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 50, origin.z),
        base_cc=21, channel=1, label_prefix="Pan", color_mode="Monochrome"
    ))
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=22, origin=Vec3(origin.x, origin.y + 35, origin.z),
        base_cc=90, channel=1, label_prefix="Transport"
    ))
    return all_elements


def generate_resolume_performance(project: Project, origin: Vec3 = None) -> List:
    """Resolume-style VJ surface: clip grid, layer mixer, scene selector, video effects."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    # Row 1: Video effect faders (top control row)
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=70, channel=1, label_prefix="FX", color_mode="Warm"
    ))
    # Row 2: Clip triggering grid (4x4 pads - main performance area)
    all_elements.extend(generate_drum_pads(
        project, count=16, arrangement="Grid", spacing=24, origin=Vec3(origin.x, origin.y + 40, origin.z),
        base_note=36, channel=1, label_prefix="Clip"
    ))
    # Row 3: Layer mixer faders
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 95, origin.z),
        base_cc=1, channel=1, label_prefix="Layer Mix", color_mode="Gradient"
    ))
    # Row 4: Layer knobs (layer effect parameters)
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=28, origin=Vec3(origin.x, origin.y + 120, origin.z),
        base_cc=48, channel=1, label_prefix="Layer Fx", color_mode="Neon"
    ))
    # Row 5: Layer selection buttons
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 145, origin.z),
        base_cc=88, channel=1, label_prefix="Layer Select"
    ))
    # Row 6: Scene selection buttons
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 170, origin.z),
        base_cc=96, channel=1, label_prefix="Scene"
    ))
    return all_elements


def generate_mc303_groovebox(project: Project, origin: Vec3 = None) -> List:
    """MC-303-inspired groovebox bank."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_drum_pads(
        project, count=16, arrangement="Grid", spacing=26, origin=Vec3(origin.x, origin.y, origin.z),
        base_note=36, channel=10, label_prefix="303 Pad"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 55, origin.z),
        base_cc=14, channel=1, label_prefix="303 Tone", color_mode="Warm"
    ))
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=22, origin=Vec3(origin.x, origin.y + 30, origin.z),
        base_cc=100, channel=1, label_prefix="303 Seq"
    ))
    return all_elements


def generate_mc505_groovebox(project: Project, origin: Vec3 = None) -> List:
    """MC-505-inspired expanded groovebox bank."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_drum_pads(
        project, count=16, arrangement="Grid", spacing=24, origin=Vec3(origin.x, origin.y, origin.z),
        base_note=36, channel=10, label_prefix="505 Pad"
    ))
    all_elements.extend(generate_knobs(
        project, count=16, arrangement="Grid", spacing=24, origin=Vec3(origin.x, origin.y + 65, origin.z),
        base_cc=24, channel=1, label_prefix="505 Knob", color_mode="Neon"
    ))
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 25, origin.z),
        base_cc=80, channel=1, label_prefix="505 Fader", color_mode="Cool"
    ))
    return all_elements


def generate_sugarbytes_drumcomputer(project: Project, origin: Vec3 = None) -> List:
    """Sugarbytes DrumComputer-style drum lanes and macro controls."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_drum_pads(
        project, count=16, arrangement="Grid", spacing=22, origin=Vec3(origin.x, origin.y, origin.z),
        base_note=36, channel=10, label_prefix="DrComp"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 50, origin.z),
        base_cc=52, channel=1, label_prefix="Macro", color_mode="Cool"
    ))
    all_elements.extend(generate_faders(
        project, count=4, arrangement="Row", spacing=30, origin=Vec3(origin.x, origin.y + 20, origin.z),
        base_cc=12, channel=1, label_prefix="Lane", color_mode="Gradient"
    ))
    return all_elements


def generate_aum_ios_performance(project: Project, origin: Vec3 = None) -> List:
    """AUM for iOS style performance control surface."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=26, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=1, channel=1, label_prefix="AUM Ch", color_mode="Cool"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=26, origin=Vec3(origin.x, origin.y + 45, origin.z),
        base_cc=16, channel=1, label_prefix="AUM Send", color_mode="Neon"
    ))
    all_elements.extend(generate_xy_pads(
        project, count=2, arrangement="Row", spacing=85, origin=Vec3(origin.x, origin.y + 15, origin.z),
        base_cc_x=48, base_cc_y=56, channel=1, label_prefix="AUM XY"
    ))
    return all_elements


def generate_ruismaker_noir(project: Project, origin: Vec3 = None) -> List:
    """Ruismaker Noir inspired drum performance layout."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_drum_pads(
        project, count=16, arrangement="Grid", spacing=23, origin=Vec3(origin.x, origin.y, origin.z),
        base_note=36, channel=10, label_prefix="Noir"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 55, origin.z),
        base_cc=24, channel=1, label_prefix="Noir Macro", color_mode="Warm"
    ))
    return all_elements


def generate_renoise_mappings(project: Project, origin: Vec3 = None) -> List:
    """Renoise tracker-focused mapping surface with transport and pattern controls."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_buttons(
        project, count=16, arrangement="Grid", spacing=22, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=64, channel=1, label_prefix="Pattern"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 50, origin.z),
        base_cc=20, channel=1, label_prefix="Track", color_mode="Monochrome"
    ))
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=22, origin=Vec3(origin.x, origin.y + 25, origin.z),
        base_cc=96, channel=1, label_prefix="Transport"
    ))
    return all_elements


def generate_reaktor_performance(project: Project, origin: Vec3 = None) -> List:
    """Reaktor ensemble-style generic macro and XY control surface."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_xy_pads(
        project, count=4, arrangement="Grid", spacing=44, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc_x=32, base_cc_y=40, channel=1, label_prefix="Reaktor XY"
    ))
    all_elements.extend(generate_knobs(
        project, count=16, arrangement="Grid", spacing=22, origin=Vec3(origin.x, origin.y + 70, origin.z),
        base_cc=72, channel=1, label_prefix="Macro", color_mode="Cool"
    ))
    return all_elements


def generate_m_audio_code49(project: Project, origin: Vec3 = None) -> List:
    """M-Audio CODE49 style preset surface."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_faders(
        project, count=9, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=1, channel=1, label_prefix="CODE49 Fader", color_mode="Gradient"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 45, origin.z),
        base_cc=16, channel=1, label_prefix="CODE49 Knob", color_mode="Warm"
    ))
    all_elements.extend(generate_drum_pads(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 20, origin.z),
        base_note=36, channel=10, label_prefix="CODE49 Pad"
    ))
    return all_elements


def generate_behringer_x_touch(project: Project, origin: Vec3 = None) -> List:
    """Behringer X-Touch style mixer/controller surface."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=26, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=1, channel=1, label_prefix="XTouch Fader", color_mode="Monochrome"
    ))
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=26, origin=Vec3(origin.x, origin.y + 45, origin.z),
        base_cc=16, channel=1, label_prefix="XTouch Enc", color_mode="Cool"
    ))
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=22, origin=Vec3(origin.x, origin.y + 20, origin.z),
        base_cc=80, channel=1, label_prefix="XTouch Tr"
    ))
    return all_elements


def generate_novation_x_station(project: Project, origin: Vec3 = None) -> List:
    """Novation X-Station style controller surface for remote VR control."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    # Typical X-Station style banking: 8 knobs + 8 faders + transport buttons + pads.
    all_elements.extend(generate_knobs(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 50, origin.z),
        base_cc=16, channel=1, label_prefix="XStation Knob", color_mode="Cool"
    ))
    all_elements.extend(generate_faders(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y, origin.z),
        base_cc=1, channel=1, label_prefix="XStation Fader", color_mode="Monochrome"
    ))
    all_elements.extend(generate_buttons(
        project, count=8, arrangement="Row", spacing=22, origin=Vec3(origin.x, origin.y + 30, origin.z),
        base_cc=80, channel=1, label_prefix="XStation Tr"
    ))
    all_elements.extend(generate_drum_pads(
        project, count=8, arrangement="Row", spacing=24, origin=Vec3(origin.x, origin.y + 80, origin.z),
        base_note=36, channel=10, label_prefix="XStation Pad"
    ))
    return all_elements




def generate_serum_performance(project: Project, origin: Vec3 = None) -> List:
    """Xfer Serum-style macros, filters, and performance XY (map in Serum MIDI learn)."""
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 1
    o = origin
    el = []
    el.extend(generate_xy_pads(project, 2, "Row", 50, o, base_cc_x=80, base_cc_y=90, channel=ch, label_prefix="Serum XY"))
    el.extend(generate_knobs(project, 4, "Row", 28, Vec3(o.x, o.y + 55, o.z), base_cc=70, channel=ch, label_prefix="Serum Macro", color_mode="Neon"))
    el.extend(generate_knobs(project, 8, "Grid", 24, Vec3(o.x, o.y + 25, o.z), base_cc=20, channel=ch, label_prefix="Serum Ctrl", color_mode="Cool"))
    el.extend(generate_faders(project, 4, "Row", 32, Vec3(o.x, o.y - 20, o.z), base_cc=40, channel=ch, label_prefix="Serum Mix", color_mode="Gradient"))
    return el


def generate_serumfx_performance(project: Project, origin: Vec3 = None) -> List:
    """SerumFX-style effect chain (map in SerumFX MIDI learn)."""
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 1
    o = origin
    el = []
    el.extend(generate_xy_pads(project, 1, "Row", 50, o, base_cc_x=40, base_cc_y=48, channel=ch, label_prefix="SerumFX XY"))
    el.extend(generate_knobs(project, 8, "Grid", 26, Vec3(o.x, o.y + 45, o.z), base_cc=50, channel=ch, label_prefix="SerumFX Macro", color_mode="Warm"))
    el.extend(generate_faders(project, 6, "Row", 28, Vec3(o.x, o.y, o.z), base_cc=20, channel=ch, label_prefix="SerumFX Send", color_mode="Monochrome"))
    el.extend(generate_buttons(project, 6, "Row", 24, Vec3(o.x, o.y - 30, o.z), base_cc=98, channel=ch, label_prefix="SerumFX Bypass"))
    return el


def _reason_note(project: Project, name: str, channel: int, base_cc: int, origin: Vec3) -> List:
    tl_id = project.generate_id("TextLabel_C")
    tl = TextLabel(
        unique_id=tl_id,
        display_name=f"{name}\nCh{channel} CC base {base_cc}\nReason Remote Override",
        transform=Transform(translation=Vec3(origin.x, origin.y - 30, origin.z + 30), scale=Vec3(0.25, 0.25, 0.25)),
        color=LABEL_COLOR,
    )
    return [tl]


def generate_reason_subtractor(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 1
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "Subtractor", ch, b, o))
    el.extend(generate_knobs(project, 8, "Row", 26, Vec3(o.x, o.y + 40, o.z), base_cc=b, channel=ch, label_prefix="Sub OSC", color_mode="Cool"))
    el.extend(generate_knobs(project, 8, "Row", 26, Vec3(o.x, o.y + 15, o.z), base_cc=b + 8, channel=ch, label_prefix="Sub Flt", color_mode="Cool"))
    el.extend(generate_xy_pads(project, 1, "Row", 45, Vec3(o.x, o.y - 15, o.z), base_cc_x=b + 16, base_cc_y=b + 17, channel=ch, label_prefix="Sub XY"))
    return el


def generate_reason_malstrom(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 2
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "Malstrom", ch, b, o))
    el.extend(generate_knobs(project, 8, "Grid", 24, Vec3(o.x, o.y + 35, o.z), base_cc=b, channel=ch, label_prefix="Mal Grains", color_mode="Neon"))
    el.extend(generate_faders(project, 4, "Row", 28, Vec3(o.x, o.y, o.z), base_cc=b + 8, channel=ch, label_prefix="Mal Mod", color_mode="Gradient"))
    el.extend(generate_xy_pads(project, 1, "Row", 45, Vec3(o.x, o.y - 25, o.z), base_cc_x=b + 12, base_cc_y=b + 13, channel=ch, label_prefix="Mal XY"))
    return el


def generate_reason_thor(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 3
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "Thor", ch, b, o))
    el.extend(generate_knobs(project, 12, "Grid", 22, Vec3(o.x, o.y + 40, o.z), base_cc=b, channel=ch, label_prefix="Thor", color_mode="Rainbow"))
    el.extend(generate_xy_pads(project, 2, "Row", 40, Vec3(o.x, o.y + 5, o.z), base_cc_x=b + 12, base_cc_y=b + 14, channel=ch, label_prefix="Thor XY"))
    return el


def generate_reason_europa(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 4
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "Europa", ch, b, o))
    el.extend(generate_knobs(project, 8, "Row", 26, Vec3(o.x, o.y + 40, o.z), base_cc=b, channel=ch, label_prefix="Eur Engine", color_mode="Cool"))
    el.extend(generate_xy_pads(project, 2, "Grid", 44, Vec3(o.x, o.y + 10, o.z), base_cc_x=b + 8, base_cc_y=b + 10, channel=ch, label_prefix="Eur XY"))
    el.extend(generate_faders(project, 4, "Row", 28, Vec3(o.x, o.y - 20, o.z), base_cc=b + 12, channel=ch, label_prefix="Eur Shp", color_mode="Monochrome"))
    return el


def generate_reason_grain(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 5
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "Grain", ch, b, o))
    el.extend(generate_knobs(project, 8, "Grid", 24, Vec3(o.x, o.y + 35, o.z), base_cc=b, channel=ch, label_prefix="Grain", color_mode="Warm"))
    el.extend(generate_xy_pads(project, 1, "Row", 48, Vec3(o.x, o.y, o.z), base_cc_x=b + 8, base_cc_y=b + 9, channel=ch, label_prefix="Grain XY"))
    return el


def generate_reason_kong(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 6
    o = origin
    el = []
    el.extend(_reason_note(project, "Kong", ch, 20, o))
    el.extend(generate_drum_pads(project, 16, "Grid", 26, Vec3(o.x, o.y + 30, o.z), base_note=36, channel=ch, label_prefix="Kong Pad"))
    el.extend(generate_knobs(project, 8, "Row", 24, Vec3(o.x, o.y - 20, o.z), base_cc=20, channel=ch, label_prefix="Kong Ctrl", color_mode="Neon"))
    return el


def generate_reason_redrum(project: Project, origin: Vec3 = None) -> List:
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 7
    o = origin
    el = []
    el.extend(_reason_note(project, "Redrum", ch, 20, o))
    el.extend(generate_drum_pads(project, 10, "Row", 22, Vec3(o.x, o.y + 40, o.z), base_note=36, channel=ch, label_prefix="Redrum"))
    el.extend(generate_buttons(project, 8, "Row", 22, Vec3(o.x, o.y + 10, o.z), base_cc=20, channel=ch, label_prefix="Redrum Step"))
    return el


def generate_reason_rex_octorex(project: Project, origin: Vec3 = None) -> List:
    """Dr. Octo Rex / REX-style slices (Reason Remote Override)."""
    if origin is None:
        origin = Vec3(0, 0, 0)
    ch = 8
    b = 20
    o = origin
    el = []
    el.extend(_reason_note(project, "REX / OctoRex", ch, b, o))
    el.extend(generate_knobs(project, 8, "Row", 24, Vec3(o.x, o.y + 40, o.z), base_cc=b, channel=ch, label_prefix="REX", color_mode="Cool"))
    el.extend(generate_xy_pads(project, 1, "Row", 45, Vec3(o.x, o.y + 10, o.z), base_cc_x=b + 8, base_cc_y=b + 9, channel=ch, label_prefix="REX XY"))
    el.extend(generate_buttons(project, 8, "Row", 22, Vec3(o.x, o.y - 15, o.z), base_cc=b + 10, channel=ch, label_prefix="REX Trig"))
    return el



def generate_calibrator(project: Project, origin: Vec3 = None) -> List:
    """Create a mixed-reality calibration helper made from TextLabels."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    elements = []
    member_ids = []

    marker_color = Color(0.2, 0.95, 0.35, 1.0)
    info_color = Color(1.0, 1.0, 1.0, 1.0)

    def _label(text: str, x: float, y: float, z: float, scale: float, color: Color) -> None:
        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=text,
            transform=Transform(
                translation=Vec3(origin.x + x, origin.y + y, origin.z + z),
                scale=Vec3(scale, scale, scale),
            ),
            color=color,
        )
        elements.append(label)
        member_ids.append(label_id)

    # Main center marker and axis indicators.
    _label("+", 0, 0, 0, 0.8, marker_color)
    _label("X+", 80, 0, 0, 0.3, marker_color)
    _label("X-", -80, 0, 0, 0.3, marker_color)
    _label("Y+", 0, 80, 0, 0.3, marker_color)
    _label("Y-", 0, -80, 0, 0.3, marker_color)

    # Corner references to align frame edges.
    corner_offset = 140
    _label("TL", -corner_offset, corner_offset, 0, 0.28, marker_color)
    _label("TR", corner_offset, corner_offset, 0, 0.28, marker_color)
    _label("BL", -corner_offset, -corner_offset, 0, 0.28, marker_color)
    _label("BR", corner_offset, -corner_offset, 0, 0.28, marker_color)

    # Scale ticks every 50cm in front of camera (Z+).
    for i in range(1, 6):
        z = i * 50.0
        _label(f"{int(z)}cm", -55, 0, z, 0.2, info_color)
        _label("|", -20, 0, z, 0.22, marker_color)

    _label("HORIZON", 0, 105, 0, 0.22, info_color)
    _label("CENTER", 0, 16, 0, 0.2, info_color)
    _label("CALIBRATOR", 0, -112, 0, 0.3, info_color)

    group = _make_group(project, "Calibrator Overlay", origin, member_ids, elements)
    elements.append(group)

    return elements


def generate_debug_everything(project: Project, origin: Vec3 = None) -> List:
    """DEBUG: Comprehensive test template with every element type and MIDI range.

    Creates multiple circular layers with different element types to test:
    - All MorphZone configurations (1D, 2D, 3D)
    - All HitZone behaviors (Hold, Toggle, TimedClose)
    - Wide MIDI range (notes 24-127, CCs 1-127)
    - Different scaling and positioning
    - Multiple channels (1-16)
    """
    if origin is None:
        origin = Vec3(0, 0, 0)

    all_elements = []
    all_member_ids = []

    # Layer heights for stacking
    layer_height = 80

    # ============ LAYER 1: 1D FADERS (bottom) ============
    layer1_origin = Vec3(origin.x, origin.y, origin.z - layer_height * 2)

    # Inner circle: 8 Y-axis faders (CCs 1-8, channel 1)
    faders_y = []
    for i in range(8):
        angle = 2 * math.pi * i / 8
        x = 60 * math.cos(angle)
        y = 60 * math.sin(angle)

        mz_id = project.generate_id("MorphZone")
        mz = MorphZone(
            unique_id=mz_id,
            display_name=f"Y-Fader {i+1}",
            transform=Transform(
                translation=Vec3(layer1_origin.x + x, layer1_origin.y + y, layer1_origin.z),
                scale=Vec3(0.2, 0.8, 0.2),
            ),
            color=_get_colors(8, "Cool")[i],
            is_y_axis_enabled=True,
            y_axis_cc_mappings=[MidiCCMapping(channel=1, control=1 + i, value=64)],
            dimensions="EDimensions::One",
            soloed_axis="Y",
        )
        faders_y.append(mz)

        # Label
        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=f"CC{1+i}",
            transform=Transform(
                translation=Vec3(layer1_origin.x + x, layer1_origin.y + y + 20, layer1_origin.z),
                scale=Vec3(0.3, 0.3, 0.3),
            ),
            color=LABEL_COLOR,
        )
        faders_y.append(label)

    # Outer circle: 8 X-axis faders (CCs 16-23, channel 2)
    for i in range(8):
        angle = 2 * math.pi * i / 8
        x = 120 * math.cos(angle)
        y = 120 * math.sin(angle)

        mz_id = project.generate_id("MorphZone")
        mz = MorphZone(
            unique_id=mz_id,
            display_name=f"X-Fader {i+1}",
            transform=Transform(
                translation=Vec3(layer1_origin.x + x, layer1_origin.y + y, layer1_origin.z),
                scale=Vec3(0.8, 0.2, 0.2),
            ),
            color=_get_colors(8, "Warm")[i],
            is_x_axis_enabled=True,
            x_axis_cc_mappings=[MidiCCMapping(channel=2, control=16 + i, value=64)],
            dimensions="EDimensions::One",
            soloed_axis="X",
        )
        faders_y.append(mz)

    all_elements.extend(faders_y)
    all_member_ids.extend([e.unique_id for e in faders_y])

    # ============ LAYER 2: 2D XY PADS (middle) ============
    layer2_origin = Vec3(origin.x, origin.y, origin.z)

    xy_pads = []
    for i in range(12):
        angle = 2 * math.pi * i / 12
        x = 80 * math.cos(angle)
        y = 80 * math.sin(angle)

        mz_id = project.generate_id("MorphZone")
        # Use different CC ranges for X and Y
        x_cc = 32 + i
        y_cc = 64 + i
        channel = (i % 4) + 1  # spread across channels 1-4

        mz = MorphZone(
            unique_id=mz_id,
            display_name=f"XY-Pad {i+1}",
            transform=Transform(
                translation=Vec3(layer2_origin.x + x, layer2_origin.y + y, layer2_origin.z),
                scale=Vec3(0.6, 0.6, 0.2),
            ),
            color=_get_colors(12, "Rainbow")[i],
            is_x_axis_enabled=True,
            x_axis_cc_mappings=[MidiCCMapping(channel=channel, control=x_cc, value=64)],
            is_y_axis_enabled=True,
            y_axis_cc_mappings=[MidiCCMapping(channel=channel, control=y_cc, value=64)],
            dimensions="EDimensions::Two",
        )
        xy_pads.append(mz)

        # Label with MIDI info
        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=f"XY Ch{channel}\nX:CC{x_cc} Y:CC{y_cc}",
            transform=Transform(
                translation=Vec3(layer2_origin.x + x, layer2_origin.y + y + 25, layer2_origin.z),
                scale=Vec3(0.25, 0.25, 0.25),
            ),
            color=LABEL_COLOR,
        )
        xy_pads.append(label)

    all_elements.extend(xy_pads)
    all_member_ids.extend([e.unique_id for e in xy_pads])

    # ============ LAYER 3: 3D XYZ KNOBS (upper middle) ============
    layer3_origin = Vec3(origin.x, origin.y, origin.z + layer_height)

    xyz_knobs = []
    for i in range(10):
        angle = 2 * math.pi * i / 10
        x = 70 * math.cos(angle)
        y = 70 * math.sin(angle)

        mz_id = project.generate_id("MorphZone")
        # High CC range for 3D knobs
        x_cc = 96 + (i * 3)
        y_cc = 97 + (i * 3)
        z_cc = 98 + (i * 3)
        channel = (i % 8) + 1  # spread across channels 1-8

        mz = MorphZone(
            unique_id=mz_id,
            display_name=f"XYZ-Knob {i+1}",
            transform=Transform(
                translation=Vec3(layer3_origin.x + x, layer3_origin.y + y, layer3_origin.z),
                scale=Vec3(0.5, 0.5, 0.5),
            ),
            color=_get_colors(10, "Neon")[i],
            is_x_axis_enabled=True,
            x_axis_cc_mappings=[MidiCCMapping(channel=channel, control=x_cc % 128, value=64)],
            is_y_axis_enabled=True,
            y_axis_cc_mappings=[MidiCCMapping(channel=channel, control=y_cc % 128, value=64)],
            is_z_axis_enabled=True,
            z_axis_cc_mappings=[MidiCCMapping(channel=channel, control=z_cc % 128, value=64)],
            dimensions="EDimensions::Three",
        )
        xyz_knobs.append(mz)

        # Label
        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=f"XYZ Ch{channel}\n{x_cc%128}/{y_cc%128}/{z_cc%128}",
            transform=Transform(
                translation=Vec3(layer3_origin.x + x, layer3_origin.y + y + 20, layer3_origin.z),
                scale=Vec3(0.2, 0.2, 0.2),
            ),
            color=LABEL_COLOR,
        )
        xyz_knobs.append(label)

    all_elements.extend(xyz_knobs)
    all_member_ids.extend([e.unique_id for e in xyz_knobs])

    # ============ LAYER 4: DRUM PADS (Hold behavior) ============
    layer4_origin = Vec3(origin.x, origin.y, origin.z + layer_height * 2)

    drum_pads = []
    # Wide note range: chromatic scale from C1 to C6 (24-84)
    note_start = 24  # C1
    for i in range(16):
        angle = 2 * math.pi * i / 16
        x = 90 * math.cos(angle)
        y = 90 * math.sin(angle)

        hz_id = project.generate_id("HitZone")
        note = note_start + (i * 3)  # every 3rd note for wider spread
        channel = (i % 16) + 1  # all 16 MIDI channels

        hz = HitZone(
            unique_id=hz_id,
            display_name=f"Drum {i+1}",
            transform=Transform(
                translation=Vec3(layer4_origin.x + x, layer4_origin.y + y, layer4_origin.z),
                scale=Vec3(0.7, 0.7, 0.7),
            ),
            color=_get_colors(16, "Warm")[i],
            midi_note_mappings=[MidiNoteMapping(channel=channel, note=note, velocity=100)],
            behavior="EHitZoneBehavior::Hold",
        )
        drum_pads.append(hz)

        # Label with note name
        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        octave = note // 12 - 1
        note_name = note_names[note % 12] + str(octave)

        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=f"{note_name} (#{note})\nCh{channel}",
            transform=Transform(
                translation=Vec3(layer4_origin.x + x, layer4_origin.y + y + 15, layer4_origin.z),
                scale=Vec3(0.25, 0.25, 0.25),
            ),
            color=LABEL_COLOR,
        )
        drum_pads.append(label)

    all_elements.extend(drum_pads)
    all_member_ids.extend([e.unique_id for e in drum_pads])

    # ============ LAYER 5: TOGGLE BUTTONS (top) ============
    layer5_origin = Vec3(origin.x, origin.y, origin.z + layer_height * 3)

    buttons = []
    # High note range for buttons
    note_start = 96
    for i in range(8):
        angle = 2 * math.pi * i / 8
        x = 50 * math.cos(angle)
        y = 50 * math.sin(angle)

        hz_id = project.generate_id("HitZone")
        note = note_start + i
        channel = (i % 4) + 1

        hz = HitZone(
            unique_id=hz_id,
            display_name=f"Toggle {i+1}",
            transform=Transform(
                translation=Vec3(layer5_origin.x + x, layer5_origin.y + y, layer5_origin.z),
                scale=Vec3(0.4, 0.4, 0.4),
            ),
            color=_get_colors(8, "Single")[0],  # all same color for toggles
            midi_note_mappings=[MidiNoteMapping(channel=channel, note=note, velocity=127)],
            behavior="EHitZoneBehavior::Toggle",
            toggle_state=False,
        )
        buttons.append(hz)

        label_id = project.generate_id("TextLabel_C")
        label = TextLabel(
            unique_id=label_id,
            display_name=f"Toggle\n#{note}",
            transform=Transform(
                translation=Vec3(layer5_origin.x + x, layer5_origin.y + y + 12, layer5_origin.z),
                scale=Vec3(0.2, 0.2, 0.2),
            ),
            color=LABEL_COLOR,
        )
        buttons.append(label)

    all_elements.extend(buttons)
    all_member_ids.extend([e.unique_id for e in buttons])

    # ============ CENTER: TIMED CLOSE SPECIAL ============
    # Special element in the center for TimedClose behavior
    center_hz_id = project.generate_id("HitZone")
    center_hz = HitZone(
        unique_id=center_hz_id,
        display_name="TimedClose Special",
        transform=Transform(
            translation=Vec3(origin.x, origin.y, origin.z),
            scale=Vec3(1.0, 1.0, 1.0),
        ),
        color=Color(1.0, 0.0, 0.0, 1.0),  # bright red
        midi_note_mappings=[MidiNoteMapping(channel=10, note=60, velocity=127)],  # C4 on channel 10 (drums)
        behavior="EHitZoneBehavior::TimedClose",
        timed_close_seconds=0.5,
    )
    all_elements.append(center_hz)
    all_member_ids.append(center_hz_id)

    # Center label
    center_label_id = project.generate_id("TextLabel_C")
    center_label = TextLabel(
        unique_id=center_label_id,
        display_name="🐛 DEBUG CENTER\nTimedClose 500ms\nC4 Ch10",
        transform=Transform(
            translation=Vec3(origin.x, origin.y + 25, origin.z),
            scale=Vec3(0.4, 0.4, 0.4),
        ),
        color=Color(1.0, 1.0, 0.0, 1.0),  # bright yellow
    )
    all_elements.append(center_label)
    all_member_ids.append(center_label_id)

    # ============ CREATE MASTER GROUP ============
    group = _make_group(project, "DEBUG: Everything Kitchen Sink", origin, all_member_ids, all_elements)
    all_elements.append(group)

    return all_elements


# Replace the sentinel with the actual function
TEMPLATES["Mixer (8 Faders + 8 Knobs)"] = lambda p, o: generate_mixer(p, o)
TEMPLATES["Acid Banger Starter (CC16-39)"] = lambda p, o: generate_acid_banger_starter(p, o)

# DAW / Performance packs
TEMPLATES["Bitwig: Performance Grid"] = lambda p, o: generate_bitwig_performance(p, o)
TEMPLATES["Reaper: Mixer + Transport"] = lambda p, o: generate_reaper_mix_transport(p, o)
TEMPLATES["Resolume: Clip + Layer Performance"] = lambda p, o: generate_resolume_performance(p, o)
TEMPLATES["MC-303 Groovebox"] = lambda p, o: generate_mc303_groovebox(p, o)
TEMPLATES["MC-505 Groovebox"] = lambda p, o: generate_mc505_groovebox(p, o)
TEMPLATES["Sugarbytes DrumComputer"] = lambda p, o: generate_sugarbytes_drumcomputer(p, o)
TEMPLATES["AUM iOS: Mixer + XY"] = lambda p, o: generate_aum_ios_performance(p, o)
TEMPLATES["Ruismaker Noir"] = lambda p, o: generate_ruismaker_noir(p, o)
TEMPLATES["Renoise: Pattern + Transport"] = lambda p, o: generate_renoise_mappings(p, o)
TEMPLATES["Reaktor: Ensemble Macros"] = lambda p, o: generate_reaktor_performance(p, o)
TEMPLATES["M-Audio CODE49 Preset"] = lambda p, o: generate_m_audio_code49(p, o)
TEMPLATES["Behringer X-Touch"] = lambda p, o: generate_behringer_x_touch(p, o)
TEMPLATES["Novation X-Station"] = lambda p, o: generate_novation_x_station(p, o)

TEMPLATES["Serum: Performance + Macros"] = lambda p, o: generate_serum_performance(p, o)
TEMPLATES["SerumFX: Effects + Mix"] = lambda p, o: generate_serumfx_performance(p, o)
TEMPLATES["Reason: Subtractor"] = lambda p, o: generate_reason_subtractor(p, o)
TEMPLATES["Reason: Malstrom"] = lambda p, o: generate_reason_malstrom(p, o)
TEMPLATES["Reason: Thor"] = lambda p, o: generate_reason_thor(p, o)
TEMPLATES["Reason: Europa"] = lambda p, o: generate_reason_europa(p, o)
TEMPLATES["Reason: Grain"] = lambda p, o: generate_reason_grain(p, o)
TEMPLATES["Reason: Kong"] = lambda p, o: generate_reason_kong(p, o)
TEMPLATES["Reason: Redrum"] = lambda p, o: generate_reason_redrum(p, o)
TEMPLATES["Reason: REX / OctoRex"] = lambda p, o: generate_reason_rex_octorex(p, o)


# ---------------------------------------------------------------------------
# Fun Shapes — TextLabel "pixel art" overlays
# ---------------------------------------------------------------------------

# Colour shortcuts for multi-colour grids
_C = {
    "R": Color(1.0, 0.2, 0.2, 1.0),    # red
    "G": Color(0.2, 0.85, 0.3, 1.0),   # green
    "B": Color(0.2, 0.5, 1.0, 1.0),    # blue
    "Y": Color(1.0, 0.9, 0.2, 1.0),    # yellow
    "P": Color(0.7, 0.3, 1.0, 1.0),    # purple
    "C": Color(0.1, 0.9, 0.9, 1.0),    # cyan
    "O": Color(1.0, 0.5, 0.1, 1.0),    # orange
    "K": Color(1.0, 0.4, 0.7, 1.0),    # pink
    "W": Color(1.0, 1.0, 1.0, 1.0),    # white
    "N": Color(0.55, 0.35, 0.15, 1.0), # brown
    "A": Color(0.5, 0.5, 0.5, 1.0),    # gray
    "D": Color(0.25, 0.25, 0.25, 1.0), # dark gray
    "L": Color(0.85, 0.85, 0.85, 1.0), # light gray
    "M": Color(0.9, 0.2, 0.7, 1.0),    # magenta
    "T": Color(0.1, 0.7, 0.6, 1.0),    # teal
    "F": Color(0.95, 0.7, 0.4, 1.0),   # flesh/tan
    "#": Color(1.0, 1.0, 1.0, 1.0),    # default white (for single-color shapes)
}


def _shape_from_grid(
    project: Project,
    grid: List[str],
    origin: Vec3,
    cell_size: float = 8.0,
    color_map: dict = None,
    default_color: Color = None,
    label_char: str = "█",
) -> List:
    """Convert a list-of-strings grid into positioned TextLabels.

    Each non-space character in the grid becomes a TextLabel.
    Characters are looked up in *color_map* (char -> Color); anything
    not found uses *default_color*.  Row 0 = top (highest Z).
    The grid is centred on *origin*.
    """
    if origin is None:
        origin = Vec3(0, 0, 0)
    if default_color is None:
        default_color = Color(1.0, 1.0, 1.0, 1.0)
    if color_map is None:
        color_map = _C

    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)

    ox = -cols * cell_size / 2
    oz = rows * cell_size / 2

    elements: List = []
    for r, line in enumerate(grid):
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            color = color_map.get(ch, default_color)
            tl_id = project.generate_id("TextLabel_C")
            tl = TextLabel(
                unique_id=tl_id,
                display_name=label_char,
                transform=Transform(
                    translation=Vec3(
                        origin.x + ox + c * cell_size,
                        origin.y,
                        origin.z + oz - r * cell_size,
                    ),
                    scale=Vec3(0.35, 0.35, 0.35),
                ),
                color=color,
            )
            elements.append(tl)
    return elements


# ======================================================================
# MULTI-COLOUR OBJECT SHAPES  (different chars = different colours)
# ======================================================================

_SHAPE_HEART = [
    " RR RR ",
    "RRKRRR",
    " RRRR ",
    "  RR  ",
    "  RR  ",
    "   R  ",
]

_SHAPE_STAR = [
    "    Y    ",
    "   YYY   ",
    "  YYYYY  ",
    "YYYYYYYYY",
    " YYYYYYY ",
    "  YYYYY  ",
    " YYY YYY ",
    "YY     YY",
]

_SHAPE_ARROW_UP = [
    "    C    ",
    "   CCC   ",
    "  CCCCC  ",
    " CCCCCCC ",
    "    B    ",
    "    B    ",
    "    B    ",
    "    B    ",
]

_SHAPE_ARROW_RIGHT = [
    "        B  ",
    "    BBBBB  ",
    "CCCCCCCCCCC",
    "    BBBBB  ",
    "        B  ",
]

_SHAPE_SMILEY = [
    "  YYYY  ",
    " Y    Y ",
    "Y B  B Y",
    "Y      Y",
    "Y R  R Y",
    "Y  RR  Y",
    " Y    Y ",
    "  YYYY  ",
]

_SHAPE_MUSIC_NOTE = [
    "   PP",
    "   P ",
    "   P ",
    "   P ",
    "   P ",
    " PPP ",
    "PP P ",
    " PP  ",
]

_SHAPE_LIGHTNING = [
    "   YYY",
    "  YYY ",
    " YYY  ",
    "YOYYYY",
    "  YOY ",
    " YOY  ",
    "YYY   ",
]

_SHAPE_CROWN = [
    " Y   Y   Y ",
    " YY O O YY ",
    " YY OOO YY ",
    " YYYYYYYYY ",
    "  RRRRRRR  ",
    "  YYYYYYY  ",
]

_SHAPE_SKULL = [
    "  WWWWW  ",
    " WWWWWWW ",
    "WW D D WW",
    "WW D D WW",
    " WWWWWWW ",
    "  WA AW  ",
    "  WWWWW  ",
    "   W W   ",
]

_SHAPE_PEACE = [
    "  PPPPP  ",
    " P  P  P ",
    "P   P   P",
    "P  PPP  P",
    "P P P P P",
    " P  P  P ",
    "  PPPPP  ",
]

_SHAPE_CHECKMARK = [
    "        G",
    "       GG",
    "      GG ",
    "G    GG  ",
    "GG  GG   ",
    " GGGG    ",
    "  GG     ",
]

_SHAPE_CROSS = [
    "   WWW   ",
    "   WLW   ",
    "   WWW   ",
    "WWWWWWWWW",
    "WLWWLWWLW",
    "WWWWWWWWW",
    "   WWW   ",
    "   WLW   ",
    "   WWW   ",
]

_SHAPE_DIAMOND_SHAPE = [
    "    C    ",
    "   CBC   ",
    "  CBCBC  ",
    " CBCBCBC ",
    "CBCBCBCBC",
    " CBCBCBC ",
    "  CBCBC  ",
    "   CBC   ",
    "    C    ",
]

_SHAPE_TABLE = [
    "NNNNNNNNNNN",
    "NFFFFFFFFFN",
    "NN       NN",
    "N         N",
    "NN       NN",
    "NN       NN",
    "NN       NN",
    "NN       NN",
]

_SHAPE_CHAIR = [
    "NN        ",
    "NF        ",
    "NN        ",
    "NNNNNNNNNN",
    "NFFFFFFFFN",
    "NN      NN",
    "NN      NN",
    "NN      NN",
    "NN      NN",
]

_SHAPE_HOUSE = [
    "     R     ",
    "    RRR    ",
    "   RRRRR   ",
    "  RRRRRRR  ",
    " RRRRRRRRR ",
    "OOOOOOOOOOO",
    "OO OO OO OO",
    "OO OO OO OO",
    "OO OO    OO",
    "OO OO BB OO",
    "OOOOOBBOOOO",
]

_SHAPE_TREE = [
    "     G     ",
    "    GGG    ",
    "   GGGGG   ",
    "  GGGGGGG  ",
    "    GGG    ",
    "   GGGGG   ",
    "  GGGGGGG  ",
    " GGGGGGGGG ",
    "    NNN    ",
    "    NNN    ",
    "    NNN    ",
]

_SHAPE_PIANO = [
    "WWWWWWWWWWW",
    "W D WW D W ",
    "W D WW D W ",
    "W D WW D W ",
    "W  W  W  W ",
    "W  W  W  W ",
    "WWWWWWWWWWW",
]

_SHAPE_HAND = [
    "  F F F  ",
    " FF F FF ",
    " FF F FF ",
    " FF F FF ",
    " FF FFFF ",
    " FFFFFFF ",
    "  FFFFF  ",
    "  FFFF   ",
    "   FFF   ",
]

_SHAPE_WAVE = [
    "  BB     BB  ",
    " B  C   C  B ",
    "B    C C    B",
    "      C      ",
]

_SHAPE_SPIRAL_ART = [
    "  MMMMMM  ",
    " M      M ",
    "M  KKKK  M",
    "M K    K M",
    "M K MK K M",
    "M K  K K M",
    "M KKKK K M",
    "M      K M",
    " MMMMMMM  ",
]

_SHAPE_QUAKE = [
    "     OOOO     ",
    "    OOOOO     ",
    "   OOOOO      ",
    "  OOOOOOOOOO  ",
    " RRRRRRRRRRR  ",
    "RRRRRRRRRRRR  ",
    "      RRRRR   ",
    "     OOOOO    ",
    "    OOOOO     ",
    "  OOOOOOOOOOOO",
    "  RRRRRRRRRRR ",
    "  RRRRRRRRRR  ",
    "      RRRRR   ",
    "     OOOO     ",
    "    OOOO      ",
]

_SHAPE_FRAME = [
    "YYYYYYYYYYYYY",
    "Y           Y",
    "Y           Y",
    "Y           Y",
    "Y           Y",
    "Y           Y",
    "Y           Y",
    "YYYYYYYYYYYYY",
]

_SHAPE_ROCKET = [
    "    R    ",
    "   ROR   ",
    "   RWR   ",
    "  RWBWR  ",
    "  RWBWR  ",
    "  RW WR  ",
    " AA R AA ",
    " O  R  O ",
    "  Y Y Y  ",
]

_SHAPE_ALIEN = [
    "   GGGGG   ",
    "  GGGGGGG  ",
    " GG YGY GG ",
    " GGGGGGGGG ",
    "   G G G   ",
    "  GG G GG  ",
    " G   G   G ",
]

_SHAPE_GAMEPAD = [
    "           ",
    "  AA   AA  ",
    " AAAA LLLL ",
    "AAAAAAALLLL",
    "AA RRRRRRL ",
    " AA B G AA ",
    "  AAAAAAA  ",
    "   AAAAA   ",
]

_SHAPE_SPEAKER = [
    "   AA    ",
    "  AAA  C ",
    " AAAA  B ",
    "AAAAA  C ",
    "AAAAA  C ",
    " AAAA  B ",
    "  AAA  C ",
    "   AA    ",
]

_SHAPE_HEADPHONES = [
    "  AAAAAAA  ",
    " A       A ",
    "A         A",
    "A         A",
    "PPP     PPP",
    "PPP     PPP",
    "PPP     PPP",
]

_SHAPE_VINYL = [
    "  DDDDDDD  ",
    " D       D ",
    "D  PPPPP  D",
    "D PP   PP D",
    "D P  W  P D",
    "D PP   PP D",
    "D  PPPPP  D",
    " D       D ",
    "  DDDDDDD  ",
]

_SHAPE_EQ_BARS = [
    "      G  C ",
    "  B   GG C ",
    "  BB  GG CC",
    " BBB  GG CC",
    " BBB GGG CC",
    "BBBB GGG CC",
    "BBBB GGCCCC",
    "BBBBBGGCCCC",
]

_SHAPE_EXCLAMATION = [
    "  RRR  ",
    "  RRR  ",
    "  RRR  ",
    "  RRR  ",
    "   R   ",
    "       ",
    "  RRR  ",
    "  RRR  ",
]

_SHAPE_QUESTION = [
    "  BBBB  ",
    " B    B ",
    "      B ",
    "    BB  ",
    "   B    ",
    "   B    ",
    "        ",
    "   BB   ",
]

_SHAPE_INFINITY = [
    "  PP   PP  ",
    " P  C C  P ",
    "P    C    P",
    " P  C C  P ",
    "  PP   PP  ",
]

_SHAPE_YIN_YANG = [
    "  WWWWW  ",
    " WWWD DD ",
    "WWWW  DDD",
    "WWW D  DD",
    "WWW   DDD",
    "WW D  DDD",
    "WW  DDDD ",
    " WW  DDD ",
    "  WWWWW  ",
]

# ======================================================================
# FULL ALPHABET  A–Z  (all use # as the pixel char → white by default)
# ======================================================================

_LETTERS = {
    "A": [
        "  ###  ",
        " #   # ",
        "#     #",
        "#######",
        "#     #",
        "#     #",
    ],
    "B": [
        "#####  ",
        "#    # ",
        "#####  ",
        "#    # ",
        "#    # ",
        "#####  ",
    ],
    "C": [
        " ##### ",
        "#      ",
        "#      ",
        "#      ",
        "#      ",
        " ##### ",
    ],
    "D": [
        "####   ",
        "#   #  ",
        "#    # ",
        "#    # ",
        "#   #  ",
        "####   ",
    ],
    "E": [
        "#######",
        "#      ",
        "#####  ",
        "#      ",
        "#      ",
        "#######",
    ],
    "F": [
        "#######",
        "#      ",
        "#####  ",
        "#      ",
        "#      ",
        "#      ",
    ],
    "G": [
        " ##### ",
        "#      ",
        "#  ### ",
        "#    # ",
        "#    # ",
        " ##### ",
    ],
    "H": [
        "#     #",
        "#     #",
        "#######",
        "#     #",
        "#     #",
        "#     #",
    ],
    "I": [
        " ##### ",
        "   #   ",
        "   #   ",
        "   #   ",
        "   #   ",
        " ##### ",
    ],
    "J": [
        "  #####",
        "     # ",
        "     # ",
        "     # ",
        "#    # ",
        " ####  ",
    ],
    "K": [
        "#    # ",
        "#   #  ",
        "####   ",
        "#  #   ",
        "#   #  ",
        "#    # ",
    ],
    "L": [
        "#      ",
        "#      ",
        "#      ",
        "#      ",
        "#      ",
        "#######",
    ],
    "M": [
        "#     #",
        "##   ##",
        "# # # #",
        "#  #  #",
        "#     #",
        "#     #",
    ],
    "N": [
        "#     #",
        "##    #",
        "# #   #",
        "#  #  #",
        "#   # #",
        "#    ##",
    ],
    "O": [
        " ##### ",
        "#     #",
        "#     #",
        "#     #",
        "#     #",
        " ##### ",
    ],
    "P": [
        "#####  ",
        "#    # ",
        "#####  ",
        "#      ",
        "#      ",
        "#      ",
    ],
    "Q": [
        " ##### ",
        "#     #",
        "#     #",
        "#   # #",
        "#    # ",
        " #### #",
    ],
    "R": [
        "#####  ",
        "#    # ",
        "#####  ",
        "#  #   ",
        "#   #  ",
        "#    # ",
    ],
    "S": [
        " ##### ",
        "#      ",
        " ##### ",
        "      #",
        "      #",
        " ##### ",
    ],
    "T": [
        "#######",
        "   #   ",
        "   #   ",
        "   #   ",
        "   #   ",
        "   #   ",
    ],
    "U": [
        "#     #",
        "#     #",
        "#     #",
        "#     #",
        "#     #",
        " ##### ",
    ],
    "V": [
        "#     #",
        "#     #",
        " #   # ",
        " #   # ",
        "  # #  ",
        "   #   ",
    ],
    "W": [
        "#     #",
        "#     #",
        "#  #  #",
        "# # # #",
        "##   ##",
        "#     #",
    ],
    "X": [
        "#     #",
        " #   # ",
        "  # #  ",
        "  # #  ",
        " #   # ",
        "#     #",
    ],
    "Y": [
        "#     #",
        " #   # ",
        "  # #  ",
        "   #   ",
        "   #   ",
        "   #   ",
    ],
    "Z": [
        "#######",
        "     # ",
        "    #  ",
        "  #    ",
        " #     ",
        "#######",
    ],
}

_DIGITS = {
    "0": [
        " ##### ",
        "#    ##",
        "#   # #",
        "#  #  #",
        "# #   #",
        " ##### ",
    ],
    "1": [
        "  ##   ",
        " # #   ",
        "   #   ",
        "   #   ",
        "   #   ",
        " ##### ",
    ],
    "2": [
        " ##### ",
        "#     #",
        "    ## ",
        "  ##   ",
        " #     ",
        "#######",
    ],
    "3": [
        " ##### ",
        "      #",
        "  #### ",
        "      #",
        "      #",
        " ##### ",
    ],
    "4": [
        "#   #  ",
        "#   #  ",
        "#######",
        "    #  ",
        "    #  ",
        "    #  ",
    ],
    "5": [
        "#######",
        "#      ",
        "#####  ",
        "     # ",
        "     # ",
        "#####  ",
    ],
    "6": [
        " ##### ",
        "#      ",
        "#####  ",
        "#    # ",
        "#    # ",
        " #### ",
    ],
    "7": [
        "#######",
        "     # ",
        "    #  ",
        "   #   ",
        "  #    ",
        "  #    ",
    ],
    "8": [
        " ##### ",
        "#     #",
        " ##### ",
        "#     #",
        "#     #",
        " ##### ",
    ],
    "9": [
        " ##### ",
        "#    # ",
        " ##### ",
        "     # ",
        "     # ",
        " ##### ",
    ],
}

_SYMBOLS = {
    "&": [
        "  ##   ",
        " #  #  ",
        "  ##   ",
        " ## # ",
        "#   ## ",
        " ### # ",
    ],
    "@": [
        " ##### ",
        "#  ## #",
        "# # # #",
        "# ### #",
        "#      ",
        " ##### ",
    ],
    "#_sym": [
        "  # #  ",
        "#######",
        "  # #  ",
        "  # #  ",
        "#######",
        "  # #  ",
    ],
    "!": [
        "  ###  ",
        "  ###  ",
        "  ###  ",
        "   #   ",
        "       ",
        "  ###  ",
    ],
    "?": [
        " ##### ",
        "#     #",
        "    ## ",
        "   #   ",
        "       ",
        "   #   ",
    ],
}


# ======================================================================
# Generator helpers
# ======================================================================

def _gen_shape(grid, default_color=None, label="█"):
    """Factory for single/multi-colour shape generators."""
    dc = default_color or Color(1.0, 1.0, 1.0, 1.0)
    def _fn(p, o):
        return _shape_from_grid(p, grid, o, cell_size=8.0, color_map=_C, default_color=dc, label_char=label)
    return _fn


def _gen_letter(char, color=None):
    """Factory for a single-letter shape."""
    c = color or Color(1.0, 1.0, 1.0, 1.0)
    grid = _LETTERS.get(char) or _DIGITS.get(char) or _SYMBOLS.get(char)
    if not grid:
        return lambda p, o: []
    return _gen_shape(grid, default_color=c)


# ======================================================================
# Register Fun Shape templates
# ======================================================================

_FUN_SHAPES = {}

# ---- Symbols & icons (multi-colour) ----
_FUN_SHAPES["Shape: Heart"]        = _gen_shape(_SHAPE_HEART)
_FUN_SHAPES["Shape: Star"]         = _gen_shape(_SHAPE_STAR)
_FUN_SHAPES["Shape: Lightning"]    = _gen_shape(_SHAPE_LIGHTNING)
_FUN_SHAPES["Shape: Crown"]        = _gen_shape(_SHAPE_CROWN)
_FUN_SHAPES["Shape: Diamond"]      = _gen_shape(_SHAPE_DIAMOND_SHAPE)
_FUN_SHAPES["Shape: Checkmark"]    = _gen_shape(_SHAPE_CHECKMARK)
_FUN_SHAPES["Shape: Cross"]        = _gen_shape(_SHAPE_CROSS)
_FUN_SHAPES["Shape: Peace"]        = _gen_shape(_SHAPE_PEACE)
_FUN_SHAPES["Shape: Infinity"]     = _gen_shape(_SHAPE_INFINITY)
_FUN_SHAPES["Shape: Yin Yang"]     = _gen_shape(_SHAPE_YIN_YANG)
_FUN_SHAPES["Shape: Exclamation"]  = _gen_shape(_SHAPE_EXCLAMATION)
_FUN_SHAPES["Shape: Question"]     = _gen_shape(_SHAPE_QUESTION)

# ---- Faces & characters (multi-colour) ----
_FUN_SHAPES["Shape: Smiley"]       = _gen_shape(_SHAPE_SMILEY)
_FUN_SHAPES["Shape: Skull"]        = _gen_shape(_SHAPE_SKULL)
_FUN_SHAPES["Shape: Alien"]        = _gen_shape(_SHAPE_ALIEN)
_FUN_SHAPES["Shape: Hand"]         = _gen_shape(_SHAPE_HAND)

# ---- Objects (multi-colour) ----
_FUN_SHAPES["Shape: Arrow Up"]     = _gen_shape(_SHAPE_ARROW_UP)
_FUN_SHAPES["Shape: Arrow Right"]  = _gen_shape(_SHAPE_ARROW_RIGHT)
_FUN_SHAPES["Shape: Table"]        = _gen_shape(_SHAPE_TABLE)
_FUN_SHAPES["Shape: Chair"]        = _gen_shape(_SHAPE_CHAIR)
_FUN_SHAPES["Shape: House"]        = _gen_shape(_SHAPE_HOUSE)
_FUN_SHAPES["Shape: Tree"]         = _gen_shape(_SHAPE_TREE)
_FUN_SHAPES["Shape: Rocket"]       = _gen_shape(_SHAPE_ROCKET)
_FUN_SHAPES["Shape: Frame"]        = _gen_shape(_SHAPE_FRAME)

# ---- Music themed (multi-colour) ----
_FUN_SHAPES["Shape: Music Note"]   = _gen_shape(_SHAPE_MUSIC_NOTE)
_FUN_SHAPES["Shape: Piano"]        = _gen_shape(_SHAPE_PIANO)
_FUN_SHAPES["Shape: Speaker"]      = _gen_shape(_SHAPE_SPEAKER)
_FUN_SHAPES["Shape: Headphones"]   = _gen_shape(_SHAPE_HEADPHONES)
_FUN_SHAPES["Shape: Vinyl"]        = _gen_shape(_SHAPE_VINYL)
_FUN_SHAPES["Shape: EQ Bars"]      = _gen_shape(_SHAPE_EQ_BARS)
_FUN_SHAPES["Shape: Wave"]         = _gen_shape(_SHAPE_WAVE)
_FUN_SHAPES["Shape: Gamepad"]      = _gen_shape(_SHAPE_GAMEPAD)

# ---- Art ----
_FUN_SHAPES["Shape: Spiral"]       = _gen_shape(_SHAPE_SPIRAL_ART)
_FUN_SHAPES["Shape: Quake"]        = _gen_shape(_SHAPE_QUAKE)

# ---- Letters A–Z ----
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _FUN_SHAPES[f"Letter: {_ch}"] = _gen_letter(_ch)

# ---- Digits 0–9 ----
for _d in "0123456789":
    _FUN_SHAPES[f"Letter: {_d}"] = _gen_letter(_d)

# ---- Extra symbols ----
_FUN_SHAPES["Letter: &"]  = _gen_letter("&")
_FUN_SHAPES["Letter: @"]  = _gen_letter("@")
_FUN_SHAPES["Letter: #"]  = _gen_letter("#_sym")
_FUN_SHAPES["Letter: !"]  = _gen_letter("!")
_FUN_SHAPES["Letter: ?"]  = _gen_letter("?")

TEMPLATES.update(_FUN_SHAPES)
