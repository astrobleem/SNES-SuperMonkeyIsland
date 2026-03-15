#!/usr/bin/env python3
"""SCUMM v5 costume decoder — decodes COST binary format into per-frame pixel images.

Parses the binary COST chunk data extracted by scumm_extract.py and decodes
the RLE-compressed limb pictures into indexed pixel arrays. Optionally renders
verification PNGs using the room palette.

The COST binary layout (after 8-byte SCUMM chunk header, which is already
stripped by the extractor):

    Offset 0:   2 padding bytes (skipped by ScummVM's ptr += 2 for v5)
    Offset 2:   _baseptr origin (all internal offsets are relative to here)
    Offset 8:   _numAnim (1 byte)
    Offset 9:   format | mirror  (1 byte: bit 7 = mirror, bits 6-0 = format)
    Offset 10:  palette[_numColors] (16 or 32 bytes)
    Offset 10+N: _animCmds offset (LE16, relative to _baseptr)
    Offset 12+N: _frameOffsets (16 x LE16) — per-limb picture table pointers
    Offset 44+N: _dataOffsets (num_anim x LE16) — per-anim command data pointers

Each limb's picture table (at _frameOffsets[limb]):
    Entry K: LE16 offset from _baseptr to picture K's data

Each picture:
    Bytes 0-1:  width (LE16)
    Bytes 2-3:  height (LE16)
    Bytes 4-5:  relX (signed LE16) — anchor X offset
    Bytes 6-7:  relY (signed LE16) — anchor Y offset
    Bytes 8-9:  moveX (signed LE16) — cumulative X movement
    Bytes 10-11: moveY (signed LE16) — cumulative Y movement
    Bytes 12+:  RLE column data

RLE encoding (format 0x58 = 16 colors):
    shr=4, mask=0x0F
    Each byte: color = byte >> 4, length = byte & 0x0F
    If length == 0, next byte is the actual length
    Column-by-column, height pixels per column

Usage:
    # List all animations and pictures in a costume
    python tools/scumm_costume_decoder.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --list

    # Decode specific frame with verification PNG
    python tools/scumm_costume_decoder.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --anim 1 --frame 0 --verify \\
        --palette data/scumm_extracted/rooms/room_020_main-beac/palette.bin

    # Decode all pictures and save binary + PNG
    python tools/scumm_costume_decoder.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --all --verify \\
        --palette data/scumm_extracted/rooms/room_020_main-beac/palette.bin \\
        --output data/scumm_decoded_costumes/cost_001
"""

import argparse
import json
import logging
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger('scumm_costume_decoder')

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ScummVM's CostumeInfo struct: 6 x LE16 = 12 bytes
COSTUME_INFO_SIZE = 12

# Number of limbs in SCUMM v5 costumes
NUM_LIMBS = 16

# Number of directions (N, E, S, W)
NUM_DIRS = 4

# Special animation command values
ANIM_CMD_START = 0x7A   # start/enable limb
ANIM_CMD_STOP = 0x79    # stop/disable limb
ANIM_CMD_HIDE = 0x7B    # hide limb (nothing to draw)


@dataclass
class CostumePicture:
    """A single decoded costume picture (one limb pose)."""
    width: int
    height: int
    rel_x: int    # anchor X offset
    rel_y: int    # anchor Y offset
    move_x: int   # cumulative X movement
    move_y: int   # cumulative Y movement
    pixels: np.ndarray  # shape (height, width), dtype uint8, palette indices
    rle_size: int  # bytes consumed by RLE data


@dataclass
class CostumeAnim:
    """An animation entry — maps limbs to picture indices."""
    anim_id: int
    limb_mask: int  # 16-bit mask of which limbs are active
    limb_cmds: dict  # limb_index -> (cmd_start, cmd_end, looping)


@dataclass
class Costume:
    """A fully parsed SCUMM v5 costume."""
    num_anim: int
    format: int
    mirror: bool
    num_colors: int
    palette: bytes        # costume palette (indices into global palette)
    limb_pictures: list   # [limb_index] -> list of CostumePicture
    animations: list      # [anim_id] -> CostumeAnim or None
    anim_cmds: bytes      # raw animation command table
    raw_data: bytes       # original binary data


def _bp_to_data(bp_offset: int) -> int:
    """Convert a _baseptr-relative offset to a data[] index.

    _baseptr = resource_start + 2 = chunk_payload - 6
    So _baseptr + X = data[X - 6].
    """
    return bp_offset - 6


