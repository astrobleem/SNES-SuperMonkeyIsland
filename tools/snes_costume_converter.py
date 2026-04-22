#!/usr/bin/env python3
"""SNES costume converter — converts decoded SCUMM costume frames to SNES sprite format.

Takes decoded costume pictures (from scumm_costume_decoder.py) and converts them
to SNES-native sprite data:
  - 4bpp planar CHR tiles (32 bytes/tile)
  - BGR555 palette (32 bytes = 16 colors)
  - OAM layout table: per-tile (dx, dy, tile_id, attr) entries

SNES sprites use OBJ tiles with 4bpp color depth and 16-color sub-palettes.
Each frame is split into 8x8 tiles, deduped, and encoded.

Usage:
    # Convert Guybrush's standing-south frame
    python tools/snes_costume_converter.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \\
        --anim 4 --frame 0 \\
        --output data/snes_converted/costumes/cost_001

    # Convert all pictures in a costume
    python tools/snes_costume_converter.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \\
        --all \\
        --output data/snes_converted/costumes/cost_001

    # Convert with verification PNG
    python tools/snes_costume_converter.py \\
        --costume data/scumm_extracted/costumes/cost_001_room002.bin \\
        --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \\
        --all --verify \\
        --output data/snes_converted/costumes/cost_001
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

log = logging.getLogger('snes_costume_converter')

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# SNES sprite constants
TILE_SIZE = 8
BYTES_PER_4BPP_TILE = 32
COLORS_PER_SPRITE_PAL = 16  # 4bpp = 16 colors including transparent


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


def encode_4bpp_tile(indexed_tile):
    """Encode 8x8 indexed pixels (0-15) to 32-byte SNES 4bpp planar.

    SNES 4bpp format: rows 0-7, each row has bitplane0+bitplane1 (bytes 0-15),
    then bitplane2+bitplane3 (bytes 16-31).
    """
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


@dataclass
class OamEntry:
    """A single OAM sprite entry for tile placement."""
    dx: int       # X offset from actor origin (signed)
    dy: int       # Y offset from actor origin (signed)
    tile_id: int  # index into CHR tile data
    hflip: bool   # horizontal flip
    vflip: bool   # vertical flip

    def to_bytes(self) -> bytes:
        """Encode as 4 bytes: dx(s8), dy(s8), tile_id(u8), flags(u8).

        Flags byte: bit 6 = hflip, bit 7 = vflip.
        """
        dx_byte = self.dx & 0xFF  # signed -> unsigned byte
        dy_byte = self.dy & 0xFF
        flags = 0
        if self.hflip:
            flags |= 0x40
        if self.vflip:
            flags |= 0x80
        return struct.pack('BBBx', dx_byte, dy_byte, self.tile_id)

    def to_bytes_full(self) -> bytes:
        """Encode as 4 bytes: dx(s8), dy(s8), tile_id(u8), flags(u8)."""
        dx_byte = self.dx & 0xFF
        dy_byte = self.dy & 0xFF
        flags = 0
        if self.hflip:
            flags |= 0x40
        if self.vflip:
            flags |= 0x80
        return struct.pack('BBBB', dx_byte, dy_byte, self.tile_id, flags)


@dataclass
class ConvertedFrame:
    """A costume frame converted to SNES sprite format."""
    width: int
    height: int
    rel_x: int
    rel_y: int
    tiles_wide: int
    tiles_tall: int
    chr_data: bytes       # concatenated 4bpp tile data
    num_tiles: int        # number of unique tiles in chr_data
    oam_entries: List[OamEntry]
    palette_bgr555: List[int]  # 16 BGR555 values


def build_sprite_palette(costume_palette: bytes,
                         vga_palette: List[Tuple[int, int, int]]) -> List[int]:
    """Build a 16-color SNES BGR555 palette from costume + VGA palettes.

    Color 0 is always transparent (0x0000).
    Colors 1-15 map through the costume palette into the VGA palette, then
    convert to BGR555.
    """
    snes_pal = [0x0000]  # index 0 = transparent

    for i in range(1, COLORS_PER_SPRITE_PAL):
        if i < len(costume_palette):
            vga_idx = costume_palette[i]
            if vga_idx < len(vga_palette):
                r, g, b = vga_palette[vga_idx]
            else:
                r = g = b = 0
        else:
            r = g = b = 0
        snes_pal.append(rgb_to_bgr555(r, g, b))

    return snes_pal


def convert_frame(pixels: np.ndarray, width: int, height: int,
                  rel_x: int, rel_y: int,
                  palette_bgr555: List[int]) -> ConvertedFrame:
    """Convert a decoded costume frame to SNES sprite tiles.

    Args:
        pixels: (height, width) uint8 array of palette indices (0-15).
        width: Frame width in pixels.
        height: Frame height in pixels.
        rel_x: Anchor X offset.
        rel_y: Anchor Y offset.
        palette_bgr555: 16-entry BGR555 palette.

    Returns:
        ConvertedFrame with CHR data, OAM layout, and palette.
    """
    # Pad frame to multiple of 8 in both dimensions
    tiles_wide = (width + TILE_SIZE - 1) // TILE_SIZE
    tiles_tall = (height + TILE_SIZE - 1) // TILE_SIZE
    padded_w = tiles_wide * TILE_SIZE
    padded_h = tiles_tall * TILE_SIZE

    padded = np.zeros((padded_h, padded_w), dtype=np.uint8)
    padded[:height, :width] = pixels

    # Detect and remove costume background color. SCUMM costume frames are
    # filled with opaque "background" pixels that match the PC's sky/room
    # color. On SNES OAM, only pixel 0 is transparent, so these opaque
    # background pixels show as visible rectangles. Detect the background
    # by sampling the frame border — the most common non-zero value there
    # is the background color. Remap it to 0 (transparent).
    border = np.concatenate([
        pixels[0, :],                    # top row
        pixels[-1, :],                   # bottom row
        pixels[1:-1, 0],                 # left column (excluding corners)
        pixels[1:-1, -1],               # right column (excluding corners)
    ]) if height > 2 and width > 2 else pixels.ravel()
    zero_border = np.sum(border == 0)
    if zero_border > len(border) * 0.5:
        bg_colors = set(border[border != 0].tolist())
        if bg_colors:
            for bg_color in bg_colors:
                pixels[pixels == bg_color] = 0
            padded[:height, :width] = pixels

    # Split into 8x8 tiles and deduplicate
    tile_data_list = []       # unique tile bytes
    tile_lookup = {}          # tile_bytes -> (tile_id, hflip, vflip)
    oam_entries = []

    # Empty tile for skip detection
    empty_tile = bytes(BYTES_PER_4BPP_TILE)

    for ty in range(tiles_tall):
        for tx in range(tiles_wide):
            # Extract 8x8 tile
            y0 = ty * TILE_SIZE
            x0 = tx * TILE_SIZE
            tile_pixels = padded[y0:y0 + TILE_SIZE, x0:x0 + TILE_SIZE]

            # Skip fully transparent tiles
            if not np.any(tile_pixels):
                continue

            # Encode to 4bpp
            tile_bytes = encode_4bpp_tile(tile_pixels)

            # Check for H-flip and V-flip duplicates
            tile_id = None
            hflip = False
            vflip = False

            # Try original
            if tile_bytes in tile_lookup:
                tile_id, hflip, vflip = tile_lookup[tile_bytes]
            else:
                # Try H-flip
                hflipped = tile_pixels[:, ::-1]
                hflip_bytes = encode_4bpp_tile(hflipped)
                if hflip_bytes in tile_lookup:
                    tile_id, _, base_vflip = tile_lookup[hflip_bytes]
                    hflip = True
                    vflip = base_vflip
                else:
                    # Try V-flip
                    vflipped = tile_pixels[::-1, :]
                    vflip_bytes = encode_4bpp_tile(vflipped)
                    if vflip_bytes in tile_lookup:
                        tile_id, base_hflip, _ = tile_lookup[vflip_bytes]
                        hflip = base_hflip
                        vflip = True
                    else:
                        # Try HV-flip
                        hvflipped = tile_pixels[::-1, ::-1]
                        hvflip_bytes = encode_4bpp_tile(hvflipped)
                        if hvflip_bytes in tile_lookup:
                            tile_id, _, _ = tile_lookup[hvflip_bytes]
                            hflip = True
                            vflip = True

            # New unique tile
            if tile_id is None:
                tile_id = len(tile_data_list)
                tile_data_list.append(tile_bytes)
                tile_lookup[tile_bytes] = (tile_id, False, False)
                hflip = False
                vflip = False

            # OAM position relative to actor anchor
            dx = x0 + rel_x
            dy = y0 + rel_y

            oam_entries.append(OamEntry(
                dx=dx, dy=dy,
                tile_id=tile_id,
                hflip=hflip, vflip=vflip,
            ))

    chr_data = b''.join(tile_data_list)

    return ConvertedFrame(
        width=width, height=height,
        rel_x=rel_x, rel_y=rel_y,
        tiles_wide=tiles_wide, tiles_tall=tiles_tall,
        chr_data=chr_data,
        num_tiles=len(tile_data_list),
        oam_entries=oam_entries,
        palette_bgr555=palette_bgr555,
    )


def save_frame(frame: ConvertedFrame, output_dir: Path, prefix: str):
    """Save a converted frame to binary files.

    Output files:
        {prefix}.chr  — 4bpp tile data (N * 32 bytes)
        {prefix}.pal  — BGR555 palette (32 bytes = 16 * 2)
        {prefix}.oam  — OAM layout: header (6B) + entries (4B each)
        {prefix}.json — metadata
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # CHR tile data
    chr_path = output_dir / f'{prefix}.chr'
    chr_path.write_bytes(frame.chr_data)

    # Palette
    pal_path = output_dir / f'{prefix}.pal'
    pal_bytes = bytearray()
    for c in frame.palette_bgr555:
        pal_bytes += struct.pack('<H', c & 0x7FFF)
    pal_path.write_bytes(bytes(pal_bytes))

    # OAM layout
    # Header: num_entries (u8), width (u8), height (u8), rel_x (s8), rel_y (s8), pad (u8)
    oam_path = output_dir / f'{prefix}.oam'
    oam_bytes = bytearray()
    oam_bytes += struct.pack('BBBbbB',
                             len(frame.oam_entries),
                             frame.width, frame.height,
                             max(-128, min(127, frame.rel_x)),
                             max(-128, min(127, frame.rel_y)),
                             0)
    for entry in frame.oam_entries:
        oam_bytes += entry.to_bytes_full()
    oam_path.write_bytes(bytes(oam_bytes))

    # Metadata JSON
    meta = {
        'width': frame.width,
        'height': frame.height,
        'rel_x': frame.rel_x,
        'rel_y': frame.rel_y,
        'tiles_wide': frame.tiles_wide,
        'tiles_tall': frame.tiles_tall,
        'num_tiles': frame.num_tiles,
        'chr_size': len(frame.chr_data),
        'oam_entries': len(frame.oam_entries),
        'palette': [f'0x{c:04x}' for c in frame.palette_bgr555],
    }
    meta_path = output_dir / f'{prefix}.json'
    meta_path.write_text(json.dumps(meta, indent=2))

    return chr_path, pal_path, oam_path


