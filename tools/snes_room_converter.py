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
from scumm.cycle import build_cycle_blob, _read_pc_palette, _read_snes_palette

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

# Reserved palettes:
#   - Pal 0: SCUMM UI (cursor, setPalColor idx 0-15 targets). Scripts write here.
#   - Pal 6: verb highlight font (c1-c3) + universal art colors (c4-c15).
#   - Pal 7: verb normal font (c1-c3) + universal art colors (c4-c15).
# The HDMA CGRAM double-buffering scheme was removed 2026-04-18; pal6/pal7
# now hold static verb font colors AND "universal" colors the quantizer can
# assign tiles to as a 6th/7th effective palette (Level 2 optimization).
RESERVED_PALETTES_START = 1      # pal 0 (UI)
RESERVED_PALETTES_END = 2        # pal 6, pal 7 (verb + universal)
ART_SUBPALETTES = NUM_SUBPALETTES - RESERVED_PALETTES_START - RESERVED_PALETTES_END  # 5
# Backwards-compat alias used by legacy code paths; matches the START count since
# those paths only care about the "prepend" shift. DO NOT add the END reservation.
RESERVED_PALETTES = RESERVED_PALETTES_START

# Pal 6 (verb highlight + universal warm/saturated): c0 transparent,
# c1-c3 are verb highlight colors set by scummvm.writeVerbColors at runtime
# (fill/shadow/yellow-body), c4-c15 are universal colors any tile may borrow.
# Any change here must stay in sync with scummvm.writeVerbColors.
PAL6_VERB_PLUS_UNIVERSAL = [
    0x0000,  # c0 transparent
    0x6318,  # c1 verb highlight fill (light grey)
    0x294A,  # c2 verb highlight shadow (dark grey)
    0x03FF,  # c3 verb highlight body (yellow)
    # c4-c15: universal saturated/warm colors
    0x7FFF,  # c4 pure white
    0x7C1F,  # c5 magenta
    0x001F,  # c6 pure red
    0x03E0,  # c7 pure green
    0x7C00,  # c8 pure blue
    0x03FF,  # c9 yellow (dup c3)
    0x0CA5,  # c10 orange
    0x2F3F,  # c11 skin tone / tan
    0x18C6,  # c12 brown
    0x039F,  # c13 olive
    0x7FDB,  # c14 pale sky
    0x0000,  # c15 black (dup c0)
]

# Pal 7 (verb normal + universal cool/desaturated): c0 transparent,
# c1-c3 are verb normal colors (fill/shadow/white-body), c4-c15 universal.
PAL7_VERB_PLUS_UNIVERSAL = [
    0x0000,  # c0 transparent
    0x6318,  # c1 verb normal fill (light grey)
    0x294A,  # c2 verb normal shadow (dark grey)
    0x7FFF,  # c3 verb normal body (white)
    # c4-c15: universal cool/neutral colors
    0x4A52,  # c4 medium grey
    0x0C63,  # c5 dark grey
    0x2805,  # c6 dark purple
    0x1841,  # c7 dark blue
    0x4E94,  # c8 medium blue
    0x2A95,  # c9 teal
    0x0240,  # c10 forest green
    0x3FC0,  # c11 light green
    0x001F,  # c12 pure red (dup pal6 c6)
    0x7C00,  # c13 pure blue (dup pal6 c8)
    0x0000,  # c14 black (dup c0)
    0x0000,  # c15 black (dup c0)
]


# ---------------------------------------------------------------------------
# Tile deduplication with flip detection
# ---------------------------------------------------------------------------

def _tile_key(indexed_tile):
    """Hashable key for an indexed tile (8x8 uint8 array)."""
    return indexed_tile.tobytes()


