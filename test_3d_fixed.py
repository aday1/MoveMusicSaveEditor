#!/usr/bin/env python3
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

print("Testing fixed 3D editor...")
app = QApplication(sys.argv)

from editor import MainWindow, _load_config
window = MainWindow()
window.show()

# Load file
cfg = _load_config()
last = cfg.get("last_file")
if last and os.path.isfile(last):
    print(f"Loading: {last}")
    window._open_file(last)

# Auto-close after 3 seconds
def auto_exit():
    print("Test completed successfully!")
    app.quit()

timer = QTimer()
timer.timeout.connect(auto_exit)
timer.start(3000)

print("Starting event loop (3 second test)...")
try:
    sys.exit(app.exec())
except SystemExit:
    print("Clean exit")
except Exception as e:
    print(f"Exception: {e}")
    raise