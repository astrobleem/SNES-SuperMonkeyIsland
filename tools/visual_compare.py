#!/usr/bin/env python3
"""Compose a side-by-side comparison: original MI1 video frames vs SNES port.

Usage:
    python tools/visual_compare.py \\
        --video-time 2:40 --count 6 --step-sec 1 \\
        --snes-frames distribution/snes_001.png distribution/snes_002.png ... \\
        --output distribution/cmp_room33.png

Drives ffmpeg to pull `count` frames from the canonical MI1 PC video
(`E:/gh/scummvm/monkeyislandintrotalkie.mp4`, see project memory
`reference_mi1_intro_video.md`) starting at `video_time`, spaced
`step_sec` apart. Combines each with a corresponding pre-captured SNES
screenshot into a 2-row × N-col grid: top row = PC, bottom row = SNES.

Capture the SNES screenshots first via `mcp__smi-workflow__take_screenshot`
or `mcp__smi-workflow__run_with_input` with `screenshot_frame=...`,
saving each to `distribution/snes_NNN.png`. Pass the paths to
`--snes-frames` in order.

The reference video is what the user keeps on hand for visual
regressions ("does this look right vs the original?"). Pair this tool
with the agent's MCP-driven runtime probes to make every gameplay-bug
fix one-shot validatable.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT  = Path(__file__).resolve().parent.parent
DIST  = ROOT / "distribution"
VIDEO = Path("E:/gh/scummvm/monkeyislandintrotalkie.mp4")


def parse_time(spec: str) -> float:
    """`M:SS` or `H:MM:SS` or `S.SS` → seconds (float)."""
    if ":" in spec:
        parts = [float(p) for p in spec.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(spec)


def extract_video_frames(video: Path, start_sec: float, count: int,
                         step_sec: float, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(count):
        t = start_sec + i * step_sec
        out = out_dir / f"video_{i:03d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t}", "-i", str(video),
             "-frames:v", "1", "-q:v", "2", str(out)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        paths.append(out)
    return paths


def compose_grid(video_pngs: list[Path], snes_pngs: list[Path],
                 out_path: Path, label_top: str = "MI1 PC (reference)",
                 label_bottom: str = "SNES port (this build)") -> Path:
    if not video_pngs or not snes_pngs:
        raise ValueError("need at least one video and one snes frame")
    n = min(len(video_pngs), len(snes_pngs))
    video_pngs, snes_pngs = video_pngs[:n], snes_pngs[:n]

    # SNES native resolution as the cell size; resize video frames to match.
    cell_w, cell_h = 256, 224
    label_h = 18
    pad = 4

    grid_w = n * cell_w + (n + 1) * pad
    grid_h = 2 * cell_h + 3 * pad + 2 * label_h
    grid = Image.new("RGB", (grid_w, grid_h), (16, 16, 16))
    draw = ImageDraw.Draw(grid)

    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    def place_row(images: list[Path], y: int, row_label: str) -> None:
        draw.text((pad, y), row_label, fill=(220, 220, 220), font=font)
        for i, img_path in enumerate(images):
            with Image.open(img_path) as src:
                src = src.convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
            x = pad + i * (cell_w + pad)
            grid.paste(src, (x, y + label_h))

    place_row(video_pngs, pad, label_top)
    place_row(snes_pngs, pad + label_h + cell_h + pad, label_bottom)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out_path)
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="visual_compare")
    p.add_argument("--video-time", required=True,
                   help="Start time in the reference video, e.g. '2:40'")
    p.add_argument("--count", type=int, required=True,
                   help="Number of frames per row")
    p.add_argument("--step-sec", type=float, default=1.0,
                   help="Seconds between consecutive video frames (also the "
                        "expected SNES capture spacing for label alignment)")
    p.add_argument("--snes-frames", nargs="+", type=Path, required=True,
                   help="Pre-captured SNES screenshot paths, in order")
    p.add_argument("--output", type=Path, default=DIST / "visual_compare.png")
    p.add_argument("--video", type=Path, default=VIDEO)
    p.add_argument("--label-top", default="MI1 PC (reference)")
    p.add_argument("--label-bottom", default="SNES port (this build)")
    args = p.parse_args(argv)

    if len(args.snes_frames) < args.count:
        p.error(f"--snes-frames provided {len(args.snes_frames)} but --count={args.count}")

    start_sec = parse_time(args.video_time)
    print(f"[visual_compare] video={args.video.name} t={args.video_time} "
          f"({start_sec:.1f}s) count={args.count} step={args.step_sec}s")

    with tempfile.TemporaryDirectory(prefix="vc_") as td:
        video_dir = Path(td)
        video_pngs = extract_video_frames(args.video, start_sec, args.count,
                                          args.step_sec, video_dir)
        out = compose_grid(video_pngs, list(args.snes_frames[:args.count]),
                           args.output,
                           label_top=args.label_top,
                           label_bottom=args.label_bottom)
        print(f"[visual_compare] saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
