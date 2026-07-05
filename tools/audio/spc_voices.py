#!/usr/bin/env python3
"""Render an .spc to 8 per-DSP-voice WAVs by Mesen mixer isolation.

    py tools/audio/spc_voices.py <song.spc> <out_dir> [seconds]

For each SNES DSP voice 1..8 it rewrites mesen/settings.json so that voice's
ChannelNVol is 100 and the other seven are 0 (mixer gains the music driver
can't overwrite), spawns a fresh Mesen --mcp on the .spc, and records the
isolated voice. Also captures the full mix (all eight at 100) as master audio.
settings.json is backed up and restored.

Output: <out_dir>/voice1.wav .. voice8.wav, mix.wav
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from mcp_client import McpSession

SETTINGS = ROOT / "mesen" / "settings.json"
CHAN_RE = re.compile(r"^Channel([1-8])Vol$")


def set_channel_vols(vols):
    """vols = dict {1:100, 2:0, ...}. Rewrite every ChannelNVol in settings."""
    data = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                m = CHAN_RE.match(k)
                if m:
                    o[k] = vols[int(m.group(1))]
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def capture(spc, wav, secs, port):
    with McpSession(rom=str(spc), port=port, socket_timeout=300.0) as m:
        m.reset_emulator()           # restart the song from tick 0
        m.record_audio(str(wav))     # record from the true start
        # Chunk run_frames so the 2x wall-clock cap can't silently truncate the
        # recording. Honor the per-call framesAdvanced and loop to the target.
        remaining = int(60 * secs)
        while remaining > 0:
            r = m.run_frames(min(600, remaining))
            adv = int(r.get("framesAdvanced", 0))
            if adv <= 0:
                break
            remaining -= adv
        m.tool("stop_audio", {})


def main():
    spc = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 16.0
    out.mkdir(parents=True, exist_ok=True)

    backup = SETTINGS.with_suffix(".json.bak")
    shutil.copy2(SETTINGS, backup)
    port = 5099
    try:
        # full mix master
        set_channel_vols({n: 100 for n in range(1, 9)})
        capture(spc, out / "mix.wav", secs, port); port += 1
        # one voice at a time
        for v in range(1, 9):
            set_channel_vols({n: (100 if n == v else 0) for n in range(1, 9)})
            capture(spc, out / f"voice{v}.wav", secs, port); port += 1
            print(f"voice {v} -> {(out / f'voice{v}.wav')}")
            time.sleep(0.3)
    finally:
        shutil.move(str(backup), str(SETTINGS))
    print("done", out)


if __name__ == "__main__":
    main()
