#!/usr/bin/env python3
"""
SNES Room Tile Converter

Converts extracted VGA room backgrounds to SNES Mode 1 native tile format
for MSU-1 streaming.  Full Python pipeline: palette quantization with lossy
sub-palette assignment (gracon-inspired), tile deduplication with flip
detection, column-major tilemap, and column streaming index.

Usage:
    # Single room
    python tools/snes_room_converter.py \
        --input data/scumm_extracted/rooms/room_028_bar/background.png \
        --output data/snes_converted/rooms/

    # All rooms (batch)
    python tools/snes_room_converter.py \
        --input data/scumm_extracted/rooms/ \
        --output data/snes_converted/rooms/ \
        --verbose

    # Specific rooms with verification images
    python tools/snes_room_converter.py \
        --input data/scumm_extracted/rooms/ \
        --output data/snes_converted/rooms/ \
        --rooms 1,10,20,28 \
        --verify
"""

import argparse
import json
import logging
import struct
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# Import tile-aware palette optimizer from standalone module (same directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tiledpalettequant import build_palettes_tileaware, rgb_to_bgr555, bgr555_to_rgb

log = logging.getLogger('snes_room_converter')

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SNES Mode 1 BG1 parameters
NUM_SUBPALETTES = 8
COLORS_PER_SUBPALETTE = 16
COLORS_PER_SUBPALETTE_NONTRANS = COLORS_PER_SUBPALETTE - 1  # color 0 = transparent
BPP = 4
TILE_SIZE = 8
PIXELS_PER_TILE = TILE_SIZE * TILE_SIZE
BYTES_PER_4BPP_TILE = 32  # 8x8 x 4bpp planar
EXPECTED_PAL_SIZE = NUM_SUBPALETTES * COLORS_PER_SUBPALETTE * 2  # 256 bytes
HEADER_SIZE = 32
MAX_TILE_ID = 1024  # 10-bit tile index in SNES tilemap word
TRANS_COLOR = (0, 0, 0)  # color 0 in every sub-palette


# ---------------------------------------------------------------------------
# Tile deduplication with flip detection
# ---------------------------------------------------------------------------

def _tile_key(indexed_tile):
    """Hashable key for an indexed tile (8x8 uint8 array)."""
    return indexed_tile.tobytes()


def dedup_tiles(indexed_tiles, tile_pal_ids, max_tiles=MAX_TILE_ID):
    """Deduplicate tiles checking all 4 flip variants.

    Returns (unique_tiles, tilemap_entries):
        unique_tiles:   list of (8,8) uint8 arrays
        tilemap_entries: list of (tile_id, pal_id, hflip, vflip) per tile
    """
    unique = []
    lookup = {}  # tile_bytes -> (tile_id, hflip_needed, vflip_needed)
    entries = []

    for i in range(len(indexed_tiles)):
        tile = indexed_tiles[i]
        pal_id = int(tile_pal_ids[i])

        # Generate 4 flip variants and check
        variants = [
            (tile, False, False),
            (tile[:, ::-1], True, False),
            (tile[::-1, :], False, True),
            (tile[::-1, ::-1], True, True),
        ]

        found = False
        for var, hf, vf in variants:
            key = _tile_key(var)
            if key in lookup:
                tid = lookup[key]
                entries.append((tid, pal_id, hf, vf))
                found = True
                break

        if not found:
            tid = len(unique)
            unique.append(tile.copy())
            lookup[_tile_key(tile)] = tid
            entries.append((tid, pal_id, False, False))

    if len(unique) > max_tiles:
        log.warning("  %d unique tiles exceeds %d limit", len(unique), max_tiles)

    return unique, entries


# ---------------------------------------------------------------------------
# Binary encoders
# ---------------------------------------------------------------------------

