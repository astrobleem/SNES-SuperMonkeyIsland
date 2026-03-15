#!/usr/bin/env python3
"""Build a mapping between SCUMM costume IDs (DCOS directory slots) and the
extractor's sequential file numbering (cost_NNN_roomXXX.bin).

The DCOS directory in the index file (monkey.000) stores (room, offset) per
costume slot, where offset is the position of the COST chunk header relative
to the start of the LFLF chunk in the data file (monkey.001).

The extractor (scumm_extract.py) iterates sorted(rooms) and within each room
iterates COST chunks sequentially, assigning a global counter: cost_000, cost_001, etc.

This script re-parses the data file to discover COST chunk offsets per room,
then matches each DCOS slot to the corresponding extractor file.
"""

import json
import os
import struct
import sys
from pathlib import Path

# Add tools/ to path for scumm package
sys.path.insert(0, str(Path(__file__).parent))

from scumm.index import parse_index
from scumm.resource import parse_data_file


def build_dcos_mapping(index_path, data_path, costumes_dir):
    """Build SCUMM costume ID -> extractor file mapping.

    Returns list of (scumm_id, room, offset, extractor_file, extractor_idx).
    """
    # Parse the index file to get DCOS directory
    index_data = parse_index(index_path)
    dcos = index_data.directories.get('DCOS')
    if dcos is None:
        print("ERROR: No DCOS directory found in index file")
        return []

    # Build a lookup: (room, offset) -> DCOS slot
    # The DCOS stores room_nums[] and offsets[] indexed by slot number
    dcos_entries = {}  # slot -> (room, offset)
    for slot_id in range(dcos.count):
        room = dcos.room_nums[slot_id]
        offset = dcos.offsets[slot_id]
        if room != 0:  # slot 0 or inactive slots have room=0
            dcos_entries[slot_id] = (room, offset)

    # Parse the data file to get COST chunk offsets per room
    data_file = parse_data_file(data_path)

    # For each room (sorted, like extractor does), find COST chunks and their
    # offsets within the LFLF block. The DCOS offset should match the COST
    # chunk's position relative to LFLF start.
    #
    # LFLF structure in file:
    #   [LFLF header 8 bytes] [LFLF payload...]
    #   Inside payload: [ROOM chunk] [trailing chunks: SCRP, SOUN, COST, CHAR...]
    #
    # The LOFF table gives offsets that point 8 bytes INTO the LFLF (the payload).
    # DCOS offsets appear to be relative to the LFLF start (including header).
    # We need to figure out the exact reference point.

    # Build room -> [(cost_chunk_offset_in_lflf, cost_size)] mapping
    room_cost_offsets = {}  # room_id -> list of (offset_in_file, chunk_size)

    for room_id, room_res in sorted(data_file.rooms.items()):
        lflf_offset = room_res.lflf_offset  # absolute file offset of LFLF header
        lflf_data = room_res.lflf.data       # LFLF payload (after 8-byte header)

        # Find COST chunks in trailing chunks by scanning LFLF payload
        # The trailing_chunks have offset relative to lflf_data start
        costs = room_res.get_trailing('COST')
        cost_offsets = []
        for cost in costs:
            # cost.offset is relative to the start of lflf_data (the LFLF payload)
            # The DCOS offset might be:
            # 1. Relative to LFLF header start: cost.offset + 8
            # 2. Relative to LFLF payload start: cost.offset
            # 3. Absolute file offset: lflf_offset + 8 + cost.offset
            cost_offsets.append((cost.offset, cost.size))
        if cost_offsets:
            room_cost_offsets[room_id] = cost_offsets

    # Now try to match DCOS entries to room COST chunks
    # First, let's figure out what reference frame the DCOS offset uses
    # by checking a few known entries

    # Try all three offset interpretations for the first few DCOS entries
    print("=== Offset reference analysis ===")
    print("Testing first 5 DCOS entries to determine offset reference frame:")
    print()

    test_count = 0
    for slot_id in sorted(dcos_entries.keys()):
        if test_count >= 5:
            break
        room, dcos_offset = dcos_entries[slot_id]
        if room not in data_file.rooms:
            continue

        room_res = data_file.rooms[room]
        lflf_offset = room_res.lflf_offset
        costs = room_res.get_trailing('COST')

        print(f"  DCOS slot {slot_id}: room={room}, dcos_offset={dcos_offset}")
        print(f"    LFLF at file offset {lflf_offset} (header), {lflf_offset+8} (payload)")

        for i, cost in enumerate(costs):
            abs_offset = lflf_offset + 8 + cost.offset
            rel_payload = cost.offset
            rel_header = cost.offset + 8
            print(f"    COST[{i}]: chunk.offset={cost.offset}, "
                  f"abs={abs_offset}, rel_payload={rel_payload}, "
                  f"rel_header={rel_header}, size={cost.size}")

            if abs_offset == dcos_offset:
                print(f"    >>> MATCH: absolute file offset")
            elif rel_payload == dcos_offset:
                print(f"    >>> MATCH: relative to LFLF payload")
            elif rel_header == dcos_offset:
                print(f"    >>> MATCH: relative to LFLF header")
        print()
        test_count += 1

    # Determine reference frame by counting matches across all entries
    match_counts = {'absolute': 0, 'rel_payload': 0, 'rel_header': 0}
    for slot_id, (room, dcos_offset) in dcos_entries.items():
        if room not in data_file.rooms:
            continue
        room_res = data_file.rooms[room]
        lflf_offset = room_res.lflf_offset
        costs = room_res.get_trailing('COST')
        for cost in costs:
            abs_offset = lflf_offset + 8 + cost.offset
            if abs_offset == dcos_offset:
                match_counts['absolute'] += 1
            if cost.offset == dcos_offset:
                match_counts['rel_payload'] += 1
            if cost.offset + 8 == dcos_offset:
                match_counts['rel_header'] += 1

    print(f"Match counts across all {len(dcos_entries)} DCOS entries:")
    print(f"  absolute file offset:         {match_counts['absolute']}")
    print(f"  relative to LFLF payload:     {match_counts['rel_payload']}")
    print(f"  relative to LFLF header:      {match_counts['rel_header']}")
    print()

    # Use the best matching reference frame
    best_ref = max(match_counts, key=match_counts.get)
    print(f"Using reference frame: {best_ref}")
    print()

    # Now build the actual mapping
    # First, replicate the extractor's sequential numbering
    extractor_files = []  # list of (extractor_idx, room_id, cost_chunk_index_in_room)
    global_idx = 0
    for room_id, room_res in sorted(data_file.rooms.items()):
        costs = room_res.get_trailing('COST')
        for cost_idx, cost in enumerate(costs):
            extractor_files.append((global_idx, room_id, cost_idx, cost))
            global_idx += 1

    # Build (room, cost_index_in_room) -> extractor_idx
    room_cost_to_extractor = {}
    for ext_idx, room_id, cost_idx, cost in extractor_files:
        room_cost_to_extractor[(room_id, cost_idx)] = ext_idx

    # For each DCOS slot, find which COST chunk in the room matches
    mapping = []  # (scumm_id, room, extractor_idx, extractor_file)
    unmatched = []

    for slot_id in sorted(dcos_entries.keys()):
        room, dcos_offset = dcos_entries[slot_id]
        if room not in data_file.rooms:
            unmatched.append((slot_id, room, dcos_offset, "room not found"))
            continue

        room_res = data_file.rooms[room]
        lflf_offset = room_res.lflf_offset
        costs = room_res.get_trailing('COST')

        matched = False
        for cost_idx, cost in enumerate(costs):
            # Compute the offset to compare based on the determined reference frame
            if best_ref == 'absolute':
                computed = lflf_offset + 8 + cost.offset
            elif best_ref == 'rel_payload':
                computed = cost.offset
            else:  # rel_header
                computed = cost.offset + 8

            if computed == dcos_offset:
                ext_idx = room_cost_to_extractor.get((room, cost_idx))
                if ext_idx is not None:
                    ext_file = f"cost_{ext_idx:03d}_room{room:03d}.bin"
                    mapping.append((slot_id, room, ext_idx, ext_file))
                    matched = True
                    break

        if not matched:
            unmatched.append((slot_id, room, dcos_offset, "no matching COST chunk"))

    # Verify extractor files exist
    existing_files = set()
    if costumes_dir.exists():
        for f in costumes_dir.iterdir():
            if f.suffix == '.bin':
                existing_files.add(f.name)

    # Print results
    print("=" * 78)
    print("DCOS COSTUME ID -> EXTRACTOR FILE MAPPING")
    print("=" * 78)
    print()
    print(f"{'SCUMM_ID':>8}  {'Room':>4}  {'Ext#':>4}  {'Extractor File':<30}  {'Exists':>6}")
    print("-" * 78)

    for scumm_id, room, ext_idx, ext_file in mapping:
        exists = "YES" if ext_file in existing_files else "NO"
        marker = ""
        if scumm_id in (1, 13, 17):
            marker = "  <-- KEY"
        print(f"{scumm_id:>8}  {room:>4}  {ext_idx:>4}  {ext_file:<30}  {exists:>6}{marker}")

    if unmatched:
        print()
        print(f"UNMATCHED DCOS ENTRIES ({len(unmatched)}):")
        for slot_id, room, offset, reason in unmatched:
            print(f"  Slot {slot_id}: room={room}, offset={offset} — {reason}")

    print()
    print(f"Total DCOS slots: {dcos.count}")
    print(f"Active DCOS entries: {len(dcos_entries)}")
    print(f"Matched: {len(mapping)}")
    print(f"Unmatched: {len(unmatched)}")
    print(f"Extractor files on disk: {len(existing_files)}")

    # Highlight the specifically requested IDs
    print()
    print("=" * 78)
    print("KEY COSTUME IDs (1, 13, 17):")
    print("=" * 78)
    for scumm_id, room, ext_idx, ext_file in mapping:
        if scumm_id in (1, 13, 17):
            exists = "YES" if ext_file in existing_files else "NO"
            print(f"  SCUMM ID {scumm_id:>3} -> {ext_file}  (room {room}, exists: {exists})")

    # Check if any of the key IDs were unmatched
    for target_id in (1, 13, 17):
        if target_id not in [m[0] for m in mapping]:
            for u in unmatched:
                if u[0] == target_id:
                    print(f"  SCUMM ID {target_id:>3} -> UNMATCHED ({u[3]})")

    return mapping


def main():
    # Paths relative to project root
    project_root = Path(__file__).parent.parent
    index_path = str(project_root / 'data' / 'monkeypacks' / 'talkie' / 'monkey.000')
    data_path = str(project_root / 'data' / 'monkeypacks' / 'talkie' / 'monkey.001')
    costumes_dir = project_root / 'data' / 'scumm_extracted' / 'costumes'

    # Check files exist
    if not Path(index_path).exists():
        print(f"ERROR: Index file not found: {index_path}")
        sys.exit(1)
    if not Path(data_path).exists():
        print(f"ERROR: Data file not found: {data_path}")
        sys.exit(1)

    mapping = build_dcos_mapping(index_path, data_path, costumes_dir)


if __name__ == '__main__':
    main()
