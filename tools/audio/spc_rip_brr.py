#!/usr/bin/env python3
"""Rip BRR samples from a directory of .spc files into an AMK-style library.

  python tools/audio/spc_rip_brr.py build/raid/spc/<game> audio/samples/raid/<game> \
      [--wav-dir build/raid/wav/<game>]

For every SPC in the input dir: read the 64KB ARAM image + 128 DSP registers,
locate the sample directory (DSP $5D * 256), walk all 256 entries, extract each
structurally-valid BRR sample, and dedupe by content hash across the whole set.

Each unique sample is written as <out>/sNNN_<pitch|oneshot>.brr in AddMusicK
format (2-byte little-endian loop-offset header + raw BRR) -- the format
tad-compiler consumes verbatim with `loop: "none"` (it honors the header and
the per-block loop flags). A manifest.json records per sample: block count,
sample count, loop offset, estimated natural f0 at 32kHz playback (autocorr
over the loop segment; this is exactly TAD's `freq` field), the autocorr
confidence, and which songs referenced it (file + SRCN).

Optionally decodes audition WAVs (32kHz mono, looped samples sustained ~1.5s)
so chairs can be picked by ear without compiling anything.

Caveats: an SPC is a RAM snapshot, so a sample overlapped by the echo buffer
in one song may rip corrupted there -- the same sample from another song in
the set usually hashes clean. Junk directory entries that happen to parse are
possible; the manifest's f0 confidence and the audition WAVs make them obvious.
"""
import argparse
import hashlib
import json
import struct
import sys
import wave
from pathlib import Path

RATE = 32000
MIN_BLOCKS = 4
MAX_BLOCKS = 7280          # 64KB / 9


def load_spc(path: Path):
    data = path.read_bytes()
    if len(data) < 0x10180 or not data.startswith(b"SNES-SPC700 Sound File Data"):
        return None, None
    return data[0x100:0x10100], data[0x10100:0x10180]


def walk_sample(ram: bytes, start: int):
    """Walk BRR blocks from start; return (n_blocks, last_header) or None."""
    a = start
    while a + 9 <= 0x10000:
        hdr = ram[a]
        a += 9
        if hdr & 1:
            n = (a - start) // 9
            if MIN_BLOCKS <= n <= MAX_BLOCKS:
                return n, hdr
            return None
    return None