def parse_costume(data: bytes) -> Costume:
    """Parse a COST chunk payload into a Costume object.

    Args:
        data: Raw COST chunk payload (8-byte SCUMM header already stripped).

    Returns:
        Parsed Costume with all pictures decoded.
    """
    if len(data) < 20:
        raise ValueError(f"COST data too small: {len(data)} bytes")

    # _baseptr is at resource offset 2 = data offset -6
    # So _baseptr[K] = data[K - 6] for K >= 6
    # _numAnim = _baseptr[6] = data[0]
    # format_byte = _baseptr[7] = data[1]
    num_anim = data[0]
    format_byte = data[1]
    fmt = format_byte & 0x7F
    mirror = bool(format_byte & 0x80)

    # Determine palette size
    if fmt == 0x57:
        num_colors = 0
    elif fmt == 0x58:
        num_colors = 16
    elif fmt == 0x59:
        num_colors = 32
    elif fmt == 0x60:
        num_colors = 16
    elif fmt == 0x61:
        num_colors = 32
    else:
        raise ValueError(f"Unknown COST format: 0x{fmt:02x}")

    log.debug("COST: num_anim=%d, format=0x%02x, mirror=%s, num_colors=%d",
              num_anim, fmt, mirror, num_colors)

    # Palette at data[2] (= _baseptr[8])
    palette = data[2:2 + num_colors]

    # After palette: ptr = _baseptr + 8 + num_colors = data[2 + num_colors]
    p = 2 + num_colors

    # _animCmds = _baseptr + READ_LE_UINT16(ptr)
    anim_cmds_bp = struct.unpack_from('<H', data, p)[0]
    anim_cmds_data = _bp_to_data(anim_cmds_bp)

    # _frameOffsets = ptr + 2 -> data[4 + num_colors]
    frame_offsets_data = 4 + num_colors

    # _dataOffsets = ptr + 34 -> data[36 + num_colors]
    data_offsets_data = 36 + num_colors

    log.debug("  animCmds at data[%d], frameOffsets at data[%d], dataOffsets at data[%d]",
              anim_cmds_data, frame_offsets_data, data_offsets_data)

    # Read raw anim_cmds (from anim_cmds_data to end of useful data)
    anim_cmds = data[anim_cmds_data:] if anim_cmds_data >= 0 else b''

    # RLE decode parameters
    if fmt in (0x58, 0x60):
        shr, mask = 4, 0x0F
    elif fmt in (0x59, 0x61):
        shr, mask = 3, 0x07
    elif fmt == 0x57:
        shr, mask = 4, 0x0F  # C64 format, unlikely but handle
    else:
        shr, mask = 4, 0x0F

    # Parse per-limb picture tables
    limb_pictures = [[] for _ in range(NUM_LIMBS)]
    seen_offsets = set()

    for limb in range(NUM_LIMBS):
        frame_off_bp = struct.unpack_from('<H', data, frame_offsets_data + limb * 2)[0]
        frame_off_data = _bp_to_data(frame_off_bp)

        if frame_off_data < 0 or frame_off_data >= len(data):
            continue
        if frame_off_bp in seen_offsets and limb > 0:
            # Multiple limbs sharing the same table = unused limbs
            continue
        seen_offsets.add(frame_off_bp)

        # Read picture offsets until we hit something unreasonable
        # SCUMM allows NULL (0x0000) entries in the middle of the table — skip them
        pic_idx = 0
        consecutive_bad = 0
        while True:
            entry_pos = frame_off_data + pic_idx * 2
            if entry_pos + 2 > len(data):
                break

            pic_bp = struct.unpack_from('<H', data, entry_pos)[0]
            pic_data = _bp_to_data(pic_bp)

            if pic_bp == 0 or pic_data < 0 or pic_data + COSTUME_INFO_SIZE > len(data):
                # NULL or invalid entry — skip but track consecutive failures
                limb_pictures[limb].append(None)
                pic_idx += 1
                consecutive_bad += 1
                if consecutive_bad > 4:
                    # Too many consecutive bad entries — end of table
                    # Remove trailing None entries
                    while limb_pictures[limb] and limb_pictures[limb][-1] is None:
                        limb_pictures[limb].pop()
                    break
                continue

            consecutive_bad = 0

            # Read CostumeInfo header
            w = struct.unpack_from('<H', data, pic_data)[0]
            h = struct.unpack_from('<H', data, pic_data + 2)[0]

            # Sanity check dimensions
            if w == 0 or h == 0 or w > 256 or h > 256:
                break

            rel_x = struct.unpack_from('<h', data, pic_data + 4)[0]
            rel_y = struct.unpack_from('<h', data, pic_data + 6)[0]
            move_x = struct.unpack_from('<h', data, pic_data + 8)[0]
            move_y = struct.unpack_from('<h', data, pic_data + 10)[0]

            # Decode RLE pixel data
            pixels, rle_size = _decode_rle(data, pic_data + COSTUME_INFO_SIZE,
                                           w, h, shr, mask)

            pic = CostumePicture(
                width=w, height=h,
                rel_x=rel_x, rel_y=rel_y,
                move_x=move_x, move_y=move_y,
                pixels=pixels,
                rle_size=rle_size,
            )
            limb_pictures[limb].append(pic)
            pic_idx += 1

            log.debug("  Limb %d pic %d: %dx%d rel=(%d,%d) move=(%d,%d) rle=%d bytes",
                      limb, pic_idx - 1, w, h, rel_x, rel_y, move_x, move_y, rle_size)

    # Parse animations
    animations = []
    for anim_id in range(num_anim):
        off_pos = data_offsets_data + anim_id * 2
        if off_pos + 2 > len(data):
            animations.append(None)
            continue

        anim_bp = struct.unpack_from('<H', data, off_pos)[0]
        anim_data = _bp_to_data(anim_bp)

        if anim_bp == 0 or anim_data < 0 or anim_data + 2 > len(data):
            animations.append(None)
            continue

        # Read limb mask
        limb_mask = struct.unpack_from('<H', data, anim_data)[0]
        pos = anim_data + 2
        limb_cmds = {}

        for limb in range(NUM_LIMBS):
            if limb_mask & (0x8000 >> limb):
                if pos + 2 > len(data):
                    break
                j = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                if j != 0xFFFF:
                    if pos < len(data):
                        extra = data[pos]
                        pos += 1
                        cmd_end = j + (extra & 0x7F)
                        looping = bool(extra & 0x80)
                        limb_cmds[limb] = (j, cmd_end, looping)
                    else:
                        limb_cmds[limb] = (j, j, False)
                else:
                    limb_cmds[limb] = None  # disabled

        animations.append(CostumeAnim(
            anim_id=anim_id,
            limb_mask=limb_mask,
            limb_cmds=limb_cmds,
        ))

    return Costume(
        num_anim=num_anim,
        format=fmt,
        mirror=mirror,
        num_colors=num_colors,
        palette=palette,
        limb_pictures=limb_pictures,
        animations=animations,
        anim_cmds=anim_cmds,
        raw_data=data,
    )


