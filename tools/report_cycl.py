#!/usr/bin/env python3
"""Report which rooms have CYCL data after extraction."""
import json
from pathlib import Path

rooms = sorted(Path('data/scumm_extracted/rooms').iterdir())
any_found = False
for rd in rooms:
    mj = rd / 'metadata.json'
    if not mj.exists():
        continue
    m = json.load(open(mj))
    cc = m.get('color_cycling', [])
    if cc:
        any_found = True
        print(f"{rd.name}: {len(cc)} cycle(s)")
        for c in cc:
            n = c['end'] - c['start'] + 1
            print(f"  idx={c['index']:2d} delay_raw={c['delay_raw']:5d} fps={c['frames_per_step']:3d} "
                  f"flags={c['flags']:04x} pc=[{c['start']:3d}..{c['end']:3d}] ({n} colors)")
if not any_found:
    print("No rooms have color cycling data")
