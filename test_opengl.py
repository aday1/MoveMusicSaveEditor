#!/usr/bin/env python3
import sys

print("1. Testing basic PyQt6...")
try:
    from PyQt6.QtWidgets import QApplication, QMainWindow
    print("   OK PyQt6 widgets imported")
except Exception as e:
    print(f"   ERROR PyQt6 widgets failed: {e}")
    sys.exit(1)

print("2. Testing OpenGL...")
try:
    from PyQt6.QtOpenGLWidgets import QOpenGLWidget
    print("   OK QOpenGLWidget imported")
except Exception as e:
    print(f"   ERROR QOpenGLWidget failed: {e}")
    sys.exit(1)

try:
    from OpenGL.GL import *
    print("   OK PyOpenGL imported")
except Exception as e:
    print(f"   ERROR PyOpenGL failed: {e}")
    sys.exit(1)

print("3. Testing QApplication...")
app = QApplication(sys.argv)
print("   OK QApplication created")

print("4. Testing QOpenGLWidget creation...")
widget = QOpenGLWidget()
print("   OK QOpenGLWidget created")

print("5. Testing show...")
widget.show()
print("   OK Widget shown")

print("6. All tests passed!")