def encode_4bpp_tile(indexed_tile):
    """Encode 8x8 indexed pixels (0-15) to 32-byte SNES 4bpp planar."""
    data = bytearray(32)
    for row in range(8):
        bp0 = bp1 = bp2 = bp3 = 0
        for col in range(8):
            idx = int(indexed_tile[row, col]) & 0x0F
            bit = 7 - col
            bp0 |= ((idx >> 0) & 1) << bit
            bp1 |= ((idx >> 1) & 1) << bit
            bp2 |= ((idx >> 2) & 1) << bit
            bp3 |= ((idx >> 3) & 1) << bit
        data[row * 2] = bp0
        data[row * 2 + 1] = bp1
        data[16 + row * 2] = bp2
        data[16 + row * 2 + 1] = bp3
    return bytes(data)


def encode_tilemap_word(tile_id, pal_id, hflip, vflip, priority=False):
    """Build custom WRAM tilemap word: vh_pppTTTTTTTTTTT.

    Custom format for tile cache (NOT raw SNES format):
      Bits 0-10:  Tile ID (11 bits, 0-2047)
      Bits 11-13: Palette (3 bits, 0-7)
      Bit 14:     H flip
      Bit 15:     V flip
    Priority is dropped (always 0 for BG backgrounds).
    The SNES engine remaps to hardware format when writing to VRAM.
    """
    word = (tile_id & 0x07FF)
    word |= (pal_id & 0x07) << 11
    if hflip:
        word |= 1 << 14
    if vflip:
        word |= 1 << 15
    return word


def encode_palette(palettes):
    """Encode palette list to SNES CGRAM binary (BGR555, little-endian)."""
    data = bytearray()
    for pal in palettes:
        for color in pal:
            data += struct.pack('<H', color & 0x7FFF)
    # Pad to expected size
    while len(data) < EXPECTED_PAL_SIZE:
        data += b'\x00\x00'
    return bytes(data[:EXPECTED_PAL_SIZE])


def encode_tileset(unique_tiles):
    """Encode all unique tiles to 4bpp planar binary."""
    parts = []
    for tile in unique_tiles:
        parts.append(encode_4bpp_tile(tile))
    return b''.join(parts)


def encode_tilemap_column_major(entries, w_tiles, h_tiles):
    """Encode tilemap in column-major order (column 0 top-to-bottom, then column 1, ...)."""
    data = bytearray()
    for col in range(w_tiles):
        for row in range(h_tiles):
            idx = row * w_tiles + col
            tid, pal, hf, vf = entries[idx]
            word = encode_tilemap_word(tid, pal, hf, vf)
            data += struct.pack('<H', word)
    return bytes(data)


def build_column_index(map_data, width_tiles, height_tiles):
    """Build column streaming index from column-major tilemap data.

    Format:
        num_columns (LE16)
        offsets[num_columns] (LE16) -- byte offset into tile list data section
        Per column (at each offset):
            num_tiles (LE16)
            tile_entries[num_tiles]:
                tile_id (LE16)       -- index into tileset
                tilemap_word (LE16)  -- full SNES tilemap word
                row (u8)             -- row position (0..height_tiles-1)
    """
    tile_data_parts = []
    for col in range(width_tiles):
        entry = struct.pack('<H', height_tiles)
        for row in range(height_tiles):
            offset = (col * height_tiles + row) * 2
            tilemap_word = struct.unpack_from('<H', map_data, offset)[0]
            tile_id = tilemap_word & 0x07FF
            entry += struct.pack('<HHB', tile_id, tilemap_word, row)
        tile_data_parts.append(entry)

    offsets = []
    pos = 0
    for part in tile_data_parts:
        offsets.append(pos)
        pos += len(part)

    result = struct.pack('<H', width_tiles)
    for off in offsets:
        result += struct.pack('<H', off)
    for part in tile_data_parts:
        result += part
    return result


def build_room_header(room_id, width_px, height_px, width_tiles, height_tiles,
                      num_tiles, pal_size, chr_size, map_size, col_size):
    """Build 32-byte room header."""
    return struct.pack('<HHHHHHHHIIII',
        room_id,
        width_px,
        height_px,
        width_tiles,
        height_tiles,
        num_tiles,
        pal_size,
        min(chr_size, 0xFFFF),
        chr_size,
        map_size,
        col_size,
        0,
    )


# ---------------------------------------------------------------------------
# Verification image generation
# ---------------------------------------------------------------------------

