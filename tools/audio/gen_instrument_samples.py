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


# MI1-specific instrument syntheses. We name them after what each MI1 MIDI
# channel plays so the MML and project config never need to change — the
# same `@shakuhachi`, `@glockenspiel`, etc. references keep working whether
# the sample is synthesized here or dropped in from an SF2 / sample pack.

def shakuhachi_cycle(n: int = 128) -> list[float]:
    """Breathy bamboo flute.

    Low harmonics with a hint of air-noise for character. One-cycle loop;
    all harmonics decay faster than fundamental so the sample has a
    mellow, pure-ish voice."""
    rng = random.Random(0x5A4B1)
    out = []
    for i in range(n):
        t = i / n
        # Fundamental + soft harmonics
        s = math.sin(2 * math.pi * t)
        s += 0.25 * math.sin(2 * math.pi * 2 * t + 0.3)
        s += 0.12 * math.sin(2 * math.pi * 3 * t + 0.6)
        s += 0.05 * math.sin(2 * math.pi * 4 * t + 1.1)
        # Very light air-noise component modulated by a slow sinusoid
        air = 0.04 * rng.uniform(-1, 1)
        out.append(s + air)
    filtered = onepole_lowpass(out, 3500.0)
    # Crossfade at loop seam
    k = 6
    for i in range(k):
        a = i / k
        filtered[i] = a * filtered[i] + (1 - a) * filtered[-(k - i)]
    return normalize(filtered)


def glockenspiel_cycle(n: int = 128) -> list[float]:
    """FM bell — same structure as the original celesta (1:3.5 ratio).

    Aliased from celesta_cycle so the project can reference
    `@glockenspiel` instead. MI1 SOUN 010's chord channel is Glockenspiel,
    which is close enough to celesta timbrally."""
    return celesta_cycle(n)


def acobass_cycle(n: int = 128) -> list[float]:
    """Acoustic bass — alias for bass_cycle.

    MI1's bass channel (ch5) is labeled Alto Sax in the MIDI but plays in
    a bass register; we've called it acobass throughout to reflect the
    role, not the MIDI tag."""
    return bass_cycle(n)


def atmosphere_cycle(n: int = 256) -> list[float]:
    """Pad / atmosphere — detuned-sines drone for the FX 8 sci-fi slot.

    Three sines at 1× / 1.003× / 0.997× the fundamental produce slow
    beating that gives the pad motion without being harmonically rich.
    Low-passed to keep it soft rather than sizzly."""
    out = []
    for i in range(n):
        t = i / n
        s = math.sin(2 * math.pi * t)
        s += 0.8 * math.sin(2 * math.pi * 1.003 * t + 0.4)
        s += 0.8 * math.sin(2 * math.pi * 0.997 * t + 0.8)
        # A small higher partial for body
        s += 0.3 * math.sin(2 * math.pi * 2 * t + 1.3)
        out.append(s)
    filtered = onepole_lowpass(out, 2200.0)
    k = 6
    for i in range(k):
        a = i / k
        filtered[i] = a * filtered[i] + (1 - a) * filtered[-(k - i)]
    return normalize(filtered)


# ---------------------------------------------------------------------------
# Percussion — one-shot decays, no loop
# ---------------------------------------------------------------------------
#
# All drums share the same design philosophy:
#  * A short broadband transient (2-10 ms) at the start carries the "attack"
#    character — this is what the ear uses to identify a drum hit. Without it,
#    drums sound mushy.
#  * A tonal body below that carries the pitch identity (kick = sub, snare =
#    shell, conga = tuned head, etc.) with an exponential amplitude decay.
#  * A post-process one-pole filter carves the overall timbre.
# These are synthesized at 32 kHz; tad-compiler handles the BRR conversion.

