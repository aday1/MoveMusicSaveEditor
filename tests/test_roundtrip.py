"""Round-trip test: parse .mmc file, serialize back, assert byte-identical."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from io import BytesIO
from gvas import load_gvas, GvasWriter, GvasFile, find_property


SAMPLE_FILE = os.path.join(os.path.dirname(__file__), '..', 'SaveTestProject.test.mmc')


def test_roundtrip():
    original = open(SAMPLE_FILE, 'rb').read()
    gvas_file = load_gvas(SAMPLE_FILE)

    buf = BytesIO()
    GvasWriter(buf).write(gvas_file)
    output = buf.getvalue()

    assert len(original) == len(output), f"Size mismatch: {len(original)} vs {len(output)}"
    assert original == output, "Byte mismatch in round-trip"


def test_header():
    gvas_file = load_gvas(SAMPLE_FILE)
    h = gvas_file.header
    assert h.save_game_version == 2
    assert h.package_version == 522
    assert h.engine_major == 4
    assert h.engine_minor == 27
    assert h.engine_patch == 2
    assert h.engine_branch == "++UE4+Partner-Oculus-4.27"
    assert h.custom_version_format == 3
    assert len(h.custom_versions) == 49
    assert h.save_game_class_name == "/Script/MoveMusic.Project"


def test_top_level_properties():
    gvas_file = load_gvas(SAMPLE_FILE)
    props = gvas_file.properties
    assert len(props) == 6

    pname = find_property(props, "ProjectName")
    assert pname is not None
    assert pname.value == "jesuschrist"

    vi = find_property(props, "VirtualInterfaceRecord")
    assert vi is not None
    assert vi.struct_type == "ObjectRecord"

    ws = find_property(props, "WorkspaceRecords")
    assert ws is not None
    assert len(ws.elements) == 4

    elems = find_property(props, "InterfaceElementRecords")
    assert elems is not None
    assert len(elems.elements) == 3


if __name__ == "__main__":
    test_roundtrip()
    print("test_roundtrip PASSED")
    test_header()
    print("test_header PASSED")
    test_top_level_properties()
    print("test_top_level_properties PASSED")
    print("All tests passed!")