def decode_4bpp_tile(data):
    """Decode 32 bytes of 4bpp SNES planar tile to 8x8 pixel indices."""
    pixels = np.zeros((8, 8), dtype=np.uint8)
    for row in range(8):
        bp0 = data[row * 2]
        bp1 = data[row * 2 + 1]
        bp2 = data[16 + row * 2]
        bp3 = data[16 + row * 2 + 1]
        for col in range(8):
            bit = 7 - col
            pixels[row, col] = (
                ((bp0 >> bit) & 1) |
                (((bp1 >> bit) & 1) << 1) |
                (((bp2 >> bit) & 1) << 2) |
                (((bp3 >> bit) & 1) << 3)
            )
    return pixels


def generate_verification_image(pal_data, chr_data, map_data,
                                width_tiles, height_tiles):
    """Reconstruct room image from SNES native data for visual QA."""
    # Parse palette
    palette = []
    for i in range(0, len(pal_data), 2):
        palette.append(bgr555_to_rgb(struct.unpack_from('<H', pal_data, i)[0]))

    # Decode tiles
    num_tiles = len(chr_data) // BYTES_PER_4BPP_TILE
    tiles = [decode_4bpp_tile(chr_data[i*32:(i+1)*32]) for i in range(num_tiles)]

    w = width_tiles * TILE_SIZE
    h = height_tiles * TILE_SIZE
    img = Image.new('RGB', (w, h), (0, 0, 0))
    px = img.load()

    for col in range(width_tiles):
        for row in range(height_tiles):
            off = (col * height_tiles + row) * 2
            word = struct.unpack_from('<H', map_data, off)[0]
            tid = word & 0x07FF
            pal_id = (word >> 11) & 0x07
            hflip = bool(word & 0x4000)
            vflip = bool(word & 0x8000)

            if tid >= len(tiles):
                continue

            tile = tiles[tid]
            pal_base = pal_id * COLORS_PER_SUBPALETTE

            for ty in range(8):
                for tx in range(8):
                    sy = (7 - ty) if vflip else ty
                    sx = (7 - tx) if hflip else tx
                    ci = int(tile[sy, sx])
                    pi = pal_base + ci
                    rgb = palette[pi] if pi < len(palette) else (255, 0, 255)
                    px[col * 8 + tx, row * 8 + ty] = rgb

    return img


# ---------------------------------------------------------------------------
# Room conversion (full pipeline)
# ---------------------------------------------------------------------------

def parse_room_dir(room_dir):
    """Extract room_id, room_name, and header dict from a room directory."""
    room_dir = Path(room_dir)
    meta_path = room_dir / "metadata.json"
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)
        return meta['room_id'], meta['room_name'], meta.get('header', {})

    name = room_dir.name
    if name.startswith('room_') and len(name) >= 8:
        try:
            room_id = int(name[5:8])
            room_name = name[9:] if len(name) > 9 else str(room_id)
            return room_id, room_name, {}
        except ValueError:
            pass
    raise ValueError(f"Cannot determine room info from {room_dir}")


