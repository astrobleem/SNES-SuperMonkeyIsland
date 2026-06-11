#!/usr/bin/env python3
"""Collect batch renders into build/eartest_batch/ with clip checks.

  python tools/audio/collect_eartest.py r002_monkey1 r019_shdeck ...

Copies build/verify_<song>/<song>_actual.wav -> build/eartest_batch/<song>.wav
and prints a summary row per song: duration, peak dBFS, clipped samples.
Exits 1 if any song clipped or any WAV is missing.
"""
import shutil
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'build/eartest_batch'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    for song in sys.argv[1:]:
        src = REPO / f'build/verify_{song}/{song}_actual.wav'
        if not src.exists():
            print(f'{song:<18} MISSING')
            bad += 1
            continue
        dst = OUT / f'{song}.wav'
        shutil.copyfile(src, dst)
        w = wave.open(str(src))
        n, sr = w.getnframes(), w.getframerate()
        d = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
        peak = np.abs(d).max() / 32768.0
        clip = int((np.abs(d) >= 32767).sum())
        flag = '  CLIPPED!' if clip else ''
        if clip:
            bad += 1
        print(f'{song:<18} {n/sr:6.1f}s  peak {peak:5.3f}  clip {clip}{flag}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
