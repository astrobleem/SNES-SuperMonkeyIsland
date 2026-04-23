"""Per-song synthesis helpers for the SMI TAD pipeline.

Every SOUN song gets its own instrument palette and we generate all of them
from scratch (no third-party sample packs). This module exposes composable
numpy-based waveform generators, an ADSR envelope, a zero-crossing trim
helper for loop-friendly samples, and a 16-bit mono WAV writer.

Conventions:
  * Sample rate defaults to 32_000 Hz (SNES-friendly; tad-compiler resamples
    to match the instrument's configured first_octave anyway).
  * All generators return float32 in roughly [-1, 1]; `write_wav` clamps and
    quantizes on the way out.
  * Samples targeted at looping instruments should end with `loop_tail` so
    the tail is at a zero-crossing and the level is normalized.

Typical per-song usage::

    from tools.synth_samples import saw, adsr, loop_tail, write_wav

    lead = saw(220.0, 0.4, bright=0.7)
    lead = adsr(lead, a=0.005, d=0.05, s=0.8, r=0.1)
    lead = loop_tail(lead, loop_start_frac=0.5)
    write_wav("audio/songs/r010_lucasarts/samples/lead.wav", lead)
"""
from __future__ import annotations

import os
import struct
import wave
from typing import Iterable, Sequence

import numpy as np

SR = 32_000  # default sample rate (SNES-friendly)


# ============================================================
# Primitive waveforms
# ============================================================
def _t(dur: float, sr: int = SR) -> np.ndarray:
    n = max(1, int(round(dur * sr)))
    return np.arange(n, dtype=np.float32) / sr


def sine(freq: float, dur: float, *, sr: int = SR, phase: float = 0.0) -> np.ndarray:
    return np.sin(2 * np.pi * freq * _t(dur, sr) + phase).astype(np.float32)


def triangle(freq: float, dur: float, *, sr: int = SR) -> np.ndarray:
    t = _t(dur, sr)
    phase = (freq * t) % 1.0
    return (4 * np.abs(phase - 0.5) - 1).astype(np.float32)


def square(freq: float, dur: float, *, sr: int = SR, pwm: float = 0.5) -> np.ndarray:
    """Pulse with duty cycle `pwm` (0..1). Not band-limited — use for
    lo-fi/8-bit-style lead/bass where aliasing adds character."""
    t = _t(dur, sr)
    phase = (freq * t) % 1.0
    return np.where(phase < pwm, 1.0, -1.0).astype(np.float32)


def saw(freq: float, dur: float, *, sr: int = SR, bright: float = 1.0,
        max_partials: int = 32) -> np.ndarray:
    """Additive-synthesis sawtooth. `bright` rolls off the top harmonics —
    1.0 = fully bright, 0.5 = softer (harmonics scaled by 1/(k^1.5)).

    Additive build avoids aliasing that a naive modulo saw would cause
    at high fundamentals, which matters at 32 kHz SR where a 1 kHz saw's
    32nd harmonic already hits 32 kHz."""
    t = _t(dur, sr)
    nyquist = sr / 2
    n_partials = min(max_partials, int(nyquist / max(freq, 1.0)))
    exponent = 1.0 + (1.0 - bright)  # 1.0 → pure saw, >1 → darker
    acc = np.zeros_like(t)
    for k in range(1, n_partials + 1):
        acc += np.sin(2 * np.pi * freq * k * t) / (k ** exponent)
    # Normalize peak
    peak = np.max(np.abs(acc))
    if peak > 0:
        acc /= peak
    return acc.astype(np.float32)


def additive(freq: float, dur: float, harmonics: Sequence[float],
             *, sr: int = SR, phases: Sequence[float] | None = None) -> np.ndarray:
    """Sum of harmonic sines. `harmonics[k]` is the amplitude of the
    (k+1)-th harmonic. Handy for bells, metallic pads, choir-like stacks."""
    t = _t(dur, sr)
    phases = phases or [0.0] * len(harmonics)
    acc = np.zeros_like(t)
    for k, (amp, ph) in enumerate(zip(harmonics, phases), start=1):
        acc += amp * np.sin(2 * np.pi * freq * k * t + ph)
    peak = np.max(np.abs(acc))
    if peak > 0:
        acc /= peak
    return acc.astype(np.float32)


def fm(carrier_freq: float, mod_freq: float, dur: float,
       *, sr: int = SR, index: float = 2.0) -> np.ndarray:
    """Classic 2-op FM synthesis. `index` controls modulation depth —
    low (0.5) for electric piano, mid (2-4) for plucked strings,
    high (>5) for bells/metallic textures. Carrier-to-modulator ratio
    is derived from the two freqs (e.g. 2:1 for a bright even-harmonic
    sound, 1.414:1 for inharmonic clang)."""
    t = _t(dur, sr)
    modulator = np.sin(2 * np.pi * mod_freq * t)
    return np.sin(2 * np.pi * carrier_freq * t + index * modulator).astype(np.float32)


