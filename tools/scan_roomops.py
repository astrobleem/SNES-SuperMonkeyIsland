#!/usr/bin/env python3
"""Scan scripts for roomOps ($33/$73/$B3/$F3) calls and their sub-opcodes."""
from pathlib import Path

ROOMOPS_OPCODES = {0x33, 0x73, 0xB3, 0xF3}
SUBOP_NAMES = {
    0x01: "scroll", 0x02: "roomColor", 0x03: "setScreen",
    0x04: "setPalColor", 0x05: "shakeOn", 0x06: "shakeOff",
    0x07: "roomScale", 0x08: "roomIntensity", 0x09: "saveGame",
    0x0A: "screenEffect", 0x0B: "rgbRoomIntensity", 0x0C: "roomShadow",
    0x0D: "saveString", 0x0E: "loadString", 0x0F: "palManipulate",
    0x10: "colorCycleDelay",
}

for name in ["scrp_152_room010.bin"]:
    p = Path("data/scumm_extracted/scripts") / name
    d = p.read_bytes()
    print(f"{name}: {len(d)} bytes")
    hits = []
    for i in range(len(d) - 1):
        if d[i] in ROOMOPS_OPCODES:
            sub = d[i+1]
            sub_key = sub & 0x1F
            if sub_key in SUBOP_NAMES:
                hits.append((i, d[i], sub, sub_key))
    for i, op, sub, key in hits:
        print(f"  @{i:04x}  op=${op:02x} sub=${sub:02x} ({SUBOP_NAMES[key]})")
