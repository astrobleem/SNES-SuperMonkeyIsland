"""Convert SCUMM SBL sound-effect resources to 16-bit signed WAV.

An SBL resource wraps a Creative VOC block:

    SOU  <size>                    (outer wrapper, present on standalone dumps)
    SBL  <size>
    AUhd <3>  00 00 80
    AUdt <size>
      01                           (VOC block type 1 = sound data)
      <3 bytes LE size>            (VOC block payload size including sr+pack)
      <1 byte sr>                  (VOC rate code: rate_hz = 1_000_000 / (256 - sr))
      <1 byte pack>                (0 = uncompressed)
      <pcm...>                     (unsigned 8-bit PCM, 128 == silence)

This converter writes signed-16-bit mono WAV at the VOC's native sample rate,
leaving BRR conversion to tad-compiler downstream.

Usage:
    python sbl_to_wav.py data/scumm_extracted/sounds/ --outdir audio/samples/sfx/
    python sbl_to_wav.py data/scumm_extracted/sounds/soun_006_room010.bin
"""

from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path


def vocrate_to_hz(sr: int) -> int:
    if sr == 256:
        raise ValueError("invalid VOC rate code 256")
    return round(1_000_000 / (256 - sr))


def decode_sbl(data: bytes) -> tuple[int, bytes]:
    """Return (sample_rate_hz, unsigned_8bit_pcm)."""
    # Locate AUdt chunk (may be preceded by SOU/SBL/AUhd depending on source).
    idx = data.find(b"AUdt")
    if idx < 0:
        raise ValueError("no AUdt chunk")
    # tag (4) + 4-byte BE chunk size
    chunk_size = struct.unpack(">I", data[idx + 4 : idx + 8])[0]
    body = data[idx + 8 : idx + 8 + chunk_size]
    if len(body) < 6:
        raise ValueError("AUdt body too short")
    if body[0] != 0x01:
        raise ValueError(f"VOC blocktype {body[0]:#x} not 0x01")
    voc_size = body[1] | (body[2] << 8) | (body[3] << 16)
    sr_code = body[4]
    pack = body[5]
    if pack != 0:
        raise ValueError(f"VOC pack mode {pack} not 0 (uncompressed)")
    pcm_size = voc_size - 2  # subtract sr + pack bytes
    pcm = body[6 : 6 + pcm_size]
    if len(pcm) != pcm_size:
        raise ValueError(f"PCM truncated: expected {pcm_size}, got {len(pcm)}")
    rate = vocrate_to_hz(sr_code)
    return rate, bytes(pcm)


def u8_to_s16_pcm(u8: bytes) -> bytes:
    # Unsigned 8-bit (0..255, 128 = silence) -> signed 16-bit little-endian.
    # Pad tail with silence so the total sample count is a multiple of 16
    # (BRR block size; tad-compiler rejects non-aligned input).
    pad = (-len(u8)) % 16
    padded = u8 + b"\x80" * pad  # 0x80 is unsigned-8-bit silence
    return b"".join(struct.pack("<h", (b - 128) * 256) for b in padded)


def convert_one(src: Path, out_dir: Path) -> tuple[Path, int, int] | None:
    data = src.read_bytes()
    if len(data) < 12 or data[:4] != b"SOU ":
        return None
    # Peek the inner tag — only convert SBL resources.
    if data[8:12] != b"SBL ":
        return None
    rate, u8_pcm = decode_sbl(data)
    s16 = u8_to_s16_pcm(u8_pcm)
    out = out_dir / f"{src.stem}.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s16)
    return out, rate, len(u8_pcm)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="SBL file(s) or directory of soun_*.bin")
    ap.add_argument("--outdir", type=Path, default=Path("audio/samples/sfx"))
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for p in args.inputs:
        if p.is_dir():
            files.extend(sorted(p.glob("soun_*.bin")))
        else:
            files.append(p)

    ok = 0
    skipped = 0
    failed: list[tuple[Path, str]] = []
    for src in files:
        try:
            result = convert_one(src, args.outdir)
        except Exception as e:  # pragma: no cover - diagnostic path
            failed.append((src, str(e)))
            continue
        if result is None:
            skipped += 1
            continue
        out, rate, pcm_size = result
        print(f"  {src.name:30s} -> {out.name:30s} {rate:6d} Hz  {pcm_size:7d} samples")
        ok += 1

    print()
    print(f"converted: {ok}")
    print(f"skipped (not SBL): {skipped}")
    if failed:
        print(f"failed: {len(failed)}")
        for p, msg in failed:
            print(f"  {p.name}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