def noise(dur: float, *, sr: int = SR, band: tuple[float, float] | None = None,
          seed: int | None = None) -> np.ndarray:
    """White noise, optionally band-limited via a single-pole filter pair
    (not surgical — good enough for drum transients).

    `band=(low, high)` in Hz applies a high-pass at `low` and low-pass at
    `high`. Either bound can be None to skip that end."""
    rng = np.random.default_rng(seed)
    n = max(1, int(round(dur * sr)))
    x = rng.standard_normal(n).astype(np.float32)
    if band is None:
        return x / max(np.max(np.abs(x)), 1e-9)
    low, high = band
    if low is not None:
        # One-pole HPF: y[n] = a*(y[n-1] + x[n] - x[n-1]), a = RC/(RC+dt)
        rc = 1.0 / (2 * np.pi * low)
        a = rc / (rc + 1.0 / sr)
        y = np.zeros_like(x)
        prev_x = 0.0
        for i in range(len(x)):
            y[i] = a * (y[i - 1] + x[i] - prev_x) if i else a * (x[i] - prev_x)
            prev_x = x[i]
        x = y
    if high is not None:
        # One-pole LPF: y[n] = a*x[n] + (1-a)*y[n-1]
        rc = 1.0 / (2 * np.pi * high)
        a = (1.0 / sr) / (rc + 1.0 / sr)
        y = np.zeros_like(x)
        for i in range(len(x)):
            y[i] = a * x[i] + (1 - a) * (y[i - 1] if i else 0.0)
        x = y
    peak = np.max(np.abs(x))
    return (x / peak if peak > 0 else x).astype(np.float32)


# ============================================================
# Envelope + loop shaping
# ============================================================
def adsr(buf: np.ndarray, *, a: float = 0.01, d: float = 0.05, s: float = 0.7,
         r: float = 0.1, sr: int = SR) -> np.ndarray:
    """Multiply a buffer by an ADSR envelope. Times in seconds, sustain is
    level 0-1. If a+d+r exceeds the buffer, the stages get proportionally
    squeezed so the envelope always fits."""
    n = len(buf)
    env = np.ones(n, dtype=np.float32)
    na = int(a * sr)
    nd = int(d * sr)
    nr = int(r * sr)
    if na + nd + nr > n:
        scale = n / max(1, na + nd + nr)
        na = int(na * scale)
        nd = int(nd * scale)
        nr = int(nr * scale)
    ns = max(0, n - na - nd - nr)
    pos = 0
    if na > 0:
        env[pos:pos + na] = np.linspace(0, 1, na, endpoint=False)
        pos += na
    if nd > 0:
        env[pos:pos + nd] = np.linspace(1, s, nd, endpoint=False)
        pos += nd
    env[pos:pos + ns] = s
    pos += ns
    if nr > 0:
        env[pos:pos + nr] = np.linspace(s, 0, nr)
    return (buf * env).astype(np.float32)


def loop_tail(buf: np.ndarray, *, loop_start_frac: float = 0.5,
              min_block: int = 16) -> np.ndarray:
    """Trim the tail of a buffer to a zero crossing and pad to a 16-sample
    boundary (BRR requirement) so it loops cleanly when the SPC driver
    sets the loop point.

    `loop_start_frac` picks a *starting* search point for the zero-cross;
    the actual loop point is chosen by the driver via BRR block alignment —
    this function just guarantees the tail lands on a zero-cross and the
    total length is a multiple of 16 samples."""
    if len(buf) < 32:
        return buf
    n = len(buf)
    # Find the last zero-crossing from a target search-point onward so we
    # don't truncate too aggressively; prefer a crossing in the final quarter.
    search_start = max(int(n * loop_start_frac), n - n // 4)
    end = n - 1
    for i in range(n - 1, search_start, -1):
        if (buf[i] == 0) or (buf[i] * buf[i - 1] <= 0):
            end = i
            break
    trimmed = buf[: end + 1]
    # Pad with zeros to the next 16-sample boundary.
    pad = (-len(trimmed)) % min_block
    if pad:
        trimmed = np.concatenate([trimmed, np.zeros(pad, dtype=np.float32)])
    return trimmed


def normalize(buf: np.ndarray, *, peak: float = 0.95) -> np.ndarray:
    m = np.max(np.abs(buf))
    if m < 1e-9:
        return buf
    return (buf * (peak / m)).astype(np.float32)


# ============================================================
# WAV writer
# ============================================================
def write_wav(path: str, buf: np.ndarray, *, sr: int = SR) -> None:
    """Write a float buffer as 16-bit signed mono WAV at `sr` Hz."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clipped = np.clip(buf, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ============================================================
# Convenience: mix and concat
# ============================================================
def mix(*bufs: np.ndarray, weights: Sequence[float] | None = None) -> np.ndarray:
    """Point-wise add buffers, padding shorter ones with zeros. Peak-normalizes."""
    if not bufs:
        return np.zeros(1, dtype=np.float32)
    n = max(len(b) for b in bufs)
    acc = np.zeros(n, dtype=np.float32)
    weights = list(weights) if weights else [1.0] * len(bufs)
    for b, w in zip(bufs, weights):
        padded = np.zeros(n, dtype=np.float32)
        padded[: len(b)] = b
        acc += w * padded
    return normalize(acc)


def concat(*bufs: np.ndarray) -> np.ndarray:
    return np.concatenate(bufs).astype(np.float32)