def convert_room(room_dir, output_dir, verbose=False, verify=False):
    """Convert a single room's background to SNES tile format.

    Pipeline:
        1. Load RGB image, convert to SNES BGR555 color space
        2. Build 8 sub-palettes (gracon-style: collect, reduce, partition)
        3. Assign each 8x8 tile to best sub-palette, remap pixels (lossy)
        4. Deduplicate tiles with horizontal/vertical flip detection
        5. Encode binary outputs: palette, tileset, tilemap, column index, header

    Returns a stats dict on success, or None on failure.
    """
    room_dir = Path(room_dir)
    output_dir = Path(output_dir)

    bg_png = room_dir / "background.png"
    if not bg_png.exists():
        log.warning("No background.png in %s, skipping", room_dir.name)
        return None

    try:
        room_id, room_name, header = parse_room_dir(room_dir)
    except ValueError as e:
        log.error("Skipping %s: %s", room_dir.name, e)
        return None

    # Load image
    img = Image.open(bg_png).convert('RGB')
    width_px, height_px = img.size

    if width_px % TILE_SIZE or height_px % TILE_SIZE:
        log.error("Room %d: %dx%d not tile-aligned", room_id, width_px, height_px)
        return None

    width_tiles = width_px // TILE_SIZE
    height_tiles = height_px // TILE_SIZE

    # Convert to SNES color space (BGR555)
    rgb = np.array(img, dtype=np.int32)
    snes_pixels = (
        ((rgb[:, :, 0] >> 3) & 0x1F) |
        (((rgb[:, :, 1] >> 3) & 0x1F) << 5) |
        (((rgb[:, :, 2] >> 3) & 0x1F) << 10)
    ).astype(np.uint16)

    # Step 1+2: Split into tiles, build palettes, and palettize (tile-aware)
    t0 = time.time()
    snes_tiles = snes_pixels.reshape(
        height_tiles, TILE_SIZE, width_tiles, TILE_SIZE
    ).transpose(0, 2, 1, 3).reshape(-1, TILE_SIZE, TILE_SIZE)

    palettes, indexed_tiles, tile_pal_ids = build_palettes_tileaware(snes_tiles)
    pals_used = sum(1 for p in palettes if any(c != rgb_to_bgr555(*TRANS_COLOR)
                                                 for c in p[1:]))

    # Step 3: Deduplicate
    unique_tiles, tilemap_entries = dedup_tiles(indexed_tiles, tile_pal_ids)

    # Step 4: Encode outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"room_{room_id:03d}"

    pal_data = encode_palette(palettes)
    chr_data = encode_tileset(unique_tiles)
    map_data = encode_tilemap_column_major(tilemap_entries, width_tiles, height_tiles)
    col_data = build_column_index(map_data, width_tiles, height_tiles)

    hdr_data = build_room_header(
        room_id=room_id,
        width_px=width_px,
        height_px=height_px,
        width_tiles=width_tiles,
        height_tiles=height_tiles,
        num_tiles=len(unique_tiles),
        pal_size=len(pal_data),
        chr_size=len(chr_data),
        map_size=len(map_data),
        col_size=len(col_data),
    )

    # Write files
    (output_dir / f"{prefix}.pal").write_bytes(pal_data)
    (output_dir / f"{prefix}.chr").write_bytes(chr_data)
    (output_dir / f"{prefix}.map").write_bytes(map_data)
    (output_dir / f"{prefix}.col").write_bytes(col_data)
    (output_dir / f"{prefix}.hdr").write_bytes(hdr_data)

    elapsed = time.time() - t0
    num_tiles = len(unique_tiles)
    total_slots = width_tiles * height_tiles
    dedup = 1.0 - (num_tiles / total_slots) if total_slots > 0 else 0.0

    # Verification image
    if verify:
        vimg = generate_verification_image(pal_data, chr_data, map_data,
                                           width_tiles, height_tiles)
        if vimg:
            vpath = output_dir / f"{prefix}_verify.png"
            vimg.save(vpath)
            log.info("  Verification: %s", vpath.name)

    exceeds = num_tiles > MAX_TILE_ID

    stats = {
        'room_id': room_id,
        'room_name': room_name,
        'width_px': width_px,
        'height_px': height_px,
        'width_tiles': width_tiles,
        'height_tiles': height_tiles,
        'num_tiles': num_tiles,
        'total_tile_slots': total_slots,
        'dedup_ratio': round(dedup, 4),
        'palettes_used': pals_used,
        'pal_bytes': len(pal_data),
        'chr_bytes': len(chr_data),
        'map_bytes': len(map_data),
        'col_bytes': len(col_data),
        'hdr_bytes': len(hdr_data),
        'exceeds_tile_limit': exceeds,
        'time_s': round(elapsed, 2),
    }

    flag = ' !!!' if exceeds else ''
    log.info("Room %03d %-16s %4dx%-3d  %4d tiles (%4.0f%% dedup)  %d pals  %.1fs%s",
             room_id, room_name, width_px, height_px, num_tiles,
             dedup * 100, pals_used, elapsed, flag)

    return stats


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def discover_rooms(input_dir, room_filter=None):
    """Find room directories to process. Returns sorted (room_id, path) list."""
    rooms = []
    for d in sorted(Path(input_dir).iterdir()):
        if not d.is_dir() or not d.name.startswith('room_'):
            continue
        try:
            room_id = int(d.name[5:8])
        except (ValueError, IndexError):
            continue
        if room_filter and room_id not in room_filter:
            continue
        rooms.append((room_id, d))
    return rooms