def render_verification_png(frame: ConvertedFrame, scale: int = 4) -> 'Image.Image':
    """Reconstruct the frame from SNES tile data for visual verification."""
    from PIL import Image

    w = frame.tiles_wide * TILE_SIZE
    h = frame.tiles_tall * TILE_SIZE
    img = Image.new('RGBA', (w * scale, h * scale), (0, 0, 0, 0))
    pixels = img.load()

    for entry in frame.oam_entries:
        # Tile position in padded image
        tx = entry.dx - frame.rel_x
        ty = entry.dy - frame.rel_y

        # Decode tile from CHR data
        tile_offset = entry.tile_id * BYTES_PER_4BPP_TILE
        tile_data = frame.chr_data[tile_offset:tile_offset + BYTES_PER_4BPP_TILE]
        if len(tile_data) < BYTES_PER_4BPP_TILE:
            continue

        for row in range(TILE_SIZE):
            bp0 = tile_data[row * 2]
            bp1 = tile_data[row * 2 + 1]
            bp2 = tile_data[16 + row * 2]
            bp3 = tile_data[16 + row * 2 + 1]

            for col in range(TILE_SIZE):
                bit = 7 - col
                idx = (((bp0 >> bit) & 1) |
                       (((bp1 >> bit) & 1) << 1) |
                       (((bp2 >> bit) & 1) << 2) |
                       (((bp3 >> bit) & 1) << 3))

                if idx == 0:
                    continue

                # Apply flip
                px_col = (TILE_SIZE - 1 - col) if entry.hflip else col
                px_row = (TILE_SIZE - 1 - row) if entry.vflip else row

                # Map to image coordinates
                ix = tx + px_col
                iy = ty + px_row

                if 0 <= ix < w and 0 <= iy < h:
                    bgr = frame.palette_bgr555[idx] if idx < len(frame.palette_bgr555) else 0
                    r, g, b = bgr555_to_rgb(bgr)
                    for sy in range(scale):
                        for sx in range(scale):
                            pixels[ix * scale + sx, iy * scale + sy] = (r, g, b, 255)

    return img


