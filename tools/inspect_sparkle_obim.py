#!/usr/bin/env python3
"""Dump OBIM structure for sparkle objects 110-114 in room 10 of MI1 talkie.

Goal: confirm whether each sparkle object really has only one image state
(a solid magenta rectangle) or multiple states with actual star shapes.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.scumm.chunks import iter_chunks
from tools.scumm.resource import parse_data_file
from tools.scumm.smap import decode_smap

DATA_DIR = Path("data/monkeypacks/talkie")
res_file = DATA_DIR / "monkey.001"
print(f"Reading: {res_file}")

df = parse_data_file(str(res_file))
room = df.rooms.get(10)
if room is None:
    print("Available rooms:", list(df.rooms.keys())[:20])
    raise SystemExit("Room 10 not found")
obims = room.get_all_room_sub('OBIM')
print(f"Room 10: {len(obims)} OBIM chunks")

for obim in obims:
    subs = list(iter_chunks(obim.data))
    imhd = next((s for s in subs if s.tag == 'IMHD'), None)
    if imhd is None:
        continue
    obj_id = struct.unpack_from('<H', imhd.data, 0)[0]
    num_imnn = struct.unpack_from('<H', imhd.data, 2)[0]
    num_zplanes = struct.unpack_from('<H', imhd.data, 4)[0]
    flags = struct.unpack_from('<H', imhd.data, 6)[0]
    imhd_x = struct.unpack_from('<h', imhd.data, 8)[0]
    imhd_y = struct.unpack_from('<h', imhd.data, 10)[0]
    imhd_w = struct.unpack_from('<H', imhd.data, 12)[0]
    imhd_h = struct.unpack_from('<H', imhd.data, 14)[0]
    if obj_id not in (110, 111, 112, 113, 114):
        continue
    print(f"\n=== obj {obj_id}: IMHD num_imnn={num_imnn} zplanes={num_zplanes} "
          f"flags={flags:#x} pos=({imhd_x},{imhd_y}) size={imhd_w}x{imhd_h} ===")
    # List all sub-chunks
    for s in subs:
        print(f"  {s.tag} {len(s.data)} bytes")
        if s.tag.startswith('IM') and s.tag != 'IMHD':
            im_subs = list(iter_chunks(s.data))
            for imsub in im_subs:
                print(f"    {imsub.tag} {len(imsub.data)} bytes")
                if imsub.tag.startswith('ZP'):
                    print(f"    ZP raw: {imsub.data.hex()}")
            smap = next((x for x in im_subs if x.tag == 'SMAP'), None)
            if smap and imhd_w and imhd_h:
                pixels = decode_smap(smap.data, imhd_w, imhd_h)
                uniq = set()
                for row in pixels:
                    for p in row:
                        uniq.add(p)
                print(f"    SMAP colors = {sorted(uniq)}")
