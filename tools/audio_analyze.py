"""Offline analysis for WAVs captured via MCP record_audio.

Returns structured information an LLM (or human) can reason about:
  - per-channel energy + peak frequency
  - dominant pitches (top-N spectral peaks)
  - silence / clipping detection
  - optional comparison to a reference WAV (RMS difference per band)

No fancy DSP — stdlib + numpy. The point is to convert "is the song
right" into 'is column N of the spectrogram within X dB of the
reference' so we can act on it without ears.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("numpy required. pip install numpy", file=sys.stderr)
    sys.exit(2)


def load_wav(path: Path) -> tuple[int, np.ndarray]:
    """Returns (sample_rate, samples) where samples is shape (n,) for
    mono or (n, 2) for stereo, dtype float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        nchan = w.getnchannels()
        sw = w.getsampwidth()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    fmt = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    samples = np.frombuffer(raw, dtype=fmt).astype(np.float32)
    samples /= float(2 ** (8 * sw - 1))
    if nchan > 1:
        samples = samples.reshape(-1, nchan)
    return rate, samples


def spectrum_summary(rate: int, samples: np.ndarray, top_n: int = 8) -> dict:
    """Compute an FFT over the whole recording and return the top-N
    spectral peaks. Robust enough to identify a melody's dominant notes
    without overfitting to noise."""
    if samples.ndim > 1:
        mono = samples.mean(axis=1)
    else:
        mono = samples
    n = len(mono)
    if n == 0:
        return {"empty": True}
    # Hann window so peaks are clean
    window = np.hanning(n)
    fft = np.fft.rfft(mono * window)
    mag = np.abs(fft)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)

    rms = float(np.sqrt((mono ** 2).mean()))
    peak = float(np.max(np.abs(mono)))
    # Top-N peak frequencies, ignoring DC (idx 0)
    if mag.size > 1:
        idx = np.argsort(mag[1:])[::-1][:top_n] + 1
        peaks = [{
            "freq": float(freqs[i]),
            "magnitude": float(mag[i]),
            "note": _hz_to_note(float(freqs[i])),
        } for i in idx]
    else:
        peaks = []

    silent = rms < 1e-4
    clipping = peak > 0.999
    return {
        "samples": int(n),
        "duration_s": n / rate,
        "rms": rms,
        "peak": peak,
        "silent": bool(silent),
        "clipping": bool(clipping),
        "topPeaks": peaks,
    }


def _hz_to_note(freq: float) -> str:
    if freq < 20:
        return "-"
    # A4 = 440 Hz, MIDI note 69
    midi = 69 + 12 * math.log2(freq / 440.0)
    midi_round = int(round(midi))
    cents = round((midi - midi_round) * 100)
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    note = names[midi_round % 12]
    octave = midi_round // 12 - 1
    return f"{note}{octave}{cents:+d}c"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", help="WAV file to analyze")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--ref", default=None,
                    help="Reference WAV; if given, also report RMS delta per band")
    args = ap.parse_args()

    rate, samples = load_wav(Path(args.wav))
    summary = spectrum_summary(rate, samples, top_n=args.top)
    summary["sampleRate"] = rate
    summary["channels"] = samples.shape[1] if samples.ndim > 1 else 1

    if args.ref:
        rate2, samples2 = load_wav(Path(args.ref))
        summary["ref"] = {"sampleRate": rate2}
        # Trim to common length, mono-mix, then per-band RMS delta over
        # 12 logarithmic bands across the audible range.
        m1 = samples.mean(axis=1) if samples.ndim > 1 else samples
        m2 = samples2.mean(axis=1) if samples2.ndim > 1 else samples2
        n = min(len(m1), len(m2))
        m1 = m1[:n]; m2 = m2[:n]
        if rate != rate2:
            summary["ref"]["error"] = f"sample rates differ: {rate} vs {rate2}"
        else:
            band_edges = np.geomspace(50.0, rate / 2 - 50, 13)
            f1 = np.abs(np.fft.rfft(m1))
            f2 = np.abs(np.fft.rfft(m2))
            freqs = np.fft.rfftfreq(n, d=1.0 / rate)
            bands = []
            for i in range(12):
                mask = (freqs >= band_edges[i]) & (freqs < band_edges[i + 1])
                e1 = float(np.sqrt((f1[mask] ** 2).mean())) if mask.any() else 0.0
                e2 = float(np.sqrt((f2[mask] ** 2).mean())) if mask.any() else 0.0
                bands.append({
                    "lo": float(band_edges[i]),
                    "hi": float(band_edges[i + 1]),
                    "actual": e1,
                    "ref": e2,
                    "delta_db": 20 * math.log10(max(e1, 1e-9) / max(e2, 1e-9)),
                })
            summary["ref"]["bands"] = bands

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
