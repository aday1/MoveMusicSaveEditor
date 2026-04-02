# MMC Editor — MoveMusic Save File Editor

A desktop tool for editing [MoveMusic](https://movemusic.com/) `.mmc` save files outside of VR.

MoveMusic is a VR music creation app by **Tim Arterbury** that lets you build MIDI instruments in virtual reality — HitZones (drums/pads), MorphZones (XY/XYZ controllers), TextLabels, and Groups arranged in 3D space.

**MMC Editor** was developed by **aday** so he could edit save files and deploy 3D scenes before putting on the headset. Instead of fumbling with VR controllers to nudge elements around, you get a full desktop editor with multi-select, undo/redo, auto-layout, and 3D export.

---

## Features

- **Full .mmc round-trip** — Parse and re-serialize UE4 GVAS save files byte-perfectly
- **3D viewport** — Orbit/pan/zoom scene view with OpenGL rendering (single + quad view)
- **Multi-select** — Shift+click, Ctrl+click, rubber-band marquee selection
- **Property editing** — All HitZone, MorphZone, TextLabel, and GroupIE properties with undo/redo
- **MIDI mapping** — Edit note mappings, CC mappings, channels, velocity curves
- **Auto-layout** — Arrange selected elements in Row, Grid, or Circle formations
- **Templates** — Grouped template menu (Keyboard, Controllers, Drums, Utility, Debug)
- **Keyboard templates** — Full MIDI row and circle layouts with octave color coding
- **Mass editing** — Select multiple elements in the tree to batch-change properties
- **Grid snapping** — Snap-to-grid during drag (toggle with G key)
- **3D export** — Wavefront OBJ and glTF Binary (.glb) with optional orbit camera for AR overlays
- **Futuristic UI** — Dark cyberpunk theme inspired by VR interfaces and DAW channel strips

## Quick Start

```bash
pip install -r requirements.txt
cd MoveMusicSaveEditor
python main.py
```

Open any `.mmc` file (typically found in your Quest/VR device save data).

### Windows Shortcut Launch (recommended)

To ensure the editor loads templates from this repo copy, launch with:

```bat
launch_editor.bat
```

If you use a desktop shortcut, point it to `launch_editor.bat` in this folder.

## Template Menu Groups

- **Keyboard**
	- Keyboard (Full MIDI Row)
	- Keyboard (Full MIDI Circle)
	- KEYBOARD L->R (All Octaves 0-127)
	- KEYBOARD Circle (All Octaves 0-127)
- **Controllers**
	- Faders, Knobs, XY Pads, Buttons
- **Drums**
	- Drum pad layouts (8/16)
- **Utility**
	- Mixer (8 Faders + 8 Knobs)
- **Debug**
	- DEBUG: Everything Kitchen Sink

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Open file |
| `Ctrl+S` | Save |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+A` | Select all elements |
| `Ctrl+D` | Duplicate selection |
| `Delete` | Delete selection |
| `Ctrl+L` | Auto-layout: Row |
| `G` | Toggle grid snap |
| `Home` | Fit all in view |
| `Escape` | Deselect all |
| `Shift+Click` | Add/remove from selection |
| `Right-drag` | Orbit camera |
| `Middle-drag` | Pan |
| `Scroll` | Zoom |

## File Structure

```
MoveMusicSaveEditor/
├── editor.py              # Main PyQt6 application
├── main.py                # Main app entry point
├── launch_editor.bat      # Windows launcher that pins working directory
├── model.py               # Domain model (HitZone, MorphZone, TextLabel, GroupIE)
├── gvas.py                # UE4 GVAS binary format parser/writer
├── viewport3d.py          # OpenGL 3D viewport with multi-select & overlays
├── template_generator.py  # Preset layout generators
├── export3d.py            # OBJ + glTF/GLB export
└── theme.py               # Cyberpunk UI stylesheet
```

## License

Personal tool. MoveMusic is created by Tim Arterbury — https://movemusic.com/