def dedup_bgr555(raw_tiles):
    """Collapse identical 8x8 BGR555 tiles across 4 flip variants.

    Runs BEFORE palette quantization so the quantizer sees every visual
    pattern exactly once. Without this pass, `build_palettes_tileaware`
    can assign two copies of an identical BGR555 pattern to different
    sub-palettes; the indexed pixel bytes then differ (palette-relative
    indices) and the later `dedup_tiles` pass cannot collapse them. Each
    such split inflates the unique-tile count by one and eats into the
    11-bit tilemap tile-ID budget (rooms 33/53/58/59 visibly corrupted).

    Args:
        raw_tiles: (N, 8, 8) uint16 BGR555 tiles in positional order.

    Returns:
        unique_raw:         (M, 8, 8) uint16, M <= N, one entry per
                            distinct visual pattern (modulo H/V flip).
        position_to_unique: list of M-indices, length N.
        flip_flags:         list of (hflip, vflip) per position; apply
                            these flips to unique_raw[position_to_unique[i]]
                            to reconstruct raw_tiles[i].
    """
    unique = []
    lookup = {}  # tile_bytes -> unique_id
    position_to_unique = []
    flip_flags = []

    for i in range(len(raw_tiles)):
        tile = raw_tiles[i]
        variants = [
            (tile, False, False),
            (tile[:, ::-1], True, False),
            (tile[::-1, :], False, True),
            (tile[::-1, ::-1], True, True),
        ]

        found = False
        for var, hf, vf in variants:
            key = var.tobytes()
            if key in lookup:
                position_to_unique.append(lookup[key])
                flip_flags.append((hf, vf))
                found = True
                break

        if not found:
            uid = len(unique)
            unique.append(tile.copy())
            lookup[tile.tobytes()] = uid
            position_to_unique.append(uid)
            flip_flags.append((False, False))

    unique_arr = np.array(unique, dtype=np.uint16) if unique else np.zeros((0, 8, 8), dtype=np.uint16)
    return unique_arr, position_to_unique, flip_flags


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


