#!/usr/bin/env python3
"""Batch-convert all SCUMM costumes to SNES format.

Iterates data/scumm_extracted/costumes/cost_NNN_roomMMM.bin files,
finds the corresponding room palette, and runs snes_costume_converter.py --all
for each. Logs failures without aborting.

Usage:
    python tools/convert_all_costumes.py [--verify] [--force]

Options:
    --verify   Generate verification PNGs (slower)
    --force    Re-convert even if output directory already exists
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
COSTUMES_DIR = os.path.join(PROJECT_ROOT, "data", "scumm_extracted", "costumes")
ROOMS_DIR = os.path.join(PROJECT_ROOT, "data", "scumm_extracted", "rooms")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "snes_converted", "costumes")
CONVERTER = os.path.join(SCRIPT_DIR, "snes_costume_converter.py")


def find_room_palette(room_num: int) -> str | None:
    """Find palette.bin for a room number, searching room_NNN_name/ directories."""
    pattern = os.path.join(ROOMS_DIR, f"room_{room_num:03d}_*", "palette.bin")
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    # Fallback: try bare room_NNN/
    bare = os.path.join(ROOMS_DIR, f"room_{room_num:03d}", "palette.bin")
    if os.path.exists(bare):
        return bare
    return None


def parse_costume_filename(filename: str) -> tuple[int, int] | None:
    """Extract costume ID and room number from cost_NNN_roomMMM.bin."""
    m = re.match(r"cost_(\d+)_room(\d+)\.bin$", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def main():
    parser = argparse.ArgumentParser(description="Batch-convert all SCUMM costumes to SNES format")
    parser.add_argument("--verify", action="store_true", help="Generate verification PNGs")
    parser.add_argument("--force", action="store_true", help="Re-convert existing costumes")
    args = parser.parse_args()

    costume_bins = sorted(glob.glob(os.path.join(COSTUMES_DIR, "cost_*_room*.bin")))
    print(f"Found {len(costume_bins)} costume files")

    successes = 0
    failures = []
    skipped = 0
    total_chr_bytes = 0
    total_oam_bytes = 0
    t0 = time.time()

    for costume_bin in costume_bins:
        basename = os.path.basename(costume_bin)
        parsed = parse_costume_filename(basename)
        if not parsed:
            failures.append((basename, "Could not parse filename"))
            continue

        cost_id, room_num = parsed
        out_dir = os.path.join(OUTPUT_DIR, f"cost_{cost_id:03d}")

        # Skip if already converted (unless --force)
        if not args.force and os.path.isdir(out_dir) and glob.glob(os.path.join(out_dir, "pic*.chr")):
            skipped += 1
            continue

        palette = find_room_palette(room_num)
        if not palette:
            failures.append((basename, f"No palette found for room {room_num:03d}"))
            continue

        # Build converter command
        cmd = [
            sys.executable, CONVERTER,
            "--costume", costume_bin,
            "--palette", palette,
            "--all",
            "--output", out_dir,
        ]
        if args.verify:
            cmd.append("--verify")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip().split("\n")[-1] if result.stderr.strip() else "unknown error"
                failures.append((basename, err_msg))
                print(f"  FAIL  cost_{cost_id:03d} (room {room_num:03d}): {err_msg}")
                continue
        except subprocess.TimeoutExpired:
            failures.append((basename, "timeout (60s)"))
            print(f"  FAIL  cost_{cost_id:03d} (room {room_num:03d}): timeout")
            continue

        # Count output sizes
        chr_bytes = sum(
            os.path.getsize(f)
            for f in glob.glob(os.path.join(out_dir, "*.chr"))
        )
        oam_bytes = sum(
            os.path.getsize(f)
            for f in glob.glob(os.path.join(out_dir, "*.oam"))
        )
        total_chr_bytes += chr_bytes
        total_oam_bytes += oam_bytes

        pic_count = len(glob.glob(os.path.join(out_dir, "pic*.chr")))
        head_count = len(glob.glob(os.path.join(out_dir, "head_pic*.chr")))
        successes += 1
        print(f"  OK    cost_{cost_id:03d} (room {room_num:03d}): {pic_count} pics + {head_count} heads, {chr_bytes:,}B CHR + {oam_bytes:,}B OAM")

    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"Conversion complete in {elapsed:.1f}s")
    print(f"  Converted: {successes}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {len(failures)}")
    print(f"  Total CHR: {total_chr_bytes:,} bytes ({total_chr_bytes/1024:.1f} KB)")
    print(f"  Total OAM: {total_oam_bytes:,} bytes ({total_oam_bytes/1024:.1f} KB)")
    print(f"  Combined:  {(total_chr_bytes + total_oam_bytes):,} bytes ({(total_chr_bytes + total_oam_bytes)/1024:.1f} KB)")

    if failures:
        print(f"\nFailed costumes:")
        for name, reason in failures:
            print(f"  {name}: {reason}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
