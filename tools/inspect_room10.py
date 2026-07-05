#!/usr/bin/env python3
import json
m = json.load(open("data/scumm_extracted/rooms/room_010_logo/metadata.json"))
print("Room 10 header:", m.get("header"))
print("Color cycling:", m.get("color_cycling", []))
print("EPAL present:", m.get("has_epal", False), "size:", m.get("epal_size", 0))
print("Transparent color:", m.get("transparent_color", "none"))
print("Objects:", len(m.get("objects", [])))
for o in m.get("objects", []):
    oid = o.get("obj_id")
    name = o.get("name", "")
    x = o.get("x", 0)
    y = o.get("y", 0)
    w = o.get("width", 0)
    h = o.get("height", 0)
    states = o.get("initial_state", 1)
    print(f"  id={oid} name='{name}' pos=({x},{y}) sz={w}x{h} state={states}")