def encode_priority_mask(room_dir, width_px, height_px, w_tiles, h_tiles):
    """Build per-tile priority bitmap from ZP01 zplane.

    Always returns exactly ceil(w_tiles * h_tiles / 8) bytes so the engine
    can derive pri_size from room dimensions at load time (no header
    field needed). Rooms without zplane_01.png get an all-zero bitmap.

    Layout: column-major, packed 8 tiles per byte, bit 0 = first tile in
    column. A tile's bit is 1 if ANY pixel in its 8x8 footprint is set in
    ZP01 (ScummVM's foreground mask — render in front of actors).
    """
    num_tiles = w_tiles * h_tiles
    out = bytearray((num_tiles + 7) // 8)

    zp01_path = room_dir / "zplane_01.png"
    if not zp01_path.exists():
        return bytes(out)

    zp_img = Image.open(zp01_path).convert('1')
    if zp_img.size != (width_px, height_px):
        log.warning("zplane_01.png %s != background %dx%d; ignoring",
                    zp_img.size, width_px, height_px)
        return bytes(out)

    zp_arr = np.array(zp_img, dtype=np.uint8)
    zp_tiled = zp_arr.reshape(h_tiles, TILE_SIZE, w_tiles, TILE_SIZE)
    any_fg = zp_tiled.any(axis=(1, 3))  # (h_tiles, w_tiles), bool

    bit_idx = 0
    for col in range(w_tiles):
        for row in range(h_tiles):
            if any_fg[row, col]:
                out[bit_idx >> 3] |= 1 << (bit_idx & 7)
            bit_idx += 1
    return bytes(out)


def _load_zplane_pixels(room_dir, width_px, height_px, w_tiles, h_tiles):
    """Load zplane_01.png and return per-pixel boolean array + per-tile classification.

    Returns (zp_tiled, tile_class) or (None, None) if no z-plane.
    zp_tiled: (h_tiles, 8, w_tiles, 8) uint8 array, 1=foreground 0=background.
    tile_class: (h_tiles, w_tiles) array: 0=bg, 1=partial, 2=full_fg.
    """
    zp01_path = room_dir / "zplane_01.png"
    if not zp01_path.exists():
        return None, None

    zp_img = Image.open(zp01_path).convert('1')
    if zp_img.size != (width_px, height_px):
        return None, None

    zp_arr = np.array(zp_img, dtype=np.uint8)
    zp_tiled = zp_arr.reshape(h_tiles, TILE_SIZE, w_tiles, TILE_SIZE)
    tile_sums = zp_tiled.sum(axis=(1, 3))
    tile_class = np.zeros((h_tiles, w_tiles), dtype=np.uint8)
    tile_class[(tile_sums > 0) & (tile_sums < PIXELS_PER_TILE)] = 1  # partial
    tile_class[tile_sums == PIXELS_PER_TILE] = 2  # fully foreground
    return zp_tiled, tile_class


def build_masked_tiles_and_bg2(bg_entries, unique_tiles, room_dir,
                                width_px, height_px, w_tiles, h_tiles,
                                max_budget=896):
    """Create masked tile variants for partial z-plane tiles; build BG2 tilemap.

    For partial foreground tiles (some pixels fg, some bg):
      - BG1 gets a masked copy (non-fg pixels → index 0 = transparent)
      - BG2 gets the original unmasked tile
    For fully foreground tiles:
      - BG1 keeps the original tile (all pixels are opaque anyway)
      - BG2 gets the original tile (fills behind, harmless)
    For background tiles:
      - BG1 unchanged, BG2 = tile 0 word 0 (transparent)

    Returns (bg_entries, unique_tiles, bg2_data):
      bg_entries: updated list (partial fg tiles remapped to masked variants)
      unique_tiles: extended list with masked variants appended
      bg2_data: bytes, column-major tilemap words for BG2 layer (or empty if no z-plane)
    """
    zp_tiled, tile_class = _load_zplane_pixels(
        room_dir, width_px, height_px, w_tiles, h_tiles
    )
    num_tile_slots = w_tiles * h_tiles
    if tile_class is None or not tile_class.any():
        return bg_entries, unique_tiles, b'\x00' * (num_tile_slots * 2)

    bg_entries = list(bg_entries)
    unique_tiles = list(unique_tiles)
    masked_cache = {}
    bg2_words = []
    stats_masked = 0
    stats_overflow = 0

    for col in range(w_tiles):
        for row in range(h_tiles):
            idx = row * w_tiles + col
            tc = tile_class[row, col]

            if tc == 0:
                bg2_words.append(0)
                continue

            orig_tid, pal, hf, vf = bg_entries[idx]
            orig_word = encode_tilemap_word(orig_tid, pal, hf, vf)
            bg2_words.append(orig_word)

            if tc == 2:
                continue

            if len(unique_tiles) >= max_budget:
                stats_overflow += 1
                continue

            zp_mask = zp_tiled[row, :, col, :]  # (8, 8)
            if hf:
                zp_mask = zp_mask[:, ::-1]
            if vf:
                zp_mask = zp_mask[::-1, :]

            cache_key = (orig_tid, zp_mask.tobytes())
            if cache_key in masked_cache:
                masked_tid = masked_cache[cache_key]
            else:
                orig_tile = unique_tiles[orig_tid].copy()
                orig_tile[zp_mask == 0] = 0
                masked_tid = len(unique_tiles)
                unique_tiles.append(orig_tile)
                masked_cache[cache_key] = masked_tid
                stats_masked += 1

            bg_entries[idx] = (masked_tid, pal, hf, vf)

    if stats_masked > 0:
        log.info("  BG2 mask: %d masked variants, %d overflow skipped, %d total tiles",
                 stats_masked, stats_overflow, len(unique_tiles))

    bg2_data = b''.join(struct.pack('<H', w) for w in bg2_words)
    return bg_entries, unique_tiles, bg2_data


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
                      num_tiles, pal_size, chr_size, map_size, col_size,
                      box_size=0, ochr_size=0, cyc_size=0):
    """Build 34-byte room header.

    Layout: same as the legacy 32-byte format, plus a 2-byte cyc_size
    trailer at offset $20. The .cyc blob lives between .ochr and .pri
    in the room block (pri stays last so its offset calc is unchanged).
    """
    box_field_32 = (box_size & 0xFFFF) | ((ochr_size & 0xFFFF) << 16)
    return struct.pack('<HHHHHHHHIIIIH',
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
        box_field_32,
        cyc_size,
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


def _rgb_to_bgr555_array(rgb_array):
    """Convert (H, W, 3) uint8 RGB array to (H, W) uint16 BGR555."""
    a = rgb_array.astype(np.int32)
    return (
        ((a[:, :, 0] >> 3) & 0x1F) |
        (((a[:, :, 1] >> 3) & 0x1F) << 5) |
        (((a[:, :, 2] >> 3) & 0x1F) << 10)
    ).astype(np.uint16)


# Magic pink transparent colors in SCUMM MI1 object images.
# (171,0,171) = VGA palette index 5 in most rooms.
# (168,0,168) = same after BGR555 round-trip (171 >> 3 << 3 = 168).
# (252,84,252) = alternate transparency marker used by some extractors.
# (248,80,248) = same after BGR555 round-trip.
SCUMM_TRANS_COLORS = {(171, 0, 171), (168, 0, 168), (252, 84, 252), (248, 80, 248)}


def _load_room_objects(room_dir, bg_width, bg_height):
    """Load object images and metadata for compositing.

    Returns list of dicts: {obj_id, x, y, width, height, rgb_array, name}
    Only includes objects that have extracted PNGs and fall within bg bounds.
    """
    meta_path = room_dir / "metadata.json"
    if not meta_path.exists():
        return []

    with open(meta_path) as f:
        meta = json.load(f)

    objects_meta = meta.get('objects', [])
    if not objects_meta:
        return []

    obj_dir = room_dir / "objects"
    if not obj_dir.exists():
        return []

    result = []
    for obj in objects_meta:
        oid = obj.get('obj_id', 0)
        x = obj.get('x', 0)
        y = obj.get('y', 0)
        w = obj.get('width', 0)
        h = obj.get('height', 0)
        name = obj.get('name', '')

        if w == 0 or h == 0:
            continue

        # Find PNG (may have name suffix)
        candidates = list(obj_dir.glob(f'obj_{oid:04d}*.png'))
        # Skip multi-state PNGs (we only want state 0 = default image)
        candidates = [c for c in candidates if '_state' not in c.name]
        if not candidates:
            continue

        # Verify object fits within background bounds
        if x < 0 or y < 0 or x + w > bg_width or y + h > bg_height:
            log.debug("  Object %d out of bounds (%d,%d %dx%d vs %dx%d), skipping",
                      oid, x, y, w, h, bg_width, bg_height)
            continue

        img = Image.open(candidates[0]).convert('RGB')
        if img.size != (w, h):
            # Metadata dimensions may not match image; use image dimensions
            w, h = img.size
            if x + w > bg_width or y + h > bg_height:
                continue

        result.append({
            'obj_id': oid,
            'x': x, 'y': y,
            'width': w, 'height': h,
            'rgb': np.array(img, dtype=np.uint8),
            'name': name,
        })

    return result


def _composite_object_tiles(bg_rgb, obj_info, width_tiles, height_tiles):
    """Composite one object onto the background, returning affected tile data.

    Args:
        bg_rgb: (H, W, 3) uint8 background RGB array
        obj_info: dict with x, y, width, height, rgb keys
        width_tiles, height_tiles: background tile dimensions

    Returns list of (col, row, composited_tile_bgr555) for tiles that differ
    from the background. composited_tile_bgr555 is (8, 8) uint16.
    """
    ox, oy = obj_info['x'], obj_info['y']
    ow, oh = obj_info['width'], obj_info['height']
    obj_rgb = obj_info['rgb']

    # Determine tile range covered by this object
    col_min = ox // TILE_SIZE
    col_max = (ox + ow - 1) // TILE_SIZE
    row_min = oy // TILE_SIZE
    row_max = (oy + oh - 1) // TILE_SIZE

    # Clamp to room bounds
    col_max = min(col_max, width_tiles - 1)
    row_max = min(row_max, height_tiles - 1)

    result = []
    for col in range(col_min, col_max + 1):
        for row in range(row_min, row_max + 1):
            # Extract background tile
            ty = row * TILE_SIZE
            tx = col * TILE_SIZE
            bg_tile = bg_rgb[ty:ty+TILE_SIZE, tx:tx+TILE_SIZE].copy()

            # Composite object pixels over background
            has_obj_pixel = False
            for py in range(TILE_SIZE):
                for px in range(TILE_SIZE):
                    # Room-space coords of this pixel
                    rx = tx + px
                    ry = ty + py
                    # Object-local coords
                    lx = rx - ox
                    ly = ry - oy
                    if 0 <= lx < ow and 0 <= ly < oh:
                        r, g, b = int(obj_rgb[ly, lx, 0]), int(obj_rgb[ly, lx, 1]), int(obj_rgb[ly, lx, 2])
                        if (r, g, b) not in SCUMM_TRANS_COLORS:
                            bg_tile[py, px] = obj_rgb[ly, lx]
                            has_obj_pixel = True

            if not has_obj_pixel:
                continue

            # Convert composited tile to BGR555
            comp_bgr = _rgb_to_bgr555_array(bg_tile.reshape(1, TILE_SIZE * TILE_SIZE, 3)
                                             .reshape(TILE_SIZE, TILE_SIZE, 3))
            result.append((col, row, comp_bgr))

    return result


def _build_object_patches(bg_entries, obj_comp_entries, obj_infos,
                          width_tiles, height_tiles):
    """Build per-object patch tables from composited tile entries.

    Args:
        bg_entries: list of (tile_id, pal_id, hflip, vflip) for background tiles
                    in row-major order (row * width_tiles + col)
        obj_comp_entries: dict mapping (obj_id, col, row) to
                         (tile_id, pal_id, hflip, vflip) from dedup
        obj_infos: list of object info dicts

    Returns list of per-object patch dicts:
        {obj_id, patches: [(col, row, obj_word)], bg_words: [word]}
    """
    patches_by_obj = []

    for obj in obj_infos:
        oid = obj['obj_id']
        ox, oy = obj['x'], obj['y']
        ow, oh = obj['width'], obj['height']

        col_min = ox // TILE_SIZE
        col_max = min((ox + ow - 1) // TILE_SIZE, width_tiles - 1)
        row_min = oy // TILE_SIZE
        row_max = min((oy + oh - 1) // TILE_SIZE, height_tiles - 1)

        patches = []
        bg_words = []

        for col in range(col_min, col_max + 1):
            for row in range(row_min, row_max + 1):
                key = (oid, col, row)
                if key not in obj_comp_entries:
                    continue

                # Get composited entry
                c_tid, c_pal, c_hf, c_vf = obj_comp_entries[key]
                c_word = encode_tilemap_word(c_tid, c_pal, c_hf, c_vf)

                # Get background entry
                bg_idx = row * width_tiles + col
                b_tid, b_pal, b_hf, b_vf = bg_entries[bg_idx]
                b_word = encode_tilemap_word(b_tid, b_pal, b_hf, b_vf)

                if c_word != b_word:
                    patches.append((col, row, c_word))
                    bg_words.append(b_word)

        if patches:
            patches_by_obj.append({
                'obj_id': oid,
                'patches': patches,
                'bg_words': bg_words,
                'col_min': min(p[0] for p in patches),
                'col_max': max(p[0] for p in patches),
            })

    return patches_by_obj


def _encode_ochr(patches_by_obj):
    """Encode per-object patch data into .ochr binary.

    Format:
      Header:
        obj_patch_count (LE16)  — number of objects with patches
        total_data_size (LE16)  — total bytes of patch data section
      Index (per object):
        obj_id       (LE16)
        patch_offset (LE16)  — offset into data section
      Data (per object):
        patch_count  (u8)
        col_min      (u8)
        col_max      (u8)
        reserved     (u8)
        patches[n]:  { col(u8), row(u8), obj_word(LE16) }  — 4 bytes each
        bg_words[n]: LE16[]  — original background words
    """
    if not patches_by_obj:
        return b''

    # Build data blobs
    data_blobs = []
    for obj_patch in patches_by_obj:
        patches = obj_patch['patches']
        bg_words = obj_patch['bg_words']
        count = len(patches)

        blob = struct.pack('<BBBB',
            count,
            obj_patch['col_min'],
            obj_patch['col_max'],
            0,  # reserved
        )
        for col, row, obj_word in patches:
            blob += struct.pack('<BBH', col, row, obj_word)
        for bw in bg_words:
            blob += struct.pack('<H', bw)
        data_blobs.append(blob)

    # Compute offsets
    index_size = len(patches_by_obj) * 4  # 4 bytes per index entry
    offsets = []
    pos = 0
    for blob in data_blobs:
        offsets.append(pos)
        pos += len(blob)

    total_data_size = pos

    # Build binary
    result = struct.pack('<HH', len(patches_by_obj), total_data_size)
    for i, obj_patch in enumerate(patches_by_obj):
        result += struct.pack('<HH', obj_patch['obj_id'], offsets[i])
    for blob in data_blobs:
        result += blob

    return result


def convert_room(room_dir, output_dir, verbose=False, verify=False):
    """Convert a single room's background to SNES tile format.

    Pipeline:
        1. Load RGB image + object images, convert to SNES BGR555
        2. Composite object tiles over background tiles
        3. Build 8 sub-palettes jointly (gracon-style: collect, reduce, partition)
        4. Assign each 8x8 tile to best sub-palette, remap pixels (lossy)
        5. Deduplicate tiles with horizontal/vertical flip detection
        6. Generate per-object patch tables (composited vs background tile IDs)
        7. Encode binary outputs: palette, tileset, tilemap, column index, header, ochr

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
    bg_rgb = np.array(img, dtype=np.uint8)

    # Convert background to SNES color space (BGR555)
    snes_pixels = _rgb_to_bgr555_array(bg_rgb.astype(np.int32))

    t0 = time.time()

    # Load object images for this room
    obj_infos = _load_room_objects(room_dir, width_px, height_px)

    # Composite object tiles over background
    # Track: (obj_id, col, row) -> index into extra_tiles array
    obj_comp_map = {}  # (obj_id, col, row) -> index in extra_snes_tiles
    extra_snes_tiles = []

    for obj in obj_infos:
        comp_tiles = _composite_object_tiles(bg_rgb, obj, width_tiles, height_tiles)
        for col, row, comp_bgr in comp_tiles:
            obj_comp_map[(obj['obj_id'], col, row)] = len(extra_snes_tiles)
            extra_snes_tiles.append(comp_bgr)

    # Split background into tiles
    bg_snes_tiles = snes_pixels.reshape(
        height_tiles, TILE_SIZE, width_tiles, TILE_SIZE
    ).transpose(0, 2, 1, 3).reshape(-1, TILE_SIZE, TILE_SIZE)

    # Joint palette quantization: background tiles + composited object tiles
    if extra_snes_tiles:
        all_snes_tiles = np.concatenate(
            [bg_snes_tiles, np.array(extra_snes_tiles, dtype=np.uint16)],
            axis=0
        )
    else:
        all_snes_tiles = bg_snes_tiles

    # Pre-quantization BGR555 dedup: collapse identical visual patterns
    # before the quantizer sees them. Without this, identical raw tiles
    # can be scattered across different sub-palettes and the later
    # indexed-byte dedup cannot collapse them (different palette ->
    # different index representation). See dedup_bgr555 docstring.
    unique_raw, pos_to_unique, flip_flags = dedup_bgr555(all_snes_tiles)

    # Quantize with 2 fixed palettes (pal6/pal7 verb+universal) + 5 mutable
    # (art). Quantizer can assign tiles to any of the 7 palettes; fixed ones
    # hold verb colors plus a global "universal" set that many tiles may
    # match, effectively raising the art palette budget above 5.
    # Quantizer output order: [pal6_fixed, pal7_fixed, art0, art1, art2, art3, art4].
    # We re-arrange to [pal0_ui, art0-art4, pal6, pal7] for the .pal file.
    # Operates on unique_raw (M <= N tiles), not the positional all_snes_tiles.
    fixed_palettes = [PAL6_VERB_PLUS_UNIVERSAL, PAL7_VERB_PLUS_UNIVERSAL]
    raw_palettes, unique_indexed, raw_unique_pal_ids = build_palettes_tileaware(
        unique_raw, num_palettes=ART_SUBPALETTES,
        fixed_palettes=fixed_palettes
    )
    # Remap quantizer pal_ids (0..6) -> output pal_ids (0..7 with pal0 reserved)
    # Quantizer: 0 = fixed_0 (pal6), 1 = fixed_1 (pal7), 2..6 = mutable art.
    # Output:    0 = UI empty, 1..5 = art, 6 = pal6, 7 = pal7.
    #   quantizer 0 -> output 6
    #   quantizer 1 -> output 7
    #   quantizer 2..6 -> output 1..5
    remap = np.array([6, 7, 1, 2, 3, 4, 5], dtype=np.uint8)
    unique_pal_ids = remap[raw_unique_pal_ids]
    empty_pal = [rgb_to_bgr555(*TRANS_COLOR)] * COLORS_PER_SUBPALETTE
    palettes = (
        [empty_pal]                 # pal 0 (UI)
        + list(raw_palettes[2:])    # pal 1-5 (art)
        + list(raw_palettes[0:2])   # pal 6, pal 7 (verb + universal)
    )
    pals_used = sum(1 for p in palettes if any(c != rgb_to_bgr555(*TRANS_COLOR)
                                                 for c in p[1:]))

    # Dedup the M unique indexed tiles (catches residual same-palette flip
    # equivalences the BGR555 pass cannot see — e.g. two distinct raw
    # patterns that quantize to identical indexed bytes under one palette).
    final_unique_tiles, unique_entries = dedup_tiles(
        list(unique_indexed), list(unique_pal_ids)
    )

    # Expand M unique entries back to N positional entries, composing
    # the pre-dedup raw flips with the post-dedup flips via XOR. Both
    # are independent involutions on their respective axes, so XOR is
    # the correct composition.
    all_entries = []
    for i in range(len(all_snes_tiles)):
        u = pos_to_unique[i]
        tid, pal, hf_post, vf_post = unique_entries[u]
        hf_raw, vf_raw = flip_flags[i]
        all_entries.append((tid, pal, hf_post ^ hf_raw, vf_post ^ vf_raw))

    unique_tiles = final_unique_tiles

    # Split entries back into background and object groups (same split as
    # before, now operating on the N positional entries).
    bg_count = len(bg_snes_tiles)
    bg_entries = all_entries[:bg_count]
    obj_entries = all_entries[bg_count:]

    # Map composited tile entries back to (obj_id, col, row)
    obj_comp_entries = {}
    for (oid, col, row), extra_idx in obj_comp_map.items():
        obj_comp_entries[(oid, col, row)] = obj_entries[extra_idx]

    # Build per-object patch tables
    obj_patches = _build_object_patches(
        bg_entries, obj_comp_entries, obj_infos,
        width_tiles, height_tiles
    )

    # Z-plane masked tile variants + BG2 tilemap for dual-layer masking
    bg_entries, unique_tiles, bg2_data = build_masked_tiles_and_bg2(
        bg_entries, unique_tiles, room_dir,
        width_px, height_px, width_tiles, height_tiles
    )

    # Encode binary outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"room_{room_id:03d}"

    pal_data = encode_palette(palettes)
    chr_data = encode_tileset(unique_tiles)
    map_data = encode_tilemap_column_major(bg_entries, width_tiles, height_tiles)
    col_data = build_column_index(map_data, width_tiles, height_tiles)

    # ZP01 per-tile priority bitmap (appended to room block). Empty if the
    # room has no zplane_01.png or the mask is all zeros.
    pri_data = encode_priority_mask(room_dir, width_px, height_px,
                                     width_tiles, height_tiles)

    # Walkbox binary
    box_path = room_dir / "walkbox.box"
    if box_path.exists():
        box_data = box_path.read_bytes()
    else:
        box_data = struct.pack('<H', 0)

    # Object patch binary (.ochr)
    ochr_data = _encode_ochr(obj_patches)

    # Color cycling descriptor (.cyc) — uses metadata.json + PC CLUT + final SNES palette
    cyc_data = b'\x00'
    meta_path = room_dir / 'metadata.json'
    pc_pal_path = room_dir / 'palette.bin'
    if meta_path.exists():
        with open(meta_path) as f:
            _meta = json.load(f)
        _cycles = _meta.get('color_cycling', [])
        if _cycles and pc_pal_path.exists():
            pc_pal = _read_pc_palette(pc_pal_path)
            snes_words = _read_snes_palette(pal_data)
            cyc_data = build_cycle_blob(_cycles, pc_pal, snes_words)

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
        box_size=len(box_data),
        ochr_size=len(ochr_data),
        cyc_size=len(cyc_data),
    )

    # Write files
    (output_dir / f"{prefix}.pal").write_bytes(pal_data)
    (output_dir / f"{prefix}.chr").write_bytes(chr_data)
    (output_dir / f"{prefix}.map").write_bytes(map_data)
    (output_dir / f"{prefix}.pri").write_bytes(pri_data)
    (output_dir / f"{prefix}.col").write_bytes(col_data)
    (output_dir / f"{prefix}.box").write_bytes(box_data)
    (output_dir / f"{prefix}.ochr").write_bytes(ochr_data)
    (output_dir / f"{prefix}.cyc").write_bytes(cyc_data)
    (output_dir / f"{prefix}.bg2").write_bytes(bg2_data)
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
    obj_patch_count = sum(len(p['patches']) for p in obj_patches)

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
        'box_bytes': len(box_data),
        'ochr_bytes': len(ochr_data),
        'ochr_objects': len(obj_patches),
        'ochr_patches': obj_patch_count,
        'cyc_bytes': len(cyc_data),
        'hdr_bytes': len(hdr_data),
        'exceeds_tile_limit': exceeds,
        'time_s': round(elapsed, 2),
    }

    flag = ' !!!' if exceeds else ''
    obj_str = f'  {len(obj_patches)}obj/{obj_patch_count}pat' if obj_patches else ''
    log.info("Room %03d %-16s %4dx%-3d  %4d tiles (%4.0f%% dedup)  %d pals  %.1fs%s%s",
             room_id, room_name, width_px, height_px, num_tiles,
             dedup * 100, pals_used, elapsed, obj_str, flag)

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

    skipped = []   # legitimately empty dirs (no background.png) — not real failures
    for room_id, room_dir in rooms:
        try:
            stats = convert_room(room_dir, output_dir, verbose=verbose, verify=verify)
            if stats:
                results.append(stats)
            elif (room_dir / "background.png").exists():
                failures.append(room_id)  # had a bg but convert_room returned None
            else:
                skipped.append(room_id)   # no bg, normal — just the short-name twin dir
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
        log.info("Rooms: %d converted, %d failed, %d skipped (of %d total dirs)",
                 len(results), len(failures), len(skipped), len(rooms))
        if failures:
            log.warning("Failed rooms: %s", failures)
        if skipped:
            log.info("Skipped (no background.png — usually short-name twins): %d dirs",
                     len(skipped))
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
