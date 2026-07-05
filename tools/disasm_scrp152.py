#!/usr/bin/env python3
"""Disassemble scrp_152 with offsets so we can see what opcode is at pc=0x72."""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.opcodes_v5 import BASE_OPCODES, OPCODE_MAP

data = Path("data/scumm_extracted/scripts/scrp_152_room010.bin").read_bytes()
stream = BytesIO(data)

while stream.tell() < len(data):
    pos = stream.tell()
    op = stream.read(1)[0]
    name = OPCODE_MAP[op]
    start = pos
    try:
        decoder = BASE_OPCODES[name]
        decoder(op, stream)
    except Exception as e:
        print(f"{pos:04X}  ${op:02X}  ERROR:{e}")
        stream.seek(pos + 1)
        continue
    end = stream.tell()
    raw = data[start:end].hex()
    print(f"{pos:04X}  ${op:02X}  {name:<20} {raw}")
