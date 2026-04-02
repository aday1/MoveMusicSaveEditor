"""Model tests: load, save, modify, add, duplicate, delete."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from io import BytesIO
from gvas import GvasWriter, load_gvas, find_property
from model import (
    load_project, save_project, load_project_from_file,
    HitZone, MorphZone, TextLabel, GroupIE, Workspace, duplicate_element,
)

SAMPLE_FILE = os.path.join(os.path.dirname(__file__), '..', 'SaveTestProject.test.mmc')
FEATURE_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'FeatureImports.mmc')


def test_model_roundtrip():
    original = open(SAMPLE_FILE, 'rb').read()
    project = load_project_from_file(SAMPLE_FILE)
    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)
    assert buf.getvalue() == original, "Model round-trip failed"


def test_load_project():
    project = load_project_from_file(SAMPLE_FILE)
    assert project.project_name == "jesuschrist"
    assert len(project.workspaces) == 4
    assert len(project.elements) == 3
    assert project.workspaces[0].display_name == "Workspace 1"
    assert project.workspaces[0].enabled is True
    assert len(project.workspaces[0].element_ids) == 3
    assert project.workspaces[1].enabled is False


def test_hitzone_properties():
    project = load_project_from_file(SAMPLE_FILE)
    hz = project.elements[0]
    assert isinstance(hz, HitZone)
    assert hz.unique_id == "HitZone_2147482446"
    assert hz.behavior == "EHitZoneBehavior::Hold"
    assert hz.midi_message_type == "EMidiMessageType::Note"
    assert len(hz.midi_note_mappings) == 1
    assert hz.midi_note_mappings[0].note == 36
    assert hz.midi_note_mappings[0].channel == 1
    assert hz.is_enabled is True


def test_morphzone_properties():
    project = load_project_from_file(SAMPLE_FILE)
    mz = project.elements[2]
    assert isinstance(mz, MorphZone)
    assert mz.unique_id == "MorphZone_2147482430"
    assert mz.dimensions == "EDimensions::Three"
    assert len(mz.x_axis_cc_mappings) == 1
    assert mz.x_axis_cc_mappings[0].control == 70
    assert mz.is_x_axis_enabled is True


def test_modify_and_save():
    project = load_project_from_file(SAMPLE_FILE)
    project.project_name = "modified_project"
    project.elements[0].midi_note_mappings[0].note = 42

    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)

    # Reload from buffer and verify
    from gvas import GvasReader
    gvas2 = GvasReader(BytesIO(buf.getvalue())).read()
    project2 = load_project(gvas2)
    assert project2.project_name == "modified_project"
    assert project2.elements[0].midi_note_mappings[0].note == 42


def test_duplicate_element():
    project = load_project_from_file(SAMPLE_FILE)
    ws = project.workspaces[0]
    orig_count = len(project.elements)
    orig_ws_count = len(ws.element_ids)

    new_elem = duplicate_element(project, project.elements[0], ws)
    assert len(project.elements) == orig_count + 1
    assert len(ws.element_ids) == orig_ws_count + 1
    assert new_elem.unique_id != project.elements[0].unique_id
    assert new_elem.unique_id in ws.element_ids

    # Verify it serializes correctly
    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)
    assert len(buf.getvalue()) > 0


def test_add_and_delete():
    project = load_project_from_file(SAMPLE_FILE)
    ws = project.workspaces[0]

    # Add
    new_hz = HitZone(unique_id=project.generate_id("HitZone"))
    project.elements.append(new_hz)
    ws.element_ids.append(new_hz.unique_id)
    assert len(project.elements) == 4

    # Delete
    project.elements.remove(new_hz)
    ws.element_ids.remove(new_hz.unique_id)
    assert len(project.elements) == 3


def test_unknown_element_roundtrip_preserves_raw_property_data():
    gvas_in = load_gvas(SAMPLE_FILE)
    elem_array = find_property(gvas_in.properties, "InterfaceElementRecords")
    assert elem_array is not None
    assert len(elem_array.elements) > 0

    # Force first element class to an unknown class path.
    first_actor = elem_array.elements[0]
    cls_prop = find_property(first_actor, "Class")
    assert cls_prop is not None
    cls_prop.value = "/Script/MoveMusic.SomeFutureElement"

    pd_prop = find_property(first_actor, "PropertyData")
    assert pd_prop is not None
    original_pd = pd_prop.raw_bytes

    project = load_project(gvas_in)
    gvas_out = save_project(project)

    out_elem_array = find_property(gvas_out.properties, "InterfaceElementRecords")
    out_first_actor = out_elem_array.elements[0]
    out_cls_prop = find_property(out_first_actor, "Class")
    out_pd_prop = find_property(out_first_actor, "PropertyData")

    assert out_cls_prop.value == "/Script/MoveMusic.SomeFutureElement"
    assert out_pd_prop.raw_bytes == original_pd


# ---------------------------------------------------------------------------
# FeatureImports.mmc tests (includes GroupIE)
# ---------------------------------------------------------------------------

def test_feature_imports_roundtrip():
    """FeatureImports.mmc round-trip: parse -> save -> byte-identical."""
    if not os.path.isfile(FEATURE_FILE):
        print("SKIP test_feature_imports_roundtrip (file not found)")
        return
    original = open(FEATURE_FILE, 'rb').read()
    project = load_project_from_file(FEATURE_FILE)
    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)
    output = buf.getvalue()
    assert len(original) == len(output), f"Size mismatch: {len(original)} vs {len(output)}"
    assert original == output, "FeatureImports round-trip byte mismatch"


def test_feature_imports_element_counts():
    """FeatureImports.mmc has 33 elements: HitZones, MorphZones, TextLabels, GroupIE."""
    if not os.path.isfile(FEATURE_FILE):
        print("SKIP test_feature_imports_element_counts (file not found)")
        return
    project = load_project_from_file(FEATURE_FILE)
    assert len(project.elements) == 33
    hitzones = [e for e in project.elements if isinstance(e, HitZone)]
    morphzones = [e for e in project.elements if isinstance(e, MorphZone)]
    textlabels = [e for e in project.elements if isinstance(e, TextLabel)]
    groups = [e for e in project.elements if isinstance(e, GroupIE)]
    assert len(hitzones) > 0, "Should have HitZones"
    assert len(morphzones) > 0, "Should have MorphZones"
    assert len(textlabels) > 0, "Should have TextLabels"
    assert len(groups) >= 1, "Should have at least one GroupIE"


def test_groupie_properties():
    """GroupIE should have bounding box, group_items, and other properties."""
    if not os.path.isfile(FEATURE_FILE):
        print("SKIP test_groupie_properties (file not found)")
        return
    project = load_project_from_file(FEATURE_FILE)
    groups = [e for e in project.elements if isinstance(e, GroupIE)]
    assert len(groups) >= 1
    grp = groups[0]

    # Identity
    assert grp.unique_id.startswith("GroupIE_")
    assert isinstance(grp.display_name, str)

    # Bounding box
    bb = grp.bounding_box
    assert bb.is_valid == 1
    assert bb.min.x < bb.max.x
    assert bb.min.y < bb.max.y

    # Group members
    assert isinstance(grp.group_items, list)
    assert len(grp.group_items) >= 1
    for item_id in grp.group_items:
        assert isinstance(item_id, str)
        # Each member should exist in the project
        found = project.find_element(item_id)
        assert found is not None, f"Group member {item_id} not found in project"

    # Flags
    assert isinstance(grp.is_enabled, bool)
    assert isinstance(grp.is_locked, bool)
    assert isinstance(grp.b_can_be_damaged, bool)


def test_groupie_duplicate():
    """Duplicating a GroupIE should produce a new group with a new ID."""
    if not os.path.isfile(FEATURE_FILE):
        print("SKIP test_groupie_duplicate (file not found)")
        return
    project = load_project_from_file(FEATURE_FILE)
    ws = project.workspaces[0]
    groups = [e for e in project.elements if isinstance(e, GroupIE)]
    assert len(groups) >= 1
    grp = groups[0]

    orig_count = len(project.elements)
    new_grp = duplicate_element(project, grp, ws)
    assert len(project.elements) == orig_count + 1
    assert new_grp.unique_id != grp.unique_id
    assert isinstance(new_grp, GroupIE)
    assert new_grp.bounding_box.is_valid == grp.bounding_box.is_valid

    # Should serialize without error
    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)
    assert len(buf.getvalue()) > 0


def test_groupie_add_and_delete():
    """Add a new GroupIE and delete it."""
    if not os.path.isfile(FEATURE_FILE):
        print("SKIP test_groupie_add_and_delete (file not found)")
        return
    project = load_project_from_file(FEATURE_FILE)
    ws = project.workspaces[0]
    n = len(project.elements)

    new_grp = GroupIE(unique_id=project.generate_id("GroupIE"))
    new_grp.group_items = [project.elements[0].unique_id]
    project.elements.append(new_grp)
    ws.element_ids.append(new_grp.unique_id)
    assert len(project.elements) == n + 1

    # Should serialize
    gvas_out = save_project(project)
    buf = BytesIO()
    GvasWriter(buf).write(gvas_out)
    assert len(buf.getvalue()) > 0

    # Delete
    project.elements.remove(new_grp)
    ws.element_ids.remove(new_grp.unique_id)
    assert len(project.elements) == n


if __name__ == "__main__":
    test_model_roundtrip()
    print("test_model_roundtrip PASSED")
    test_load_project()
    print("test_load_project PASSED")
    test_hitzone_properties()
    print("test_hitzone_properties PASSED")
    test_morphzone_properties()
    print("test_morphzone_properties PASSED")
    test_modify_and_save()
    print("test_modify_and_save PASSED")
    test_duplicate_element()
    print("test_duplicate_element PASSED")
    test_add_and_delete()
    print("test_add_and_delete PASSED")
    test_unknown_element_roundtrip_preserves_raw_property_data()
    print("test_unknown_element_roundtrip_preserves_raw_property_data PASSED")
    test_feature_imports_roundtrip()
    print("test_feature_imports_roundtrip PASSED")
    test_feature_imports_element_counts()
    print("test_feature_imports_element_counts PASSED")
    test_groupie_properties()
    print("test_groupie_properties PASSED")
    test_groupie_duplicate()
    print("test_groupie_duplicate PASSED")
    test_groupie_add_and_delete()
    print("test_groupie_add_and_delete PASSED")
    print("All model tests passed!")
