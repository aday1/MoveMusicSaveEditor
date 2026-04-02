"""Test GroupIE functionality."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import Project, GroupIE, HitZone, Vec3, Transform, BoundingBox, Workspace
from template_generator import _make_group


def test_groupie_creation():
    """Test basic GroupIE creation."""
    proj = Project()

    # Create some elements to group
    hz1 = HitZone(unique_id=proj.generate_id("HitZone"))
    hz2 = HitZone(unique_id=proj.generate_id("HitZone"))
    hz1.transform.translation = Vec3(-10, 0, 0)
    hz2.transform.translation = Vec3(10, 0, 0)

    elements = [hz1, hz2]
    member_ids = [hz1.unique_id, hz2.unique_id]
    origin = Vec3(0, 0, 0)

    # Create group using template helper
    group = _make_group(proj, "Test Group", origin, member_ids, elements)

    assert group.display_name == "Test Group"
    assert len(group.group_items) == 2
    assert hz1.unique_id in group.group_items
    assert hz2.unique_id in group.group_items
    assert group.bounding_box.is_valid == 1

    print("✓ GroupIE creation works")


def test_groupie_bounding_box():
    """Test bounding box calculation."""
    proj = Project()

    # Create elements at specific positions
    elements = []
    member_ids = []

    # Element at (-50, -30, 0) with scale (1, 1, 1) -> bbox extends ±25 -> (-75, -55, 0)
    hz1 = HitZone(unique_id=proj.generate_id("HitZone"))
    hz1.transform.translation = Vec3(-50, -30, 0)
    hz1.transform.scale = Vec3(1, 1, 1)
    elements.append(hz1)
    member_ids.append(hz1.unique_id)

    # Element at (50, 30, 0) with scale (1, 1, 1) -> bbox extends ±25 -> (75, 55, 0)
    hz2 = HitZone(unique_id=proj.generate_id("HitZone"))
    hz2.transform.translation = Vec3(50, 30, 0)
    hz2.transform.scale = Vec3(1, 1, 1)
    elements.append(hz2)
    member_ids.append(hz2.unique_id)

    group = _make_group(proj, "Bbox Test", Vec3(0, 0, 0), member_ids, elements)

    # Check group position (should be at centroid)
    assert abs(group.transform.translation.x) < 1e-6  # should be 0
    assert abs(group.transform.translation.y) < 1e-6  # should be 0

    # Check bounding box (should encompass both elements + padding)
    # Total range: x=150, y=120 -> half-extents: 75+10=85, 60+10=70
    bb = group.bounding_box
    assert abs(bb.min.x - (-85)) < 1e-6
    assert abs(bb.max.x - 85) < 1e-6
    assert abs(bb.min.y - (-70)) < 1e-6
    assert abs(bb.max.y - 70) < 1e-6

    print("✓ GroupIE bounding box calculation works")


def test_groupie_empty():
    """Test empty group handling."""
    proj = Project()

    # Create empty group
    group = _make_group(proj, "Empty Group", Vec3(0, 0, 0), [], [])

    assert group.display_name == "Empty Group"
    assert len(group.group_items) == 0
    # Should still have valid bounding box with default size
    assert group.bounding_box.is_valid == 1

    print("✓ Empty GroupIE works")


def test_groupie_in_project():
    """Test GroupIE integration with Project."""
    proj = Project()
    proj.project_name = "Group Test Project"

    # Create workspace
    ws = Workspace(unique_id=proj.generate_id("Workspace"), display_name="Test WS", enabled=True)
    proj.workspaces.append(ws)

    # Create elements
    hz = HitZone(unique_id=proj.generate_id("HitZone"))
    group = GroupIE(unique_id=proj.generate_id("GroupIE"))
    group.display_name = "Test Group"
    group.group_items = [hz.unique_id]

    # Add to project
    proj.elements.extend([hz, group])
    ws.element_ids.extend([hz.unique_id, group.unique_id])

    # Verify structure
    assert len(proj.elements) == 2
    assert len(ws.element_ids) == 2
    assert hz.unique_id in group.group_items

    print("✓ GroupIE project integration works")


if __name__ == "__main__":
    test_groupie_creation()
    test_groupie_bounding_box()
    test_groupie_empty()
    test_groupie_in_project()
    print("All GroupIE tests passed!")