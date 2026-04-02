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
    """Generate a row/circle of rotary knob MorphZones (1D X-axis, small cube)."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    if arrangement == "Circle":
        positions = _circle_positions(count, spacing)
    else:
        positions = _row_positions(count, spacing)

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
            color=PALETTE[i % len(PALETTE)],
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
    """Generate a row/circle of 2D XY MorphZone pads."""
    if origin is None:
        origin = Vec3(0, 0, 0)

    if arrangement == "Circle":
        positions = _circle_positions(count, spacing)
    else:
        positions = _row_positions(count, spacing)

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
            color=PALETTE[i % len(PALETTE)],
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

    if arrangement == "Circle":
        positions = _circle_positions(count, spacing)
    else:
        positions = _row_positions(count, spacing)

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
            color=PALETTE[i % len(PALETTE)],
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

    if arrangement == "Circle":
        positions = _circle_positions(count, spacing)
    else:
        positions = _row_positions(count, spacing)

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
            color=PALETTE[i % len(PALETTE)],
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
        radius = max(120.0, count * 2.6)
        positions = _circle_positions(count, radius)
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
            color=Color(0.95, 0.95, 0.95, 1.0) if not is_black else Color(0.08, 0.08, 0.08, 1.0),
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
                color=LABEL_COLOR,
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
    "8 Faders (Row)":       lambda p, o: generate_faders(p, 8, "Row", 30, o, base_cc=1),
    "8 Faders (Circle)":    lambda p, o: generate_faders(p, 8, "Circle", 60, o, base_cc=1),
    "8 Knobs (Row)":        lambda p, o: generate_knobs(p, 8, "Row", 30, o, base_cc=20),
    "8 Knobs (Circle)":     lambda p, o: generate_knobs(p, 8, "Circle", 60, o, base_cc=20),
    "8 XY Pads (Row)":      lambda p, o: generate_xy_pads(p, 8, "Row", 40, o, base_cc_x=40, base_cc_y=50),
    "8 XY Pads (Circle)":   lambda p, o: generate_xy_pads(p, 8, "Circle", 80, o, base_cc_x=40, base_cc_y=50),
    "8 Drum Pads (2x4)":    lambda p, o: generate_drum_pads(p, 8, "Row", 30, o, base_note=36, channel=10),
    "8 Drum Pads (Circle)": lambda p, o: generate_drum_pads(p, 8, "Circle", 60, o, base_note=36, channel=10),
    "8 Buttons (Row)":      lambda p, o: generate_buttons(p, 8, "Row", 25, o, base_cc=70),
    "8 Buttons (Circle)":   lambda p, o: generate_buttons(p, 8, "Circle", 50, o, base_cc=70),
    "16 Drum Pads (4x4)":   lambda p, o: generate_drum_pads(p, 16, "Row", 30, o, base_note=36, channel=10),
    "Keyboard (Full MIDI Row)": lambda p, o: generate_keyboard(
        p, arrangement="Row", spacing=12, origin=o, channel=1, base_note=0, max_note=127, label_prefix="Key"
    ),
    "Keyboard (Full MIDI Circle)": lambda p, o: generate_keyboard(
        p, arrangement="Circle", spacing=12, origin=o, channel=1, base_note=0, max_note=127, label_prefix="Key"
    ),
    "Mixer (8 Faders + 8 Knobs)": None,  # special composite — handled separately
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
