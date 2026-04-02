#!/usr/bin/env python3
"""
Quick test to verify template functionality
"""

import sys
from PyQt6.QtWidgets import QApplication
from editor import MainWindow

def test_template_no_file():
    """Test that templates work without opening a file first"""

    app = QApplication(sys.argv)
    window = MainWindow()

    # Check if project was auto-created
    if window.project is None:
        print("❌ FAILED: No project auto-created")
        return False

    if not window.project.workspaces:
        print("❌ FAILED: No workspaces in auto-created project")
        return False

    print("✅ SUCCESS: Project auto-created with workspace")
    print(f"   Project: {window.project.project_name}")
    print(f"   Workspaces: {len(window.project.workspaces)}")

    # Try to add a template
    try:
        window._on_add_template("8 Faders (Row)")
        print("✅ SUCCESS: Template added without 'Open file first' error")
        print(f"   Elements in project: {len(window.project.elements)}")
        return True
    except Exception as e:
        print(f"❌ FAILED: Template add error: {e}")
        return False

if __name__ == "__main__":
    test_template_no_file()