def _decode_rle(data: bytes, start: int, width: int, height: int,
                shr: int, mask: int) -> Tuple[np.ndarray, int]:
    """Decode RLE-compressed column data into a pixel array.

    ScummVM carries leftover run lengths across column boundaries — when a run
    extends past the bottom of a column, the remaining length becomes the start
    of the next column. This is critical for correct decoding.

    Returns:
        (pixels, rle_bytes_consumed) where pixels is (height, width) uint8 array.
    """
    pixels = np.zeros((height, width), dtype=np.uint8)
    src = start
    rep_len = 0
    rep_color = 0

    for col in range(width):
        row = 0
        while row < height:
            if rep_len == 0:
                # Need to read a new RLE entry
                if src >= len(data):
                    log.debug("RLE: end of data at col=%d/%d, row=%d/%d", col, width, row, height)
                    return pixels, src - start

                b = data[src]
                src += 1
                rep_color = b >> shr
                rep_len = b & mask

                if rep_len == 0:
                    if src >= len(data):
                        log.debug("RLE: end of data reading extended length at col=%d", col)
                        return pixels, src - start
                    rep_len = data[src]
                    src += 1

            # Draw as many pixels as fit in this column
            draw = min(rep_len, height - row)
            if rep_color != 0:
                pixels[row:row + draw, col] = rep_color
            row += draw
            rep_len -= draw

    return pixels, src - start


