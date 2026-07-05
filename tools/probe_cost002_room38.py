#!/usr/bin/env python3
"""Deep-dive: cost_002 from room 38 — 5 large pics. Suspected campfire costume."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm_costume_decoder import parse_costume

data = Path('data/scumm_extracted/rooms/room_038_lookout/costumes/cost_002.bin').read_bytes()
c = parse_costume(data)
print(f'cost_002 room 38: numAnim={c.num_anim}, fmt=0x{c.format:02x}')
print(f'anim_cmds len={len(c.anim_cmds)}')
print(f'anim_cmds first 48: {c.anim_cmds[:48].hex(" ")}')
print(f'palette: {c.palette.hex(" ")}')
print()
print('All anims:')
for a in c.animations:
    if a is None:
        continue
    parts = []
    for limb, lc in sorted(a.limb_cmds.items()):
        if lc is None:
            parts.append(f'limb{limb}=DIS')
        else:
            start, end, loop = lc
            loop_str = ' LOOP' if loop else ''
            parts.append(f'limb{limb}=[{start}..{end}{loop_str}]')
    print(f'  anim{a.anim_id}: mask=0x{a.limb_mask:04x} {" ".join(parts) if parts else "(empty)"}')
print()
print('Per-anim raw command streams:')
for a in c.animations:
    if a is None or not a.limb_cmds:
        continue
    print(f'  anim {a.anim_id}:')
    for limb, lc in sorted(a.limb_cmds.items()):
        if lc is None:
            continue
        start, end, loop = lc
        stream = c.anim_cmds[start:end + 1]
        print(f'    limb{limb} [{start}..{end}] loop={loop}: {stream.hex(" ")}')

# Show pic details for this costume
print()
print('limb0 picture details:')
for i, pic in enumerate(c.limb_pictures[0]):
    if pic is None:
        print(f'  pic{i}: NULL')
    else:
        print(f'  pic{i}: {pic.width}x{pic.height} rel=({pic.rel_x},{pic.rel_y}) rle={pic.rle_size}B')
