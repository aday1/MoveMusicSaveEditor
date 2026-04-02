#!/usr/bin/env python3
import sys
import os
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel
from PyQt6.QtCore import QTimer

print("Testing without 3D viewport...")
app = QApplication(sys.argv)

# Simple window without OpenGL
window = QMainWindow()
window.setCentralWidget(QLabel("Simple test window - no OpenGL"))
window.show()

# Auto-close after 2 seconds
def auto_exit():
    print("Auto-closing...")
    app.quit()

timer = QTimer()
timer.timeout.connect(auto_exit)
timer.start(2000)

print("Starting event loop...")
try:
    sys.exit(app.exec())
except Exception as e:
    print(f"Event loop crashed: {e}")
    raise