// PERFORMANCE TEST MODE - Desktop MIDI/OSC Testing Without VR

## How to Access Performance Mode

1. **Launch the editor:** Run `python main.py`
2. **Select an element** from the tree view (left panel)
3. **Click the "Performance Test" tab** at the bottom right of the properties panel
   - You'll see interactive controls for the selected element

## Testing Different Element Types

### HitZone (Drum Pads, Buttons, Piano Keys)
- **Display:** Green clickable button showing element name and MIDI note info
- **Test:** Click the button to send Note On (press) / Note Off (release)
- **MIDI Output:** Note On/Off messages to configured MIDI output or OSC

### MorphZone (Faders, Knobs, XY Pads)

#### Individual Axis Sliders
- **Display:** Horizontal sliders for each enabled axis (X, Y, Z)
- **Test:** Drag slider left/right to send CC values 0-127
- **MIDI Output:** CC messages to configured MIDI output or OSC

#### XY Pad (when both X and Y axes enabled)
- **Display:** Dark 2D square with crosshair
- **Test:** Click or drag anywhere in the pad
- **MIDI Output:** Real-time X and Y CC values (one per axis)

## Configuring MIDI Output

1. **Open MIDI Overview:** File → MIDI Configuration (or Ctrl+M)
2. **Select output port:** Choose your MIDI device or OSC bridge
3. **Choose transport mode:**
   - **OSC Only**: Sends OSC bridge format (/mmc/midi/cc, /mmc/midi/note)
   - **MIDI Only**: Direct MIDI device output
   - **Both**: Sends to both OSC and MIDI simultaneously

## Desktop Workflow (No VR Headset Needed)

**Typical Testing Loop:**
1. Add a template (Templates menu)
2. Select an element in the tree
3. Switch to "Performance Test" tab
4. Click/drag controls to test MIDI output
5. Watch the "Test MIDI" status at bottom to verify messages sent
6. Adjust element mappings if needed
7. Lock in final positions/mappings
8. Repeat for all elements

## Tips

- **Quick Selection:** Use Ctrl+Click in the 3D viewport to select elements
- **Test Output Verification:** Check your MIDI monitor or DAW to confirm signals arriving
- **Multiple Elements:** Select multiple elements in the tree (Shift+Click) to test controls together
- **OSC Testing:** Use Open Sound Control (OSC) monitors like Protokol or TouchOSC editor to verify messages
- **Vr Integration:** Once desktop testing is complete, put on VR headset and the same controls will work in 3D space

## Keyboard Shortcuts for Viewport MIDI

Even in Properties panel, you can use these shortcuts when the 3D viewport has focus:
- **Ctrl+Shift+Down**: Send Note Off to selected element
- **Ctrl+Shift+Up**: Send Note On to selected element
- **Alt+Down**: Decrease CC value by 1
- **Alt+Up**: Increase CC value by 1

## Finding Issues

If MIDI isn't sending:
1. Check "Test MIDI" status text (bottom of window)
2. Verify MIDI port is selected: File → MIDI Configuration
3. Ensure transport mode matches your hardware
4. For OSC: confirm bridge host/port and namespace are correct
5. Check element has valid MIDI mappings (look at Properties tab)
