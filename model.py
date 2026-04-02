"""
MoveMusic domain model and GVAS <-> model conversion.

Bridges the generic GVAS parser to typed MoveMusic objects.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional, Union

from gvas import (
    GvasFile, GvasHeader, GvasReader, GvasWriter,
    GvasProperty, StrProperty, IntProperty, FloatProperty, BoolProperty,
    NameProperty, ObjectProperty, EnumProperty, ByteEnumProperty,
    StructProperty, ArrayProperty, SetProperty, StructArrayHeader,
    find_property,
)


# ---------------------------------------------------------------------------
# Domain data classes
# ---------------------------------------------------------------------------

@dataclass
class MidiNoteMapping:
    channel: int = 1
    note: int = 60
    velocity: float = 1.0


@dataclass
class MidiCCMapping:
    channel: int = 1
    control: int = 0
    value: int = 0


@dataclass
class Color:
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Quat:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass
class Transform:
    rotation: Quat = field(default_factory=Quat)
    translation: Vec3 = field(default_factory=Vec3)
    scale: Vec3 = field(default_factory=lambda: Vec3(0.5, 0.5, 0.5))


@dataclass
class HitZone:
    unique_id: str = ""
    display_name: str = ""
    transform: Transform = field(default_factory=Transform)
    color: Color = field(default_factory=lambda: Color(0.0, 0.37, 0.8, 1.0))
    should_use_velocity_sensitivity: bool = False
    fixed_midi_velocity_output: float = 1.0
    min_physics_velocity_input: float = 0.0
    max_physics_velocity_input: float = 600.0
    min_midi_velocity_output: float = 0.0
    max_midi_velocity_output: float = 1.0
    midi_note_mappings: list = field(default_factory=lambda: [MidiNoteMapping()])
    midi_cc_mappings: list = field(default_factory=lambda: [MidiCCMapping(channel=1, control=69, value=127)])
    timed_close_seconds: float = 1.0
    one_shot_sample: str = "EOneShotSample::None"
    behavior: str = "EHitZoneBehavior::Hold"
    midi_message_type: str = "EMidiMessageType::Note"
    toggle_state: bool = False
    is_enabled: bool = True
    is_locked: bool = False
    b_can_be_damaged: bool = True


@dataclass
class MorphZone:
    unique_id: str = ""
    display_name: str = ""
    transform: Transform = field(default_factory=Transform)
    color: Color = field(default_factory=lambda: Color(1.0, 0.0, 1.0, 1.0))
    control_position_normalized: Vec3 = field(default_factory=Vec3)
    return_control_position_normalized: Vec3 = field(default_factory=Vec3)
    mesh_min_local_position: Vec3 = field(default_factory=lambda: Vec3(-25, -25, -25))
    mesh_max_local_position: Vec3 = field(default_factory=lambda: Vec3(25, 25, 25))
    mesh_extent: Vec3 = field(default_factory=lambda: Vec3(50, 50, 50))
    is_x_axis_enabled: bool = True
    x_axis_cc_mappings: list = field(default_factory=lambda: [MidiCCMapping(channel=1, control=70, value=0)])
    is_y_axis_enabled: bool = True
    y_axis_cc_mappings: list = field(default_factory=lambda: [MidiCCMapping(channel=1, control=71, value=0)])
    is_z_axis_enabled: bool = True
    z_axis_cc_mappings: list = field(default_factory=lambda: [MidiCCMapping(channel=1, control=72, value=0)])
    soloed_axis: str = "EAxis::None"
    dimensions: str = "EDimensions::Three"
    release_behavior: str = "EMorphZoneReleaseBehavior::Stop"
    is_enabled: bool = True
    is_locked: bool = False
    b_can_be_damaged: bool = True


@dataclass
class TextLabel:
    unique_id: str = ""
    display_name: str = ""  # This IS the label text shown in 3D space
    transform: Transform = field(default_factory=Transform)
    color: Color = field(default_factory=lambda: Color(1.0, 1.0, 1.0, 1.0))
    is_enabled: bool = True
    is_locked: bool = False
    b_can_be_damaged: bool = True


@dataclass
class BoundingBox:
    min: Vec3 = field(default_factory=lambda: Vec3(-25, -25, -25))
    max: Vec3 = field(default_factory=lambda: Vec3(25, 25, 25))
    is_valid: int = 1


@dataclass
class GroupIE:
    unique_id: str = ""
    display_name: str = ""
    transform: Transform = field(default_factory=Transform)
    color: Color = field(default_factory=lambda: Color(0.8, 0.8, 0.2, 1.0))
    bounding_box: BoundingBox = field(default_factory=BoundingBox)
    group_items: list = field(default_factory=list)  # list of element unique_id strings
    is_enabled: bool = True
    is_locked: bool = False
    b_can_be_damaged: bool = True


@dataclass
class UnknownElement:
    """
    Catch-all for interface element classes we do not model yet.
    Preserves original PropertyData bytes for safe round-trip saves.
    """
    unique_id: str = ""
    class_path: str = ""
    transform: Transform = field(default_factory=Transform)
    raw_property_data: bytes = b""
    display_name: str = ""
    color: Color = field(default_factory=lambda: Color(1.0, 1.0, 1.0, 1.0))
    is_enabled: bool = True
    is_locked: bool = False
    b_can_be_damaged: bool = True


@dataclass
class Workspace:
    unique_id: str = ""
    display_name: str = ""
    enabled: bool = False
    element_ids: list = field(default_factory=list)


@dataclass
class Project:
    # Header (preserved for round-trip)
    header: GvasHeader = None
    trailing_bytes: bytes = b'\x00\x00\x00\x00'

    # Project-level fields
    project_name: str = ""
    timestamp: int = 0
    user_location: Vec3 = field(default_factory=Vec3)

    # Virtual Interface metadata
    vi_name: str = "Virtual Interface"
    vi_class_path: str = "/Script/MoveMusic.VirtualInterface"
    vi_outer_path: str = ""
    active_workspace_index: int = 0

    # Domain data
    workspaces: list = field(default_factory=list)
    elements: list = field(default_factory=list)  # list[HitZone | MorphZone | TextLabel | GroupIE | UnknownElement]

    # Path templates (extracted from original file)
    persistent_level_path: str = "/Game/VirtualRealityBP/Maps/Stage.Stage:PersistentLevel"
    game_mode_suffix: str = "MoveMusicGameMode_2147482570"

    # ID counter
    _next_id: int = 0

    def generate_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}_{self._next_id}"

    def find_element(self, unique_id: str) -> Optional[Union[HitZone, MorphZone, TextLabel, GroupIE, UnknownElement]]:
        for e in self.elements:
            if e.unique_id == unique_id:
                return e
        return None

    @property
    def game_mode_path(self) -> str:
        return f"{self.persistent_level_path}.{self.game_mode_suffix}"

    @property
    def vi_full_path(self) -> str:
        return f"{self.game_mode_path}.{self.vi_name}"

    def element_full_path(self, unique_id: str) -> str:
        return f"{self.persistent_level_path}.{unique_id}"

    def workspace_full_path(self, unique_id: str) -> str:
        return f"{self.vi_full_path}.{unique_id}"


# ---------------------------------------------------------------------------
# Helper: parse embedded PropertyData blobs
# ---------------------------------------------------------------------------

def _parse_property_data(raw_bytes: bytes) -> tuple:
    """Parse a PropertyData byte blob. Returns (properties, trailing_bytes)."""
    reader = GvasReader(BytesIO(raw_bytes))
    props = reader._read_property_list()
    trailing = reader.stream.read()
    return props, trailing


def _serialize_property_data(props: list, trailing: bytes = b'\x00\x00\x00\x00') -> bytes:
    """Serialize properties back to a PropertyData byte blob."""
    buf = BytesIO()
    writer = GvasWriter(buf)
    writer._write_property_list(props)
    buf.write(trailing)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helper: extract MidiNoteMapping / MidiCCMapping from GVAS struct arrays
# ---------------------------------------------------------------------------

def _parse_midi_notes(elements: list) -> list:
    result = []
    for elem_props in elements:
        ch = find_property(elem_props, "Channel")
        note = find_property(elem_props, "Note")
        vel = find_property(elem_props, "Velocity")
        result.append(MidiNoteMapping(
            channel=ch.value if ch else 1,
            note=note.value if note else 60,
            velocity=vel.value if vel else 1.0,
        ))
    return result


def _parse_midi_ccs(elements: list) -> list:
    result = []
    for elem_props in elements:
        ch = find_property(elem_props, "Channel")
        ctrl = find_property(elem_props, "Control")
        val = find_property(elem_props, "Value")
        result.append(MidiCCMapping(
            channel=ch.value if ch else 1,
            control=ctrl.value if ctrl else 0,
            value=val.value if val else 0,
        ))
    return result


def _parse_vec3(prop) -> Vec3:
    if prop is None:
        return Vec3()
    v = prop.value
    return Vec3(v["x"], v["y"], v["z"])


def _parse_quat(prop) -> Quat:
    if prop is None:
        return Quat()
    v = prop.value
    return Quat(v["x"], v["y"], v["z"], v["w"])


def _parse_color(prop) -> Color:
    if prop is None:
        return Color()
    v = prop.value
    return Color(v["r"], v["g"], v["b"], v["a"])


def _parse_transform(actor_props: list) -> Transform:
    t_prop = find_property(actor_props, "Transform")
    if t_prop is None:
        return Transform()
    rot = find_property(t_prop.value, "Rotation")
    trans = find_property(t_prop.value, "Translation")
    scale = find_property(t_prop.value, "Scale3D")
    return Transform(
        rotation=_parse_quat(rot),
        translation=_parse_vec3(trans),
        scale=_parse_vec3(scale),
    )


def _extract_id_from_path(path: str) -> str:
    """Extract 'HitZone_2147482446' from a full object path."""
    return path.rsplit('.', 1)[-1] if '.' in path else path


# ---------------------------------------------------------------------------
# Load: GVAS -> Project
# ---------------------------------------------------------------------------

def load_project(gvas_file: GvasFile) -> Project:
    props = gvas_file.properties
    project = Project()
    project.header = gvas_file.header
    project.trailing_bytes = gvas_file.trailing_bytes

    # Project-level fields
    pn = find_property(props, "ProjectName")
    project.project_name = pn.value if pn else ""

    ts = find_property(props, "Timestamp")
    project.timestamp = ts.value["ticks"] if ts else 0

    ul = find_property(props, "UserLocation")
    project.user_location = _parse_vec3(ul)

    # Virtual Interface
    vi = find_property(props, "VirtualInterfaceRecord")
    if vi:
        vi_name = find_property(vi.value, "Name")
        project.vi_name = vi_name.value if vi_name else "Virtual Interface"
        vi_class = find_property(vi.value, "Class")
        project.vi_class_path = vi_class.value if vi_class else ""
        vi_outer = find_property(vi.value, "Outer")
        project.vi_outer_path = vi_outer.value if vi_outer else ""

        # Extract game mode suffix from outer path
        if vi_outer:
            # e.g. ".../PersistentLevel.MoveMusicGameMode_2147482570"
            parts = vi_outer.value.rsplit('.', 1)
            if len(parts) == 2:
                project.game_mode_suffix = parts[1]
            project.persistent_level_path = parts[0] if len(parts) == 2 else vi_outer.value

        vi_pd = find_property(vi.value, "PropertyData")
        if vi_pd and vi_pd.raw_bytes:
            vi_props, _ = _parse_property_data(vi_pd.raw_bytes)
            awi = find_property(vi_props, "activeWorkspaceIndex")
            project.active_workspace_index = awi.value if awi else 0

    # Workspaces
    ws_array = find_property(props, "WorkspaceRecords")
    if ws_array:
        for ws_props in ws_array.elements:
            ws_name_prop = find_property(ws_props, "Name")
            ws_unique_id = ws_name_prop.value if ws_name_prop else ""

            ws_pd = find_property(ws_props, "PropertyData")
            ws = Workspace(unique_id=ws_unique_id)
            if ws_pd and ws_pd.raw_bytes:
                inner_props, _ = _parse_property_data(ws_pd.raw_bytes)
                dn = find_property(inner_props, "Name")
                ws.display_name = dn.value if dn else ""
                en = find_property(inner_props, "Enabled")
                ws.enabled = en.value if en else False
                ie = find_property(inner_props, "InterfaceElements")
                if ie:
                    ws.element_ids = [_extract_id_from_path(p) for p in ie.elements]
            project.workspaces.append(ws)

    # Elements (Interface Element Records)
    elem_array = find_property(props, "InterfaceElementRecords")
    max_id = 0
    if elem_array:
        for actor_props in elem_array.elements:
            transform = _parse_transform(actor_props)
            name_prop = find_property(actor_props, "Name")
            unique_id = name_prop.value if name_prop else ""
            class_prop = find_property(actor_props, "Class")
            class_path = class_prop.value if class_prop else ""

            # Extract numeric ID for counter
            m = re.search(r'_(\d+)$', unique_id)
            if m:
                max_id = max(max_id, int(m.group(1)))

            pd = find_property(actor_props, "PropertyData")
            pd_props = []
            pd_raw = b""
            if pd and pd.raw_bytes:
                pd_props, _ = _parse_property_data(pd.raw_bytes)
                pd_raw = pd.raw_bytes

            if "HitZone" in class_path:
                hz = HitZone(unique_id=unique_id, transform=transform)
                hz.display_name = _get_str(pd_props, "Name", "")
                hz.color = _parse_color(find_property(pd_props, "Color"))
                hz.should_use_velocity_sensitivity = _get_bool(pd_props, "ShouldUseVelocitySensitivity", False)
                hz.fixed_midi_velocity_output = _get_float(pd_props, "FixedMidiVelocityOutput", 1.0)
                hz.min_physics_velocity_input = _get_float(pd_props, "MinPhysicsVelocityInput", 0.0)
                hz.max_physics_velocity_input = _get_float(pd_props, "MaxPhysicsVelocityInput", 600.0)
                hz.min_midi_velocity_output = _get_float(pd_props, "MinMidiVelocityOutput", 0.0)
                hz.max_midi_velocity_output = _get_float(pd_props, "MaxMidiVelocityOutput", 1.0)
                nm = find_property(pd_props, "MidiNoteMappings")
                hz.midi_note_mappings = _parse_midi_notes(nm.elements) if nm else []
                cm = find_property(pd_props, "MidiCCMappings")
                hz.midi_cc_mappings = _parse_midi_ccs(cm.elements) if cm else []
                hz.timed_close_seconds = _get_float(pd_props, "TimedCloseSeconds", 1.0)
                hz.one_shot_sample = _get_enum(pd_props, "OneShotSample", "EOneShotSample::None")
                hz.behavior = _get_enum(pd_props, "Behavior", "EHitZoneBehavior::Hold")
                hz.midi_message_type = _get_enum(pd_props, "MidiMessageTypeToSend", "EMidiMessageType::Note")
                hz.toggle_state = _get_bool(pd_props, "ToggleState", False)
                hz.is_enabled = _get_bool(pd_props, "IsEnabled", True)
                hz.is_locked = _get_bool(pd_props, "IsLocked", False)
                hz.b_can_be_damaged = _get_bool(pd_props, "bCanBeDamaged", True)
                project.elements.append(hz)

            elif "MorphZone" in class_path:
                mz = MorphZone(unique_id=unique_id, transform=transform)
                mz.display_name = _get_str(pd_props, "Name", "")
                mz.color = _parse_color(find_property(pd_props, "Color"))
                mz.control_position_normalized = _parse_vec3(find_property(pd_props, "ControlPositionNormalized"))
                mz.return_control_position_normalized = _parse_vec3(find_property(pd_props, "ReturnControlPositionNormalized"))
                mz.mesh_min_local_position = _parse_vec3(find_property(pd_props, "MeshMinLocalPosition"))
                mz.mesh_max_local_position = _parse_vec3(find_property(pd_props, "MeshMaxLocalPosition"))
                mz.mesh_extent = _parse_vec3(find_property(pd_props, "MeshExtent"))
                mz.is_x_axis_enabled = _get_bool(pd_props, "isXAxisMappingEnabled", True)
                xm = find_property(pd_props, "XAxisMidiCCMappings")
                mz.x_axis_cc_mappings = _parse_midi_ccs(xm.elements) if xm else []
                mz.is_y_axis_enabled = _get_bool(pd_props, "isYAxisMappingEnabled", True)
                ym = find_property(pd_props, "YAxisMidiCCMappings")
                mz.y_axis_cc_mappings = _parse_midi_ccs(ym.elements) if ym else []
                mz.is_z_axis_enabled = _get_bool(pd_props, "isZAxisMappingEnabled", True)
                zm = find_property(pd_props, "ZAxisMidiCCMappings")
                mz.z_axis_cc_mappings = _parse_midi_ccs(zm.elements) if zm else []
                mz.soloed_axis = _get_byte_enum(pd_props, "SoloedAxis", "EAxis::None")
                mz.dimensions = _get_enum(pd_props, "Dimensions", "EDimensions::Three")
                mz.release_behavior = _get_enum(pd_props, "ReleaseBehavior", "EMorphZoneReleaseBehavior::Stop")
                mz.is_enabled = _get_bool(pd_props, "IsEnabled", True)
                mz.is_locked = _get_bool(pd_props, "IsLocked", False)
                mz.b_can_be_damaged = _get_bool(pd_props, "bCanBeDamaged", True)
                project.elements.append(mz)

            elif "TextLabel" in class_path:
                tl = TextLabel(unique_id=unique_id, transform=transform)
                tl.display_name = _get_str(pd_props, "Name", "")
                tl.color = _parse_color(find_property(pd_props, "Color"))
                tl.is_enabled = _get_bool(pd_props, "IsEnabled", True)
                tl.is_locked = _get_bool(pd_props, "IsLocked", False)
                tl.b_can_be_damaged = _get_bool(pd_props, "bCanBeDamaged", True)
                project.elements.append(tl)

            elif "GroupIE" in class_path:
                grp = GroupIE(unique_id=unique_id, transform=transform)
                grp.display_name = _get_str(pd_props, "Name", "")
                grp.color = _parse_color(find_property(pd_props, "Color"))
                bb_prop = find_property(pd_props, "BoundingBoxCenteredAtOrigin")
                if bb_prop and isinstance(bb_prop.value, dict):
                    v = bb_prop.value
                    grp.bounding_box = BoundingBox(
                        min=Vec3(v["min"]["x"], v["min"]["y"], v["min"]["z"]),
                        max=Vec3(v["max"]["x"], v["max"]["y"], v["max"]["z"]),
                        is_valid=v.get("is_valid", 1),
                    )
                gi = find_property(pd_props, "GroupItems")
                if gi and gi.elements:
                    grp.group_items = [_extract_id_from_path(p) for p in gi.elements]
                grp.is_enabled = _get_bool(pd_props, "IsEnabled", True)
                grp.is_locked = _get_bool(pd_props, "IsLocked", False)
                grp.b_can_be_damaged = _get_bool(pd_props, "bCanBeDamaged", True)
                project.elements.append(grp)
            else:
                # Preserve unknown element classes losslessly via raw PropertyData bytes.
                unk = UnknownElement(
                    unique_id=unique_id,
                    class_path=class_path,
                    transform=transform,
                    raw_property_data=pd_raw,
                )
                if pd_props:
                    unk.display_name = _get_str(pd_props, "Name", "")
                    unk.color = _parse_color(find_property(pd_props, "Color"))
                    unk.is_enabled = _get_bool(pd_props, "IsEnabled", True)
                    unk.is_locked = _get_bool(pd_props, "IsLocked", False)
                    unk.b_can_be_damaged = _get_bool(pd_props, "bCanBeDamaged", True)
                project.elements.append(unk)

    project._next_id = max_id
    return project


# Helpers for extracting typed values
def _get_str(props, name, default=""):
    p = find_property(props, name)
    return p.value if p else default

def _get_int(props, name, default=0):
    p = find_property(props, name)
    return p.value if p else default

def _get_float(props, name, default=0.0):
    p = find_property(props, name)
    return p.value if p else default

def _get_bool(props, name, default=False):
    p = find_property(props, name)
    return p.value if p else default

def _get_enum(props, name, default=""):
    p = find_property(props, name)
    return p.value if p else default

def _get_byte_enum(props, name, default=""):
    p = find_property(props, name)
    return p.value if p else default


# ---------------------------------------------------------------------------
# Save: Project -> GVAS
# ---------------------------------------------------------------------------

def save_project(project: Project) -> GvasFile:
    # Create a default header if none exists (for new projects)
    if project.header is None:
        from gvas import GvasHeader
        project.header = GvasHeader(
            save_game_version=2,
            package_version=522,
            engine_major=4,
            engine_minor=27,
            engine_patch=2,
            engine_build=0,
            engine_branch="++UE4+Partner-Oculus-4.27",
            custom_version_format=3,
            custom_versions=[],  # Will be populated with 49 default entries if needed
            save_game_class_name="/Script/MoveMusic.Project"
        )

    props = []

    # ProjectName
    props.append(StrProperty("ProjectName", project.project_name))

    # Timestamp
    props.append(StructProperty("Timestamp", "DateTime", b'\x00' * 16,
                                {"ticks": project.timestamp}))

    # UserLocation
    props.append(StructProperty("UserLocation", "Vector", b'\x00' * 16,
                                {"x": project.user_location.x,
                                 "y": project.user_location.y,
                                 "z": project.user_location.z}))

    # VirtualInterfaceRecord
    vi_inner = []
    vi_inner.append(NameProperty("Name", project.vi_name))
    vi_inner.append(ObjectProperty("Class", project.vi_class_path))
    vi_inner.append(ObjectProperty("Outer", project.game_mode_path))

    # VI PropertyData: Workspaces + activeWorkspaceIndex
    vi_pd_props = []
    ws_refs = [project.workspace_full_path(ws.unique_id) for ws in project.workspaces]
    vi_pd_props.append(ArrayProperty("Workspaces", "ObjectProperty", ws_refs))
    vi_pd_props.append(IntProperty("activeWorkspaceIndex", project.active_workspace_index))
    vi_pd_bytes = _serialize_property_data(vi_pd_props)
    vi_inner.append(ArrayProperty("PropertyData", "ByteProperty", raw_bytes=vi_pd_bytes))

    props.append(StructProperty("VirtualInterfaceRecord", "ObjectRecord", b'\x00' * 16, vi_inner))

    # WorkspaceRecords
    ws_elements = []
    for ws in project.workspaces:
        ws_props = []
        ws_props.append(NameProperty("Name", ws.unique_id))
        ws_props.append(ObjectProperty("Class", "/Script/MoveMusic.Workspace"))
        ws_props.append(ObjectProperty("Outer", project.vi_full_path))

        # Workspace PropertyData
        ws_pd_props = []
        ws_pd_props.append(StrProperty("Name", ws.display_name))
        ws_pd_props.append(BoolProperty("Enabled", ws.enabled))
        elem_refs = [project.element_full_path(eid) for eid in ws.element_ids]
        ws_pd_props.append(SetProperty("InterfaceElements", "ObjectProperty", 0, elem_refs))
        ws_pd_bytes = _serialize_property_data(ws_pd_props)
        ws_props.append(ArrayProperty("PropertyData", "ByteProperty", raw_bytes=ws_pd_bytes))

        ws_elements.append(ws_props)

    ws_header = StructArrayHeader("WorkspaceRecords", "ObjectRecord", b'\x00' * 16)
    props.append(ArrayProperty("WorkspaceRecords", "StructProperty", ws_elements, ws_header))

    # InterfaceElementRecords
    elem_elements = []
    for elem in project.elements:
        actor_props = []

        # Transform
        t = elem.transform
        t_inner = []
        t_inner.append(StructProperty("Rotation", "Quat", b'\x00' * 16,
                                       {"x": t.rotation.x, "y": t.rotation.y,
                                        "z": t.rotation.z, "w": t.rotation.w}))
        t_inner.append(StructProperty("Translation", "Vector", b'\x00' * 16,
                                       {"x": t.translation.x, "y": t.translation.y,
                                        "z": t.translation.z}))
        t_inner.append(StructProperty("Scale3D", "Vector", b'\x00' * 16,
                                       {"x": t.scale.x, "y": t.scale.y, "z": t.scale.z}))
        actor_props.append(StructProperty("Transform", "Transform", b'\x00' * 16, t_inner))

        # Name, Class, Outer
        actor_props.append(NameProperty("Name", elem.unique_id))
        if isinstance(elem, HitZone):
            actor_props.append(ObjectProperty("Class", "/Script/MoveMusic.HitZone"))
        elif isinstance(elem, MorphZone):
            actor_props.append(ObjectProperty("Class", "/Script/MoveMusic.MorphZone"))
        elif isinstance(elem, TextLabel):
            actor_props.append(ObjectProperty("Class", "/Game/VirtualRealityBP/Blueprints/TextLabel.TextLabel_C"))
        elif isinstance(elem, GroupIE):
            actor_props.append(ObjectProperty("Class", "/Script/MoveMusic.GroupIE"))
        elif isinstance(elem, UnknownElement):
            actor_props.append(ObjectProperty("Class", elem.class_path))
        actor_props.append(ObjectProperty("Outer", project.persistent_level_path))

        # PropertyData
        if isinstance(elem, UnknownElement) and elem.raw_property_data:
            pd_bytes = elem.raw_property_data
        else:
            pd_props = _build_element_property_data(elem, project)
            pd_bytes = _serialize_property_data(pd_props)
        actor_props.append(ArrayProperty("PropertyData", "ByteProperty", raw_bytes=pd_bytes))

        elem_elements.append(actor_props)

    elem_header = StructArrayHeader("InterfaceElementRecords", "ActorRecord", b'\x00' * 16)
    props.append(ArrayProperty("InterfaceElementRecords", "StructProperty", elem_elements, elem_header))

    return GvasFile(
        header=project.header,
        properties=props,
        trailing_bytes=project.trailing_bytes,
    )


def _build_element_property_data(elem, project=None) -> list:
    """Build the list of properties for an element's PropertyData blob."""
    props = []

    if isinstance(elem, HitZone):
        props.append(BoolProperty("ShouldUseVelocitySensitivity", elem.should_use_velocity_sensitivity))
        props.append(FloatProperty("FixedMidiVelocityOutput", elem.fixed_midi_velocity_output))
        props.append(FloatProperty("MinPhysicsVelocityInput", elem.min_physics_velocity_input))
        props.append(FloatProperty("MaxPhysicsVelocityInput", elem.max_physics_velocity_input))
        props.append(FloatProperty("MinMidiVelocityOutput", elem.min_midi_velocity_output))
        props.append(FloatProperty("MaxMidiVelocityOutput", elem.max_midi_velocity_output))

        # MidiNoteMappings
        note_elems = []
        for nm in elem.midi_note_mappings:
            note_elems.append([
                IntProperty("Channel", nm.channel),
                IntProperty("Note", nm.note),
                FloatProperty("Velocity", nm.velocity),
            ])
        note_header = StructArrayHeader("MidiNoteMappings", "MidiNote", b'\x00' * 16)
        props.append(ArrayProperty("MidiNoteMappings", "StructProperty", note_elems, note_header))

        # MidiCCMappings
        cc_elems = []
        for cm in elem.midi_cc_mappings:
            cc_elems.append([
                IntProperty("Channel", cm.channel),
                IntProperty("Control", cm.control),
                IntProperty("Value", cm.value),
            ])
        cc_header = StructArrayHeader("MidiCCMappings", "MidiControlChange", b'\x00' * 16)
        props.append(ArrayProperty("MidiCCMappings", "StructProperty", cc_elems, cc_header))

        props.append(FloatProperty("TimedCloseSeconds", elem.timed_close_seconds))
        props.append(EnumProperty("OneShotSample", "EOneShotSample", elem.one_shot_sample))
        props.append(EnumProperty("Behavior", "EHitZoneBehavior", elem.behavior))
        props.append(EnumProperty("MidiMessageTypeToSend", "EMidiMessageType", elem.midi_message_type))
        props.append(BoolProperty("ToggleState", elem.toggle_state))

    elif isinstance(elem, MorphZone):
        props.append(_vec3_struct("ControlPositionNormalized", elem.control_position_normalized))
        props.append(_vec3_struct("ReturnControlPositionNormalized", elem.return_control_position_normalized))
        props.append(_vec3_struct("MeshMinLocalPosition", elem.mesh_min_local_position))
        props.append(_vec3_struct("MeshMaxLocalPosition", elem.mesh_max_local_position))
        props.append(_vec3_struct("MeshExtent", elem.mesh_extent))

        props.append(BoolProperty("isXAxisMappingEnabled", elem.is_x_axis_enabled))
        props.append(_cc_array("XAxisMidiCCMappings", elem.x_axis_cc_mappings))
        props.append(BoolProperty("isYAxisMappingEnabled", elem.is_y_axis_enabled))
        props.append(_cc_array("YAxisMidiCCMappings", elem.y_axis_cc_mappings))
        props.append(BoolProperty("isZAxisMappingEnabled", elem.is_z_axis_enabled))
        props.append(_cc_array("ZAxisMidiCCMappings", elem.z_axis_cc_mappings))

        props.append(ByteEnumProperty("SoloedAxis", "EAxis", elem.soloed_axis))
        props.append(EnumProperty("Dimensions", "EDimensions", elem.dimensions))
        props.append(EnumProperty("ReleaseBehavior", "EMorphZoneReleaseBehavior", elem.release_behavior))

    elif isinstance(elem, TextLabel):
        pass  # TextLabel has no type-specific properties beyond common fields

    elif isinstance(elem, GroupIE):
        bb = elem.bounding_box
        props.append(StructProperty("BoundingBoxCenteredAtOrigin", "Box", b'\x00' * 16,
                                    {"min": {"x": bb.min.x, "y": bb.min.y, "z": bb.min.z},
                                     "max": {"x": bb.max.x, "y": bb.max.y, "z": bb.max.z},
                                     "is_valid": bb.is_valid}))
        item_refs = [project.element_full_path(item_id) for item_id in elem.group_items]
        props.append(ArrayProperty("GroupItems", "ObjectProperty", item_refs))
    elif isinstance(elem, UnknownElement):
        # Unknown element fallback: retain only common editable fields if raw bytes are unavailable.
        pass

    # Common fields
    props.append(StrProperty("Name", elem.display_name))
    props.append(StructProperty("Color", "LinearColor", b'\x00' * 16,
                                {"r": elem.color.r, "g": elem.color.g,
                                 "b": elem.color.b, "a": elem.color.a}))
    props.append(BoolProperty("IsEnabled", elem.is_enabled))
    props.append(BoolProperty("IsLocked", elem.is_locked))
    props.append(BoolProperty("bCanBeDamaged", elem.b_can_be_damaged))

    return props


