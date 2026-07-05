#!/usr/bin/env python3
"""Disassemble room 10 local scripts to find sparkle animation driver."""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.opcodes_v5 import BASE_OPCODES, OPCODE_MAP

for path in sorted(Path("data/scumm_extracted/rooms/room_010_logo/scripts").glob("*.bin")):
    data = path.read_bytes()
    # Only look for ones that mention objects 110-114 in raw form
    tag = False
    for i in range(len(data) - 1):
        lo = data[i]
        hi = data[i+1] if i+1 < len(data) else 0
        obj = lo | (hi << 8)
        if 110 <= obj <= 114:
            tag = True
            break
    if not tag:
        continue
    print(f"=== {path.name} ({len(data)} bytes) ===")
    stream = BytesIO(data)
    count = 0
    while stream.tell() < len(data) and count < 80:
        pos = stream.tell()
        op = stream.read(1)[0]
        name = OPCODE_MAP[op]
        try:
            BASE_OPCODES[name](op, stream)
            end = stream.tell()
            raw = data[pos:end].hex()
            print(f"  {pos:04X}  ${op:02X}  {name:<20} {raw}")
        except Exception as e:
            print(f"  {pos:04X}  ${op:02X}  ERROR:{e}")
            stream.seek(pos+1)
        count += 1
    print()
