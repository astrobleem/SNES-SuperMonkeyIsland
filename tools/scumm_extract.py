#!/usr/bin/env python3
"""SCUMM v5 resource extractor for The Secret of Monkey Island (CD Talkie).

Extracts all game resources from monkey.000 (index) + monkey.001 (data) into
organized directories with PNGs for visual assets, raw binaries for bytecode,
and JSON for metadata.

Usage:
    python tools/scumm_extract.py \\
        --index data/monkeypacks/talkie/monkey.000 \\
        --data data/monkeypacks/talkie/monkey.001 \\
        --output data/scumm_extracted
"""

import argparse
import json
import logging
import struct
import sys
from pathlib import Path

# Add parent dir to path so we can import the scumm package
sys.path.insert(0, str(Path(__file__).parent))

from scumm.index import parse_index
from scumm.resource import parse_data_file
from scumm.palette import parse_clut, save_palette_bin, save_palette_png
from scumm.room_gfx import extract_background
from scumm.object_gfx import extract_object_images
from scumm.metadata import extract_metadata, extract_scripts
from scumm.costume import extract_costumes
from scumm.charset import extract_charsets
from scumm.manifest import generate_manifest

log = logging.getLogger('scumm_extract')


def save_index_data(index_data, output_dir: Path):
    """Save parsed index data to JSON files."""
    idx_dir = output_dir / 'index'
    idx_dir.mkdir(parents=True, exist_ok=True)

    # MAXS
    with open(idx_dir / 'maxs.json', 'w') as f:
        json.dump(index_data.maxs, f, indent=2)

    # Room names
    with open(idx_dir / 'room_names.json', 'w') as f:
        json.dump({str(k): v for k, v in sorted(index_data.room_names.items())},
                  f, indent=2)

    # Directories
    dirs = {}
    for tag, d in index_data.directories.items():
        active = [(i, d.room_nums[i], d.offsets[i])
                  for i in range(d.count) if d.room_nums[i] != 0]
        dirs[tag] = {
            'total_slots': d.count,
            'active_count': len(active),
            'entries': [{'slot': i, 'room': r, 'offset': o}
                       for i, r, o in active],
        }
    with open(idx_dir / 'directories.json', 'w') as f:
        json.dump(dirs, f, indent=2)

    # Object owner/state
    objects = []
    for i, (owner, state) in enumerate(index_data.object_owner_state):
        if owner != 0 or state != 0:
            objects.append({'id': i, 'owner': owner, 'state': state})
    with open(idx_dir / 'objects.json', 'w') as f:
        json.dump({'total': len(index_data.object_owner_state),
                   'active': objects}, f, indent=2)

    log.info("Index data saved to %s", idx_dir)


