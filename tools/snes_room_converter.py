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
# Color-space helpers
# ---------------------------------------------------------------------------

def rgb_to_bgr555(r, g, b):
    """Convert 8-bit RGB to SNES BGR555 (15-bit word)."""
    return int(((r >> 3) & 0x1F) |
               (((g >> 3) & 0x1F) << 5) |
               (((b >> 3) & 0x1F) << 10))


def bgr555_to_rgb(word):
    """Convert SNES BGR555 to 8-bit RGB tuple."""
    return (
        (word & 0x1F) << 3,
        ((word >> 5) & 0x1F) << 3,
        ((word >> 10) & 0x1F) << 3,
    )


# ---------------------------------------------------------------------------
# Palette builder (gracon-inspired)
# ---------------------------------------------------------------------------

def _collect_unique_colors(snes_pixels):
    """Get sorted list of unique BGR555 values, excluding transparent."""
    unique = set(snes_pixels.ravel().tolist())
    trans = rgb_to_bgr555(*TRANS_COLOR)
    unique.discard(trans)
    return sorted(unique)


def _bgr555_to_components(colors):
    """Decompose BGR555 array into float R, G, B component arrays (0-31)."""
    arr = np.asarray(colors, dtype=np.int32)
    return (arr & 0x1F).astype(np.float64), \
           ((arr >> 5) & 0x1F).astype(np.float64), \
           ((arr >> 10) & 0x1F).astype(np.float64)


def _components_to_bgr555(r, g, b):
    """Convert float R, G, B (0-31) back to BGR555 uint16."""
    ri = np.clip(np.round(r), 0, 31).astype(np.int32)
    gi = np.clip(np.round(g), 0, 31).astype(np.int32)
    bi = np.clip(np.round(b), 0, 31).astype(np.int32)
    return (ri | (gi << 5) | (bi << 10)).astype(np.uint16)


def _weighted_dist_sq(r1, g1, b1, r2, g2, b2):
    """Perceptual weighted squared distance: 2*dR² + 4*dG² + 1*dB²."""
    dr = r1 - r2
    dg = g1 - g2
    db = b1 - b2
    return 2.0 * dr * dr + 4.0 * dg * dg + db * db


def _tile_color_histograms(snes_tiles):
    """Build per-tile unique color lists and pixel counts.

    Returns list of (colors_bgr555, counts) tuples, one per tile.
    colors_bgr555 is a 1D uint16 array, counts is a matching int array.
    """
    n_tiles = snes_tiles.shape[0]
    flat = snes_tiles.reshape(n_tiles, PIXELS_PER_TILE)
    histograms = []
    for i in range(n_tiles):
        colors, counts = np.unique(flat[i], return_counts=True)
        histograms.append((colors.astype(np.uint16), counts.astype(np.int32)))
    return histograms


def _assign_tiles_to_palettes(palettes_rgb, histograms):
    """Assign each tile to its lowest-error palette.

    palettes_rgb: (num_pals, pal_size, 3) float64 — R,G,B components
    histograms: list of (colors_bgr555, counts) per tile

    Returns (assignments, total_errors):
        assignments: (n_tiles,) int — palette index
        total_errors: (n_tiles,) float — weighted error per tile
    """
    n_pals = palettes_rgb.shape[0]
    n_tiles = len(histograms)
    assignments = np.zeros(n_tiles, dtype=np.int32)
    total_errors = np.zeros(n_tiles, dtype=np.float64)

    for i, (colors, counts) in enumerate(histograms):
        cr, cg, cb = _bgr555_to_components(colors)
        best_err = np.inf
        best_pal = 0
        for p in range(n_pals):
            pr, pg, pb = palettes_rgb[p, :, 0], palettes_rgb[p, :, 1], palettes_rgb[p, :, 2]
            # Distance from each tile color to each palette color
            dist = _weighted_dist_sq(
                cr[:, None], cg[:, None], cb[:, None],
                pr[None, :], pg[None, :], pb[None, :]
            )  # (n_colors, pal_size)
            min_dist = dist.min(axis=1)  # (n_colors,)
            err = (min_dist * counts).sum()
            if err < best_err:
                best_err = err
                best_pal = p
        assignments[i] = best_pal
        total_errors[i] = best_err

    return assignments, total_errors


