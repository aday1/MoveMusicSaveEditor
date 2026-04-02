#!/usr/bin/env python3
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)

from editor import MainWindow, _load_config
window = MainWindow()
window.show()

# Load file
cfg = _load_config()
last = cfg.get("last_file")
if last and os.path.isfile(last):
    window._open_file(last)

# Auto-close after 5 seconds
def auto_exit():
    print("SUCCESS: 5-second test completed")
    app.quit()

timer = QTimer()
timer.timeout.connect(auto_exit)
timer.start(5000)

try:
    sys.exit(app.exec())
except SystemExit:
    pass