def _attack_click(out: list[float], rng: random.Random, length: int,
                  amp: float = 0.4, shape: float = 3.0) -> None:
    """Write a broadband transient click into `out[0:length]`.

    Noise shaped by `(1-t)**shape` envelope — larger shape = punchier click."""
    for i in range(min(length, len(out))):
        t = i / length
        out[i] += amp * ((1.0 - t) ** shape) * rng.uniform(-1.0, 1.0)


def kick_oneshot(n: int = 1024) -> list[float]:
    """Kick drum: transient click + pitch-swept sine body + sub tail.

    Three layers: a noise click (5 ms) for the beater attack, a sine that
    sweeps exponentially from 150 Hz to 45 Hz for the "thump", and a low
    fundamental that decays slowly for the body. Post LP at 900 Hz removes
    any residual hiss leaving a clean thump-sub."""
    rng = random.Random(0xC1C1)
    out = [0.0] * n
    _attack_click(out, rng, length=160, amp=0.3, shape=4.0)

    phase = 0.0
    for i in range(n):
        t = i / n
        freq = 45.0 + 105.0 * math.exp(-5.5 * t)
        phase += 2 * math.pi * freq / SR
        # Short ramp-up then exponential decay so the initial click + pitch
        # sweep fuse into a single "boom".
        env = (t / 0.03) if t < 0.03 else math.exp(-3.5 * (t - 0.03))
        out[i] += 0.85 * env * math.sin(phase)

    out = onepole_lowpass(out, 900.0)
    return normalize(out)


def snare_oneshot(n: int = 1024) -> list[float]:
    """Snare: bandpassed noise crack + detuned tonal shell.

    Previous version was too buzzy (full-band noise). Real snare has a
    band-limited "crack" centered around 2-4 kHz plus a tonal drum-head that
    rings briefly. Two detuned sines at 175 and 225 Hz give the shell its
    "rattle" character."""
    rng = random.Random(0x5A2E)
    out = [0.0] * n

    raw = [rng.uniform(-1.0, 1.0) for _ in range(n)]
    hp = onepole_highpass(raw, 1500.0)
    noise = onepole_lowpass(hp, 7000.0)
    for i, s in enumerate(noise):
        t = i / n
        env = ((1.0 - t) ** 1.5) * math.exp(-5 * t)
        out[i] += 0.75 * env * s

    for i in range(n):
        t = i / n
        env = math.exp(-9 * t)
        out[i] += 0.35 * env * math.sin(2 * math.pi * 175.0 * i / SR)
        out[i] += 0.25 * env * math.sin(2 * math.pi * 225.0 * i / SR)

    return normalize(out)


def hat_oneshot(n: int = 512) -> list[float]:
    """Closed hi-hat: inharmonic metallic mix with fast decay.

    Five detuned high sines at 2.8/4.2/5.8/7.6/9.4 kHz give the metallic
    character (vs the original plain white noise which just sounded like
    hiss). Fast exponential decay and a final HP to clip any rumble."""
    out = [0.0] * n
    freqs = [2800.0, 4200.0, 5800.0, 7600.0, 9400.0]
    for i in range(n):
        t = i / n
        env = math.exp(-16 * t)
        s = 0.0
        for f in freqs:
            s += math.sin(2 * math.pi * f * i / SR)
        out[i] = env * s
    out = onepole_highpass(out, 2000.0)
    return normalize(out)


def conga_oneshot(n: int, base_freq: float) -> list[float]:
    """Conga: pitched hand drum, short attack + resonant tonal body.

    Distinct from kick by being higher-pitched and tonal (you can sing
    along). The pitch "droops" ~15% over the decay — characteristic of a
    hand hitting a tensioned drumhead. Adds a 2nd harmonic for warmth."""
    rng = random.Random(int(base_freq) ^ 0xC00A)
    out = [0.0] * n
    _attack_click(out, rng, length=40, amp=0.35, shape=3.0)

    phase = 0.0
    for i in range(n):
        t = i / n
        freq = base_freq * (0.85 + 0.15 * math.exp(-6 * t))
        phase += 2 * math.pi * freq / SR
        env = math.exp(-5 * t)
        out[i] += 0.80 * env * math.sin(phase)
        out[i] += 0.20 * env * math.sin(2 * phase)

    out = onepole_lowpass(out, 2500.0)
    return normalize(out)