def batch_convert(input_dir, output_dir, room_filter=None,
                  verbose=False, verify=False):
    """Convert all rooms in batch mode."""
    rooms = discover_rooms(input_dir, room_filter)
    if not rooms:
        log.error("No rooms found in %s", input_dir)
        return

    log.info("Converting %d room%s...", len(rooms), 's' if len(rooms) != 1 else '')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    failures = []
    t0 = time.time()

    for room_id, room_dir in rooms:
        try:
            stats = convert_room(room_dir, output_dir, verbose=verbose, verify=verify)
            if stats:
                results.append(stats)
            else:
                failures.append(room_id)
        except Exception as e:
            log.error("Room %d: unhandled error: %s", room_id, e)
            failures.append(room_id)

    elapsed = time.time() - t0

    # Write manifest
    manifest = {
        'total_rooms': len(rooms),
        'converted': len(results),
        'failed': failures,
        'rooms': results,
    }
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    # Summary
    if results:
        avg_tiles = sum(r['num_tiles'] for r in results) / len(results)
        avg_dedup = sum(r['dedup_ratio'] for r in results) / len(results)
        total_chr = sum(r['chr_bytes'] for r in results)
        over_limit = [r for r in results if r['exceeds_tile_limit']]

        log.info("")
        log.info("=== Conversion Summary ===")
        log.info("Rooms: %d converted, %d failed (of %d total)",
                 len(results), len(failures), len(rooms))
        if failures:
            log.warning("Failed rooms: %s", failures)
        log.info("Avg tiles/room: %.0f   Avg dedup: %.1f%%",
                 avg_tiles, avg_dedup * 100)
        log.info("Total tileset data: %.1f KB", total_chr / 1024)
        if over_limit:
            log.warning("Over %d-tile limit: %s", MAX_TILE_ID,
                        ', '.join(f"{r['room_id']}({r['num_tiles']})" for r in over_limit))
        log.info("Manifest: %s", manifest_path)
        log.info("Elapsed: %.1fs (%.2fs/room)", elapsed, elapsed / len(results))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert VGA room backgrounds to SNES Mode 1 tile format")
    parser.add_argument('--input', '-i', required=True,
                        help="Room directory (with background.png) or rooms parent dir")
    parser.add_argument('--output', '-o', required=True,
                        help="Output directory for SNES data files")
    parser.add_argument('--rooms', '-r',
                        help="Comma-separated room IDs to process (default: all)")
    parser.add_argument('--verify', action='store_true',
                        help="Generate verification PNGs from SNES data")
    parser.add_argument('--verbose', '-v', action='store_true',
                        help="Verbose output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s: %(message)s',
    )

    room_filter = None
    if args.rooms:
        try:
            room_filter = set(int(x.strip()) for x in args.rooms.split(','))
        except ValueError:
            log.error("Invalid --rooms: use comma-separated IDs (e.g. --rooms 1,10,28)")
            sys.exit(1)

    input_path = Path(args.input)

    if input_path.is_file():
        stats = convert_room(input_path.parent, args.output,
                             verbose=args.verbose, verify=args.verify)
        sys.exit(0 if stats else 1)

    if not input_path.is_dir():
        log.error("Input path not found: %s", input_path)
        sys.exit(1)

    if (input_path / "background.png").exists():
        stats = convert_room(input_path, args.output,
                             verbose=args.verbose, verify=args.verify)
        sys.exit(0 if stats else 1)

    batch_convert(input_path, args.output, room_filter=room_filter,
                  verbose=args.verbose, verify=args.verify)


if __name__ == '__main__':
    main()
