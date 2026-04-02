#!/usr/bin/env python3
import sys
import os
from PyQt6.QtWidgets import QApplication

print("Stage 1: Basic setup...")
app = QApplication(sys.argv)

print("Stage 2: Import editor...")
from editor import MainWindow, _load_config

print("Stage 3: Create window...")
window = MainWindow()
window.show()

print("Stage 4: Test file loading...")
cfg = _load_config()
last = cfg.get("last_file")
print(f"   Config last_file: {last}")

if last and os.path.isfile(last):
    print(f"Stage 5: Loading file: {last}")
    window._open_file(last)
    print("   OK File loaded")
else:
    print("   No file to load")

print("All stages passed!")