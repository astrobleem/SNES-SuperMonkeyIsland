#!/usr/bin/env python3
"""Sanity-check export_anim_tables against cost_002 (flame) and cost_058 (Guybrush)."""
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm_costume_decoder import parse_costume, export_anim_tables, NUM_LIMBS


def summarize(path):
    data = path.read_bytes()
    c = parse_costume(data)
    cmds, dispatch = export_anim_tables(c)
    print(f'\n=== {path.name} ===')
    print(f'  numAnim={c.num_anim}  fmt=0x{c.format:02x}')
    print(f'  anim_cmds: {len(cmds)} bytes (first 32: {cmds[:32].hex(" ")})')
    print(f'  dispatch: {len(dispatch)} bytes')
    # Walk dispatch to prove re-parsing matches the parser
    num_anim = c.num_anim
    offsets_size = num_anim * 2
    for anim_id in range(num_anim):
        offs = struct.unpack_from('<H', dispatch, anim_id * 2)[0]
        if offs == 0:
            continue
        mask = struct.unpack_from('<H', dispatch, offs)[0]
        pos = offs + 2
        limb_seqs = []
        for limb in range(NUM_LIMBS):
            if not (mask & (0x8000 >> limb)):
                continue
            j = struct.unpack_from('<H', dispatch, pos)[0]
            pos += 2
            if j == 0xFFFF:
                limb_seqs.append(f'limb{limb}=DIS')
                continue
            extra = dispatch[pos]
            pos += 1
            cmd_len = extra & 0x7F
            looping = bool(extra & 0x80)
            end = j + cmd_len
            stream = cmds[j:end + 1]
            loop_str = ' LOOP' if looping else ''
            limb_seqs.append(f'limb{limb}=[{j}..{end}{loop_str}] "{stream.hex(" ")}"')
        parser = c.animations[anim_id]
        parser_str = []
        for limb in range(NUM_LIMBS):
            if limb in parser.limb_cmds:
                lc = parser.limb_cmds[limb]
                if lc is None:
                    parser_str.append(f'limb{limb}=DIS')
                else:
                    parser_str.append(f'limb{limb}=[{lc[0]}..{lc[1]}{" LOOP" if lc[2] else ""}]')
        if limb_seqs:
            print(f'  anim{anim_id}: {"  ".join(limb_seqs)}')
            # Quick parity check (ignoring exact strings)
            expected = sorted(parser.limb_cmds.keys())
            actual = sorted(limb for limb in range(NUM_LIMBS)
                            if (mask & (0x8000 >> limb)))
            assert expected == actual, (
                f'mask mismatch for anim {anim_id}: '
                f'expected {expected} got {actual}')


summarize(Path('data/scumm_extracted/rooms/room_038_lookout/costumes/cost_002.bin'))
summarize(Path('data/scumm_extracted/rooms/room_038_lookout/costumes/cost_000.bin'))