def brr_decode(data: bytes):
    """Standard SPC700 BRR decoder -> list of int16 PCM samples."""
    out = []
    p1 = p2 = 0
    for b in range(len(data) // 9):
        hdr = data[b * 9]
        shift = hdr >> 4
        filt = (hdr >> 2) & 3
        for i in range(8):
            byte = data[b * 9 + 1 + i]
            for nib in ((byte >> 4) & 0xF, byte & 0xF):
                s = nib - 16 if nib >= 8 else nib
                s = (s << shift) >> 1 if shift <= 12 else (s >> 3) << 12
                if filt == 1:
                    s += p1 + (-p1 >> 4)
                elif filt == 2:
                    s += (p1 << 1) + ((-((p1 << 1) + p1)) >> 5) - p2 + (p2 >> 4)
                elif filt == 3:
                    s += (p1 << 1) + ((-(p1 + (p1 << 2) + (p1 << 3))) >> 6) \
                         - p2 + (((p2 << 1) + p2) >> 4)
                s = max(-32768, min(32767, s))
                out.append(s)
                p2, p1 = p1, s
    return out


def estimate_f0(pcm, looped, loop_start_smp):
    """Autocorrelation pitch estimate; returns (f0_hz, confidence)."""
    seg = pcm[loop_start_smp:] if looped else pcm
    if looped:
        while len(seg) < 2048 and seg:
            seg = seg + seg
    seg = seg[:4096]
    n = len(seg)
    if n < 64:
        return None, 0.0
    mean = sum(seg) / n
    x = [s - mean for s in seg]
    e0 = sum(v * v for v in x)
    if e0 == 0:
        return None, 0.0
    best_lag, best_r = None, 0.0
    lag = 16                              # 2kHz ceiling
    max_lag = min(1600, n // 2)           # 20Hz floor
    while lag < max_lag:
        m = n - lag
        r = sum(x[i] * x[i + lag] for i in range(m)) / e0 * (n / m)
        if r > best_r:
            best_r, best_lag = r, lag
        lag += 1
    if best_lag is None or best_r < 0.3:
        return None, round(best_r, 3)
    return round(RATE / best_lag, 2), round(best_r, 3)


def write_wav(path: Path, pcm, looped, loop_start_smp):
    if looped and len(pcm) > loop_start_smp:
        body = list(pcm)
        loop = pcm[loop_start_smp:]
        while len(body) < len(pcm) + int(1.5 * RATE) and loop:
            body.extend(loop)
        pcm = body
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spc_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--wav-dir", type=Path, default=None,
                    help="also decode audition WAVs here")
    args = ap.parse_args()

    spcs = sorted(args.spc_dir.rglob("*.spc"))
    if not spcs:
        print(f"no .spc files under {args.spc_dir}", file=sys.stderr)
        return 2

    samples = {}   # sha1 -> record
    for spc in spcs:
        ram, dsp = load_spc(spc)
        if ram is None:
            continue
        dirbase = dsp[0x5D] * 256
        if dirbase == 0:
            continue
        # Echo buffer region: actively overwritten while echo writes are on
        # (FLG bit5 clear), so any "sample" intersecting it is torn.
        echo_lo = echo_hi = -1
        if not (dsp[0x6C] & 0x20):
            echo_lo = dsp[0x6D] * 256
            echo_hi = echo_lo + max(1, dsp[0x7D] & 0x0F) * 2048
        cands = []
        for srcn in range(256):
            off = dirbase + srcn * 4
            if off + 4 > 0x10000:
                break
            start, loop = struct.unpack_from("<HH", ram, off)
            if start < 0x200 or start == 0xFFFF:
                continue
            walked = walk_sample(ram, start)
            if not walked:
                continue
            nblk, last_hdr = walked
            end = start + nblk * 9
            if echo_lo >= 0 and start < echo_hi and end > echo_lo:
                continue
            looped = bool(last_hdr & 2)
            if looped and not (start <= loop < end and (loop - start) % 9 == 0):
                continue
            cands.append((srcn, start, end, loop, looped))
        # Junk directory entries often point into the middle of a real
        # sample (the block walk still finds the same END flag). Keep only
        # entries whose start is not strictly inside another entry's range.
        for srcn, start, end, loop, looped in cands:
            if any(c[1] < start < c[2] for c in cands if c[1] != start):
                continue
            brr = ram[start:end]
            loop_off = (loop - start) if looped else 0
            key = hashlib.sha1(brr + struct.pack("<H", loop_off)).hexdigest()
            rec = samples.get(key)
            if rec is None:
                rec = {"brr": brr, "loop_off": loop_off, "looped": looped,
                       "found_in": []}
                samples[key] = rec
            ref = f"{spc.name}:srcn{srcn}"
            if len(rec["found_in"]) < 8 and ref not in rec["found_in"]:
                rec["found_in"].append(ref)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.wav_dir:
        args.wav_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    ordered = sorted(samples.values(),
                     key=lambda r: (-len(r["found_in"]), -len(r["brr"])))
    for i, rec in enumerate(ordered):
        pcm = brr_decode(rec["brr"])
        loop_smp = rec["loop_off"] // 9 * 16
        f0, conf = estimate_f0(pcm, rec["looped"], loop_smp)
        if rec["looped"]:
            tag = f"{round(f0)}hz" if f0 else "loop"
        else:
            tag = "oneshot"
        name = f"s{i:03d}_{tag}.brr"
        (args.out_dir / name).write_bytes(
            struct.pack("<H", rec["loop_off"]) + rec["brr"])
        if args.wav_dir:
            write_wav(args.wav_dir / (name[:-4] + ".wav"),
                      pcm, rec["looped"], loop_smp)
        midi = (69 + 12 * __import__("math").log2(f0 / 440)) if f0 else None
        manifest.append({
            "file": name,
            "blocks": len(rec["brr"]) // 9,
            "samples": len(pcm),
            "bytes": len(rec["brr"]) + 2,
            "looped": rec["looped"],
            "loop_offset_bytes": rec["loop_off"],
            "loop_samples": (len(pcm) - loop_smp) if rec["looped"] else 0,
            "est_f0_hz": f0,
            "est_natural_midi": round(midi, 2) if midi else None,
            "f0_confidence": conf,
            "found_in": rec["found_in"],
        })

    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    total = sum(m["bytes"] for m in manifest)
    print(f"{args.spc_dir.name}: {len(spcs)} SPCs -> {len(manifest)} unique "
          f"samples ({total // 1024}KB) -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
