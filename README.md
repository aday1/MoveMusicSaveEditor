# MMC Editor — MoveMusic Save File Editor

A desktop tool for editing [MoveMusic](https://movemusic.com/) `.mmc` save files outside of VR.

MoveMusic is a VR music creation app by **Tim Arterbury** that lets you build MIDI instruments in virtual reality — HitZones (drums/pads), MorphZones (XY/XYZ controllers), TextLabels, and Groups arranged in 3D space.

**MMC Editor** was developed by **aday** so he could edit save files and deploy 3D scenes before putting on the headset. Instead of fumbling with VR controllers to nudge elements around, assign MIDI CC, you get a full desktop editor with multi-select, undo/redo, auto-layout, and 3D export.

---

## Screenshots & Demos

| Media | What it shows |
|---|---|
| <img src="test.gif" alt="MMC Editor Animated Preview" width="640" /> | Quick look at viewport interaction and editor workflow. |
| <video src="test.mp4" controls preload="metadata" width="640">Your viewer does not support embedded video. Open <a href="test.mp4">test.mp4</a> directly.</video> | Full demo clip with longer interaction flow. |

Tip: If your markdown viewer does not render embedded video, open `test.mp4` directly.

---

## Features

- **Full .mmc round-trip** — Parse and re-serialize UE4 GVAS save files byte-perfectly
- **3D viewport** — Orbit/pan/zoom scene view with OpenGL rendering (single + quad view)
- **Multi-select** — Shift+click, Ctrl+click, rubber-band marquee selection
- **Batch delete** — Select All (Ctrl+A) or multi-select then Delete removes all selected in one undo step
- **Property editing** — All HitZone, MorphZone, TextLabel, and GroupIE properties with undo/redo
- **MIDI mapping** — Edit note mappings, CC mappings, channels, velocity curves
- **MIDI overview test send** — Use the MIDI Overview table to pick constrained MIDI values from dropdowns and fire a test note/CC to a real MIDI output
- **Performance Test tab** — Test HitZones and MorphZones directly from desktop with click/drag controls, transport override (OSC/MIDI/both), and live sent-message log
- **TouchOSC export** — Generate a multi-page TouchOSC layout blueprint JSON for notes/drums, CC controls, and XY MorphZones with OSC address bindings
- **MIDI nudge** — Alt+Up/Down to increment/decrement CC values on selected elements
- **Auto-layout** — Arrange selected elements in Row, Grid, or Circle formations
- **Templates** — 100+ preset templates across Faders, Knobs, XY Pads, Buttons, Drum Pads, Keyboards, Mixer, and Fun Shapes
- **Template placement orientation** — Drop templates Flat (XY), Vertical (XZ), or Side (YZ) via the Templates menu
- **Auto-increment MIDI** — Templates automatically assign non-overlapping CC and note values
- **Geometry layouts** — Each template type supports Row, Circle, Grid, Arc, Diamond, Spiral, Zigzag, and Triangle arrangements
- **Fun Shapes** — Pixel-art TextLabel overlays (letters A-Z, digits 0-9, symbols, plus real-world objects like House, Tree, Car, Piano) with multi-color definitions
- **3D gizmo** — Translate, resize, and rotate elements with axis handles and ring gizmos; hover highlights and cursor feedback
- **Group transform** — Resize and rotate multiple selected elements together
- **Keyboard templates** — Full MIDI keyboards in 1, 2, 3, 5, and 10 octave sizes with Row, Circle, and Triangle layouts
- **Mass editing** — Select multiple elements in the tree to batch-change properties
- **Grid snapping** — Snap-to-grid during drag (toggle with G key)
- **3D export** — Wavefront OBJ and glTF Binary (.glb) with optional orbit camera for AR overlays
- **Fancy Futuristic UI** — Dark

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

- **Faders** — 8/16 fader layouts (Row, Circle, Grid, Arc, Diamond, Spiral, Zigzag, Triangle)
- **Knobs** — 8/16 knob layouts with the same geometry options
- **XY Pads** — 4/8 XY pad layouts
- **Buttons** — 8/16 button layouts
- **Drum Pads** — 8/16 drum pad layouts
- **Keyboards** — 1, 2, 3, 5, and 10 octave keyboards in Row, Circle, and Triangle
- **Mixer** — 8-channel mixer strip (Faders + Knobs)
- **Bitwig** — Performance grid with macros, scenes, and XY pads
- **Reaper** — Mixer + transport layout for desktop DAW control
- **Resolume** — Clip/layer performance layout for VJ triggering
- **Grooveboxes** — MC-303 and MC-505 inspired drum/synth control banks
- **Sugarbytes** — DrumComputer style drum lanes + macro controls
- **iOS / AUM** — Mixer, send, and XY-style controls for TouchOSC/AUM setups
- **Ruismaker** — Noir-inspired drum pad + macro control layout
- **Renoise** — Pattern and transport-oriented tracker mappings
- **Reaktor** — Ensemble macro + XY style control layout
- **Hardware Controllers** — M-Audio CODE49, Behringer X-Touch, and Novation X-Station style presets
- **Fun Shapes** — Pixel-art overlays built from TextLabels
  - Letters A-Z, Digits 0-9
  - Symbols: &, @, #, !, ?
  - Objects: House, Tree, Car, Star, Heart, Smiley, Piano, Guitar, Rocket, Robot, Crown, Diamond, Anchor, Umbrella
  - Multi-color per shape (e.g., red roof, orange walls, blue door on House)
- **Debug** — Kitchen sink template for testing

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Open file |
| `Ctrl+S` | Save |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+A` | Select all elements |
| `Ctrl+D` | Duplicate selection |
| `Delete` | Delete selection (single or batch) |
| `Ctrl+L` | Auto-layout: Row |
| `G` | Toggle grid snap |
| `Home` | Fit all in view |
| `Escape` | Deselect all |
| `Alt+Up` / `Alt+Down` | Nudge MIDI CC +1 / -1 |
| `Shift+Click` | Add/remove from selection |
| `Right-drag` | Orbit camera |
| `Middle-drag` | Pan |
| `Scroll` | Zoom |
| Left-drag gizmo cube | Resize on axis |
| Left-drag gizmo ring | Rotate on axis |

## File Structure

```
MoveMusicSaveEditor/
├── editor.py              # Main PyQt6 application, undo commands, template wiring
├── main.py                # Main app entry point
├── launch_editor.bat      # Windows launcher that pins working directory
├── model.py               # Domain model (HitZone, MorphZone, TextLabel, GroupIE)
├── gvas.py                # UE4 GVAS binary format parser/writer
├── viewport3d.py          # OpenGL 3D viewport with gizmos, multi-select & overlays
├── template_generator.py  # 100+ preset layout generators with geometry & pixel art
├── export3d.py            # OBJ + glTF/GLB export
├── export_touchosc.py     # TouchOSC layout blueprint export
└── theme.py               # Cyberpunk UI stylesheet
```

## License

MoveMusic is created by Tim Arterbury — https://movemusic.com/
The save editor was a weekend vibe by https://aday.net.au
