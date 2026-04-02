#!/usr/bin/env python3
"""Entry point for the Move Music Save Editor."""

import sys
import os

# Allow running from the repo root without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from gui.main_window import MainWindow


def main() -> None:
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