def _vec3_struct(name: str, v: Vec3) -> StructProperty:
    return StructProperty(name, "Vector", b'\x00' * 16,
                          {"x": v.x, "y": v.y, "z": v.z})


def _cc_array(name: str, mappings: list) -> ArrayProperty:
    cc_elems = []
    for cm in mappings:
        cc_elems.append([
            IntProperty("Channel", cm.channel),
            IntProperty("Control", cm.control),
            IntProperty("Value", cm.value),
        ])
    header = StructArrayHeader(name, "MidiControlChange", b'\x00' * 16)
    return ArrayProperty(name, "StructProperty", cc_elems, header)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def load_project_from_file(path: str) -> Project:
    from gvas import load_gvas
    return load_project(load_gvas(path))


def save_project_to_file(path: str, project: Project):
    from gvas import save_gvas
    save_gvas(path, save_project(project))


def duplicate_element(project: Project, elem, workspace: Workspace) -> Union[HitZone, MorphZone, TextLabel, GroupIE, UnknownElement]:
    """Deep-copy an element, assign a new ID, add to the given workspace."""
    new_elem = copy.deepcopy(elem)
    if isinstance(elem, HitZone):
        prefix = "HitZone"
    elif isinstance(elem, MorphZone):
        prefix = "MorphZone"
    elif isinstance(elem, TextLabel):
        prefix = "TextLabel_C"
    elif isinstance(elem, GroupIE):
        prefix = "GroupIE"
    elif isinstance(elem, UnknownElement):
        prefix = "UnknownIE"
    else:
        prefix = "Element"
    new_elem.unique_id = project.generate_id(prefix)
    new_elem.display_name = (elem.display_name or elem.unique_id) + " (copy)"
    # Offset translation slightly
    new_elem.transform.translation.x += 10.0
    project.elements.append(new_elem)
    workspace.element_ids.append(new_elem.unique_id)
    return new_elem
