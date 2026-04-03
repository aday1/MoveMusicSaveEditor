"""TouchOSC export helpers for MoveMusic projects.

This exporter writes a JSON layout blueprint that mirrors MoveMusic element
MIDI/OSC mappings with page grouping for drums/notes, CC controls, and XY/XYZ
MorphZones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


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
    """Build CC/note addresses from a base namespace.

    Supports both base namespaces ("/mmc" -> "/mmc/midi/cc") and
    already-midi namespaces ("/mmc/midi" -> "/mmc/midi/cc").
    """
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


def export_touchosc_layout(project, file_path: str, osc_host: str = "127.0.0.1", osc_port: int = 57121, osc_namespace: str = "/mmc") -> dict:
    """Export a TouchOSC-oriented multi-page layout blueprint as JSON.

    The resulting JSON is intended as an easy-to-build TouchOSC page guide:
    each page includes positioned controls and explicit OSC address/arguments.
    """
    if not project:
        raise ValueError("No project is loaded.")

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
            vel = int(max(0, min(127, round(float(getattr(note, "velocity", 1.0)) * 127)))) if float(getattr(note, "velocity", 1.0)) <= 1.0 else int(max(0, min(127, round(float(getattr(note, "velocity", 127))))))
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
                "port": int(osc_port or 9001),
                "namespace": namespace,
                "addresses": {"cc": cc_addr, "note": note_addr},
            },
            "notes": [
                "Import this JSON in your workflow as a build sheet for TouchOSC pages.",
                "For pad controls, bind press and release messages.",
                "For faders/XY, map value 0..1 to MIDI 0..127 where value placeholders are present.",
            ],
            "pages": [{"name": p.name, "controls": p.controls} for p in pages],
        },
    }

    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    return {
        "page_count": len(pages),
        "note_controls": len(note_controls),
        "cc_controls": len(cc_controls),
        "morph_controls": len(morph_controls),
    }