def conga_hi_oneshot(n: int = 768) -> list[float]:
    return conga_oneshot(n, base_freq=340.0)


def conga_lo_oneshot(n: int = 1024) -> list[float]:
    return conga_oneshot(n, base_freq=220.0)


def bongo_oneshot(n: int, base_freq: float) -> list[float]:
    """Bongo: higher than conga, shorter, more click forward."""
    rng = random.Random(int(base_freq) ^ 0xB0B0)
    out = [0.0] * n
    _attack_click(out, rng, length=30, amp=0.45, shape=2.5)

    phase = 0.0
    for i in range(n):
        t = i / n
        freq = base_freq * (0.9 + 0.1 * math.exp(-9 * t))
        phase += 2 * math.pi * freq / SR
        env = math.exp(-10 * t)
        out[i] += 0.7 * env * math.sin(phase)

    out = onepole_lowpass(out, 3500.0)
    return normalize(out)


def bongo_hi_oneshot(n: int = 512) -> list[float]:
    return bongo_oneshot(n, base_freq=620.0)


def bongo_lo_oneshot(n: int = 512) -> list[float]:
    return bongo_oneshot(n, base_freq=440.0)


def claves_oneshot(n: int = 256) -> list[float]:
    """Claves: a pair of sticks clacked together. Narrow, pitched, brief.

    Basically a very short decaying tone around 2.5-3 kHz, with a 2nd sine
    an interval above for the characteristic sharp "crack"."""
    out = []
    for i in range(n):
        t = i / n
        env = math.exp(-22 * t)
        s = math.sin(2 * math.pi * 2500.0 * i / SR)
        s += 0.5 * math.sin(2 * math.pi * 3100.0 * i / SR)
        out.append(env * s)
    return normalize(out)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

INSTRUMENTS = {
    # MI1-named melodic synths. Sample filenames match what
    # `smi.terrificaudio` and `soun_010.mml` reference, so this generator
    # is a pure drop-in replacement for any extracted-sample source.
    #
    # base Hz = 32000 / frame_count  (one cycle per N frames).
    # Each pitched instrument gets a `_high` variant at half the frame count
    # (double base Hz). SPC DSP can only pitch a sample ~2 octaves above its
    # native rate, so songs sitting in an upper register use the `_high`
    # sample to get the reach without per-channel MML transpose.
    "shakuhachi":        (shakuhachi_cycle,   128, "MI1 ch1 lead — flute fund+harmonics+air"),
    "shakuhachi_high":   (shakuhachi_cycle,    64, "shakuhachi at 500Hz base (reaches o7)"),
    "glockenspiel":      (glockenspiel_cycle, 128, "MI1 ch3 chord — FM 1:3.5 bell"),
    "glockenspiel_high": (glockenspiel_cycle,  64, "glock at 500Hz base"),
    "sitar":             (sitar_snapshot,     256, "MI1 ch4 arp — Karplus-Strong buzz"),
    "sitar_high":        (sitar_snapshot,     128, "sitar at 250Hz base"),
    "acobass":           (acobass_cycle,      128, "MI1 ch5 bass — triangle + 3rd harmonic"),
    "atmosphere":        (atmosphere_cycle,   256, "MI1 ch2 FX 8 — detuned-sine pad"),
    "atmosphere_high":   (atmosphere_cycle,   128, "atmosphere at 250Hz base"),

    # Drums: kick/snare/conga_hi/claves stay as SC-55 extracts (user
    # confirmed those sound fine). Only bongo_hi is synthesized because
    # the SC-55 bongo didn't fit the feel we want.
    "bongo_hi":          (bongo_hi_oneshot,   512, "pitched hand drum, higher than conga"),
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