def _recompute_palette_centroids(palettes_rgb, histograms, assignments):
    """Recompute each palette color as the weighted centroid of assigned pixels.

    Uses weighted k-means: each palette color attracts its nearest pixels from
    all tiles assigned to that palette.
    """
    n_pals = palettes_rgb.shape[0]
    pal_size = palettes_rgb.shape[1]
    new_pals = palettes_rgb.copy()

    for p in range(n_pals):
        tile_mask = (assignments == p)
        if not tile_mask.any():
            continue

        # Gather all unique colors and their total weights for this palette
        all_colors_r = []
        all_colors_g = []
        all_colors_b = []
        all_weights = []
        indices = np.where(tile_mask)[0]
        for i in indices:
            colors, counts = histograms[i]
            cr, cg, cb = _bgr555_to_components(colors)
            all_colors_r.append(cr)
            all_colors_g.append(cg)
            all_colors_b.append(cb)
            all_weights.append(counts.astype(np.float64))

        if not all_colors_r:
            continue

        ar = np.concatenate(all_colors_r)
        ag = np.concatenate(all_colors_g)
        ab = np.concatenate(all_colors_b)
        aw = np.concatenate(all_weights)

        pr = new_pals[p, :, 0]
        pg = new_pals[p, :, 1]
        pb = new_pals[p, :, 2]

        # Assign each pixel-color to nearest palette entry
        dist = _weighted_dist_sq(
            ar[:, None], ag[:, None], ab[:, None],
            pr[None, :], pg[None, :], pb[None, :]
        )  # (n_pixels, pal_size)
        nearest = dist.argmin(axis=1)  # (n_pixels,)

        # Recompute centroids
        for c in range(pal_size):
            mask = (nearest == c)
            w = aw[mask]
            if w.sum() > 0:
                wsum = w.sum()
                new_pals[p, c, 0] = (ar[mask] * w).sum() / wsum
                new_pals[p, c, 1] = (ag[mask] * w).sum() / wsum
                new_pals[p, c, 2] = (ab[mask] * w).sum() / wsum

    return new_pals


