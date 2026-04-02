#!/usr/bin/env python3
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow

print("Stage 1: Basic app...")
app = QApplication(sys.argv)

print("Stage 2: Import our model...")
from model import Project, HitZone, MorphZone
print("   OK Model imported")

print("Stage 3: Import viewport3d...")
from viewport3d import SceneViewport
print("   OK Viewport imported")

print("Stage 4: Create viewport...")
viewport = SceneViewport()
print("   OK Viewport created")

print("Stage 5: Show viewport...")
viewport.show()
print("   OK Viewport shown")

print("Stage 6: Import full editor...")
from editor import MainWindow
print("   OK Editor imported")

print("Stage 7: Create main window...")
window = MainWindow()
print("   OK Main window created")

print("Stage 8: Show main window...")
window.show()
print("   OK Main window shown")

print("All stages passed!")