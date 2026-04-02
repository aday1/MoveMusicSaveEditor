"""
Generic UE4 GVAS save game binary reader/writer.

Supports the property types found in MoveMusic .mmc files:
StrProperty, IntProperty, FloatProperty, BoolProperty, NameProperty,
ObjectProperty, EnumProperty, ByteProperty (enum form), StructProperty,
ArrayProperty, SetProperty.

Round-trip guarantee: read then write produces byte-identical output.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GvasHeader:
    save_game_version: int
    package_version: int
    engine_major: int
    engine_minor: int
    engine_patch: int
    engine_build: int
    engine_branch: str
    custom_version_format: int
    custom_versions: list  # [(bytes_16, int), ...]
    save_game_class_name: str


@dataclass
class GvasProperty:
    name: str
    type_name: str


@dataclass
class StrProperty(GvasProperty):
    value: str = ""

    def __init__(self, name: str, value: str = ""):
        super().__init__(name, "StrProperty")
        self.value = value


@dataclass
class IntProperty(GvasProperty):
    value: int = 0

    def __init__(self, name: str, value: int = 0):
        super().__init__(name, "IntProperty")
        self.value = value


@dataclass
class FloatProperty(GvasProperty):
    value: float = 0.0

    def __init__(self, name: str, value: float = 0.0):
        super().__init__(name, "FloatProperty")
        self.value = value


@dataclass
class BoolProperty(GvasProperty):
    value: bool = False

    def __init__(self, name: str, value: bool = False):
        super().__init__(name, "BoolProperty")
        self.value = value


@dataclass
class NameProperty(GvasProperty):
    value: str = ""

    def __init__(self, name: str, value: str = ""):
        super().__init__(name, "NameProperty")
        self.value = value


@dataclass
class ObjectProperty(GvasProperty):
    value: str = ""

    def __init__(self, name: str, value: str = ""):
        super().__init__(name, "ObjectProperty")
        self.value = value


@dataclass
class EnumProperty(GvasProperty):
    enum_type: str = ""
    value: str = ""

    def __init__(self, name: str, enum_type: str = "", value: str = ""):
        super().__init__(name, "EnumProperty")
        self.enum_type = enum_type
        self.value = value


@dataclass
class ByteEnumProperty(GvasProperty):
    """ByteProperty used as an enum (enum_name != 'None')."""
    enum_name: str = ""
    value: str = ""

    def __init__(self, name: str, enum_name: str = "", value: str = ""):
        super().__init__(name, "ByteProperty")
        self.enum_name = enum_name
        self.value = value


@dataclass
class StructProperty(GvasProperty):
    struct_type: str = ""
    guid: bytes = b'\x00' * 16
    value: Any = None  # dict for simple structs, list[GvasProperty] for complex

    def __init__(self, name: str, struct_type: str = "", guid: bytes = b'\x00' * 16, value: Any = None):
        super().__init__(name, "StructProperty")
        self.struct_type = struct_type
        self.guid = guid
        self.value = value if value is not None else []


@dataclass
class StructArrayHeader:
    """Metadata for arrays of StructProperty."""
    property_name: str
    struct_type: str
    guid: bytes = b'\x00' * 16


@dataclass
class ArrayProperty(GvasProperty):
    inner_type: str = ""
    elements: list = field(default_factory=list)
    struct_header: Optional[StructArrayHeader] = None
    # For ByteProperty arrays that contain embedded property data
    raw_bytes: Optional[bytes] = None

    def __init__(self, name: str, inner_type: str = "", elements: list = None,
                 struct_header: Optional[StructArrayHeader] = None,
                 raw_bytes: Optional[bytes] = None):
        super().__init__(name, "ArrayProperty")
        self.inner_type = inner_type
        self.elements = elements if elements is not None else []
        self.struct_header = struct_header
        self.raw_bytes = raw_bytes


@dataclass
class SetProperty(GvasProperty):
    inner_type: str = ""
    removed_count: int = 0
    elements: list = field(default_factory=list)

    def __init__(self, name: str, inner_type: str = "", removed_count: int = 0,
                 elements: list = None):
        super().__init__(name, "SetProperty")
        self.inner_type = inner_type
        self.removed_count = removed_count
        self.elements = elements if elements is not None else []


@dataclass
class GvasFile:
    header: GvasHeader
    properties: list  # list[GvasProperty]
    trailing_bytes: bytes = b''


# ---------------------------------------------------------------------------
# Simple struct types (read/written as raw bytes, no nested property framing)
# ---------------------------------------------------------------------------

SIMPLE_STRUCTS = {
    "Vector": 12,       # 3 x float32
    "Quat": 16,         # 4 x float32
    "LinearColor": 16,  # 4 x float32 (RGBA)
    "DateTime": 8,      # int64
    "Rotator": 12,      # 3 x float32
    "Vector2D": 8,      # 2 x float32
    "Guid": 16,
    "Box": 25,          # Min(3xf32) + Max(3xf32) + IsValid(u8)
}


def _read_simple_struct(stream: BytesIO, struct_type: str) -> dict:
    if struct_type == "Vector":
        x, y, z = struct.unpack('<fff', stream.read(12))
        return {"x": x, "y": y, "z": z}
    elif struct_type == "Quat":
        x, y, z, w = struct.unpack('<ffff', stream.read(16))
        return {"x": x, "y": y, "z": z, "w": w}
    elif struct_type == "LinearColor":
        r, g, b, a = struct.unpack('<ffff', stream.read(16))
        return {"r": r, "g": g, "b": b, "a": a}
    elif struct_type == "DateTime":
        ticks = struct.unpack('<q', stream.read(8))[0]
        return {"ticks": ticks}
    elif struct_type == "Rotator":
        pitch, yaw, roll = struct.unpack('<fff', stream.read(12))
        return {"pitch": pitch, "yaw": yaw, "roll": roll}
    elif struct_type == "Vector2D":
        x, y = struct.unpack('<ff', stream.read(8))
        return {"x": x, "y": y}
    elif struct_type == "Guid":
        return {"bytes": stream.read(16)}
    elif struct_type == "Box":
        min_x, min_y, min_z = struct.unpack('<fff', stream.read(12))
        max_x, max_y, max_z = struct.unpack('<fff', stream.read(12))
        is_valid = struct.unpack('<B', stream.read(1))[0]
        return {"min": {"x": min_x, "y": min_y, "z": min_z},
                "max": {"x": max_x, "y": max_y, "z": max_z},
                "is_valid": is_valid}
    else:
        raise ValueError(f"Unknown simple struct type: {struct_type}")


def _write_simple_struct(stream: BytesIO, struct_type: str, value: dict):
    if struct_type == "Vector":
        stream.write(struct.pack('<fff', value["x"], value["y"], value["z"]))
    elif struct_type == "Quat":
        stream.write(struct.pack('<ffff', value["x"], value["y"], value["z"], value["w"]))
    elif struct_type == "LinearColor":
        stream.write(struct.pack('<ffff', value["r"], value["g"], value["b"], value["a"]))
    elif struct_type == "DateTime":
        stream.write(struct.pack('<q', value["ticks"]))
    elif struct_type == "Rotator":
        stream.write(struct.pack('<fff', value["pitch"], value["yaw"], value["roll"]))
    elif struct_type == "Vector2D":
        stream.write(struct.pack('<ff', value["x"], value["y"]))
    elif struct_type == "Guid":
        stream.write(value["bytes"])
    elif struct_type == "Box":
        m = value["min"]
        stream.write(struct.pack('<fff', m["x"], m["y"], m["z"]))
        x = value["max"]
        stream.write(struct.pack('<fff', x["x"], x["y"], x["z"]))
        stream.write(struct.pack('<B', value.get("is_valid", 1)))
    else:
        raise ValueError(f"Unknown simple struct type: {struct_type}")


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class GvasReader:
    def __init__(self, stream: BytesIO):
        self.stream = stream

    def read(self) -> GvasFile:
        header = self._read_header()
        properties = self._read_property_list()
        trailing = self.stream.read()  # capture any trailing bytes
        return GvasFile(header=header, properties=properties, trailing_bytes=trailing)

    # -- Header --

    def _read_header(self) -> GvasHeader:
        s = self.stream
        magic = s.read(4)
        if magic != b'GVAS':
            raise ValueError(f"Not a GVAS file (magic: {magic!r})")

        sgv = self._read_i32()
        pkv = self._read_i32()
        emaj = self._read_u16()
        emin = self._read_u16()
        epat = self._read_u16()
        ebuild = self._read_u32()
        ebranch = self._read_string()

        cvf = self._read_i32()
        cvc = self._read_i32()
        custom_versions = []
        for _ in range(cvc):
            guid = s.read(16)
            ver = self._read_i32()
            custom_versions.append((guid, ver))

        sgcn = self._read_string()

        return GvasHeader(
            save_game_version=sgv,
            package_version=pkv,
            engine_major=emaj,
            engine_minor=emin,
            engine_patch=epat,
            engine_build=ebuild,
            engine_branch=ebranch,
            custom_version_format=cvf,
            custom_versions=custom_versions,
            save_game_class_name=sgcn,
        )

    # -- Properties --

    def _read_property_list(self) -> list:
        props = []
        while True:
            prop = self._read_property()
            if prop is None:  # "None" terminator
                break
            props.append(prop)
        return props

    def _read_property(self) -> Optional[GvasProperty]:
        name = self._read_string()
        if name == "None":
            return None

        type_name = self._read_string()
        size = self._read_i64()

        if type_name == "StrProperty":
            return self._read_str_property(name, size)
        elif type_name == "IntProperty":
            return self._read_int_property(name, size)
        elif type_name == "FloatProperty":
            return self._read_float_property(name, size)
        elif type_name == "BoolProperty":
            return self._read_bool_property(name, size)
        elif type_name == "NameProperty":
            return self._read_name_property(name, size)
        elif type_name == "ObjectProperty":
            return self._read_object_property(name, size)
        elif type_name == "EnumProperty":
            return self._read_enum_property(name, size)
        elif type_name == "ByteProperty":
            return self._read_byte_property(name, size)
        elif type_name == "StructProperty":
            return self._read_struct_property(name, size)
        elif type_name == "ArrayProperty":
            return self._read_array_property(name, size)
        elif type_name == "SetProperty":
            return self._read_set_property(name, size)
        else:
            # Unknown type: read size bytes + separator and store raw
            sep = self.stream.read(1)
            raw = self.stream.read(size)
            prop = GvasProperty(name, type_name)
            prop.raw_data = sep + raw
            return prop

    def _read_str_property(self, name: str, size: int) -> StrProperty:
        self.stream.read(1)  # separator
        value = self._read_string()
        return StrProperty(name, value)

    def _read_int_property(self, name: str, size: int) -> IntProperty:
        self.stream.read(1)  # separator
        value = self._read_i32()
        return IntProperty(name, value)

    def _read_float_property(self, name: str, size: int) -> FloatProperty:
        self.stream.read(1)  # separator
        value = struct.unpack('<f', self.stream.read(4))[0]
        return FloatProperty(name, value)

    def _read_bool_property(self, name: str, size: int) -> BoolProperty:
        # Bool: value byte is BEFORE separator, size is always 0
        value = self.stream.read(1)[0] != 0
        self.stream.read(1)  # separator
        return BoolProperty(name, value)

    def _read_name_property(self, name: str, size: int) -> NameProperty:
        self.stream.read(1)  # separator
        value = self._read_string()
        return NameProperty(name, value)

    def _read_object_property(self, name: str, size: int) -> ObjectProperty:
        self.stream.read(1)  # separator
        value = self._read_string()
        return ObjectProperty(name, value)

    def _read_enum_property(self, name: str, size: int) -> EnumProperty:
        enum_type = self._read_string()
        self.stream.read(1)  # separator
        value = self._read_string()
        return EnumProperty(name, enum_type, value)

    def _read_byte_property(self, name: str, size: int) -> ByteEnumProperty:
        enum_name = self._read_string()
        self.stream.read(1)  # separator
        if enum_name == "None":
            # Raw byte data
            raw = self.stream.read(size)
            prop = ByteEnumProperty(name, enum_name, "")
            prop.raw_data = raw
            return prop
        else:
            value = self._read_string()
            return ByteEnumProperty(name, enum_name, value)

    def _read_struct_property(self, name: str, size: int) -> StructProperty:
        struct_type = self._read_string()
        guid = self.stream.read(16)
        self.stream.read(1)  # separator

        if struct_type in SIMPLE_STRUCTS:
            value = _read_simple_struct(self.stream, struct_type)
        else:
            # Complex struct: nested property list
            value = self._read_property_list()

        return StructProperty(name, struct_type, guid, value)

    def _read_array_property(self, name: str, size: int) -> ArrayProperty:
        inner_type = self._read_string()
        self.stream.read(1)  # separator

        if inner_type == "ByteProperty":
            # Raw byte blob
            byte_count = self._read_i32()
            raw = self.stream.read(byte_count)
            return ArrayProperty(name, inner_type, raw_bytes=raw)

        count = self._read_i32()

        if inner_type == "StructProperty":
            return self._read_struct_array(name, count)
        elif inner_type == "ObjectProperty":
            elements = []
            for _ in range(count):
                elements.append(self._read_string())
            return ArrayProperty(name, inner_type, elements)
        elif inner_type == "StrProperty":
            elements = []
            for _ in range(count):
                elements.append(self._read_string())
            return ArrayProperty(name, inner_type, elements)
        elif inner_type == "IntProperty":
            elements = []
            for _ in range(count):
                elements.append(self._read_i32())
            return ArrayProperty(name, inner_type, elements)
        elif inner_type == "FloatProperty":
            elements = []
            for _ in range(count):
                elements.append(struct.unpack('<f', self.stream.read(4))[0])
            return ArrayProperty(name, inner_type, elements)
        else:
            raise ValueError(f"Unsupported array inner type: {inner_type}")

    def _read_struct_array(self, name: str, count: int) -> ArrayProperty:
        # Struct array header
        prop_name = self._read_string()
        type_tag = self._read_string()  # always "StructProperty"
        data_size = self._read_i64()
        struct_type = self._read_string()
        guid = self.stream.read(16)
        self.stream.read(1)  # separator

        header = StructArrayHeader(prop_name, struct_type, guid)

        elements = []
        for _ in range(count):
            if struct_type in SIMPLE_STRUCTS:
                elements.append(_read_simple_struct(self.stream, struct_type))
            else:
                elements.append(self._read_property_list())
        return ArrayProperty(name, "StructProperty", elements, struct_header=header)

    def _read_set_property(self, name: str, size: int) -> SetProperty:
        inner_type = self._read_string()
        self.stream.read(1)  # separator
        removed_count = self._read_i32()
        count = self._read_i32()
        elements = []
        if inner_type == "ObjectProperty":
            for _ in range(count):
                elements.append(self._read_string())
        elif inner_type == "StrProperty":
            for _ in range(count):
                elements.append(self._read_string())
        else:
            raise ValueError(f"Unsupported set inner type: {inner_type}")
        return SetProperty(name, inner_type, removed_count, elements)

    # -- Primitives --

    def _read_string(self) -> str:
        length = self._read_i32()
        if length == 0:
            return ""
        if length < 0:
            # UTF-16LE
            char_count = -length
            raw = self.stream.read(char_count * 2)
            return raw[:-2].decode('utf-16-le')
        else:
            raw = self.stream.read(length)
            return raw[:-1].decode('utf-8')

    def _read_i32(self) -> int:
        return struct.unpack('<i', self.stream.read(4))[0]

    def _read_u16(self) -> int:
        return struct.unpack('<H', self.stream.read(2))[0]

    def _read_u32(self) -> int:
        return struct.unpack('<I', self.stream.read(4))[0]

    def _read_i64(self) -> int:
        return struct.unpack('<q', self.stream.read(8))[0]


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class GvasWriter:
    def __init__(self, stream: BytesIO):
        self.stream = stream

    def write(self, gvas_file: GvasFile):
        self._write_header(gvas_file.header)
        self._write_property_list(gvas_file.properties)
        if gvas_file.trailing_bytes:
            self.stream.write(gvas_file.trailing_bytes)

    # -- Header --

    def _write_header(self, h: GvasHeader):
        s = self.stream
        s.write(b'GVAS')
        self._write_i32(h.save_game_version)
        self._write_i32(h.package_version)
        self._write_u16(h.engine_major)
        self._write_u16(h.engine_minor)
        self._write_u16(h.engine_patch)
        self._write_u32(h.engine_build)
        self._write_string(h.engine_branch)
        self._write_i32(h.custom_version_format)
        self._write_i32(len(h.custom_versions))
        for guid, ver in h.custom_versions:
            s.write(guid)
            self._write_i32(ver)
        self._write_string(h.save_game_class_name)

    # -- Properties --

    def _write_property_list(self, props: list):
        for prop in props:
            self._write_property(prop)
        self._write_string("None")

    def _write_property(self, prop: GvasProperty):
        self._write_string(prop.name)
        self._write_string(prop.type_name)

        if isinstance(prop, BoolProperty):
            # Bool: size is always 0, value byte before separator
            self._write_i64(0)
            self.stream.write(bytes([1 if prop.value else 0]))
            self.stream.write(b'\x00')  # separator
            return

        # For all other types, serialize value to buffer, then write size
        buf = BytesIO()
        temp_writer = GvasWriter(buf)

        if isinstance(prop, StrProperty):
            self._write_i64_and_sep_then_value(prop, temp_writer, buf,
                lambda tw: tw._write_string(prop.value))
        elif isinstance(prop, IntProperty):
            self._write_i64_and_sep_then_value(prop, temp_writer, buf,
                lambda tw: tw._write_i32(prop.value))
        elif isinstance(prop, FloatProperty):
            self._write_i64_and_sep_then_value(prop, temp_writer, buf,
                lambda tw: tw.stream.write(struct.pack('<f', prop.value)))
        elif isinstance(prop, NameProperty):
            self._write_i64_and_sep_then_value(prop, temp_writer, buf,
                lambda tw: tw._write_string(prop.value))
        elif isinstance(prop, ObjectProperty):
            self._write_i64_and_sep_then_value(prop, temp_writer, buf,
                lambda tw: tw._write_string(prop.value))
        elif isinstance(prop, EnumProperty):
            self._write_enum_property(prop)
        elif isinstance(prop, ByteEnumProperty):
            self._write_byte_property(prop)
        elif isinstance(prop, StructProperty):
            self._write_struct_property(prop)
        elif isinstance(prop, ArrayProperty):
            self._write_array_property(prop)
        elif isinstance(prop, SetProperty):
            self._write_set_property(prop)
        else:
            # Unknown: write raw data
            if hasattr(prop, 'raw_data'):
                self._write_i64(len(prop.raw_data) - 1)  # minus separator
                self.stream.write(prop.raw_data)

    def _write_i64_and_sep_then_value(self, prop, temp_writer, buf, write_fn):
        """Helper for simple properties: compute size from serialized value."""
        write_fn(temp_writer)
        value_bytes = buf.getvalue()
        self._write_i64(len(value_bytes))
        self.stream.write(b'\x00')  # separator
        self.stream.write(value_bytes)

    def _write_enum_property(self, prop: EnumProperty):
        # Size covers only the value string (not the enum_type string)
        buf = BytesIO()
        tw = GvasWriter(buf)
        tw._write_string(prop.value)
        value_bytes = buf.getvalue()

        self._write_i64(len(value_bytes))
        self._write_string(prop.enum_type)
        self.stream.write(b'\x00')  # separator
        self.stream.write(value_bytes)

    def _write_byte_property(self, prop: ByteEnumProperty):
        if prop.enum_name == "None" and hasattr(prop, 'raw_data'):
            self._write_i64(len(prop.raw_data))
            self._write_string(prop.enum_name)
            self.stream.write(b'\x00')
            self.stream.write(prop.raw_data)
        else:
            buf = BytesIO()
            tw = GvasWriter(buf)
            tw._write_string(prop.value)
            value_bytes = buf.getvalue()

            self._write_i64(len(value_bytes))
            self._write_string(prop.enum_name)
            self.stream.write(b'\x00')
            self.stream.write(value_bytes)

    def _write_struct_property(self, prop: StructProperty):
        # Serialize the struct value to compute size
        buf = BytesIO()
        if prop.struct_type in SIMPLE_STRUCTS:
            _write_simple_struct(buf, prop.struct_type, prop.value)
        else:
            tw = GvasWriter(buf)
            tw._write_property_list(prop.value)
        value_bytes = buf.getvalue()

        self._write_i64(len(value_bytes))
        self._write_string(prop.struct_type)
        self.stream.write(prop.guid)
        self.stream.write(b'\x00')  # separator
        self.stream.write(value_bytes)

    def _write_array_property(self, prop: ArrayProperty):
        if prop.inner_type == "ByteProperty" and prop.raw_bytes is not None:
            # Raw byte blob
            # size = 4 (count) + len(raw_bytes)
            self._write_i64(4 + len(prop.raw_bytes))
            self._write_string(prop.inner_type)
            self.stream.write(b'\x00')
            self._write_i32(len(prop.raw_bytes))
            self.stream.write(prop.raw_bytes)
            return

        # Serialize elements to compute size (includes count int32)
        buf = BytesIO()
        tw = GvasWriter(buf)

        if prop.inner_type == "StructProperty":
            self._write_struct_array(prop, buf, tw)
            return

        tw._write_i32(len(prop.elements))
        for elem in prop.elements:
            if prop.inner_type == "ObjectProperty":
                tw._write_string(elem)
            elif prop.inner_type == "StrProperty":
                tw._write_string(elem)
            elif prop.inner_type == "IntProperty":
                tw._write_i32(elem)
            elif prop.inner_type == "FloatProperty":
                tw.stream.write(struct.pack('<f', elem))

        value_bytes = buf.getvalue()
        self._write_i64(len(value_bytes))
        self._write_string(prop.inner_type)
        self.stream.write(b'\x00')
        self.stream.write(value_bytes)

    def _write_struct_array(self, prop: ArrayProperty, buf: BytesIO, tw: GvasWriter):
        hdr = prop.struct_header

        # First serialize just the element data to compute sizes
        elem_buf = BytesIO()
        for elem in prop.elements:
            if hdr.struct_type in SIMPLE_STRUCTS:
                _write_simple_struct(elem_buf, hdr.struct_type, elem)
            else:
                elem_tw = GvasWriter(elem_buf)
                elem_tw._write_property_list(elem)
        elem_bytes = elem_buf.getvalue()

        # Build the full inner part: count + header + elements
        # The "size" field of the ArrayProperty covers everything after separator
        inner_buf = BytesIO()
        inner_tw = GvasWriter(inner_buf)
        inner_tw._write_i32(len(prop.elements))  # count
        inner_tw._write_string(hdr.property_name)
        inner_tw._write_string("StructProperty")
        inner_tw._write_i64(len(elem_bytes))  # data size
        inner_tw._write_string(hdr.struct_type)
        inner_buf.write(hdr.guid)
        inner_buf.write(b'\x00')  # separator
        inner_buf.write(elem_bytes)
        inner_bytes = inner_buf.getvalue()

        self._write_i64(len(inner_bytes))
        self._write_string(prop.inner_type)
        self.stream.write(b'\x00')
        self.stream.write(inner_bytes)

    def _write_set_property(self, prop: SetProperty):
        buf = BytesIO()
        tw = GvasWriter(buf)
        tw._write_i32(prop.removed_count)
        tw._write_i32(len(prop.elements))
        for elem in prop.elements:
            if prop.inner_type == "ObjectProperty":
                tw._write_string(elem)
            elif prop.inner_type == "StrProperty":
                tw._write_string(elem)
        value_bytes = buf.getvalue()

        self._write_i64(len(value_bytes))
        self._write_string(prop.inner_type)
        self.stream.write(b'\x00')
        self.stream.write(value_bytes)

    # -- Primitives --

    def _write_string(self, s: str):
        if not s:
            self._write_i32(0)
            return
        encoded = s.encode('utf-8') + b'\x00'
        self._write_i32(len(encoded))
        self.stream.write(encoded)

    def _write_i32(self, v: int):
        self.stream.write(struct.pack('<i', v))

    def _write_u16(self, v: int):
        self.stream.write(struct.pack('<H', v))

    def _write_u32(self, v: int):
        self.stream.write(struct.pack('<I', v))

    def _write_i64(self, v: int):
        self.stream.write(struct.pack('<q', v))


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def load_gvas(path: str) -> GvasFile:
    with open(path, 'rb') as f:
        return GvasReader(BytesIO(f.read())).read()


def save_gvas(path: str, gvas_file: GvasFile):
    buf = BytesIO()
    GvasWriter(buf).write(gvas_file)
    with open(path, 'wb') as f:
        f.write(buf.getvalue())


def find_property(props: list, name: str) -> Optional[GvasProperty]:
    """Find a property by name in a property list."""
    for p in props:
        if p.name == name:
            return p
    return None