def extract_room(room_resource, room_name: str, output_dir: Path,
                 extract_types: set, verbose: bool) -> dict:
    """Extract all resources from a single room.

    Returns dict with extraction info.
    """
    room_id = room_resource.room_id
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_'
                       for c in room_name).strip('_')
    room_dir_name = f'room_{room_id:03d}_{safe_name}'
    room_dir = output_dir / 'rooms' / room_dir_name

    info = {
        'directory': f'rooms/{room_dir_name}',
        'background': False,
        'object_images': 0,
        'num_objects': 0,
        'scripts': 0,
        'costumes': 0,
        'sounds': 0,
        'charsets': 0,
    }

    # Get dimensions
    rmhd = room_resource.get_room_sub('RMHD')
    if rmhd:
        w, h, num_obj = struct.unpack_from('<HHH', rmhd.data, 0)
        info['dimensions'] = [w, h]
        info['num_objects'] = num_obj

    # Get palette (needed for backgrounds and object images)
    palette = None
    clut = room_resource.get_room_sub('CLUT')
    if clut:
        palette = parse_clut(clut.data)

    # Background
    if 'backgrounds' in extract_types or 'all' in extract_types:
        if extract_background(room_resource, room_dir):
            info['background'] = True
        # Save palette
        if clut and palette:
            save_palette_bin(clut.data, room_dir / 'palette.bin')
            save_palette_png(palette, room_dir / 'palette.png')

    # Metadata
    if 'metadata' in extract_types or 'all' in extract_types:
        extract_metadata(room_resource, room_dir, room_name)

    # Scripts
    if 'scripts' in extract_types or 'all' in extract_types:
        extract_scripts(room_resource, room_dir)
        # Count scripts
        scripts_dir = room_dir / 'scripts'
        if scripts_dir.exists():
            info['scripts'] = len(list(scripts_dir.glob('*.bin')))

    # Object images
    if 'objects' in extract_types or 'all' in extract_types:
        if palette:
            info['object_images'] = extract_object_images(
                room_resource, room_dir, palette)

    # Costumes
    if 'costumes' in extract_types or 'all' in extract_types:
        info['costumes'] = extract_costumes(room_resource, room_dir)

    # Charsets
    if 'charsets' in extract_types or 'all' in extract_types:
        info['charsets'] = extract_charsets(room_resource, room_dir)

    # Sounds (raw binary)
    if 'sounds' in extract_types or 'all' in extract_types:
        souns = room_resource.get_trailing('SOUN')
        if souns:
            sounds_dir = room_dir / 'sounds'
            sounds_dir.mkdir(parents=True, exist_ok=True)
            for i, soun in enumerate(souns):
                path = sounds_dir / f'soun_{i:03d}.bin'
                path.write_bytes(soun.data)
            info['sounds'] = len(souns)

    return info


def extract_global_scripts(data_file, index_data, output_dir: Path) -> int:
    """Extract global scripts (SCRP) to a dedicated directory."""
    scripts_dir = output_dir / 'scripts'
    count = 0

    for room_id, room in sorted(data_file.rooms.items()):
        scrps = room.get_trailing('SCRP')
        for scrp in scrps:
            scripts_dir.mkdir(parents=True, exist_ok=True)
            path = scripts_dir / f'scrp_{count:03d}_room{room_id:03d}.bin'
            path.write_bytes(scrp.data)
            count += 1

    if count > 0:
        log.info("Saved %d global scripts to %s", count, scripts_dir)
    return count


def extract_global_sounds(data_file, output_dir: Path) -> int:
    """Extract all sounds (SOUN) to a dedicated directory."""
    sounds_dir = output_dir / 'sounds'
    count = 0

    for room_id, room in sorted(data_file.rooms.items()):
        souns = room.get_trailing('SOUN')
        for soun in souns:
            sounds_dir.mkdir(parents=True, exist_ok=True)
            path = sounds_dir / f'soun_{count:03d}_room{room_id:03d}.bin'
            path.write_bytes(soun.data)
            count += 1

    if count > 0:
        log.info("Saved %d sounds to %s", count, sounds_dir)
    return count


def extract_global_costumes(data_file, output_dir: Path) -> int:
    """Extract all costumes (COST) to a dedicated directory."""
    costumes_dir = output_dir / 'costumes'
    count = 0

    for room_id, room in sorted(data_file.rooms.items()):
        costs = room.get_trailing('COST')
        for cost in costs:
            costumes_dir.mkdir(parents=True, exist_ok=True)
            path = costumes_dir / f'cost_{count:03d}_room{room_id:03d}.bin'
            path.write_bytes(cost.data)
            count += 1

    if count > 0:
        log.info("Saved %d costumes to %s", count, costumes_dir)
    return count


def extract_global_charsets(data_file, output_dir: Path) -> int:
    """Extract all charsets (CHAR) to a dedicated directory."""
    charsets_dir = output_dir / 'charsets'
    count = 0

    for room_id, room in sorted(data_file.rooms.items()):
        chars = room.get_trailing('CHAR')
        for char_chunk in chars:
            charsets_dir.mkdir(parents=True, exist_ok=True)
            path = charsets_dir / f'char_{count:03d}_room{room_id:03d}.bin'
            path.write_bytes(char_chunk.data)
            count += 1

    if count > 0:
        log.info("Saved %d charsets to %s", count, charsets_dir)
    return count


