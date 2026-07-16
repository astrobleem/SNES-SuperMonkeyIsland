#!/usr/bin/env python3
"""Targeted pass: generate object ZP01 foreground masks for every already-
extracted room, without re-running the full extractor.

The object extractor historically dropped OBIM z-plane strips; this backfills
obj_<id>[_name]_zp01.png into each room's objects/ dir so the SNES converter
can route foreground object pixels onto the BG2 priority layer (walk-behind /
title-logo masking). Idempotent.

    python tools/extract_obj_zplanes.py \
        --data data/monkeypacks/talkie/monkey.001 \
        --extracted data/scumm_extracted
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm.resource import parse_data_file
from scumm.object_gfx import extract_object_zplanes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/monkeypacks/talkie/monkey.001')
    ap.add_argument('--extracted', default='data/scumm_extracted')
    args = ap.parse_args()

    df = parse_data_file(args.data)
    rooms_root = Path(args.extracted) / 'rooms'

    # Map room_id -> extracted room dir. A room can have twin dirs (bare
    # "room_010" + named "room_010_logo"); the converter uses the one with a
    # background.png, so prefer that (fall back to any matching dir).
    dir_by_id = {}
    for d in sorted(rooms_root.iterdir()):
        if not d.is_dir():
            continue
        stem = d.name.replace('room_', '')
        num = stem.split('_')[0]
        if not num.isdigit():
            continue
        rid = int(num)
        has_bg = (d / 'background.png').exists()
        if rid not in dir_by_id or has_bg:
            dir_by_id[rid] = d

    total = 0
    for rid, room in sorted(df.rooms.items()):
        room_dir = dir_by_id.get(rid)
        if room_dir is None:
            continue
        total += extract_object_zplanes(room, room_dir)
    print(f"Wrote {total} object z-plane masks across {len(dir_by_id)} rooms")


if __name__ == '__main__':
    main()