def get_anim_frame_picture(costume: Costume, anim_id: int,
                           frame_idx: int = 0) -> Optional[CostumePicture]:
    """Get the picture for a specific animation + frame.

    For v5 costumes, animations map to limb 0 (full-body) pictures via
    the animation command table.

    Args:
        costume: Parsed Costume object.
        anim_id: Animation ID (0..num_anim-1). In SCUMM, anim = direction + frame*4.
        frame_idx: Frame within the animation sequence (default 0 = first frame).

    Returns:
        CostumePicture or None if animation/frame not found.
    """
    if anim_id >= len(costume.animations) or costume.animations[anim_id] is None:
        return None

    anim = costume.animations[anim_id]

    # Find the first active limb with picture data
    for limb in range(NUM_LIMBS):
        if limb not in anim.limb_cmds or anim.limb_cmds[limb] is None:
            continue

        cmd_start, cmd_end, looping = anim.limb_cmds[limb]

        if not costume.limb_pictures[limb]:
            continue

        # Read the animation command at cmd_start + frame_idx
        cmd_pos = cmd_start + frame_idx
        if cmd_pos > cmd_end:
            if looping:
                cmd_pos = cmd_start + (frame_idx % (cmd_end - cmd_start + 1))
            else:
                cmd_pos = cmd_end

        if cmd_pos >= len(costume.anim_cmds):
            continue

        pic_num = costume.anim_cmds[cmd_pos] & 0x7F

        # Skip special commands
        if pic_num in (ANIM_CMD_START & 0x7F, ANIM_CMD_STOP & 0x7F,
                       ANIM_CMD_HIDE & 0x7F):
            continue

        if pic_num < len(costume.limb_pictures[limb]):
            pic = costume.limb_pictures[limb][pic_num]
            if pic is not None:
                return pic

    return None


def load_vga_palette(palette_path: Path) -> List[Tuple[int, int, int]]:
    """Load a 256-color VGA palette from a raw 768-byte .bin file."""
    data = palette_path.read_bytes()
    if len(data) < 768:
        raise ValueError(f"Palette file too small: {len(data)} bytes (expected 768)")
    palette = []
    for i in range(256):
        r, g, b = data[i * 3], data[i * 3 + 1], data[i * 3 + 2]
        palette.append((r, g, b))
    return palette


def render_picture_png(pic: CostumePicture, costume: Costume,
                       vga_palette: Optional[List[Tuple[int, int, int]]] = None,
                       scale: int = 4) -> 'Image.Image':
    """Render a CostumePicture to a PIL Image.

    Args:
        pic: Decoded picture to render.
        costume: Parent costume (for palette mapping).
        vga_palette: Optional 256-color VGA palette for true-color rendering.
        scale: Pixel scaling factor for visibility.

    Returns:
        PIL Image in RGBA mode.
    """
    from PIL import Image

    img = Image.new('RGBA', (pic.width * scale, pic.height * scale), (0, 0, 0, 0))
    img_pixels = img.load()

    for y in range(pic.height):
        for x in range(pic.width):
            cidx = int(pic.pixels[y, x])
            if cidx == 0:
                continue  # transparent

            # Map costume palette index to global palette
            if cidx < len(costume.palette) and vga_palette:
                global_idx = costume.palette[cidx]
                if global_idx < len(vga_palette):
                    r, g, b = vga_palette[global_idx]
                else:
                    r = g = b = (cidx * 17) & 255
            else:
                # Fallback: grayscale based on index
                r = g = b = (cidx * 17) & 255

            for sy in range(scale):
                for sx in range(scale):
                    img_pixels[x * scale + sx, y * scale + sy] = (r, g, b, 255)

    return img


def render_costume_sheet(costume: Costume,
                         vga_palette: Optional[List[Tuple[int, int, int]]] = None,
                         scale: int = 3) -> 'Image.Image':
    """Render all pictures of limb 0 as a sprite sheet."""
    from PIL import Image

    pictures = costume.limb_pictures[0]
    if not pictures:
        return Image.new('RGBA', (1, 1))

    # Calculate sheet layout
    cols = min(8, len(pictures))
    rows = (len(pictures) + cols - 1) // cols

    max_w = max(p.width for p in pictures)
    max_h = max(p.height for p in pictures)
    cell_w = (max_w + 2) * scale
    cell_h = (max_h + 2) * scale

    sheet = Image.new('RGBA', (cols * cell_w, rows * cell_h), (40, 40, 40, 255))

    for i, pic in enumerate(pictures):
        col = i % cols
        row = i // cols
        img = render_picture_png(pic, costume, vga_palette, scale)
        # Center in cell
        ox = col * cell_w + (cell_w - pic.width * scale) // 2
        oy = row * cell_h + (cell_h - pic.height * scale) // 2
        sheet.paste(img, (ox, oy), img)

    return sheet


