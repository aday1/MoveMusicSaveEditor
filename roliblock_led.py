"""Roli Lightpad Block LED output (BLOCKS protocol). Ported from Macroversed roliblock.ts."""
from __future__ import annotations
import logging
import threading
import time
from typing import List, Optional, Sequence

DEFAULT_DEVICE_INDEX = 0
LED_DATA_OFFSET = 113
GRID_COLS = 15
GRID_ROWS = 15
LED_PIXEL_COUNT = GRID_COLS * GRID_ROWS
LED_BYTE_COUNT = LED_PIXEL_COUNT * 2
MAX_PACKET_SIZE = 200
PACKET_COUNTER_MAX = 0x03FF
LED_SEND_INTERVAL_MS = 40

BITMAP_LED_DUMP_1 = (
    "02 01 00 30 5A 3E 47 0B 20 01 3A 00 10 71 01 12 4B 31 09 08 60 46 5F 25 11 40 05 02 28 61 01 17 54 11 40 "
    "10 36 78 21 12 6D 1C 30 5B 00 2E 28 63 00 23 6C 70 43 24 5A 39 60 32 01 28 09 41 0D 3E 28 24 10 1B 04 51 "
    "48 1A 0A 08 22 09 1B 2C 30 45 0D 2E 08 24 20 1B 1C 00 5B 6C 50 41 16 36 58 20 10 01 6D 50 40 2D 36 58 60 "
    "0B 01 6D 70 40 2D 3A 78 3F 00 0F 1C 78 4F 07 2E 28 78 08 19 04 52 06 15 01 48 24 00 21 64 10 48 1A 02 18 "
    "60 0C 01 4C 70 40 05 7C 3F 00 7F 0F 60 7F 03 78 7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 7F 0F 60 7F 03 78 "
    "7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 7F 0F 00"
)
BITMAP_LED_DUMP_2 = (
    "02 02 00 0C 5C 7F 07 70 7F 01 7C 3F 00 7F 0F 60 7F 03 78 7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 7F 0F 60 "
    "7F 03 78 7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 7F 0F 60 7F 03 78 7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 "
    "7F 0F 60 7F 03 78 7F 00 7E 1F 40 7F 07 70 7F 01 7C 3F 00 7F 0F 60 7F 03 78 7F 00 1E 19 00 4B"
)

def _parse_dump(hex_str: str) -> List[int]:
    return [int(h, 16) for h in hex_str.split()]

def build_block_sysex(device_index: int, payload: List[int]) -> bytes:
    plen = len(payload) + 8
    d = bytearray(plen)
    d[0] = 0xF0
    d[1] = 0x00
    d[2] = 0x21
    d[3] = 0x10
    d[4] = 0x77
    d[5] = device_index & 0x7F
    for i, p in enumerate(payload):
        d[6 + i] = p & 0x7F
    d[plen - 1] = 0xF7
    ck = (plen - 8) & 0xFF
    for i in range(6, plen - 2):
        ck = (ck + ck * 2 + d[i]) & 0xFF
    d[plen - 2] = ck & 0x7F
    return bytes(d)

def rgba_to_bgr565(r: int, g: int, b: int, a: int) -> int:
    af = a / 255.0
    r5 = (int(r * af) >> 3) & 0x1F
    g6 = (int(g * af) >> 2) & 0x3F
    b5 = (int(b * af) >> 3) & 0x1F
    return (b5 << 11) | (g6 << 5) | r5


