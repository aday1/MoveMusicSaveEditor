
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from roliblock_led import build_block_sysex, rgba_to_bgr565  # noqa: E402
from template_generator import TEMPLATES  # noqa: E402


def test_build_block_sysex_shape():
    b = build_block_sysex(0, [0x01, 0x02, 0x00])
    assert b[0] == 0xF0 and b[-1] == 0xF7
    assert len(b) == 3 + 8
    assert b[1:6] == bytes([0x00, 0x21, 0x10, 0x77, 0x00])


def test_rgba_to_bgr565_opaque_white():
    c = rgba_to_bgr565(255, 255, 255, 255)
    assert c == 0xFFFF


def test_new_templates_registered():
    assert "Serum: Performance + Macros" in TEMPLATES
    assert "Reason: Subtractor" in TEMPLATES
    assert "Reason: REX / OctoRex" in TEMPLATES


if __name__ == "__main__":
    test_build_block_sysex_shape()
    test_rgba_to_bgr565_opaque_white()
    test_new_templates_registered()
    print("roliblock_led tests ok")
