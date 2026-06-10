#!/usr/bin/env python3
"""Apply text/position patches to extracted SCUMM script binaries.

Reads a patches manifest (tools/scumm_patches.json) and rewrites matching
print ops inside the extracted script .bin files. Patched outputs land in
build/scumm_patched_scripts/ mirroring the source tree under
data/scumm_extracted/.

rom_pack_data.py picks these up via --scripts-override-dir; any file not
listed in the manifest is read from the original location.
"""

import argparse
import json
import logging
import sys
from pathlib import Path


# SCUMM v5 print sub-op byte codes (cross-referenced with
# ScummVM engines/scumm/script_v5.cpp:3519+).
SUBOP_POS       = 0x00    # 2x word
SUBOP_COLOR     = 0x01    # byte or word (BIT7 variant)
SUBOP_CLIPPING  = 0x02    # word
SUBOP_ERASE     = 0x03    # 2x word (unused in our engine)
SUBOP_CENTER    = 0x04    # zero-arg
SUBOP_LEFT      = 0x06    # zero-arg
SUBOP_OVERHEAD  = 0x07    # zero-arg
SUBOP_SAY_VOICE = 0x08    # 2x word (talkie, unused in our engine)
SUBOP_TEXT      = 0x0F    # terminal, followed by bytes until $00
SUBOP_END       = 0xFF

PRINT_OPCODE    = 0x14


def encode_text(s: str) -> bytes:
    """Convert a Python string to SCUMM text bytes.

    '\\n' becomes the SCUMM newline escape $FF $01 (first-line-only cutoff
    in our renderer). Other bytes pass through as latin-1.
    """
    return s.replace('\n', '\xff\x01').encode('latin-1')


def find_text_terminator(src: bytes, op_offset: int) -> int:
    """Return the offset of the $00 string terminator ending the given print op.

    Walks the sub-op sequence starting at op_offset+2 (after $14 + actor byte),
    honoring known sub-op argument sizes, until it hits $0F (text) and the
    string terminator that follows.
    """
    assert src[op_offset] == PRINT_OPCODE, (
        f"Expected $14 at offset {op_offset:#x}, got {src[op_offset]:#x}"
    )
    i = op_offset + 2   # skip opcode + actor byte
    while True:
        sub = src[i]
        if sub == SUBOP_END:
            raise ValueError(
                f"Print op at {op_offset:#x} ends with $FF before $0F text — "
                "manifest entry probably targets the wrong op."
            )
        if sub == SUBOP_TEXT:
            text_start = i + 1
            try:
                return src.index(0x00, text_start)
            except ValueError:
                raise ValueError(
                    f"No $00 terminator after $0F at {i:#x} in op {op_offset:#x}"
                )
        if sub == SUBOP_POS:
            i += 5          # sub + 2x word
        elif sub == SUBOP_COLOR:
            i += 2          # sub + byte (direct form; variable form also 2 bytes)
        elif sub == SUBOP_CLIPPING:
            i += 3          # sub + word
        elif sub in (SUBOP_ERASE, SUBOP_SAY_VOICE):
            i += 5          # sub + 2x word
        elif sub in (SUBOP_CENTER, SUBOP_LEFT, SUBOP_OVERHEAD):
            i += 1          # zero-arg flag
        else:
            raise ValueError(
                f"Unknown sub-op {sub:#x} at {i:#x} inside op {op_offset:#x}"
            )