class Packed7BitBuilder:
    __slots__ = ("_data", "_written", "_bits")

    def __init__(self) -> None:
        self._data: List[int] = []
        self._written = 0
        self._bits = 0

    def clone(self) -> "Packed7BitBuilder":
        c = Packed7BitBuilder()
        c._data = self._data[:]
        c._written = self._written
        c._bits = self._bits
        return c

    def size(self) -> int:
        return self._written + (1 if self._bits > 0 else 0)

    def get_data(self) -> List[int]:
        return self._data[: self.size()]

    def write_bits(self, value: int, num_bits: int) -> None:
        v = value
        n = num_bits
        while n > 0:
            if self._bits == 0:
                if n < 7:
                    self._data.append(v & 0x7F)
                    self._bits = n
                    return
                if n == 7:
                    self._data.append(v & 0x7F)
                    self._written = len(self._data)
                    return
                self._data.append(v & 0x7F)
                self._written = len(self._data)
                v >>= 7
                n -= 7
            else:
                todo = min(7 - self._bits, n)
                mask = (1 << todo) - 1
                if self._written >= len(self._data):
                    self._data.append(0)
                self._data[self._written] = self._data[self._written] | ((v & mask) << self._bits)
                v >>= todo
                n -= todo
                self._bits += todo
                if self._bits == 7:
                    self._bits = 0
                    self._written += 1


def _build_data_change_messages(new_data: bytes, old_data: bytes, packet_counter_start: int):
    b = Packed7BitBuilder()
    queued: List[List[int]] = []
    pkt_idx = packet_counter_start

    def init_packet() -> None:
        nonlocal b
        b = Packed7BitBuilder()
        b.write_bits(0x02, 7)
        b.write_bits(pkt_idx & PACKET_COUNTER_MAX, 16)

    def flush_packet(end_of_changes: bool) -> None:
        nonlocal b, pkt_idx
        fin = b.clone()
        fin.write_bits(1 if end_of_changes else 0, 3)
        queued.append(fin.get_data())
        pkt_idx += 1
        if not end_of_changes:
            init_packet()

    current_offset = 0

    def append_skip_to_offset() -> None:
        skip_bytes(current_offset)

    def skip_bytes(count: int) -> None:
        nonlocal b, current_offset
        while count > 0:
            if b.size() >= MAX_PACKET_SIZE - 3:
                flush_packet(False)
                append_skip_to_offset()
            if count > 15:
                chunk = min(255, count)
                b.write_bits(3, 3)
                b.write_bits(chunk, 8)
                count -= chunk
            else:
                b.write_bits(2, 3)
                b.write_bits(count, 4)
                count = 0

    init_packet()
    skip_bytes(LED_DATA_OFFSET)

    i = 0
    while i < LED_BYTE_COUNT:
        if new_data[i] == old_data[i]:
            i += 1
            current_offset = i
            continue
        run_end = i
        while run_end < LED_BYTE_COUNT and new_data[run_end] != old_data[run_end]:
            run_end += 1
        seq = new_data[i:run_end]
        if i > current_offset:
            skip_bytes(i - current_offset)
        written = 0
        while written < len(seq):
            if MAX_PACKET_SIZE - b.size() < 4:
                current_offset = i + written
                flush_packet(False)
                append_skip_to_offset()
            room = (MAX_PACKET_SIZE - b.size() - 1) * 7 // 9
            chunk = min(len(seq) - written, max(1, room))
            b.write_bits(4, 3)
            for j in range(chunk):
                b.write_bits(seq[written + j], 8)
                b.write_bits(1 if j < chunk - 1 else 0, 1)
            written += chunk
        i = run_end
        current_offset = i

    flush_packet(True)
    return queued, pkt_idx


def _make_rgba_buffer() -> bytearray:
    return bytearray(LED_PIXEL_COUNT * 4)


def _fill_black(buf: bytearray) -> None:
    for i in range(LED_PIXEL_COUNT * 4):
        buf[i] = 0 if (i % 4) != 3 else 255


def parse_device_id_list(s: Optional[str]) -> List[int]:
    """Parse '0,1,2' or '0' into BLOCK device indices (0-15). Empty -> [0]."""
    if s is None or not str(s).strip():
        return [DEFAULT_DEVICE_INDEX]
    out: List[int] = []
    for part in str(s).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part, 10)
            if 0 <= v <= 15:
                out.append(v)
        except ValueError:
            continue
    return out or [DEFAULT_DEVICE_INDEX]


def morph_axis_label(en_x: bool, en_y: bool, en_z: bool) -> str:
    parts: List[str] = []
    if en_x:
        parts.append("X")
    if en_y:
        parts.append("Y")
    if en_z:
        parts.append("Z")
    return "".join(parts) if parts else "?"