def main():
    parser = argparse.ArgumentParser(
        description='SNES costume sprite converter')
    parser.add_argument('--costume', required=True,
                        help='Path to costume .bin file')
    parser.add_argument('--palette', required=True,
                        help='Path to 768-byte VGA palette .bin')
    parser.add_argument('--output', required=True,
                        help='Output directory')
    parser.add_argument('--all', action='store_true',
                        help='Convert all limb-0 pictures')
    parser.add_argument('--anim', type=int, default=None,
                        help='Specific animation ID')
    parser.add_argument('--frame', type=int, default=0,
                        help='Frame index within animation')
    parser.add_argument('--pic', type=int, default=None,
                        help='Specific picture index (limb 0)')
    parser.add_argument('--verify', action='store_true',
                        help='Save verification PNGs')
    parser.add_argument('--scale', type=int, default=4,
                        help='Pixel scale for verification PNGs')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')

    # Import costume decoder
    sys.path.insert(0, str(Path(__file__).parent))
    from scumm_costume_decoder import (parse_costume, load_vga_palette,
                                       get_anim_frame_picture)

    costume_path = Path(args.costume)
    palette_path = Path(args.palette)
    output_dir = Path(args.output)

    if not costume_path.exists():
        log.error("Costume file not found: %s", costume_path)
        sys.exit(1)
    if not palette_path.exists():
        log.error("Palette file not found: %s", palette_path)
        sys.exit(1)

    # Parse costume
    data = costume_path.read_bytes()
    costume = parse_costume(data)
    vga_palette = load_vga_palette(palette_path)

    # Build SNES palette
    snes_pal = build_sprite_palette(costume.palette, vga_palette)

    log.info("Costume: format=0x%02x, %d limb-0 pictures, %d colors",
             costume.format, len(costume.limb_pictures[0]), costume.num_colors)
    log.info("SNES palette: %s",
             ' '.join(f'{c:04x}' for c in snes_pal))

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save shared palette
    pal_bytes = bytearray()
    for c in snes_pal:
        pal_bytes += struct.pack('<H', c & 0x7FFF)
    (output_dir / 'palette.pal').write_bytes(bytes(pal_bytes))

    if args.all:
        # Use limb 0 if it has pictures; otherwise collect from all limbs
        pictures = costume.limb_pictures[0]
        if not pictures:
            log.info("Limb 0 empty, collecting pictures from all limbs")
            pictures = []
            for limb_idx, limb_pics in enumerate(costume.limb_pictures):
                for pic in limb_pics:
                    pictures.append(pic)
            if pictures:
                log.info("Found %d pictures across %d limbs",
                         len(pictures),
                         sum(1 for lp in costume.limb_pictures if lp))

        total_tiles = 0
        total_oam = 0

        for i, pic in enumerate(pictures):
            if pic is None:
                log.debug("Skipping NULL pic %d", i)
                continue
            # Skip corrupt/sentinel frames (dimensions or offsets out of SNES range)
            if pic.width > 128 or pic.height > 128 or pic.width <= 0 or pic.height <= 0 or \
               pic.rel_x > 127 or pic.rel_y > 127:
                log.warning("Skipping corrupt body pic %d: %dx%d rel=(%d,%d)",
                            i, pic.width, pic.height, pic.rel_x, pic.rel_y)
                continue
            frame = convert_frame(pic.pixels, pic.width, pic.height,
                                  pic.rel_x, pic.rel_y, snes_pal)
            prefix = f'pic{i:02d}'
            save_frame(frame, output_dir, prefix)
            total_tiles += frame.num_tiles
            total_oam += len(frame.oam_entries)

            if args.verify:
                from PIL import Image
                img = render_verification_png(frame, args.scale)
                bg = Image.new('RGBA', img.size, (100, 180, 255, 255))
                bg.paste(img, (0, 0), img)
                bg.save(str(output_dir / f'{prefix}_verify.png'))

            print(f"  pic[{i:2d}] {pic.width:3d}x{pic.height:3d}  "
                  f"tiles={frame.num_tiles:3d} ({len(frame.chr_data):5d}B)  "
                  f"oam={len(frame.oam_entries):3d}  "
                  f"dedup={frame.tiles_wide * frame.tiles_tall - frame.num_tiles}")

        print(f"\nConverted {len(pictures)} limb-0 pictures:")
        print(f"  Total unique tiles: {total_tiles}")
        print(f"  Total CHR size: {total_tiles * BYTES_PER_4BPP_TILE} bytes")
        print(f"  Total OAM entries: {total_oam}")
        print(f"  Output: {output_dir}")

        # --- Convert Limb 1 head pics (if available) ---
        head_pictures = (costume.limb_pictures[1]
                         if len(costume.limb_pictures) > 1 else [])
        if head_pictures:
            head_tiles = 0
            head_oam = 0
            head_count = 0
            for i, pic in enumerate(head_pictures):
                if pic is None:
                    log.debug("Skipping NULL head pic %d", i)
                    continue
                # Skip corrupt/sentinel frames (dimensions or offsets out of SNES range)
                if pic.width > 128 or pic.height > 128 or pic.width <= 0 or pic.height <= 0 or \
                   pic.rel_x > 127 or pic.rel_y > 127:
                    log.warning("Skipping corrupt head pic %d: %dx%d rel=(%d,%d)",
                                i, pic.width, pic.height, pic.rel_x, pic.rel_y)
                    continue
                frame = convert_frame(pic.pixels, pic.width, pic.height,
                                      pic.rel_x, pic.rel_y, snes_pal)
                prefix = f'head_pic{i:02d}'
                save_frame(frame, output_dir, prefix)
                head_tiles += frame.num_tiles
                head_oam += len(frame.oam_entries)
                head_count += 1

                if args.verify:
                    from PIL import Image
                    img = render_verification_png(frame, args.scale)
                    bg = Image.new('RGBA', img.size, (100, 180, 255, 255))
                    bg.paste(img, (0, 0), img)
                    bg.save(str(output_dir / f'{prefix}_verify.png'))

                print(f"  head[{i:2d}] {pic.width:3d}x{pic.height:3d}  "
                      f"tiles={frame.num_tiles:3d} ({len(frame.chr_data):5d}B)  "
                      f"oam={len(frame.oam_entries):3d}")

            if head_count:
                print(f"\nConverted {head_count} limb-1 head pictures:")
                print(f"  Total head tiles: {head_tiles}")
                print(f"  Total head CHR size: "
                      f"{head_tiles * BYTES_PER_4BPP_TILE} bytes")
                print(f"  Total head OAM entries: {head_oam}")

        return

    if args.pic is not None:
        pictures = costume.limb_pictures[0]
        if args.pic >= len(pictures):
            log.error("Picture %d not found (max %d)", args.pic, len(pictures) - 1)
            sys.exit(1)
        pic = pictures[args.pic]
        frame = convert_frame(pic.pixels, pic.width, pic.height,
                              pic.rel_x, pic.rel_y, snes_pal)
        prefix = f'pic{args.pic:02d}'
        save_frame(frame, output_dir, prefix)
        print(f"Converted pic {args.pic}: {pic.width}x{pic.height}, "
              f"{frame.num_tiles} tiles, {len(frame.oam_entries)} OAM entries")

        if args.verify:
            from PIL import Image
            img = render_verification_png(frame, args.scale)
            bg = Image.new('RGBA', img.size, (100, 180, 255, 255))
            bg.paste(img, (0, 0), img)
            bg.save(str(output_dir / f'{prefix}_verify.png'))
            print(f"  Saved verify: {output_dir / f'{prefix}_verify.png'}")
        return

    if args.anim is not None:
        pic = get_anim_frame_picture(costume, args.anim, args.frame)
        if pic is None:
            log.error("No picture for anim=%d frame=%d", args.anim, args.frame)
            sys.exit(1)
        frame = convert_frame(pic.pixels, pic.width, pic.height,
                              pic.rel_x, pic.rel_y, snes_pal)
        prefix = f'anim{args.anim:03d}_f{args.frame:02d}'
        save_frame(frame, output_dir, prefix)
        print(f"Converted anim {args.anim} frame {args.frame}: "
              f"{pic.width}x{pic.height}, {frame.num_tiles} tiles")

        if args.verify:
            from PIL import Image
            img = render_verification_png(frame, args.scale)
            bg = Image.new('RGBA', img.size, (100, 180, 255, 255))
            bg.paste(img, (0, 0), img)
            bg.save(str(output_dir / f'{prefix}_verify.png'))
        return

    # Default: convert first picture
    if costume.limb_pictures[0]:
        pic = costume.limb_pictures[0][0]
        frame = convert_frame(pic.pixels, pic.width, pic.height,
                              pic.rel_x, pic.rel_y, snes_pal)
        save_frame(frame, output_dir, 'pic00')
        print(f"Converted pic 0: {pic.width}x{pic.height}, "
              f"{frame.num_tiles} tiles, {len(frame.oam_entries)} OAM entries")


if __name__ == '__main__':
    main()
