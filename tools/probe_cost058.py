#!/usr/bin/env python3
"""Check Guybrush's costume (cost_058_room038) for the specific anim_ids
observed being set in the MI1 intro scripts: 244-250, plus the low-index
stand/walk ids 6/7/8 seen in scrp_121."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm_costume_decoder import parse_costume

data = Path('data/scumm_extracted/costumes/cost_058_room038.bin').read_bytes()
c = parse_costume(data)
print(f'cost_058: numAnim={c.num_anim}, format=0x{c.format:02x}, mirror={c.mirror}')
print(f'anim_cmds table len={len(c.anim_cmds)}')
print()

checks = [244, 245, 246, 247, 248, 249, 250, 0, 1, 2, 3, 4, 5, 6, 7, 8]
for anim_id in checks:
    if anim_id >= c.num_anim:
        print(f'  anim {anim_id}: OUT OF RANGE (numAnim={c.num_anim})')
        continue
    a = c.animations[anim_id]
    if a is None or not a.limb_cmds:
        print(f'  anim {anim_id}: null/empty')
        continue
    parts = []
    for limb, lc in sorted(a.limb_cmds.items()):
        if lc is None:
            parts.append(f'limb{limb}=DIS')
        else:
            start, end, loop = lc
            loop_flag = ' LOOP' if loop else ''
            parts.append(f'limb{limb}=[{start}..{end}{loop_flag}]')
    parts_str = ' '.join(parts)
    print(f'  anim {anim_id}: mask=0x{a.limb_mask:04x} {parts_str}')

# Also show which anim_ids exist
present = sorted(a.anim_id for a in c.animations if a and a.limb_cmds)
print()
print(f'Present anim_ids in cost_058: count={len(present)}')
print(f'Ranges: first 30 = {present[:30]}')
print(f'        last 30 = {present[-30:]}')
