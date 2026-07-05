#!/usr/bin/env python3
"""Ad-hoc probe: dump cost_033 anim command details for the chore-interpreter plan.
Prints every anim's limb_mask + limb_cmds, then shows the raw command byte stream
the interpreter will need to execute."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm_costume_decoder import parse_costume

cost_path = Path('data/scumm_extracted/costumes/cost_033_room025.bin')
data = cost_path.read_bytes()
c = parse_costume(data)

print(f'costume: {cost_path}')
print(f'numAnim={c.num_anim}, format=0x{c.format:02x}, mirror={c.mirror}')
print(f'anim_cmds table len={len(c.anim_cmds)}')
prev = c.anim_cmds[:96]
hex_str = prev.hex(' ')
print(f'First 96 bytes of anim_cmds: {hex_str}')
print()

print('ALL animations with non-empty limb_cmds:')
for a in c.animations:
    if a is None:
        continue
    if not a.limb_cmds:
        continue
    parts = []
    for limb, lc in sorted(a.limb_cmds.items()):
        if lc is None:
            parts.append(f'limb{limb}=DISABLED')
        else:
            start, end, loop = lc
            loop_str = ' LOOP' if loop else ''
            parts.append(f'limb{limb}=[{start}..{end}{loop_str}]')
    print(f'  anim{a.anim_id}: mask=0x{a.limb_mask:04x} {" ".join(parts)}')

print()
print('--- Per-anim: raw command bytes per limb ---')
for anim_id in sorted({a.anim_id for a in c.animations if a and a.limb_cmds}):
    a = c.animations[anim_id]
    print(f'anim {anim_id}:')
    for limb, lc in sorted(a.limb_cmds.items()):
        if lc is None:
            continue
        start, end, loop = lc
        # The command bytes are indices [start..end] into anim_cmds
        if start < len(c.anim_cmds):
            stream = c.anim_cmds[start:end + 1]
            print(f'  limb{limb} [{start}..{end}] loop={loop}: {stream.hex(" ")}')
