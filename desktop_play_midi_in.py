"""MIDI input for Desktop Play: map hardware CC (e.g. Roli Block) to bound MorphZone."""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal


def resolve_midi_input_port(output_or_guess: str) -> Optional[str]:
    """Map a MIDI output port name to an input port (often identical on Windows)."""
    if not output_or_guess or not str(output_or_guess).strip():
        return None
    want = str(output_or_guess).strip()
    try:
        import mido
    except ImportError:
        return None
    try:
        names = list(mido.get_input_names())
    except Exception:
        return None
    if want in names:
        return want
    wl = want.lower()
    for n in names:
        if n.lower() == wl:
            return n
    for n in names:
        nl = n.lower()
        if wl in nl or nl in wl:
            return n
    return None


class DesktopPlayMidiInThread(QThread):
    """Background receive loop; emits control_change (channel 0-15, control, value)."""

    control_change = pyqtSignal(int, int, int)
    pitchwheel = pyqtSignal(int, int)

    def __init__(self, input_port_name: str):
        super().__init__()
        self._input_port_name = input_port_name
        self._port = None
        self._running = True

    def stop_gracefully(self) -> None:
        self._running = False
        p = self._port
        if p is not None:
            try:
                p.close()
            except Exception:
                pass

    def run(self) -> None:
        try:
            import mido
        except ImportError:
            logging.warning("Desktop play MIDI in: mido not installed")
            return
        try:
            self._port = mido.open_input(self._input_port_name)
        except Exception as exc:
            logging.warning(
                "Desktop play MIDI in: cannot open input %r (%s)",
                self._input_port_name,
                exc,
            )
            return
        try:
            for msg in self._port:
                if not self._running:
                    break
                if msg.type == "control_change":
                    self.control_change.emit(int(msg.channel), int(msg.control), int(msg.value))
                elif msg.type == "pitchwheel":
                    p = int(getattr(msg, "pitch", 8192))
                    p = max(0, min(16383, p))
                    v = int(round(p * 127.0 / 16383.0))
                    self.pitchwheel.emit(int(msg.channel), v)
        except Exception:
            if self._running:
                logging.exception("Desktop play MIDI in loop")
        finally:
            try:
                if self._port is not None:
                    self._port.close()
            except Exception:
                pass
            self._port = None