def build_replacement(actor: int, new_text: str, position=None,
                      target_size: int = None) -> bytes:
    """Assemble bytes for: $14 <actor> [$00 <x16> <y16>] $0F <text> $00.

    If target_size is given, the output is padded with spaces (after the text,
    before the $00 terminator) to match that length. SCUMM relative-jump
    offsets break if ops change size, so in-place patching must preserve the
    original byte count of each op. Padding spaces land after the first
    $FF $01 newline in the source and never render (our renderer stops at the
    first newline).
    """
    out = bytearray()
    out.append(PRINT_OPCODE)
    out.append(actor)
    if position is not None:
        x, y = position
        if not (0 <= x < 0x10000 and 0 <= y < 0x10000):
            raise ValueError(f"Position ({x},{y}) out of 16-bit range")
        out.append(SUBOP_POS)
        out.append(x & 0xFF)
        out.append((x >> 8) & 0xFF)
        out.append(y & 0xFF)
        out.append((y >> 8) & 0xFF)
    out.append(SUBOP_TEXT)
    out += encode_text(new_text)
    if target_size is not None:
        # Reserve 1 byte for the $00 terminator we're about to append.
        pad = target_size - len(out) - 1
        if pad < 0:
            raise ValueError(
                f"Replacement op is {-pad} bytes too large for target size "
                f"{target_size} (text: {new_text!r}). Shorten the text or drop "
                f"the position sub-op to fit."
            )
        # Pad with $FF $03 escape pairs — our renderer's scanFF/nlScanFF/
        # renderFF all treat $FF $03 as "continue scanning, count no chars, no
        # render" (scummvm.65816). So padding doesn't skew the per-line
        # centering the way trailing spaces would. If pad is odd, one space
        # byte is added first to reach an even pad count.
        if pad % 2:
            out.append(0x20)
            pad -= 1
        out += bytes([0xFF, 0x03]) * (pad // 2)
    out.append(0x00)
    if target_size is not None and len(out) != target_size:
        raise AssertionError(f"padding failed: {len(out)} != {target_size}")
    return bytes(out)


def _discover_print_ops(src: bytes) -> list:
    """Scan for every `$14 FF 0F ... $00` print op and return their start offsets.

    Used to auto-apply a file-level default_position to every anonymous-actor
    print op (the "screen text" pattern MI1's intro uses for all credits).
    """
    offsets = []
    i = 0
    while i < len(src) - 4:
        if src[i] == PRINT_OPCODE and src[i+1] == 0xFF and src[i+2] == SUBOP_TEXT:
            try:
                end = src.index(0x00, i + 3)
            except ValueError:
                break
            body = src[i+3:end]
            # Skip ops whose body is just whitespace / escape punctuation —
            # the intro uses `14 FF 0F 20 00` as a cheap "clear-line" no-op.
            stripped = body.replace(b'\xff\x01', b'').replace(b'\xff\x02', b'').strip()
            if stripped:
                offsets.append(i)
            i = end + 1
        else:
            i += 1
    return offsets


def apply_file_patches(src: bytes, patches: list, path_label: str,
                       default_position=None) -> bytes:
    """Rebuild a script file with the given patches applied in order.

    If default_position is given, every $14 FF 0F print op not explicitly
    listed in `patches` also gets a position-only patch using that position
    (text preserved verbatim). This is how the intro credits all move to a
    consistent Y without listing every op in the JSON.
    """
    patches = list(patches)
    explicit = {p['op_offset'] for p in patches}

    if default_position is not None:
        # Auto-patching: each op gets a $00 X X Y Y pos sub-op injected.
        # build_replacement() size-preserves by stripping 5 bytes of trailing
        # text (our renderer only shows the first line, so that loss is
        # cosmetic) and padding any remainder with spaces.
        POS_OVERHEAD = 5
        for off in _discover_print_ops(src):
            if off in explicit:
                continue
            end = src.index(0x00, off + 3)
            orig_body = src[off + 3:end]
            if len(orig_body) <= POS_OVERHEAD:
                logging.warning(
                    "Auto-pos skip at %#x: op body too short (%d bytes) to shrink by %d",
                    off, len(orig_body), POS_OVERHEAD,
                )
                continue
            shrunk = orig_body[:-POS_OVERHEAD]
            text = shrunk.replace(b'\xff\x01', b'\n').decode('latin-1')
            patches.append({
                'op_offset': off,
                'new_text': text,
                'position': list(default_position),
                '_auto': True,
            })

    patches.sort(key=lambda p: p['op_offset'])
    out = bytearray()
    cursor = 0
    for p in patches:
        off = p['op_offset']
        if off < cursor:
            raise ValueError(
                f"{path_label}: patches overlap — op_offset {off:#x} lies inside "
                f"a previously-patched region ending at {cursor:#x}"
            )
        terminator = find_text_terminator(src, off)
        # Sanity-check original_text if the manifest provided one.
        orig_text = src[off + 3:terminator]   # assumes $14 <actor> $0F ... $00
        if src[off + 2] == SUBOP_TEXT and 'original_text' in p:
            expected = encode_text(p['original_text'])
            if orig_text != expected:
                logging.warning(
                    "%s @ %#x: original_text mismatch.\n  file:     %r\n  manifest: %r\n"
                    "  Proceeding anyway — update the manifest if the original script was re-extracted.",
                    path_label, off, orig_text, expected,
                )

        original_size = terminator + 1 - off
        out += src[cursor:off]
        out += build_replacement(
            actor=src[off + 1],
            new_text=p['new_text'],
            position=p.get('position'),
            target_size=original_size,
        )
        cursor = terminator + 1

    out += src[cursor:]
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src-dir', default='data/scumm_extracted',
                        help='Directory of extracted SCUMM data (default: data/scumm_extracted)')
    parser.add_argument('--out-dir', default='build/scumm_patched_scripts',
                        help='Where to write patched outputs (default: build/scumm_patched_scripts)')
    parser.add_argument('--patches', default='tools/scumm_patches.json',
                        help='JSON patch manifest (default: tools/scumm_patches.json)')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s: %(message)s',
    )

    src_dir = Path(args.src_dir)
    out_dir = Path(args.out_dir)
    patches_path = Path(args.patches)

    with patches_path.open() as f:
        manifest = json.load(f)

    patched_count = 0
    for rel_path, spec in manifest.items():
        if rel_path.startswith('_'):
            continue   # skip comment keys
        src_file = src_dir / rel_path
        dst_file = out_dir / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        if not src_file.exists():
            logging.error("Source file missing: %s", src_file)
            sys.exit(1)

        src_bytes = src_file.read_bytes()
        patched = apply_file_patches(
            src_bytes,
            spec.get('patches', []),
            rel_path,
            default_position=spec.get('default_position'),
        )
        # Raw byte patches: overwrite specific byte ranges in-place. Use for
        # non-print-op fixes (e.g. setCameraAt immediate values). Each entry
        # needs identical-length replacement bytes so jumps stay valid.
        raw = spec.get('byte_patches', [])
        if raw:
            patched = bytearray(patched)
            for bp in raw:
                off = bp['offset']
                old_bytes = bytes.fromhex(bp['expect'].replace(' ', ''))
                new_bytes = bytes.fromhex(bp['replace'].replace(' ', ''))
                if len(old_bytes) != len(new_bytes):
                    raise ValueError(
                        f"{rel_path} byte_patch @ {off:#x}: expect "
                        f"({len(old_bytes)}B) != replace ({len(new_bytes)}B)"
                    )
                have = bytes(patched[off:off + len(old_bytes)])
                if have != old_bytes:
                    logging.warning(
                        "%s byte_patch @ %#x: expected %s, found %s. Proceeding anyway.",
                        rel_path, off, old_bytes.hex(' '), have.hex(' '),
                    )
                patched[off:off + len(new_bytes)] = new_bytes
            patched = bytes(patched)
        dst_file.write_bytes(patched)
        delta = len(patched) - len(src_bytes)
        sign = '+' if delta >= 0 else ''
        desc = spec.get('description', '')
        logging.info(
            "%s -> %s  [%d -> %d bytes, %s%d]  %s",
            src_file, dst_file, len(src_bytes), len(patched), sign, delta, desc,
        )
        patched_count += 1

    logging.info("Patched %d file(s).", patched_count)


if __name__ == '__main__':
    main()
