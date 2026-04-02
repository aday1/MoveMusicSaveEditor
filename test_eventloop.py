#!/usr/bin/env python3
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

print("Creating app with event loop test...")
app = QApplication(sys.argv)

print("Importing editor...")
from editor import MainWindow, _load_config

print("Creating window...")
window = MainWindow()
window.show()

print("Loading file...")
cfg = _load_config()
last = cfg.get("last_file")
if last and os.path.isfile(last):
    window._open_file(last)

# Auto-close after 2 seconds to test event loop
def auto_exit():
    print("Auto-closing...")
    app.quit()

timer = QTimer()
timer.timeout.connect(auto_exit)
timer.start(2000)  # 2 seconds

print("Starting event loop...")
try:
    sys.exit(app.exec())
except Exception as e:
    print(f"Event loop crashed: {e}")
    raise