def list_costume(costume: Costume):
    """Print a summary of costume contents."""
    print(f"Format: 0x{costume.format:02x}  Mirror: {costume.mirror}")
    print(f"Animations: {costume.num_anim}")
    print(f"Palette ({costume.num_colors} colors): "
          f"{' '.join(f'{b:02x}' for b in costume.palette)}")
    print()

    for limb in range(NUM_LIMBS):
        pics = costume.limb_pictures[limb]
        if pics:
            print(f"Limb {limb}: {len(pics)} pictures")
            for i, pic in enumerate(pics):
                if pic is None:
                    print(f"  [{i:2d}] (NULL)")
                    continue
                nz = int(np.count_nonzero(pic.pixels))
                print(f"  [{i:2d}] {pic.width:3d}x{pic.height:3d}  "
                      f"rel=({pic.rel_x:+4d},{pic.rel_y:+4d})  "
                      f"move=({pic.move_x:+3d},{pic.move_y:+3d})  "
                      f"pixels={nz}  rle={pic.rle_size}B")

    print()
    active_anims = [(i, a) for i, a in enumerate(costume.animations) if a is not None]
    print(f"Active animations: {len(active_anims)}/{costume.num_anim}")
    for anim_id, anim in active_anims:
        direction = anim_id % NUM_DIRS
        frame = anim_id // NUM_DIRS
        dir_names = ['S', 'W', 'N', 'E']
        dir_name = dir_names[direction] if direction < len(dir_names) else '?'
        limb_str = ', '.join(
            f"L{l}=[{s}-{e}{'L' if loop else ''}]"
            for l, cmd in sorted(anim.limb_cmds.items())
            if cmd is not None
            for s, e, loop in [cmd]
        )
        print(f"  anim[{anim_id:3d}] frame={frame:2d} dir={dir_name}  "
              f"mask=0x{anim.limb_mask:04x}  {limb_str}")