def main():
    parser = argparse.ArgumentParser(
        description='SCUMM v5 resource extractor for Monkey Island 1 CD Talkie')
    parser.add_argument('--index', required=True,
                        help='Path to monkey.000 index file')
    parser.add_argument('--data', required=True,
                        help='Path to monkey.001 data file')
    parser.add_argument('--output', required=True,
                        help='Output directory for extracted resources')
    parser.add_argument('--rooms', default=None,
                        help='Comma-separated list of room IDs to extract (default: all)')
    parser.add_argument('--types', default='all',
                        help='Comma-separated resource types: '
                             'backgrounds,metadata,scripts,objects,costumes,charsets,sounds,all')
    parser.add_argument('--index-only', action='store_true',
                        help='Only parse and dump the index file')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse index
    log.info("Parsing index file: %s", args.index)
    index_data = parse_index(args.index)
    save_index_data(index_data, output_dir)

    log.info("Index: %d rooms, %d script slots, %d sound slots, "
             "%d costume slots, %d charset slots, %d objects",
             len(index_data.room_names),
             index_data.num_scripts,
             index_data.num_sounds,
             index_data.num_costumes,
             index_data.num_charsets,
             len(index_data.object_owner_state))

    if args.index_only:
        log.info("Index-only mode, stopping here.")
        return

    # Parse data file
    log.info("Parsing data file: %s", args.data)
    data_file = parse_data_file(args.data)
    log.info("Found %d rooms in data file", len(data_file.rooms))

    # Determine which rooms to extract
    if args.rooms:
        room_ids = [int(r.strip()) for r in args.rooms.split(',')]
    else:
        room_ids = sorted(data_file.rooms.keys())

    extract_types = set(args.types.split(','))

    # Extract rooms
    rooms_extracted = {}
    errors = 0
    for room_id in room_ids:
        if room_id not in data_file.rooms:
            log.warning("Room %d not found in data file", room_id)
            continue

        room_name = index_data.room_names.get(room_id, f'unknown_{room_id}')
        log.info("Extracting room %d (%s)...", room_id, room_name)

        try:
            info = extract_room(data_file.rooms[room_id], room_name,
                               output_dir, extract_types, args.verbose)
            rooms_extracted[room_id] = info
        except Exception as e:
            log.error("Room %d: extraction failed: %s", room_id, e)
            errors += 1
            if args.verbose:
                import traceback
                traceback.print_exc()

    # Extract global resources
    global_scripts = extract_global_scripts(data_file, index_data, output_dir)
    global_sounds = extract_global_sounds(data_file, output_dir)
    global_costumes = extract_global_costumes(data_file, output_dir)
    global_charsets = extract_global_charsets(data_file, output_dir)

    # Generate manifest
    manifest = generate_manifest(output_dir, index_data, rooms_extracted,
                                 global_scripts, global_sounds,
                                 global_costumes, global_charsets)

    # Summary
    print()
    print("=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"  Rooms extracted:    {len(rooms_extracted)}/{len(room_ids)}")
    print(f"  Backgrounds:        {manifest['extraction']['total_backgrounds']}")
    print(f"  Object images:      {manifest['extraction']['total_object_images']}")
    print(f"  Global scripts:     {global_scripts}")
    print(f"  Global sounds:      {global_sounds}")
    print(f"  Global costumes:    {global_costumes}")
    print(f"  Global charsets:    {global_charsets}")
    if errors:
        print(f"  Errors:             {errors}")
    print(f"  Output directory:   {output_dir}")
    print("=" * 60)


if __name__ == '__main__':
    main()
