"""TouchOSC export helpers for MoveMusic projects.

Writes:
1) movemusic.touchosc.blueprint.v1 JSON (OSC args per control; use as a build sheet).
2) Companion .touchosc_mk1_gen.json for martinwittmann/touchosc-generator (produces .touchosc).

Hexler TouchOSC Editor does not open the blueprint JSON as a layout file. Use the blueprint
to configure OSC messages in the editor, or run touchosc.py on the *_mk1_gen.json file.
See TouchOSCExample/README.txt in this repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class _Page:
    name: str
    controls: list


def _norm_namespace(namespace: str) -> str:
    ns = (namespace or "/mmc").strip()
    if not ns.startswith("/"):
        ns = "/" + ns
    ns = ns.rstrip("/")
    return ns or "/mmc"


def _osc_addresses(namespace: str) -> tuple[str, str]:
    if namespace.endswith("/midi"):
        base = namespace
    else:
        base = namespace + "/midi"
    return f"{base}/cc", f"{base}/note"


def _element_workspace_map(project) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for ws in getattr(project, "workspaces", []):
        ws_name = getattr(ws, "display_name", "") or "Workspace"
        for elem_id in getattr(ws, "element_ids", []):
            mapping[elem_id] = ws_name
    return mapping


def _grid_box(index: int, cols: int, rows: int) -> dict:
    col = index % cols
    row = index // cols
    pad_x = 0.02
    pad_y = 0.04
    cell_w = (1.0 - pad_x * (cols + 1)) / max(cols, 1)
    cell_h = (1.0 - pad_y * (rows + 1)) / max(rows, 1)
    x = pad_x + col * (cell_w + pad_x)
    y = pad_y + row * (cell_h + pad_y)
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(cell_w, 4),
        "h": round(cell_h, 4),
    }


def _chunk(items: list, chunk_size: int) -> list[list]:
    if chunk_size <= 0:
        chunk_size = 1
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def _scrub_nones(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub_nones(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_scrub_nones(v) for v in obj if v is not None]
    return obj


def _json_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _scrub_nones(obj)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(safe, handle, indent=2, allow_nan=False)


def ensure_json_extension(file_path: str) -> Path:
    p = Path(file_path)
    if p.suffix.lower() != ".json":
        p = p.with_suffix(".json")
    return p


def _note_velocity_for_export(note) -> int:
    try:
        raw = float(getattr(note, "velocity", 1.0))
        if raw <= 1.0:
            return int(max(0, min(127, round(raw * 127))))
        return int(max(0, min(127, round(raw))))
    except (TypeError, ValueError):
        return 127


def build_touchosc_mk1_generator_json(project, cc_addr: str, note_addr: str) -> dict[str, Any]:
    """JSON input for github.com/martinwittmann/touchosc-generator touchosc.py."""
    ws_map = _element_workspace_map(project)
    lines: list[str] = [
        "MoveMusic bridge OSC:",
        f"  {cc_addr}  -> 3 ints: channel, cc, value 0-127",
        f"  {note_addr} -> 3 ints: channel, note, velocity 0-127",
        "",
        "Bindings:",
        "",
    ]

    fader_specs: list[dict[str, Any]] = []
    pad_idx = 0

    for elem in getattr(project, "elements", []):
        elem_type = type(elem).__name__
        name = getattr(elem, "display_name", "") or getattr(elem, "unique_id", "Element")
        uid = getattr(elem, "unique_id", "")
        workspace = ws_map.get(uid, "(none)")
        note_maps = list(getattr(elem, "midi_note_mappings", []) or [])
        cc_maps = list(getattr(elem, "midi_cc_mappings", []) or [])
        x_maps = list(getattr(elem, "x_axis_cc_mappings", []) or [])
        y_maps = list(getattr(elem, "y_axis_cc_mappings", []) or [])
        z_maps = list(getattr(elem, "z_axis_cc_mappings", []) or [])
        msg_type = getattr(elem, "midi_message_type", "")

        if x_maps or y_maps or z_maps:
            x_map = x_maps[0] if x_maps else None
            y_map = y_maps[0] if y_maps else None
            z_map = z_maps[0] if z_maps else None
            parts = [f"[{workspace}] {name} ({elem_type}) -> {cc_addr}"]
            if x_map:
                parts.append(f"  X Ch{x_map.channel} CC{x_map.control}")
            if y_map:
                parts.append(f"  Y Ch{y_map.channel} CC{y_map.control}")
            if z_map:
                parts.append(f"  Z Ch{z_map.channel} CC{z_map.control}")
            lines.append("\n".join(parts))
            for m, tag in ((x_map, "X"), (y_map, "Y"), (z_map, "Z")):
                if m:
                    fader_specs.append(
                        {
                            "type": "faderv",
                            "osc": cc_addr,
                            "x": str(2 + (pad_idx % 5) * 18) + "%",
                            "y": str(8 + (pad_idx // 5) * 11) + "%",
                            "width": "14%",
                            "height": "28%",
                            "inverted": "true",
                            "label": f"{name[:12]} {tag}",
                        }
                    )
                    pad_idx += 1
            continue

        if note_maps and msg_type != "EMidiMessageType::CC":
            note = note_maps[0]
            vel = _note_velocity_for_export(note)
            lines.append(
                f"[{workspace}] {name} note {note_addr} ch{note.channel} n{note.note} v{vel}/0"
            )
            continue

        if cc_maps:
            cc = cc_maps[0]
            lines.append(
                f"[{workspace}] {name} CC {cc_addr} ch{cc.channel} cc{cc.control}"
            )
            fader_specs.append(
                {
                    "type": "faderv",
                    "osc": cc_addr,
                    "x": str(2 + (pad_idx % 5) * 18) + "%",
                    "y": str(8 + (pad_idx // 5) * 11) + "%",
                    "width": "14%",
                    "height": "28%",
                    "inverted": "true",
                    "label": (name or "CC")[:14],
                }
            )
            pad_idx += 1

    fader_specs = fader_specs[:40]
    max_lines = 120
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [f"... ({len(lines) - max_lines + 1} more lines omitted)"]

    tabpages: list[dict[str, Any]] = [
        {
            "type": "tabpage",
            "name": "about",
            "text": "About",
            "components": [
                {
                    "type": "labelh",
                    "text": "For TouchOSC Mk1: run python touchosc.py on this JSON (touchosc-generator).",
                    "text_size": 20,
                    "x": "0%",
                    "y": "0%",
                    "width": "100%",
                    "height": "10%",
                },
                {
                    "type": "labelh",
                    "text": "TouchOSC Editor (hexler) uses .tosc layouts from the app, not this file.",
                    "text_size": 18,
                    "x": "0%",
                    "y": "10%",
                    "width": "100%",
                    "height": "8%",
                },
            ],
        },
        {
            "type": "tabpage",
            "name": "bindings",
            "text": "Bindings",
            "components": [
                {
                    "type": "repeat",
                    "count": len(lines),
                    "columns": 1,
                    "width": "94%",
                    "height": "90%",
                    "x": "3%",
                    "y": "5%",
                    "spacer_y": "0.3%",
                    "component": {
                        "type": "labelh",
                        "text": "{{data.mm_lines.@index}}",
                        "text_size": 13,
                        "width": "100%",
                        "height": "5%",
                        "outline": "true",
                    },
                }
            ],
        },
    ]

    if fader_specs:
        tabpages.append(
            {
                "type": "tabpage",
                "name": "faderv",
                "text": "Faders",
                "components": fader_specs,
            }
        )

    return {
        "type": "layout",
        "mode": 3,
        "version": 17,
        "width": 2000,
        "height": 1200,
        "orientation": "horizontal",
        "data": {"mm_lines": lines},
        "tabpages": tabpages,
    }


def export_touchosc_layout(
    project,
    file_path: str,
    osc_host: str = "127.0.0.1",
    osc_port: int = 57121,
    osc_namespace: str = "/mmc",
) -> dict:
    """Write blueprint JSON and sibling *_touchosc_mk1_gen.json."""
    if not project:
        raise ValueError("No project is loaded.")

    main_path = ensure_json_extension(file_path)
    stem = main_path.name
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    mk1_path = main_path.with_name(stem + "_touchosc_mk1_gen.json")

    namespace = _norm_namespace(osc_namespace)
    cc_addr, note_addr = _osc_addresses(namespace)
    ws_map = _element_workspace_map(project)

    note_controls = []
    cc_controls = []
    morph_controls = []

    for elem in getattr(project, "elements", []):
        elem_type = type(elem).__name__
        name = getattr(elem, "display_name", "") or getattr(elem, "unique_id", "Element")
        uid = getattr(elem, "unique_id", "")
        workspace = ws_map.get(uid, "(none)")

        note_maps = list(getattr(elem, "midi_note_mappings", []) or [])
        cc_maps = list(getattr(elem, "midi_cc_mappings", []) or [])
        x_maps = list(getattr(elem, "x_axis_cc_mappings", []) or [])
        y_maps = list(getattr(elem, "y_axis_cc_mappings", []) or [])
        z_maps = list(getattr(elem, "z_axis_cc_mappings", []) or [])

        if x_maps or y_maps or z_maps:
            x_map = x_maps[0] if x_maps else None
            y_map = y_maps[0] if y_maps else None
            z_map = z_maps[0] if z_maps else None
            morph_controls.append(
                {
                    "id": uid,
                    "label": name,
                    "type": "xy",
                    "workspace": workspace,
                    "element_type": elem_type,
                    "osc": {
                        "x": {
                            "address": cc_addr,
                            "args": [int(x_map.channel), int(x_map.control), "$x_0_127"],
                        } if x_map else None,
                        "y": {
                            "address": cc_addr,
                            "args": [int(y_map.channel), int(y_map.control), "$y_0_127"],
                        } if y_map else None,
                        "z": {
                            "address": cc_addr,
                            "args": [int(z_map.channel), int(z_map.control), "$z_0_127"],
                        } if z_map else None,
                    },
                }
            )
            continue

        msg_type = getattr(elem, "midi_message_type", "")
        if note_maps and msg_type != "EMidiMessageType::CC":
            note = note_maps[0]
            vel = _note_velocity_for_export(note)
            note_controls.append(
                {
                    "id": uid,
                    "label": name,
                    "type": "pad",
                    "workspace": workspace,
                    "element_type": elem_type,
                    "osc": {
                        "press": {
                            "address": note_addr,
                            "args": [int(note.channel), int(note.note), vel],
                        },
                        "release": {
                            "address": note_addr,
                            "args": [int(note.channel), int(note.note), 0],
                        },
                    },
                }
            )
            continue

        if cc_maps:
            cc = cc_maps[0]
            cc_controls.append(
                {
                    "id": uid,
                    "label": name,
                    "type": "fader",
                    "workspace": workspace,
                    "element_type": elem_type,
                    "default_value": int(cc.value),
                    "osc": {
                        "change": {
                            "address": cc_addr,
                            "args": [int(cc.channel), int(cc.control), "$value_0_127"],
                        }
                    },
                }
            )

    pages: list[_Page] = []

    note_chunks = _chunk(note_controls, 16)
    for idx, group in enumerate(note_chunks, start=1):
        controls = []
        for i, ctrl in enumerate(group):
            entry = dict(ctrl)
            entry["layout"] = _grid_box(i, cols=4, rows=4)
            controls.append(entry)
        pages.append(_Page(name=f"Drums Notes {idx}", controls=controls))

    cc_chunks = _chunk(cc_controls, 12)
    for idx, group in enumerate(cc_chunks, start=1):
        controls = []
        for i, ctrl in enumerate(group):
            entry = dict(ctrl)
            entry["layout"] = _grid_box(i, cols=4, rows=3)
            controls.append(entry)
        pages.append(_Page(name=f"CC Controls {idx}", controls=controls))

    morph_chunks = _chunk(morph_controls, 6)
    for idx, group in enumerate(morph_chunks, start=1):
        controls = []
        for i, ctrl in enumerate(group):
            entry = dict(ctrl)
            entry["layout"] = _grid_box(i, cols=3, rows=2)
            controls.append(entry)
        pages.append(_Page(name=f"XY Morph {idx}", controls=controls))

    if not pages:
        pages.append(_Page(name="Empty", controls=[]))

    payload = {
        "schema": "movemusic.touchosc.blueprint.v1",
        "project": {
            "name": getattr(project, "project_name", "") or "MoveMusic Project",
            "elements": len(getattr(project, "elements", [])),
        },
        "touchosc": {
            "target": {
                "host": str(osc_host or "127.0.0.1"),
                "port": int(osc_port or 57121),
                "namespace": namespace,
                "addresses": {"cc": cc_addr, "note": note_addr},
            },
            "notes": [
                "This file is a blueprint (build sheet), not a TouchOSC Editor layout import.",
                "A sibling *_touchosc_mk1_gen.json is written for martinwittmann/touchosc-generator.",
                "For pads bind press/release; for faders/XY map 0..1 to MIDI 0..127 where noted.",
            ],
            "pages": [{"name": p.name, "controls": p.controls} for p in pages],
        },
    }

    _json_write(main_path, payload)
    mk1_layout = build_touchosc_mk1_generator_json(project, cc_addr, note_addr)
    _json_write(mk1_path, mk1_layout)

    return {
        "page_count": len(pages),
        "note_controls": len(note_controls),
        "cc_controls": len(cc_controls),
        "morph_controls": len(morph_controls),
        "blueprint_path": str(main_path.resolve()),
        "mk1_generator_path": str(mk1_path.resolve()),
    }
