TouchOSC export (MoveMusicSaveEditor)
======================================

The editor writes TWO JSON files when you export:

1) YOURNAME.json
   Schema: movemusic.touchosc.blueprint.v1
   This is a build sheet: each control lists OSC address and integer arguments
   that match the MoveMusic OSC bridge (/mmc/midi/cc and /mmc/midi/note with
   three ints each). Hexler TouchOSC Editor does NOT import this file as a
   layout. Open it in a text editor and copy settings into TouchOSC controls,
   or use it as documentation.

2) YOURNAME_touchosc_mk1_gen.json
   Input for the community tool "touchosc-generator" by Martin Wittmann:
   https://github.com/martinwittmann/touchosc-generator
   Install Python + Jinja2, then run:
     python touchosc.py YOURNAME_touchosc_mk1_gen.json
   That produces a .touchosc file for TouchOSC Mk1-style workflows.
   Default faders use a single OSC address; MoveMusic expects three integers.
   You may need TouchOSC scripting or manual message setup for full parity.
   The "Bindings" tab lists each mapping as text.

After export, use "Open last export folder" in the MIDI Overview window to
jump to the files in Explorer.

01-minimal-touchosc-generator.json in this folder is a tiny valid example
for touchosc-generator (label only), unrelated to MoveMusic mappings.
