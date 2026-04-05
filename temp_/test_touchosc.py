import os, json, sys, shutil

from model import (Project, Workspace, HitZone, MorphZone, TextLabel,
                   MidiNoteMapping, MidiCCMapping, Transform, Vec3, Color)
from export_touchosc import export_touchosc_layout

p = Project()
p.project_name = "TouchOSC Test"
ws = Workspace(unique_id="ws1", display_name="Main", enabled=True, element_ids=[])
p.workspaces = [ws]

hz = HitZone(
    unique_id="hz1", display_name="Kick",
    transform=Transform(translation=Vec3(0, 0, 0)),
    midi_note_mappings=[MidiNoteMapping(channel=10, note=36, velocity=1.0)],
    midi_cc_mappings=[MidiCCMapping(channel=10, control=69, value=127)],
    midi_message_type="EMidiMessageType::Note",
)
hz2 = HitZone(
    unique_id="hz2", display_name="Volume",
    transform=Transform(translation=Vec3(30, 0, 0)),
    midi_note_mappings=[],
    midi_cc_mappings=[MidiCCMapping(channel=1, control=7, value=64)],
    midi_message_type="EMidiMessageType::CC",
)
mz = MorphZone(
    unique_id="mz1", display_name="Filter XY",
    transform=Transform(translation=Vec3(60, 0, 0)),
)
tl = TextLabel(unique_id="tl1", display_name="My Label",
               transform=Transform(translation=Vec3(90, 0, 0)))

p.elements = [hz, hz2, mz, tl]
ws.element_ids = ["hz1", "hz2", "mz1", "tl1"]

os.makedirs("temp_", exist_ok=True)
try:
    summary = export_touchosc_layout(
        p, "temp_/test_export.json",
        osc_host="127.0.0.1", osc_port=57121, osc_namespace="/mmc",
    )
    print("=== EXPORT SUCCESS ===")
    for k, v in summary.items():
        print("  %s: %s" % (k, v))

    bp = summary["blueprint_path"]
    mk1 = summary["mk1_generator_path"]

    assert os.path.isfile(bp), "Blueprint file missing"
    assert os.path.isfile(mk1), "MK1 generator file missing"

    with open(bp) as f:
        blueprint = json.load(f)

    print("\n=== BLUEPRINT ===")
    print("Schema:", blueprint.get("schema"))
    t = blueprint["touchosc"]
    print("Host:", t["target"]["host"])
    print("Port:", t["target"]["port"])
    print("CC addr:", t["target"]["addresses"]["cc"])
    print("Note addr:", t["target"]["addresses"]["note"])

    for pg in t["pages"]:
        ctrls = pg["controls"]
        print("Page '%s': %d controls" % (pg["name"], len(ctrls)))
        for ctrl in ctrls:
            label = ctrl["label"]
            ctype = ctrl["type"]
            osc = ctrl.get("osc", {})
            addrs = []
            for key, val in osc.items():
                if val:
                    addrs.append("%s -> %s %s" % (key, val["address"], val["args"]))
            print("  %s (%s): %s" % (label, ctype, "; ".join(addrs) if addrs else "no osc"))

    with open(mk1) as f:
        mk1_data = json.load(f)

    print("\n=== MK1 GENERATOR ===")
    print("type:", mk1_data.get("type"))
    print("version:", mk1_data.get("version"))
    print("tabpages:", len(mk1_data.get("tabpages", [])))
    for tp in mk1_data["tabpages"]:
        print("  %s: %d components" % (tp["name"], len(tp.get("components", []))))
    mm_lines = mk1_data.get("data", {}).get("mm_lines", [])
    print("mm_lines (%d):" % len(mm_lines))
    for line in mm_lines:
        print("  " + line)

    raw = json.dumps(blueprint)
    raw2 = json.dumps(mk1_data)
    print("\nNull in blueprint:", raw.count("null"))
    print("Null in mk1:", raw2.count("null"))

    print("\n=== ALL PASSED ===")
except Exception as e:
    print("FAILED: %s: %s" % (type(e).__name__, e))
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    shutil.rmtree("temp_", ignore_errors=True)
