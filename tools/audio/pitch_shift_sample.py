#!/usr/bin/env python3
"""Length-preserving pitch shift for SF2-extracted instrument WAVs.

The SPC700 DSP can only pitch a sample UP by 4× (2 octaves) before
hitting its hardware pitch-register ceiling. TAD's "instrument tuning
can play octaves N - M" error reflects that limit: a sample with a low
native frequency (e.g. SF2's FANTA53 at 175 Hz / MIDI 53) can't reach
the upper notes the song needs (e.g. MIDI 79).

The fix: phase-vocoder pitch-shift the sample UP so its native frequency
sits near the *top* of the needed range, leaving the SPC's unbounded
downward-pitch room to cover the rest. Phase vocoder preserves the
sample's length (frame count and loop-point offsets stay valid) — unlike
naive resample-up, which would shorten the sample and shrink the loop
region (= audible buzz).

Usage:
    pitch_shift_sample.py <in.wav> <out.wav> --semitones 24

Reads/writes 16-bit mono WAVs at any sample rate. Preserves the exact
frame count of the input.
"""
from __future__ import annotations
import argparse
import wave
from pathlib import Path

import librosa
import numpy as np


def pitch_shift_wav(in_path: Path, out_path: Path, semitones: float) -> None:
    with wave.open(str(in_path), "rb") as w:
        sr = w.getframerate()
        n_frames = w.getnframes()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(n_frames)

    if sw != 2:
        raise SystemExit(f"only 16-bit WAVs supported; got {sw*8}-bit")
    if nch != 1:
        raise SystemExit(f"only mono WAVs supported; got {nch} channels")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    shifted = librosa.effects.pitch_shift(samples, sr=sr, n_steps=semitones)

    # librosa returns nearly-equal length but can be off by a frame or two
    # due to STFT framing. Pad/truncate to exactly match input length so
    # BRR loop offsets stay valid.
    if len(shifted) > n_frames:
        shifted = shifted[:n_frames]
    elif len(shifted) < n_frames:
        pad = np.zeros(n_frames - len(shifted), dtype=shifted.dtype)
        shifted = np.concatenate([shifted, pad])
    assert len(shifted) == n_frames, f"len mismatch: {len(shifted)} != {n_frames}"

    out = np.clip(shifted * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(out.tobytes())

    print(f"  in:  {in_path.name}  {n_frames} frames @ {sr} Hz")
    print(f"  out: {out_path.name}  {n_frames} frames @ {sr} Hz "
          f"(+{semitones:+g} semitones)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("in_wav", type=Path)
    p.add_argument("out_wav", type=Path)
    p.add_argument("--semitones", type=float, required=True,
                   help="positive = raise pitch, negative = lower")
    args = p.parse_args()

    pitch_shift_wav(args.in_wav, args.out_wav, args.semitones)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
