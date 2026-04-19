"""SCUMM CYCL → SNES .cyc blob conversion.

For each color cycle defined in a SCUMM CYCL chunk, builds a descriptor
the SNES runtime can use to rotate CGRAM slots. The tricky part is
remapping PC palette indices (0..255) to the SNES sub-palette layout
that the quantizer produced — a single PC color may land in multiple
sub-palettes (so multiple CGRAM slots rotate in lockstep), or land in
none (quantizer dropped it because no tile used it; its "phantom" color
still rotates through the ring).

Only art sub-palettes (1..5) are cycled. Sub-palettes 0, 6, 7 are
reserved (UI + verb colors) and runtime code overwrites them.
"""

import logging
import struct

log = logging.getLogger(__name__)


def _rgb_to_bgr555(r: int, g: int, b: int) -> int:
    """Standard 8bpc → BGR555 quantization (matches tiledpalettequant)."""
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def _read_pc_palette(palette_bin_path) -> list:
    """Read a 768-byte CLUT (256 × RGB888) into list of 256 BGR555 words."""
    data = palette_bin_path.read_bytes()
    out = []
    for i in range(256):
        off = i * 3
        if off + 3 > len(data):
            out.append(0)
            continue
        out.append(_rgb_to_bgr555(data[off], data[off + 1], data[off + 2]))
    return out


def _read_snes_palette(pal_bin: bytes) -> list:
    """Read a 256-byte .pal blob into 8×16 = 128 BGR555 words.
    Layout: pal0c0, pal0c1, ..., pal7c15. Word index N → subpal=N/16, color=N%16.
    CGRAM word address = subpal * 16 + color (same as N).
    """
    out = []
    for i in range(0, len(pal_bin), 2):
        out.append(struct.unpack_from('<H', pal_bin, i)[0])
    while len(out) < 128:
        out.append(0)
    return out[:128]


# Sub-palettes that are safe to cycle. Pal 0 is UI, pal 6/7 hold verb
# colors that scummvm.writeVerbColors rewrites each frame.
_ART_SUBPALETTES = (1, 2, 3, 4, 5)


def _find_cgram_slots(pc_bgr555: int, snes_words: list) -> list:
    """Return all CGRAM word addresses (0..127) in art sub-palettes whose
    BGR555 value matches pc_bgr555. Color 0 of each sub-palette is the
    transparent slot and is never cycled."""
    if pc_bgr555 == 0:
        return []  # don't cycle pure-transparent (would fight trans key)
    matches = []
    for subpal in _ART_SUBPALETTES:
        base = subpal * 16
        # skip color 0 (transparent)
        for ci in range(1, 16):
            word = base + ci
            if snes_words[word] == pc_bgr555:
                matches.append(word)
    return matches


def build_cycle_blob(cycles: list, pc_pal_words: list, snes_words: list) -> bytes:
    """Convert a list of CYCL entries into the SNES .cyc binary blob.

    cycles: parsed CYCL list from metadata.json (dicts with frames_per_step,
            flags, start, end).
    pc_pal_words: 256 BGR555 words representing the room's original PC CLUT.
    snes_words: 128 BGR555 words for the 8×16 SNES palette after quantization.

    Blob layout:
        [1 byte]  num_cycles (0 = no data, but we still emit the byte)
        per cycle:
          [1 byte]  frames_per_step   (clamped to 1..255)
          [1 byte]  flags             (low byte; bit 1 = reverse)
          [1 byte]  num_positions     (1..255 — PC range length)
          per position:
            [2 bytes LE] initial_color (BGR555 pulled from PC CLUT)
            [1 byte]     num_slots    (0..N CGRAM slots sharing this color)
            [num_slots]  CGRAM word addresses (0..127)
    """
    if not cycles:
        return b'\x00'

    # Filter: drop no-op cycles where start >= end (SCUMM treats as disabled)
    # or frames_per_step == 0 (undefined). Keep forward AND reverse.
    active = [c for c in cycles
              if c.get('start', 0) < c.get('end', 0)
              and c.get('frames_per_step', 0) > 0]
    if not active:
        return b'\x00'

    # A cycle list that's too big (>255 cycles) can't happen: CYCL max 16.
    # Each individual cycle can span up to 256 PC slots, which would be huge
    # but rare; we clamp num_positions to 255 since a byte isn't enough for
    # 256. In practice the max in MI1 is 16.
    out = bytearray()
    out.append(len(active))

    for c in active:
        fps = min(max(c['frames_per_step'], 1), 255)
        flags = c['flags'] & 0xFF
        start = c['start']
        end = c['end']
        num_positions = end - start + 1
        if num_positions > 255:
            log.warning("Cycle spans %d colors, clamping to 255", num_positions)
            num_positions = 255
            end = start + 254

        out.append(fps)
        out.append(flags)
        out.append(num_positions)

        total_slots = 0
        for pc in range(start, end + 1):
            color = pc_pal_words[pc] if pc < 256 else 0
            slots = _find_cgram_slots(color, snes_words)
            out += struct.pack('<H', color)
            out.append(len(slots))
            out += bytes(slots)
            total_slots += len(slots)

        log.info("  Cycle fps=%d flags=%02x pc[%d..%d]: %d positions, %d CGRAM slots",
                 fps, flags, start, end, num_positions, total_slots)

    return bytes(out)


def build_cycle_blob_from_files(metadata, pal_bin_path, pc_palette_bin_path) -> bytes:
    """Convenience wrapper: read the SNES .pal and PC palette.bin, build the blob."""
    cycles = metadata.get('color_cycling', [])
    if not cycles:
        return b'\x00'
    if not pc_palette_bin_path.exists():
        log.warning("No palette.bin for cycling — emitting empty")
        return b'\x00'
    pc_pal = _read_pc_palette(pc_palette_bin_path)
    snes = _read_snes_palette(pal_bin_path.read_bytes()) if hasattr(pal_bin_path, 'read_bytes') \
           else _read_snes_palette(pal_bin_path)
    return build_cycle_blob(cycles, pc_pal, snes)
