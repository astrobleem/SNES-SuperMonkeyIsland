#!/usr/bin/env python3
"""Dump room 10 CYCL data and hunt for script calls that could drive sparkles."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.resource import parse_data_file
from tools.scumm.chunks import iter_chunks

df = parse_data_file("data/monkeypacks/talkie/monkey.001")
room = df.rooms[10]

# All top-level chunks in the room
for sub in room.room_sub_chunks:
    print(f"top-level: {sub.tag} {len(sub.data)} bytes")
    if sub.tag in ('CYCL', 'PALS', 'CLUT'):
        print(f"  raw: {sub.data.hex()}")
