"""MoveMusic .mmc Save File Editor — Entry point."""

from pathlib import Path
import os
import sys

# Force local workspace modules when launched via shortcuts/pythonw.
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from editor import run_editor

if __name__ == "__main__":
    run_editor()