# Simple 5-row x 3-column glyphs (bits: col2 col1 col0, MSB = left)
_GLYPH_ROWS: dict[str, List[int]] = {
    "X": [0b101, 0b101, 0b010, 0b101, 0b101],
    "Y": [0b010, 0b101, 0b010, 0b010, 0b010],
    "Z": [0b111, 0b001, 0b010, 0b100, 0b111],
}


def _draw_glyph_3x5(
    buf: bytearray,
    ch: str,
    anchor_col: int,
    anchor_row: int,
    r: int,
    g: int,
    b: int,
    a: int = 220,
) -> None:
    rows = _GLYPH_ROWS.get(ch.upper())
    if not rows:
        return
    for dr, bits in enumerate(rows):
        for dc in range(3):
            if bits & (4 >> dc):
                _set_pixel_rgba(buf, anchor_col + dc, anchor_row + dr, r, g, b, a)


def _draw_axis_banner(buf: bytearray, label: str, row0: int = 0) -> None:
    """Draw big letter(s) for X / Y / Z / XY / XYZ across the top rows."""
    label = label.upper().strip() or "?"
    n = len(label)
    char_w = 3
    gap = 1
    total_w = n * char_w + (n - 1) * gap if n > 1 else char_w
    start_c = max(0, (GRID_COLS - total_w) // 2)
    lr, lg, lb = 0, 180, 220
    for i, c in enumerate(label):
        if c not in _GLYPH_ROWS:
            continue
        ac = start_c + i * (char_w + gap)
        _draw_glyph_3x5(buf, c, ac, row0, lr, lg, lb, 240)


def _set_pixel_rgba(buf: bytearray, col: int, row: int, r: int, g: int, b: int, a: int = 255) -> None:
    col = max(0, min(GRID_COLS - 1, col))
    row = max(0, min(GRID_ROWS - 1, row))
    idx = (row * GRID_COLS + col) * 4
    buf[idx] = r
    buf[idx + 1] = g
    buf[idx + 2] = b
    buf[idx + 3] = a


LED_LABEL_ROWS = 5
LED_VIZ_ROW0 = 6


def _draw_soft_dot(
    buf: bytearray,
    cx: float,
    cy: float,
    radius: float,
    r: int,
    g: int,
    b: int,
    row_min: int = 0,
    row_max: int = GRID_ROWS - 1,
) -> None:
    r0 = max(0, row_min)
    r1 = min(GRID_ROWS - 1, row_max)
    for row in range(r0, r1 + 1):
        for col in range(GRID_COLS):
            dx = col - cx
            dy = row - cy
            if dx * dx + dy * dy <= radius * radius:
                _set_pixel_rgba(buf, col, row, r, g, b, 255)


def rgba_buffer_to_led_bytes(rgba: bytearray) -> bytearray:
    out = bytearray(LED_BYTE_COUNT)
    for i in range(LED_PIXEL_COUNT):
        r = rgba[i * 4]
        g = rgba[i * 4 + 1]
        b = rgba[i * 4 + 2]
        a = rgba[i * 4 + 3]
        c16 = rgba_to_bgr565(r, g, b, a)
        out[i * 2] = c16 & 0xFF
        out[i * 2 + 1] = (c16 >> 8) & 0xFF
    return out


class RoliblockPadSession:
    """One MIDI port; SysEx may be broadcast to multiple BLOCK device indices (daisy-chain)."""

    def __init__(self, port_name: str, device_indices: Optional[Sequence[int]] = None) -> None:
        self.port_name = port_name
        seen: set = set()
        idxs: List[int] = []
        for d in device_indices or [DEFAULT_DEVICE_INDEX]:
            if d not in seen and 0 <= int(d) <= 15:
                seen.add(int(d))
                idxs.append(int(d))
        self.device_indices = idxs or [DEFAULT_DEVICE_INDEX]
        self._port = None
        self.handshake_done = False
        self.packet_counter = 1
        self.prev_led_data = bytes(LED_BYTE_COUNT)
        self._lock = threading.Lock()
        self._last_send = 0.0
        self._fail_count = 0

    def _open(self):
        import mido
        if self._port is None:
            self._port = mido.open_output(self.port_name)

    def close(self) -> None:
        with self._lock:
            if self._port is not None:
                try:
                    self._port.close()
                except Exception:
                    pass
                self._port = None
        self.handshake_done = False

    def _send_sysex_packet(self, data: bytes) -> None:
        import mido
        self._open()
        if self._port is None:
            return
        if len(data) < 2 or data[0] != 0xF0 or data[-1] != 0xF7:
            return
        inner = tuple(data[1:-1])
        self._port.send(mido.Message("sysex", data=inner))

    def _broadcast_payload(self, payload: List[int]) -> None:
        for dev in self.device_indices:
            self._send_sysex_packet(build_block_sysex(dev, payload))

    def do_handshake(self) -> None:
        delay = 0.1
        self._broadcast_payload([0x01, 0x02, 0x00])
        self._broadcast_payload([0x01, 0x00, 0x00])
        time.sleep(delay)
        self._broadcast_payload([0x01, 0x00, 0x00])
        self._broadcast_payload([0x01, 0x03, 0x00])
        self._broadcast_payload([0x10, 0x02])
        time.sleep(delay)
        self._broadcast_payload([0x01, 0x03, 0x00])
        self._broadcast_payload(_parse_dump(BITMAP_LED_DUMP_1))
        self._broadcast_payload(_parse_dump(BITMAP_LED_DUMP_2))
        time.sleep(delay)
        self._broadcast_payload([0x01, 0x05, 0x00])
        self.handshake_done = True
        self.packet_counter = 1
        self.prev_led_data = bytes(LED_BYTE_COUNT)

    def send_led_rgba(self, rgba: bytearray, throttle_ms: float = LED_SEND_INTERVAL_MS) -> None:
        if not self.handshake_done or self._fail_count > 20:
            return
        now = time.monotonic() * 1000.0
        if now - self._last_send < throttle_ms:
            return
        self._last_send = now
        new_led = rgba_buffer_to_led_bytes(rgba)
        messages, self.packet_counter = _build_data_change_messages(
            bytes(new_led), self.prev_led_data, self.packet_counter
        )
        self.prev_led_data = bytes(new_led)
        with self._lock:
            try:
                for msg in messages:
                    for dev in self.device_indices:
                        self._send_sysex_packet(build_block_sysex(dev, msg))
                self._fail_count = 0
            except Exception as exc:
                self._fail_count += 1
                logging.debug("Roliblock LED send failed: %s", exc)


class RoliblockMirrorController:
    def __init__(self) -> None:
        self.enabled = False
        self.pad_a_name: Optional[str] = None
        self.pad_b_name: Optional[str] = None
        self.mode = "off"
        self.bound_element_id: Optional[str] = None
        self.device_indices: List[int] = [DEFAULT_DEVICE_INDEX]
        self._pad_a: Optional[RoliblockPadSession] = None
        self._pad_b: Optional[RoliblockPadSession] = None
        self._axis_cache: dict = {}
        self._warned_no_mido = False

    def set_config(
        self,
        enabled: bool,
        pad_a: Optional[str],
        pad_b: Optional[str],
        mode: str,
        bound_element_id: Optional[str],
        device_ids_str: Optional[str] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.pad_a_name = pad_a or None
        self.pad_b_name = pad_b or None
        self.mode = mode if mode in ("xy", "xyz_split") else "off"
        self.bound_element_id = bound_element_id
        self.device_indices = parse_device_id_list(device_ids_str)
        if self._pad_a and (
            not self.pad_a_name
            or self._pad_a.port_name != self.pad_a_name
            or tuple(self._pad_a.device_indices) != tuple(self.device_indices)
        ):
            self._pad_a.close()
            self._pad_a = None
        if self._pad_b and (
            not self.pad_b_name
            or self._pad_b.port_name != self.pad_b_name
            or tuple(self._pad_b.device_indices) != tuple(self.device_indices)
        ):
            self._pad_b.close()
            self._pad_b = None

    def ensure_sessions(self) -> bool:
        try:
            import mido  # noqa: F401
        except Exception:
            if not self._warned_no_mido:
                logging.warning("Roliblock mirror: mido not available")
                self._warned_no_mido = True
            return False
        if self.pad_a_name:
            need_a = (
                self._pad_a is None
                or self._pad_a.port_name != self.pad_a_name
                or tuple(self._pad_a.device_indices) != tuple(self.device_indices)
            )
            if need_a:
                if self._pad_a:
                    self._pad_a.close()
                self._pad_a = RoliblockPadSession(self.pad_a_name, self.device_indices)
                self._pad_a.do_handshake()
        if self.mode == "xyz_split" and self.pad_b_name:
            need_b = (
                self._pad_b is None
                or self._pad_b.port_name != self.pad_b_name
                or tuple(self._pad_b.device_indices) != tuple(self.device_indices)
            )
            if need_b:
                if self._pad_b:
                    self._pad_b.close()
                self._pad_b = RoliblockPadSession(self.pad_b_name, self.device_indices)
                self._pad_b.do_handshake()
        return True

    def on_cc_perf(
        self,
        element_id: str,
        axis: str,
        value: int,
        morphzone_dimensions: str,
        is_z_enabled: bool,
        *,
        is_x_enabled: bool = True,
        is_y_enabled: bool = True,
        is_z_axis_enabled: bool = True,
    ) -> None:
        if not self.enabled or self.mode == "off":
            return
        if not self.bound_element_id or element_id != self.bound_element_id:
            return
        if not self.pad_a_name:
            return
        if not self.ensure_sessions():
            return

        ax = axis.upper().strip()
        t = self._axis_cache.setdefault(element_id, {"x": 0.5, "y": 0.5, "z": 0.5})
        v = max(0.0, min(1.0, value / 127.0))
        if ax == "X":
            t["x"] = v
        elif ax == "Y":
            t["y"] = v
        elif ax == "Z":
            t["z"] = v

        x, y, z = t["x"], t["y"], t["z"]
        label_full = morph_axis_label(is_x_enabled, is_y_enabled, is_z_axis_enabled)

        if self._pad_a:
            buf = _make_rgba_buffer()
            _fill_black(buf)
            _draw_axis_banner(buf, label_full, 0)
            cx = x * (GRID_COLS - 1)
            span = max(1, GRID_ROWS - 1 - LED_VIZ_ROW0)
            cy = float(LED_VIZ_ROW0) + (1.0 - y) * float(span)
            _draw_soft_dot(buf, cx, cy, 2.2, 0, 240, 255, LED_VIZ_ROW0, GRID_ROWS - 1)
            self._pad_a.send_led_rgba(buf)

        if self.mode == "xyz_split" and morphzone_dimensions.endswith("Three") and is_z_enabled and self._pad_b:
            buf2 = _make_rgba_buffer()
            _fill_black(buf2)
            _draw_axis_banner(buf2, "Z", 0)
            col = int(GRID_COLS // 2)
            span_b = max(1, GRID_ROWS - 1 - LED_VIZ_ROW0)
            zrow = LED_VIZ_ROW0 + int(round((1.0 - z) * float(span_b)))
            zrow = max(LED_VIZ_ROW0, min(GRID_ROWS - 1, zrow))
            for row in range(LED_VIZ_ROW0, GRID_ROWS):
                dist = abs(row - zrow)
                if dist <= 1:
                    _set_pixel_rgba(buf2, col, row, 255, 180, 40, 255)
                    if col > 0:
                        _set_pixel_rgba(buf2, col - 1, row, 200, 120, 20, 255)
                    if col < GRID_COLS - 1:
                        _set_pixel_rgba(buf2, col + 1, row, 200, 120, 20, 255)
            self._pad_b.send_led_rgba(buf2)


def default_mirror() -> RoliblockMirrorController:
    return RoliblockMirrorController()

