#!/usr/bin/env python3
"""Scan intro scripts for palette-manipulation opcodes (roomOps subops 4/F/10)."""
import sys
from pathlib import Path

scripts = [
    "data/scumm_extracted/scripts/scrp_152_room010.bin",
    "data/scumm_extracted/rooms/room_010_logo/scripts/encd.bin",
    "data/scumm_extracted/rooms/room_010_logo/scripts/excd.bin",
]
# Include all LSCRs
for p in Path("data/scumm_extracted/rooms/room_010_logo/scripts").glob("lscr_*.bin"):
    scripts.append(str(p))

ROOMOPS = {0x33, 0x73, 0xB3, 0xF3}
# Actually any op with (op & 0x1F) == 0x13 is roomOps
for name in scripts:
    p = Path(name)
    if not p.exists():
        continue
    d = p.read_bytes()
    hits = []
    # Crude scan: look for byte patterns that LOOK like roomOps
    # A roomOps starts with op byte having (op&0x1F)==0x13.
    # Scan for those and check the next byte for palette subops (4, 0x0F, 0x10)
    for i in range(len(d) - 1):
        op = d[i]
        if (op & 0x1F) == 0x13:
            sub = d[i+1]
            sub_key = sub & 0x1F
            if sub_key in (0x04, 0x0F, 0x10):
                hits.append((i, op, sub, sub_key))
    if hits:
        print(f"{p.name}: {len(hits)} palette roomOps")
        for i, op, sub, key in hits[:10]:
            label = {0x04: "setPalColor", 0x0F: "palManipulate", 0x10: "colorCycleDelay"}[key]
            print(f"  @{i:04x}  op=${op:02x} sub=${sub:02x} ({label})")