def main():
    parser = argparse.ArgumentParser(
        description='SCUMM v5 costume decoder')
    parser.add_argument('--costume', required=True,
                        help='Path to costume .bin file')
    parser.add_argument('--palette', default=None,
                        help='Path to 768-byte VGA palette .bin (for colored PNGs)')
    parser.add_argument('--output', default=None,
                        help='Output directory for decoded data')
    parser.add_argument('--list', action='store_true',
                        help='List all animations and pictures')
    parser.add_argument('--all', action='store_true',
                        help='Decode all pictures')
    parser.add_argument('--anim', type=int, default=None,
                        help='Specific animation ID to decode')
    parser.add_argument('--frame', type=int, default=0,
                        help='Frame index within animation (default: 0)')
    parser.add_argument('--limb', type=int, default=0,
                        help='Limb index (default: 0)')
    parser.add_argument('--pic', type=int, default=None,
                        help='Specific picture index for a limb')
    parser.add_argument('--verify', action='store_true',
                        help='Save verification PNGs')
    parser.add_argument('--sheet', action='store_true',
                        help='Render all limb 0 pictures as a sprite sheet')
    parser.add_argument('--scale', type=int, default=4,
                        help='Pixel scale for PNGs (default: 4)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')

    costume_path = Path(args.costume)
    if not costume_path.exists():
        log.error("Costume file not found: %s", costume_path)
        sys.exit(1)

    data = costume_path.read_bytes()
    log.info("Loaded costume: %s (%d bytes)", costume_path.name, len(data))

    costume = parse_costume(data)
    log.info("Parsed: format=0x%02x, %d anims, %d limb-0 pictures",
             costume.format, costume.num_anim,
             len(costume.limb_pictures[0]))

    # Load VGA palette if provided
    vga_palette = None
    if args.palette:
        pal_path = Path(args.palette)
        if pal_path.exists():
            vga_palette = load_vga_palette(pal_path)
            log.info("Loaded VGA palette: %s", pal_path.name)
        else:
            log.warning("Palette file not found: %s", pal_path)

    # Create output directory
    output_dir = None
    if args.output:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
    elif args.verify or args.all or args.sheet:
        output_dir = costume_path.parent / (costume_path.stem + '_decoded')
        output_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        list_costume(costume)
        return

    if args.sheet:
        from PIL import Image
        sheet = render_costume_sheet(costume, vga_palette, args.scale)
        out_path = output_dir / 'sprite_sheet.png' if output_dir else Path('sprite_sheet.png')
        sheet.save(str(out_path))
        print(f"Saved sprite sheet: {out_path} ({sheet.width}x{sheet.height})")
        return

    if args.all:
        # Decode and save all pictures for all limbs
        total = 0
        for limb in range(NUM_LIMBS):
            pictures = costume.limb_pictures[limb]
            if not pictures:
                continue
            for i, pic in enumerate(pictures):
                if output_dir:
                    # Save raw indexed pixels
                    raw_path = output_dir / f'limb{limb}_pic{i:02d}.bin'
                    raw_path.write_bytes(pic.pixels.tobytes())

                    # Save metadata
                    meta = {
                        'width': pic.width, 'height': pic.height,
                        'rel_x': pic.rel_x, 'rel_y': pic.rel_y,
                        'move_x': pic.move_x, 'move_y': pic.move_y,
                        'rle_size': pic.rle_size,
                        'palette_indices': list(costume.palette),
                    }
                    meta_path = output_dir / f'limb{limb}_pic{i:02d}.json'
                    meta_path.write_text(json.dumps(meta, indent=2))

                    if args.verify:
                        img = render_picture_png(pic, costume, vga_palette, args.scale)
                        png_path = output_dir / f'limb{limb}_pic{i:02d}.png'
                        img.save(str(png_path))

                total += 1
                print(f"  Limb {limb} pic {i}: {pic.width}x{pic.height}")

        print(f"\nDecoded {total} pictures")
        if output_dir:
            print(f"Output: {output_dir}")
        return

    if args.pic is not None:
        # Decode specific picture from specific limb
        limb = args.limb
        pic_idx = args.pic
        if limb >= NUM_LIMBS or not costume.limb_pictures[limb]:
            log.error("Limb %d has no pictures", limb)
            sys.exit(1)
        if pic_idx >= len(costume.limb_pictures[limb]):
            log.error("Limb %d only has %d pictures",
                      limb, len(costume.limb_pictures[limb]))
            sys.exit(1)

        pic = costume.limb_pictures[limb][pic_idx]
        print(f"Limb {limb} picture {pic_idx}: {pic.width}x{pic.height}")
        print(f"  rel=({pic.rel_x},{pic.rel_y}) move=({pic.move_x},{pic.move_y})")
        print(f"  RLE: {pic.rle_size} bytes")
        print(f"  Non-zero pixels: {int(np.count_nonzero(pic.pixels))}")

        if args.verify and output_dir:
            img = render_picture_png(pic, costume, vga_palette, args.scale)
            png_path = output_dir / f'limb{limb}_pic{pic_idx:02d}.png'
            img.save(str(png_path))
            print(f"  Saved: {png_path}")
        return

    if args.anim is not None:
        # Decode specific animation frame
        pic = get_anim_frame_picture(costume, args.anim, args.frame)
        if pic is None:
            log.error("No picture found for anim=%d frame=%d", args.anim, args.frame)
            sys.exit(1)

        print(f"Anim {args.anim} frame {args.frame}: {pic.width}x{pic.height}")
        print(f"  rel=({pic.rel_x},{pic.rel_y}) move=({pic.move_x},{pic.move_y})")

        if args.verify and output_dir:
            img = render_picture_png(pic, costume, vga_palette, args.scale)
            png_path = output_dir / f'anim{args.anim:03d}_frame{args.frame:02d}.png'
            img.save(str(png_path))
            print(f"  Saved: {png_path}")
        return

    # Default: decode first picture of limb 0
    if costume.limb_pictures[0]:
        pic = costume.limb_pictures[0][0]
        print(f"Default: Limb 0 picture 0: {pic.width}x{pic.height}")
        print(f"  rel=({pic.rel_x},{pic.rel_y}) move=({pic.move_x},{pic.move_y})")
        print(f"  Non-zero pixels: {int(np.count_nonzero(pic.pixels))}")
        print(f"  Colors used: {sorted(set(pic.pixels.flat))}")

        if args.verify and output_dir:
            img = render_picture_png(pic, costume, vga_palette, args.scale)
            png_path = output_dir / 'limb0_pic00.png'
            img.save(str(png_path))
            print(f"  Saved: {png_path}")
    else:
        print("No pictures found in limb 0")


if __name__ == '__main__':
    main()