def build_palettes_tileaware(snes_tiles):
    """Build 8 sub-palettes using tile-aware iterative optimization.

    Jointly assigns tiles to palettes and recomputes palette colors to minimize
    per-tile quantization error. Based on tiledpalettequant by Rilden.

    snes_tiles: (n_tiles, 8, 8) uint16 BGR555 pixels

    Returns (palettes, indexed_tiles, tile_pal_ids):
        palettes:      list of 8 lists of 16 BGR555 values
        indexed_tiles: (n_tiles, 8, 8) uint8 palette-relative indices
        tile_pal_ids:  (n_tiles,) uint8 sub-palette assignments
    """
    n_tiles = snes_tiles.shape[0]
    trans = rgb_to_bgr555(*TRANS_COLOR)
    pal_size = COLORS_PER_SUBPALETTE_NONTRANS  # 15 usable colors per sub-palette
    rng = np.random.RandomState(42)

    # Build per-tile color histograms
    histograms = _tile_color_histograms(snes_tiles)

    # Gather all pixel colors with weights for global stats
    all_r, all_g, all_b, all_w = [], [], [], []
    for colors, counts in histograms:
        cr, cg, cb = _bgr555_to_components(colors)
        all_r.append(cr)
        all_g.append(cg)
        all_b.append(cb)
        all_w.append(counts.astype(np.float64))
    glob_r = np.concatenate(all_r)
    glob_g = np.concatenate(all_g)
    glob_b = np.concatenate(all_b)
    glob_w = np.concatenate(all_w)

    # Global weighted average color
    ws = glob_w.sum()
    avg_r = (glob_r * glob_w).sum() / ws
    avg_g = (glob_g * glob_w).sum() / ws
    avg_b = (glob_b * glob_w).sum() / ws

    # --- Initialize palettes via splitting ---
    # Start with 1 palette of 1 color (global average), split to NUM_SUBPALETTES
    palettes_rgb = np.zeros((1, pal_size, 3), dtype=np.float64)
    palettes_rgb[0, 0] = [avg_r, avg_g, avg_b]

    while palettes_rgb.shape[0] < NUM_SUBPALETTES:
        n_pals = palettes_rgb.shape[0]
        assignments, _ = _assign_tiles_to_palettes(palettes_rgb, histograms)

        # Find palette with highest total error
        _, tile_errors = _assign_tiles_to_palettes(palettes_rgb, histograms)
        pal_errors = np.zeros(n_pals)
        for i in range(n_tiles):
            pal_errors[assignments[i]] += tile_errors[i]

        worst = int(np.argmax(pal_errors))

        # Duplicate the worst palette with small perturbation
        new_pal = palettes_rgb[worst].copy()
        perturbed = new_pal + rng.uniform(-1.5, 1.5, new_pal.shape)
        perturbed = np.clip(perturbed, 0, 31)

        palettes_rgb = np.concatenate([palettes_rgb, perturbed[None]], axis=0)

        # Quick refinement: 3 rounds of assign + recompute
        for _ in range(3):
            assignments, _ = _assign_tiles_to_palettes(palettes_rgb, histograms)
            palettes_rgb = _recompute_palette_centroids(palettes_rgb, histograms, assignments)

    # --- Expand palettes from initial colors to full 15 ---
    # Currently palette entries beyond [0] are zero; fill them via k-means on assigned tiles
    for expand_round in range(3):
        assignments, _ = _assign_tiles_to_palettes(palettes_rgb, histograms)

        for p in range(NUM_SUBPALETTES):
            tile_mask = (assignments == p)
            if not tile_mask.any():
                continue

            # Gather unique colors for this palette's tiles
            p_colors_r, p_colors_g, p_colors_b, p_weights = [], [], [], []
            for i in np.where(tile_mask)[0]:
                colors, counts = histograms[i]
                cr, cg, cb = _bgr555_to_components(colors)
                p_colors_r.append(cr)
                p_colors_g.append(cg)
                p_colors_b.append(cb)
                p_weights.append(counts.astype(np.float64))

            pr = np.concatenate(p_colors_r)
            pg = np.concatenate(p_colors_g)
            pb = np.concatenate(p_colors_b)
            pw = np.concatenate(p_weights)

            n_unique = len(pr)
            n_centers = min(pal_size, n_unique)

            if n_centers < 1:
                continue

            # k-means++ initialization
            centers_r = np.zeros(n_centers)
            centers_g = np.zeros(n_centers)
            centers_b = np.zeros(n_centers)

            # First center: weighted random
            probs = pw / pw.sum()
            idx = rng.choice(n_unique, p=probs)
            centers_r[0], centers_g[0], centers_b[0] = pr[idx], pg[idx], pb[idx]

            for c in range(1, n_centers):
                dist = np.full(n_unique, np.inf)
                for cc in range(c):
                    d = _weighted_dist_sq(pr, pg, pb, centers_r[cc], centers_g[cc], centers_b[cc])
                    dist = np.minimum(dist, d)
                probs = (dist * pw)
                s = probs.sum()
                if s > 0:
                    probs /= s
                else:
                    probs = pw / pw.sum()
                idx = rng.choice(n_unique, p=probs)
                centers_r[c], centers_g[c], centers_b[c] = pr[idx], pg[idx], pb[idx]

            # Run k-means iterations
            for _ in range(8):
                dist = _weighted_dist_sq(
                    pr[:, None], pg[:, None], pb[:, None],
                    centers_r[None, :], centers_g[None, :], centers_b[None, :]
                )
                nearest = dist.argmin(axis=1)
                for c in range(n_centers):
                    mask = (nearest == c)
                    w = pw[mask]
                    if w.sum() > 0:
                        wsum = w.sum()
                        centers_r[c] = (pr[mask] * w).sum() / wsum
                        centers_g[c] = (pg[mask] * w).sum() / wsum
                        centers_b[c] = (pb[mask] * w).sum() / wsum

            palettes_rgb[p, :n_centers, 0] = centers_r
            palettes_rgb[p, :n_centers, 1] = centers_g
            palettes_rgb[p, :n_centers, 2] = centers_b
            # Fill remaining slots with duplicates of first center
            for c in range(n_centers, pal_size):
                palettes_rgb[p, c] = palettes_rgb[p, 0]

    # --- Main refinement: alternating assignment + centroid recomputation ---
    best_palettes = palettes_rgb.copy()
    best_error = np.inf

    for iteration in range(10):
        assignments, tile_errors = _assign_tiles_to_palettes(palettes_rgb, histograms)
        total_err = tile_errors.sum()

        if total_err < best_error:
            best_error = total_err
            best_palettes = palettes_rgb.copy()

        palettes_rgb = _recompute_palette_centroids(palettes_rgb, histograms, assignments)

    # Final assignment with best palettes
    palettes_rgb = best_palettes
    assignments, _ = _assign_tiles_to_palettes(palettes_rgb, histograms)
    # One last centroid pass
    palettes_rgb = _recompute_palette_centroids(palettes_rgb, histograms, assignments)
    assignments, _ = _assign_tiles_to_palettes(palettes_rgb, histograms)

    # --- Quantize to BGR555 and build output ---
    palettes_out = []
    # Convert float palettes to BGR555
    for p in range(NUM_SUBPALETTES):
        pal = [trans]
        bgr_arr = _components_to_bgr555(
            palettes_rgb[p, :, 0], palettes_rgb[p, :, 1], palettes_rgb[p, :, 2]
        )
        for c in range(pal_size):
            pal.append(int(bgr_arr[c]))
        palettes_out.append(pal)

    # --- Palettize tiles: assign pixels to nearest color in assigned palette ---
    flat_tiles = snes_tiles.reshape(n_tiles, PIXELS_PER_TILE).astype(np.int32)
    tr = (flat_tiles & 0x1F).astype(np.float64)
    tg = ((flat_tiles >> 5) & 0x1F).astype(np.float64)
    tb = ((flat_tiles >> 10) & 0x1F).astype(np.float64)

    indexed_tiles = np.zeros((n_tiles, PIXELS_PER_TILE), dtype=np.uint8)
    tile_pal_ids = assignments.astype(np.uint8)

    # Pre-decompose quantized palettes (including trans at index 0)
    pal_decomp = []
    for pal in palettes_out:
        arr = np.array(pal, dtype=np.int32)
        pal_decomp.append((
            (arr & 0x1F).astype(np.float64),
            ((arr >> 5) & 0x1F).astype(np.float64),
            ((arr >> 10) & 0x1F).astype(np.float64),
        ))

    # Batch by palette assignment for efficiency
    for p in range(NUM_SUBPALETTES):
        mask = (assignments == p)
        if not mask.any():
            continue
        idxs = np.where(mask)[0]
        pr, pg, pb = pal_decomp[p]  # each (16,)

        t_r = tr[idxs]  # (n, 64)
        t_g = tg[idxs]
        t_b = tb[idxs]

        # (n, 64, 16) distance — exclude index 0 (transparent)
        dist = _weighted_dist_sq(
            t_r[:, :, None], t_g[:, :, None], t_b[:, :, None],
            pr[None, None, :], pg[None, None, :], pb[None, None, :]
        )
        dist[:, :, 0] = np.inf  # never map pixels to transparent slot
        indexed_tiles[idxs] = dist.argmin(axis=2).astype(np.uint8)

    indexed_tiles = indexed_tiles.reshape(n_tiles, TILE_SIZE, TILE_SIZE)
    return palettes_out, indexed_tiles, tile_pal_ids


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
