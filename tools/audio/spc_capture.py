#!/usr/bin/env python3
"""Spawn a dedicated Mesen --mcp instance on an .spc file and record audio.

    py tools/audio/spc_capture.py <song.spc> <out.wav> [seconds] [port]

Proof harness for standalone TAD .spc exports (song2spc). Loads the SPC as
Mesen content, lets the driver boot, records N seconds of real DSP output.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/
from mcp_client import McpSession

spc = str(Path(sys.argv[1]).resolve())
wav = str(Path(sys.argv[2]).resolve())
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
port = int(sys.argv[4]) if len(sys.argv) > 4 else 5099

with McpSession(rom=spc, port=port) as m:
    m.reset_emulator()           # restart the song from tick 0
    m.record_audio(wav)          # record from the true start
    remaining = int(60 * secs)   # chunked so the wall-clock cap can't truncate
    while remaining > 0:
        r = m.run_frames(min(600, remaining))
        adv = int(r.get("framesAdvanced", 0))
        if adv <= 0:
            break
        remaining -= adv
    m.tool("stop_audio", {})

print("wrote", wav, Path(wav).stat().st_size, "bytes")
