"""Deterministic DSP synthesis of placeholder TAD instruments.

Writes 32 kHz mono 16-bit WAVs to audio/samples/instruments/*.wav — suitable
for tad-compiler's WAV -> BRR conversion. Each sample is designed so the
whole file loops seamlessly from end back to start (for pitched instruments)
or is a one-shot decay (for drums).

These are placeholders: timbre is approximate, not MI1-authentic. Drop-in
replacement with real samples at the same paths requires no other changes.

Instruments (Tier 1, covers intro + ~80% of MI1 corpus):
    celesta     GM @9   bell/glockenspiel   FM synthesis
    sax         GM @65  alto sax            bandlimited saw + lowpass
    sitar       GM @104 sitar               Karplus-Strong snapshot
    bass        GM @32  acoustic bass       triangle + lowpass
    kick        drums   pitched sweep
    snare       drums   noise + tonal body
    hat         drums   high-frequency noise
"""

from __future__ import annotations

import argparse
import math
import random
import struct
import sys
import wave
from pathlib import Path

SR = 32_000
OUT_DIR = Path(__file__).resolve().parents[2] / "audio" / "samples" / "instruments"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def save_wav(path: Path, samples: list[float]) -> int:
    """Write float samples in [-1, 1] as signed-16 mono WAV. Clips politely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for s in samples:
        if s > 1.0:
            s = 1.0
        elif s < -1.0:
            s = -1.0
        frames += struct.pack("<h", int(round(s * 32_767)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))
    return len(samples)


def normalize(samples: list[float], peak: float = 0.9) -> list[float]:
    m = max(abs(s) for s in samples) or 1.0
    k = peak / m
    return [s * k for s in samples]


def onepole_lowpass(samples: list[float], cutoff_hz: float) -> list[float]:
    # RC one-pole LPF; stable under any cutoff up to Nyquist.
    dt = 1.0 / SR
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = dt / (rc + dt)
    out = []
    y = 0.0
    for x in samples:
        y = y + a * (x - y)
        out.append(y)
    return out


def onepole_highpass(samples: list[float], cutoff_hz: float) -> list[float]:
    dt = 1.0 / SR
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = rc / (rc + dt)
    out = []
    prev_x = 0.0
    y = 0.0
    for x in samples:
        y = a * (y + x - prev_x)
        prev_x = x
        out.append(y)
    return out


# ---------------------------------------------------------------------------
# Pitched instruments — single-cycle (or few-cycle) loops
# ---------------------------------------------------------------------------

def celesta_cycle(n: int = 256) -> list[float]:
    # 2-op FM bell. Carrier:modulator = 1:3.5 gives inharmonic bell ring.
    # Modulator index static (no envelope — TAD handles decay via ADSR).
    out = []
    for i in range(n):
        t = i / n  # one cycle from 0..1
        mod = math.sin(2 * math.pi * 3.5 * t)
        car = math.sin(2 * math.pi * t + 2.0 * mod)
        # Sprinkle a second partial for extra shimmer.
        car += 0.25 * math.sin(2 * math.pi * 4 * t + 1.5 * mod)
        out.append(car)
    return normalize(out)


def sax_cycle(n: int = 256) -> list[float]:
    # Bandlimited sawtooth via additive synthesis, then one-pole lowpass
    # to carve the sax "body" formant region. Odd+even harmonics.
    out = []
    harmonics = 12
    for i in range(n):
        t = i / n
        s = 0.0
        for h in range(1, harmonics + 1):
            s += math.sin(2 * math.pi * h * t) / h
        out.append(s)
    # Pretend the sample plays at 250 Hz (32000/128). Lowpass at 2.5 kHz
    # rolls off the higher harmonics into "woody" territory.
    # Loop cleanly: apply filter, then blend first and last samples smoothly.
    filtered = onepole_lowpass(out, 2500.0)
    # Simple loop fix: crossfade 4 samples at edges.
    k = 4
    for i in range(k):
        a = i / k
        filtered[i] = a * filtered[i] + (1 - a) * filtered[-(k - i)]
    return normalize(filtered)


def sitar_snapshot(n: int = 512) -> list[float]:
    # Karplus-Strong: noise delay line with damping feedback, then snapshot
    # a late segment once the metallic overtone structure has emerged.
    rng = random.Random(0x5174A4)  # deterministic
    delay_len = n // 2  # fundamental period within the snapshot
    buf = [rng.uniform(-1.0, 1.0) for _ in range(delay_len)]
    # Run enough iterations for the string to settle into a buzzy loop.
    # Use low damping so harmonics persist (sitar has a jawari/buzz bridge).
    DAMP = 0.997
    total_steps = delay_len * 8
    idx = 0
    for step in range(total_steps):
        a = buf[idx]
        b = buf[(idx + 1) % delay_len]
        buf[idx] = DAMP * 0.5 * (a + b)
        idx = (idx + 1) % delay_len
    # Now capture 2 periods of the settled waveform = n samples.
    out = []
    for i in range(n):
        out.append(buf[(idx + i) % delay_len])
    return normalize(out)


def bass_cycle(n: int = 256) -> list[float]:
    # Triangle + 3rd-harmonic odd partial, low-passed. Punchy fundamental
    # with enough top-end bite to survive heavy pitch-shifting.
    out = []
    for i in range(n):
        t = i / n
        tri = 2 * abs(2 * ((t + 0.25) % 1.0 - 0.5)) - 1
        out.append(tri + 0.15 * math.sin(2 * math.pi * 3 * t))
    # Lowpass at ~800 Hz relative to 250 Hz fundamental -> keeps 2nd-3rd harmonic.
    filtered = onepole_lowpass(out, 1200.0)
    k = 4
    for i in range(k):
        a = i / k
        filtered[i] = a * filtered[i] + (1 - a) * filtered[-(k - i)]
    return normalize(filtered)


# ---------------------------------------------------------------------------
# Percussion — one-shot decays, no loop
# ---------------------------------------------------------------------------

def kick_oneshot(n: int = 1024) -> list[float]:
    # Pitch sweep 160 Hz -> 40 Hz over ~30 ms, linear amplitude decay.
    out = []
    phase = 0.0
    for i in range(n):
        t = i / n
        freq = 160.0 * math.exp(-4.5 * t) + 40.0 * t  # exp pitch drop
        phase += 2 * math.pi * freq / SR
        env = (1.0 - t) ** 2
        out.append(env * math.sin(phase))
    return normalize(out)


def snare_oneshot(n: int = 1024) -> list[float]:
    rng = random.Random(0x5A1AE5)
    out = []
    noise_prev = 0.0
    tone_phase = 0.0
    for i in range(n):
        t = i / n
        env_noise = (1.0 - t) ** 1.5
        env_body = math.exp(-8 * t)
        # White noise highpassed for snare rattle.
        raw = rng.uniform(-1.0, 1.0)
        noise = 0.7 * (raw - noise_prev)  # simple HP
        noise_prev = raw
        # Tonal body at ~200 Hz for the drum shell.
        tone_phase += 2 * math.pi * 200.0 / SR
        body = math.sin(tone_phase)
        out.append(0.8 * env_noise * noise + 0.3 * env_body * body)
    return normalize(out)


def hat_oneshot(n: int = 512) -> list[float]:
    rng = random.Random(0x8A7)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    hp = onepole_highpass(raw, 6000.0)
    out = []
    for i, s in enumerate(hp):
        t = i / n
        env = math.exp(-12 * t)
        out.append(env * s)
    return normalize(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

INSTRUMENTS = {
    # Pitched: 128-frame cycles -> 250 Hz base, matches ~B3.
    # tad-compiler allows ~5 octaves of upward pitch-shift from that base.
    "celesta": (celesta_cycle, 128, "pitched: 1-cycle bell (FM 1:3.5)"),
    "sax": (sax_cycle, 128, "pitched: bandlimited saw + 2.5kHz LPF"),
    "sitar": (sitar_snapshot, 256, "pitched: Karplus-Strong settled buzz"),
    "bass": (bass_cycle, 128, "pitched: triangle + 3rd harmonic, LPF"),
    # Percussion: one-shots with lower base; octave range stays narrow.
    "kick": (kick_oneshot, 1024, "drum: 160->40Hz pitch sweep + env"),
    "snare": (snare_oneshot, 1024, "drum: HP noise + 200Hz body"),
    "hat": (hat_oneshot, 512, "drum: HP-filtered noise, fast decay"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    for name, (fn, n, desc) in INSTRUMENTS.items():
        samples = fn(n)
        path = args.outdir / f"{name}.wav"
        wrote = save_wav(path, samples)
        base_hz = SR / len(samples)
        print(f"  {name:8s} {wrote:5d} frames  base {base_hz:7.2f} Hz  {desc}")

    print()
    print(f"wrote {len(INSTRUMENTS)} samples to {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
