#!/usr/bin/env python3
"""Build MSU-1 speech tracks from the Ultimate Talkie monster.sou.

monster.sou layout: "SOU " + u32, then per voice line a VCTL chunk
(8-byte header with big-endian size, lip-sync bytes) immediately followed
by a Creative Voice File (VOC). Scripts reference lines by the ABSOLUTE
FILE OFFSET OF THE VCTL CHUNK via the FF 0A string escape (verified:
lscr_203 "Hi!" -> 0x05BC5154 -> "VCTL" at that offset).

Outputs:
  distribution/SuperMonkeyIsland-<track>.pcm   (44.1kHz s16le stereo, MSU1 header)
      track = VOICE_BASE + rank of the line's offset in ascending order
  build/voice_table.bin    u32 LE count, then count ascending u32 LE offsets
      (engine binary-searches the FF 0A offset; hit index -> track)
  build/voice_manifest.json  offset/track/duration per line (debug)

Usage: python tools/audio/build_speech_msu.py [--limit N] [--dry-run]
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOU = ROOT / 'data' / 'monkeypacks' / 'talkie' / 'monster.sou'
OUT_DIR = ROOT / 'distribution'
BUILD = ROOT / 'build'
VOICE_BASE = 1000
MSU_RATE = 44100


def parse_sou(data):
    """Yield (offset, rate, mono_float_array) per voice line."""
    pos = 8                                   # skip "SOU " + u32
    n = len(data)
    while pos + 8 <= n:
        tag = data[pos:pos + 4]
        if tag != b'VCTL':
            raise ValueError(f'expected VCTL at 0x{pos:X}, got {tag!r}')
        vctl_size = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        entry_offset = pos
        pos += vctl_size
        # VOC header: "Creative Voice File\x1a", u16 header size, version, magic
        if data[pos:pos + 19] != b'Creative Voice File':
            raise ValueError(f'expected VOC at 0x{pos:X} (VCTL at 0x{entry_offset:X})')
        hdr_size = struct.unpack('<H', data[pos + 20:pos + 22])[0]
        bpos = pos + hdr_size
        rate = None
        chunks = []
        while True:
            btype = data[bpos]
            if btype == 0:                    # terminator
                bpos += 1
                break
            bsize = data[bpos + 1] | (data[bpos + 2] << 8) | (data[bpos + 3] << 16)
            body = data[bpos + 4:bpos + 4 + bsize]
            if btype == 1:                    # sound data: sr, codec, samples
                sr_byte, codec = body[0], body[1]
                if codec != 0:
                    raise ValueError(f'codec {codec} at 0x{bpos:X}')
                r = 1000000.0 / (256 - sr_byte)
                if rate is None:
                    rate = r
                chunks.append(np.frombuffer(body[2:], dtype=np.uint8))
            elif btype == 3:                  # silence: u16 length, sr byte
                slen = body[0] | (body[1] << 8)
                chunks.append(np.full(slen + 1, 0x80, dtype=np.uint8))
            # other block types: skip
            bpos += 4 + bsize
        pos = bpos
        if not chunks or rate is None:
            continue
        samples = np.concatenate(chunks).astype(np.float32)
        samples = (samples - 128.0) / 128.0
        yield entry_offset, rate, samples


def to_msu_pcm(samples, rate):
    """Resample mono float [-1,1] to 44.1kHz s16 stereo with MSU1 header."""
    n_out = max(1, int(round(len(samples) * MSU_RATE / rate)))
    x_out = np.linspace(0.0, len(samples) - 1.0, n_out)
    resampled = np.interp(x_out, np.arange(len(samples)), samples)
    s16 = np.clip(resampled * 32767.0, -32768, 32767).astype('<i2')
    stereo = np.repeat(s16, 2)
    return b'MSU1' + struct.pack('<I', 0) + stereo.tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='stop after N lines (0 = all)')
    ap.add_argument('--dry-run', action='store_true', help='parse + table only, no pcm files')
    args = ap.parse_args()

    data = SOU.read_bytes()
    entries = []
    for offset, rate, samples in parse_sou(data):
        entries.append((offset, rate, samples))
        if args.limit and len(entries) >= args.limit:
            break
    print(f'parsed {len(entries)} voice lines '
          f'(rates: {sorted({int(e[1]) for e in entries})})')

    entries.sort(key=lambda e: e[0])
    BUILD.mkdir(exist_ok=True)
    table = struct.pack('<I', len(entries)) + b''.join(
        struct.pack('<I', off) for off, _, _ in entries)
    # data/ copy is the committed canonical (the ROM .incbins it);
    # build/ copy is informational only.
    (ROOT / 'data' / 'voice_table.bin').write_bytes(table)
    (BUILD / 'voice_table.bin').write_bytes(table)
    print(f'voice_table.bin: {len(table)} bytes, {len(entries)} entries')

    manifest = {}
    total = 0
    for rank, (offset, rate, samples) in enumerate(entries):
        track = VOICE_BASE + rank
        dur = len(samples) / rate
        manifest[f'0x{offset:08X}'] = {'track': track, 'seconds': round(dur, 2)}
        if not args.dry_run:
            pcm = to_msu_pcm(samples, rate)
            (OUT_DIR / f'SuperMonkeyIsland-{track}.pcm').write_bytes(pcm)
            total += len(pcm)
        if rank % 500 == 0:
            print(f'  {rank}/{len(entries)}...', flush=True)
    (BUILD / 'voice_manifest.json').write_text(json.dumps(manifest, indent=0))
    print(f'done: {len(entries)} tracks, {total / 1e6:.0f} MB pcm')


if __name__ == '__main__':
    sys.exit(main())
