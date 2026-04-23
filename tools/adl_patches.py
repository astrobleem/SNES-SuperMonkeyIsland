"""SCUMM AdLib (ADL) instrument-patch parsing + 2-op FM sample synthesis.

The SUPERGUYBRUSH soundbank pipeline. Every ADL-format SCUMM sound resource
ships an inline 2-op FM instrument definition per MIDI channel via a
`F0 7D 10 <ch> <26-byte patch> ... F7` sysex. This module decodes those
patches and renders each as a short BRR-friendly WAV, so we can use the
ORIGINAL AdLib timbres — bit-for-bit what MI1's AdLib-driver players heard
— as per-song instrument samples on the SNES.

Patch layout (matches `AdLibInstrument` in ScummVM `audio/adlib.cpp:59-76`):

    offset  field
    0       modCharacteristic       AM/VIB/EG/KSR/Mult (OPL2 reg 0x20)
    1       modScalingOutputLevel   KSL (bits 6-7) / TL (bits 0-5)
    2       modAttackDecay          AR (bits 4-7) / DR (bits 0-3)
    3       modSustainRelease       SL (bits 4-7) / RR (bits 0-3)
    4       modWaveformSelect       WS (bits 0-1 select sine/half/abs/quarter)
    5-9     carrier operator equivalents of 0-4
    10      feedback                FB (bits 1-3) / Connection (bit 0)
    11-25   extended envelope + duration fields (unused in our renderer)

Sysex envelope:
    F0  (opaque to mido — stripped before msg.data)
    7D  SCUMM manufacturer ID
    10  sub=0x10 instrument setup
    <ch>                         MIDI channel 0-15 the patch applies to
    <26-byte patch>              as above
    <padding to total 62 bytes>  trailing zeros — some subtypes use extras
    F7

SCUMM uses a 1:1 channel mapping: MIDI ch 0-8 → OPL2 voices 0-8, ch 9 is
drums in OPL2 percussion mode. For SNES we just render each patch as a
looped tonal sample at C4 (261.626 Hz) and let the TAD pitch table shift.
"""
from __future__ import annotations

import argparse
import sys
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Import the base waveform toolkit.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from synth_samples import write_wav, loop_tail, normalize, SR  # noqa: E402

# OPL2 frequency multiplier table (bits 0-3 of mod/carCharacteristic).
# Two entries are duplicated (index 11=10, 13=12) per OPL2 spec.
OPL_MULT = [0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 12, 12, 15, 15]

# Approximate OPL2 attack/decay times per rate code (0=forever, 15=instant).
# Real OPL2 uses key-scaling lookup tables; this is a coarse approximation
# that preserves relative envelope character across different patches.
AR_MS = [99999.0, 1000.0, 500.0, 250.0, 125.0,
         62.0, 31.0, 15.0, 8.0, 4.0,
         2.0, 1.0, 0.5, 0.3, 0.15, 0.05]
DR_MS = [99999.0, 4000.0, 2000.0, 1000.0, 500.0,
         250.0, 125.0, 62.0, 31.0, 15.0,
         8.0, 4.0, 2.0, 1.0, 0.5, 0.2]


@dataclass(frozen=True)
class AdlibPatch:
    mod_mult: float
    mod_ksl: int
    mod_tl: int        # 0=max output, 63=silent
    mod_ar: int        # 0-15 rate code (15=instant attack)
    mod_dr: int
    mod_sl: int        # 0=full sustain, 15=no sustain
    mod_rr: int
    mod_ws: int        # 0=sine 1=half 2=abs 3=quarter
    car_mult: float
    car_ksl: int
    car_tl: int
    car_ar: int
    car_dr: int
    car_sl: int
    car_rr: int
    car_ws: int
    feedback: int      # 0-7 FB amount
    connection: int    # 0=FM (serial), 1=additive (parallel)
    # Raw OPL register bytes, preserved for direct emulator use.
    mod_char_raw: int  # original data[0] (AM/VIB/EG/KSR/Mult)
    car_char_raw: int  # original data[5]


def patch_to_tad_adsr(patch: "AdlibPatch") -> str:
    """Derive a TAD `adsr A D SL SR` envelope from the patch's carrier bytes.

    The carrier operator's envelope shapes what you hear; the modulator's
    envelope shapes how FM brightness evolves over time (which we don't
    model in a static BRR sample). So we use car_ar/car_dr/car_sl/car_rr.

    OPL2 vs SNES DSP ADSR conventions differ:

    * OPL2 AR/DR/SL/RR are all 4-bit. SL is INVERTED (0 = full level, 15 =
      silent). EG-type bit (bit 5 of characteristic byte) selects "decay
      to silence past SL" (bit=0) vs "hold at SL until key-off" (bit=1).

    * SNES ADSR is (A: 4-bit, D: 3-bit, SL: 3-bit, SR: 5-bit). SL uses the
      opposite convention — 7 = full sustain, 0 = silent.

    For non-sustained OPL patches (EG=0), the note decays continuously past
    SL at DR rate. We emulate that in TAD by setting SL=7 (no initial drop)
    and letting SR carry the continuous fade derived from DR. For sustained
    patches (EG=1), we map SL directly and use RR as the decay-during-
    sustain proxy (fires when the note is held past the steady state).

    AR=0 in SCUMM's ADL corpus is the near-universal case (every one of the
    234 sub=0x00 messages surveyed had AR=0). Real OPL2 treats AR=0 as
    "silent, no attack", but the SCUMM AdLib driver must re-key or fast-
    attack somehow since these patches clearly sound on real hardware. We
    map AR=0 → TAD A=15 (instant) as the pragmatic choice.
    """
    car_eg_sustained = bool(patch.car_char_raw & 0x20)
    ar = patch.car_ar
    dr = patch.car_dr
    sl_inv = patch.car_sl  # 0=full, 15=silent (OPL convention)
    rr = patch.car_rr

    # TAD A — attack rate, 0..15, same direction (higher=faster)
    tad_a = 15 if ar == 0 else min(15, ar)

    # TAD D — decay rate, 3 bits. Scale from OPL's 4-bit.
    tad_d = min(7, dr >> 1)

    if car_eg_sustained:
        # Holds at SL until key-off; SR then controls release-after-steady.
        tad_sl = max(0, min(7, (15 - sl_inv) >> 1))
        tad_sr = min(31, rr * 2)
    else:
        # Non-sustained (EG=0): envelope decays past SL continuously. Map to
        # TAD by keeping SL=7 (no initial sustain step-down) and using SR to
        # carry the ongoing decay at OPL's DR rate, clamped to TAD's 0-31.
        tad_sl = 7
        tad_sr = min(31, max(1, dr * 2))
    return f"adsr {tad_a} {tad_d} {tad_sl} {tad_sr}"


def parse_patch(data: bytes) -> AdlibPatch:
    """Parse an 11+-byte SCUMM AdLib instrument struct."""
    if len(data) < 11:
        raise ValueError(f"patch too short: {len(data)} bytes")
    return AdlibPatch(
        mod_mult=OPL_MULT[data[0] & 0x0f],
        mod_ksl=(data[1] >> 6) & 0x03,
        mod_tl=data[1] & 0x3f,
        mod_ar=(data[2] >> 4) & 0x0f,
        mod_dr=data[2] & 0x0f,
        mod_sl=(data[3] >> 4) & 0x0f,
        mod_rr=data[3] & 0x0f,
        mod_ws=data[4] & 0x03,
        car_mult=OPL_MULT[data[5] & 0x0f],
        car_ksl=(data[6] >> 6) & 0x03,
        car_tl=data[6] & 0x3f,
        car_ar=(data[7] >> 4) & 0x0f,
        car_dr=data[7] & 0x0f,
        car_sl=(data[8] >> 4) & 0x0f,
        car_rr=data[8] & 0x0f,
        car_ws=data[9] & 0x03,
        feedback=(data[10] >> 1) & 0x07,
        connection=data[10] & 0x01,
        mod_char_raw=data[0],
        car_char_raw=data[5],
    )


def extract_channel_patches(midi_path: str | os.PathLike) -> dict[int, AdlibPatch]:
    """Parse an ADL-extracted MIDI, return {channel: patch} from sub=0x10 sysex."""
    import mido  # lazy import — mido isn't a hard dep of the toolkit
    mid = mido.MidiFile(str(midi_path))
    patches: dict[int, AdlibPatch] = {}
    for tr in mid.tracks:
        for msg in tr:
            if msg.type != 'sysex':
                continue
            d = bytes(msg.data)
            # d is the payload WITHOUT the F0/F7 framing.
            if len(d) < 4 or d[0] != 0x7d or d[1] != 0x10:
                continue
            ch = d[2]
            if ch > 15:
                continue  # not a MIDI channel; skip
            patch_bytes = d[3:29]  # 26-byte AdLibInstrument
            if len(patch_bytes) >= 11:
                patches[ch] = parse_patch(patch_bytes)
    return patches


# ============================================================
# OPL2 waveforms
# ============================================================
def _opl_waveform(phase: np.ndarray, ws: int) -> np.ndarray:
    """Return one of the 4 OPL2 waveforms evaluated at `phase` (radians)."""
    x = np.sin(phase)
    if ws == 0:
        return x
    if ws == 1:
        return np.maximum(x, 0.0)  # half-sine (positive lobe only)
    if ws == 2:
        return np.abs(x)  # full-wave rectified sine
    # ws == 3: quarter-sine — abs(sine) only during the first half of each cycle
    pos = (phase / (2 * np.pi)) % 1.0
    return np.where(pos < 0.5, np.abs(x), 0.0).astype(np.float32)


def _opl_envelope(ar: int, dr: int, sl: int, rr: int,
                  dur_s: float, sr: int = SR) -> np.ndarray:
    """Approximate OPL2 ADSR → time-domain envelope shape.

    OPL2's real behavior is key-scaled and non-linear; we aim for
    qualitative fidelity — attack-heavy patches stay attack-heavy, long
    decays stay long — without claiming register-accurate emulation.

    Special case: AR=0 in the raw patch byte is observed on ALL MI1 ADL
    patches examined so far. On real OPL2 that means "silent, no attack".
    The SCUMM AdLib driver clearly handles this differently — perhaps it
    key-scales on note-on or computes AR from other fields — but decoding
    that takes us deep into ScummVM internals. Treat AR=0 as "instant"
    for our purposes; the FM timbre comes from operator multipliers,
    waveforms, feedback, and TL, not from envelope shape at this stage.
    """
    n = max(1, int(dur_s * sr))
    effective_ar = ar if ar > 0 else 14
    a_s = min(AR_MS[effective_ar] / 1000.0, dur_s * 0.2)
    d_s = min(DR_MS[dr] / 1000.0, dur_s * 0.6)
    r_s = min(DR_MS[rr] / 1000.0, dur_s * 0.6)
    sustain = 1.0 - (sl / 15.0) if sl < 15 else 0.0

    a_n = max(1, int(a_s * sr))
    d_n = max(1, int(d_s * sr))
    r_n = max(1, int(r_s * sr))
    # Squeeze proportionally if stages don't fit.
    total = a_n + d_n + r_n
    if total > n:
        scale = n / total
        a_n = max(1, int(a_n * scale))
        d_n = max(1, int(d_n * scale))
        r_n = max(1, int(r_n * scale))
    s_n = max(0, n - a_n - d_n - r_n)

    env = np.zeros(n, dtype=np.float32)
    pos = 0
    env[pos:pos + a_n] = np.linspace(0.0, 1.0, a_n, endpoint=False)
    pos += a_n
    env[pos:pos + d_n] = np.linspace(1.0, sustain, d_n, endpoint=False)
    pos += d_n
    env[pos:pos + s_n] = sustain
    pos += s_n
    env[pos:pos + r_n] = np.linspace(sustain, 0.0, r_n)
    return env


# ============================================================
# 2-op FM renderer
# ============================================================
_DRUM_ROLES = {
    # role: (band_lo_hz, band_hi_hz, dur_s, gain, synthesis)
    # synthesis values describe what body to add to the noise burst:
    #   "opl_bd":  OPL2 rhythm-mode bass drum (channel 6, full FM)
    #   "opl_sd":  OPL2 rhythm-mode snare drum (ch7 carrier op + LFSR)
    #   "opl_tom": OPL2 rhythm-mode tom (ch8 modulator op)
    #   "opl_tcy": OPL2 rhythm-mode top cymbal (ch8 carrier, cross-modulated with HH)
    #   "opl_hh":  OPL2 rhythm-mode hi-hat (ch7 modulator, cross-modulated with TCY)
    #   "glide":   hand-rolled pitched noise burst (fallback for non-OPL roles)
    #   "body":    hand-rolled noise + stationary sine
    #   "tone":    hand-rolled near-pure pitched pop
    #   "noise":   hand-rolled band-filtered noise
    "kick":     (40.0,   180.0,   0.20, 1.20, "opl_bd"),
    "snare":    (250.0,  4000.0,  0.14, 1.10, "opl_sd"),
    "hat":      (5000.0, 11000.0, 0.05, 0.95, "opl_hh"),   # closed hat
    "crash":    (2000.0, 14000.0, 0.32, 1.00, "opl_tcy"),  # long cymbal
    "tom":      (80.0,   350.0,   0.18, 1.05, "opl_tom"),
    "conga":    (180.0,  600.0,   0.10, 1.05, "body"),     # no OPL equivalent
    "claves":   (2400.0, 3200.0,  0.05, 0.95, "tone"),
    "woodblock":(1500.0, 3500.0,  0.05, 0.90, "tone"),
    "cowbell":  (700.0,  1500.0,  0.10, 0.90, "tone"),
    "triangle": (3500.0, 9000.0,  0.25, 0.75, "noise"),
    "shaker":   (4000.0, 10000.0, 0.06, 0.70, "noise"),
}


def _opl_render_percussion(patch: AdlibPatch, role: str,
                           dur: float, sr: int = SR) -> np.ndarray:
    """Drive OPL2 rhythm mode to render a single percussion voice.

    OPL2 percussion mode repurposes channels 6/7/8 into five drum voices:
      BD  (bass drum)    — channel 6 full FM (both ops)
      SD  (snare drum)   — channel 7 carrier op (op 16) + noise gen
      TOM (tom-tom)      — channel 8 modulator op (op 14)
      TCY (top cymbal)   — channel 8 carrier op (op 17), cross-mod with HH
      HH  (hi-hat)       — channel 7 modulator op (op 13), cross-mod with TCY

    Register 0xBD controls the mode:
      bit 5: rhythm-mode enable
      bit 4: BD key
      bit 3: SD key
      bit 2: TOM key
      bit 1: TCY key
      bit 0: HH key

    We write the patch's (mod or car) operator settings into the
    appropriate op's registers, trigger the rhythm key, and render."""
    import pyopl
    opl = pyopl.opl(sr, 2, 2)

    # Op-offset lookup per role (which operator(s) to configure):
    #   BD uses ops 12 and 15 (ch6 mod + car)
    #   SD uses op 16           (ch7 car)
    #   TOM uses op 14          (ch8 mod)
    #   TCY uses op 17          (ch8 car)
    #   HH uses op 13           (ch7 mod)
    role_to_ops = {
        "opl_bd":  (12, 15, 6),   # mod, car, channel#
        "opl_sd":  (None, 16, 7),
        "opl_tom": (14, None, 8),
        "opl_tcy": (None, 17, 8),
        "opl_hh":  (13, None, 7),
    }
    mod_op, car_op, ch_num = role_to_ops[role]

    def write_op_from(op_off: int, char_raw: int, ksl: int, tl: int, ws: int):
        # AM/VIB/EG/KSR/Mult — force EG-sustained and KSR=0 so the envelope
        # holds up to the rate setting (we want a quick percussive decay).
        opl.writeReg(0x20 + op_off, (char_raw & 0xcf) | 0x20)
        opl.writeReg(0x40 + op_off, (ksl << 6) | (tl & 0x3f))
        # Fast attack, short decay — drums need punchy attack + quick fall.
        opl.writeReg(0x60 + op_off, 0xF8)  # AR=15, DR=8
        opl.writeReg(0x80 + op_off, 0x08)  # SL=0, RR=8
        opl.writeReg(0xE0 + op_off, ws & 0x03)

    # Configure the op(s) this role uses. Modulator op gets the patch's
    # mod-half params; carrier op gets the car-half.
    if mod_op is not None:
        write_op_from(mod_op, patch.mod_char_raw, patch.mod_ksl,
                      patch.mod_tl, patch.mod_ws)
    if car_op is not None:
        write_op_from(car_op, patch.car_char_raw, patch.car_ksl,
                      patch.car_tl, patch.car_ws)
    # Channel FB+Connection (only matters for BD which uses full FM path).
    if role == "opl_bd":
        opl.writeReg(0xC0 + ch_num, (patch.feedback << 1) | patch.connection)

    # Set the pitch for rhythm-mode voices via the channel's Fnum/block.
    # Roles have natural pitches: BD is low, SD mid, TOM tunable, TCY/HH
    # use noise + carrier cross-mod so pitch shapes the noise center.
    pitch_fnum_block = {
        "opl_bd":  (180, 3),   # ~60 Hz
        "opl_sd":  (345, 5),   # ~500 Hz
        "opl_tom": (345, 4),   # ~262 Hz
        "opl_tcy": (600, 5),   # ~900 Hz
        "opl_hh":  (600, 6),   # ~1800 Hz
    }[role]
    fnum, block = pitch_fnum_block
    opl.writeReg(0xA0 + ch_num, fnum & 0xFF)
    opl.writeReg(0xB0 + ch_num, (block << 2) | ((fnum >> 8) & 0x03))  # NO key-on bit

    # Enable rhythm mode + trigger the role's key bit.
    role_key_bit = {"opl_bd": 0x10, "opl_sd": 0x08,
                    "opl_tom": 0x04, "opl_tcy": 0x02, "opl_hh": 0x01}[role]
    opl.writeReg(0xBD, 0x20 | role_key_bit)

    # Render including a brief key-off tail so the full decay is captured.
    n = int(dur * sr)
    out = np.zeros(n, dtype=np.float32)
    pos = 0
    # Drop the key after ~10 ms so the release envelope plays out.
    keyoff_at = int(0.010 * sr)
    while pos < n:
        take = min(512, n - pos)
        if pos < keyoff_at <= pos + take:
            # Split the chunk at the key-off instant.
            pre = keyoff_at - pos
            if pre > 0:
                buf = bytearray(pre * 4)
                opl.getSamples(buf)
                pcm = np.frombuffer(bytes(buf), dtype=np.int16).reshape(-1, 2)
                out[pos:pos + pre] = pcm[:, 0].astype(np.float32) / 32768.0
                pos += pre
            opl.writeReg(0xBD, 0x20)  # rhythm mode on, all drums off
            continue
        buf = bytearray(take * 4)
        opl.getSamples(buf)
        pcm = np.frombuffer(bytes(buf), dtype=np.int16).reshape(-1, 2)
        out[pos:pos + take] = pcm[:, 0].astype(np.float32) / 32768.0
        pos += take
    return out


def render_drum(patch: AdlibPatch, role: str = "snare",
                sr: int = SR) -> np.ndarray:
    """Drum-specific render: short noise/tone burst shaped by the patch.

    `role` selects frequency band, duration, and synthesis strategy so
    that a kick sounds like a kick, a crash sounds like a crash, etc.
    Every role shares the same AdLib patch's carrier envelope for
    post-attack decay character (so all drums in a song have a common
    "voice" through that envelope), but the pre-envelope content — noise
    band, pitched transient, pure-tone pop — is chosen per role."""
    from synth_samples import noise, sine  # reuse base toolkit
    cfg = _DRUM_ROLES.get(role, _DRUM_ROLES["snare"])
    band_lo, band_hi, dur, gain, strategy = cfg

    # OPL2 rhythm-mode roles route to the real emulator.
    if strategy.startswith("opl_"):
        voice = _opl_render_percussion(patch, strategy, dur, sr) * gain
        pad = (-len(voice)) % 16
        if pad:
            voice = np.concatenate([voice, np.zeros(pad, dtype=np.float32)])
        return normalize(voice.astype(np.float32), peak=0.95)

    n = max(1, int(dur * sr))
    t = np.arange(n, dtype=np.float32) / sr
    center = (band_lo + band_hi) * 0.5

    noise_hit = noise(dur, band=(band_lo, band_hi),
                      seed=(hash((patch, role)) & 0xffffffff))

    if strategy == "glide":
        # Pitched transient: sweep from 2× center down to center over ~30 ms.
        # This gives kicks their "thump" and toms their pitched body.
        start_hz = min(center * 2.0, sr / 4)
        sweep = start_hz * np.exp(-t * 40.0) + center * (1.0 - np.exp(-t * 40.0))
        phase = 2 * np.pi * np.cumsum(sweep) / sr
        transient = np.sin(phase).astype(np.float32)
        hit = 0.5 * noise_hit + 0.5 * transient
    elif strategy == "body":
        # Add a stationary sine at band center — gives snares their
        # characteristic tuned crack and congas their pitched thump.
        body_sine = np.sin(2 * np.pi * center * t).astype(np.float32) * 0.6
        hit = 0.65 * noise_hit + 0.35 * body_sine
    elif strategy == "tone":
        # Near-pure pitched pop — claves/woodblock/cowbell don't have
        # much noise content at all, just a short bright click.
        tone = np.sin(2 * np.pi * center * t).astype(np.float32)
        hit = 0.15 * noise_hit + 0.85 * tone
    else:  # "noise"
        hit = noise_hit

    # Patch envelope shapes the decay. Always force a 3 ms linear attack
    # so the transient is punchy regardless of the patch's AR.
    env = _opl_envelope(patch.car_ar, patch.car_dr,
                        patch.car_sl, patch.car_rr, dur, sr)
    atk = max(1, int(0.003 * sr))
    env[:atk] = np.linspace(0.0, 1.0, atk)

    voice = hit * env * gain
    pad = (-len(voice)) % 16
    if pad:
        voice = np.concatenate([voice, np.zeros(pad, dtype=np.float32)])
    return normalize(voice.astype(np.float32), peak=0.95)


def render_patch(patch: AdlibPatch, base_freq: float = 261.626,
                 dur: float = 0.20, sr: int = SR,
                 sustain: bool = True,
                 integer_period: bool = True) -> tuple[np.ndarray, float]:
    """Render a 2-op FM voice by driving a real OPL2 emulator (pyopl).

    We configure the emulator's channel 0 directly from the patch's
    register bytes — mod/car AM/VIB/KSR/Mult, KSL/TL, Waveform,
    FB/Connection — and override the envelope bytes with
    `AR=15 DR=0 SL=0 RR=0 EG-sustained=1` so the emulator produces
    a stable sustained spectrum. Envelope timing stays in TAD (via
    the .terrificaudio `envelope` field), not in the sample.

    `base_freq` is the intended sample tonic. The emulator renders at
    its natural C4 F-number (Fnum=345, block=4 ≈ 261.6 Hz) regardless;
    we declare whatever we want for `freq` in the .terrificaudio and
    TAD pitch-shifts to match.

    `integer_period=True` trims the output to an integer number of
    modulator periods so BRR looping back to sample 0 is phase-continuous.

    Returns `(buf, effective_freq)`.
    """
    import pyopl
    opl = pyopl.opl(sr, 2, 2)  # (sample_rate, bytes_per_sample, channels)

    # Channel 0 uses op offsets 0 (modulator) and 3 (carrier).
    MOD, CAR = 0x00, 0x03

    def eg_sustained(char_raw: int) -> int:
        # Keep AM (bit 7), VIB (bit 6), Mult (bits 3:0); force EG=1 (bit 5),
        # clear KSR (bit 4). EG=1 makes the envelope hold at SL indefinitely
        # while key-on is asserted instead of decaying to zero.
        return (char_raw & 0xcf) | 0x20

    # Modulator registers
    opl.writeReg(0x20 + MOD, eg_sustained(patch.mod_char_raw))
    opl.writeReg(0x40 + MOD, (patch.mod_ksl << 6) | (patch.mod_tl & 0x3f))
    opl.writeReg(0x60 + MOD, 0xF0)  # AR=15, DR=0
    opl.writeReg(0x80 + MOD, 0x00)  # SL=0, RR=0
    opl.writeReg(0xE0 + MOD, patch.mod_ws & 0x03)
    # Carrier registers
    opl.writeReg(0x20 + CAR, eg_sustained(patch.car_char_raw))
    opl.writeReg(0x40 + CAR, (patch.car_ksl << 6) | (patch.car_tl & 0x3f))
    opl.writeReg(0x60 + CAR, 0xF0)
    opl.writeReg(0x80 + CAR, 0x00)
    opl.writeReg(0xE0 + CAR, patch.car_ws & 0x03)
    # Channel register: FB (bits 3:1) and Connection (bit 0).
    opl.writeReg(0xC0, (patch.feedback << 1) | patch.connection)

    # Key-on at C6-ish. Fnum=345/block=6 → ~1046 Hz internal.
    # Rendering two octaves up (vs C4) lets us declare freq=1046 and raise
    # last_octave to 7 without exceeding TAD's pitch-table ceiling, so MI1's
    # peak-register notes (up to MIDI 89) fit in range without channel
    # transposes. Timbral character at 1 kHz base is slightly brighter than
    # the 262 Hz base the patch was "designed for", but OPL2's FM math is
    # ratio-based (mod_mult/car_mult) so harmonic structure is preserved.
    fnum, block = 345, 6
    opl.writeReg(0xA0, fnum & 0xFF)
    opl.writeReg(0xB0, 0x20 | (block << 2) | ((fnum >> 8) & 0x03))

    # Render extra upfront so we can discard the 10 ms attack transient.
    transient_s = 0.012
    total_s = dur + transient_s
    total_n = int(total_s * sr)
    raw = np.zeros(total_n, dtype=np.float32)
    pos = 0
    while pos < total_n:
        take = min(512, total_n - pos)
        buf = bytearray(take * 4)
        opl.getSamples(buf)
        pcm = np.frombuffer(bytes(buf), dtype=np.int16).reshape(-1, 2)
        raw[pos:pos + take] = pcm[:, 0].astype(np.float32) / 32768.0
        pos += take
    transient_n = int(transient_s * sr)
    steady = raw[transient_n:]

    if integer_period:
        # OPL2 at Fnum=345/block=5 runs at 523.25 Hz internally; at our
        # output sr=32000 that's 32000/523.25 = 61.16 samples per period
        # — non-integer. Round to the nearest integer period-count that
        # still gives at least 100 ms of content; find the closest
        # zero-crossing near the target length and trim there so the loop
        # edges are phase-matched.
        render_freq = 1046.5
        samples_per_period = max(4, int(round(sr / render_freq)))
        n_periods = max(1, int(dur * sr) // samples_per_period)
        target_n = n_periods * samples_per_period
        # Search ±samples_per_period/2 around target for the best zero-cross.
        search_lo = max(0, target_n - samples_per_period // 2)
        search_hi = min(len(steady) - 1, target_n + samples_per_period // 2)
        best_n = target_n
        best_score = 1e9
        for i in range(search_lo, search_hi):
            # Good loop = sample[i] ≈ sample[0] AND slope[i] ≈ slope[0].
            if i < 2 or i >= len(steady) - 1:
                continue
            val_err = abs(steady[i] - steady[0])
            slope_i = steady[i + 1] - steady[i - 1]
            slope_0 = steady[1] - steady[0] if len(steady) > 1 else 0.0
            slope_err = abs(slope_i - slope_0)
            score = val_err + 0.5 * slope_err
            if score < best_score:
                best_score = score
                best_n = i
        voice = steady[:best_n]
        effective_freq = sr / samples_per_period
    else:
        voice = steady[:int(dur * sr)]
        effective_freq = base_freq

    if not sustain:
        # One-shot path: bake patch envelope.
        car_env = _opl_envelope(patch.car_ar, patch.car_dr,
                                patch.car_sl, patch.car_rr, len(voice) / sr, sr)
        voice = voice * car_env[:len(voice)]

    # Pad to a 16-sample BRR block boundary. For integer-period samples
    # this may add a handful of zero samples at the END — TAD loops from
    # sample 0 back through all the periods, so the zero tail is never
    # reached during playback (it only matters during release after the
    # envelope has already faded out).
    pad = (-len(voice)) % 16
    if pad:
        voice = np.concatenate([voice, np.zeros(pad, dtype=np.float32)])
    return normalize(voice.astype(np.float32), peak=0.9), effective_freq


# ============================================================
# CLI: dump patches + render samples
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("midi", help="ADL-extracted .mid file")
    ap.add_argument("--out-dir", required=True, help="where to write WAVs")
    ap.add_argument("--prefix", default="fm",
                    help="filename prefix (default: `fm` → fm_ch1.wav etc.)")
    ap.add_argument("--dur", type=float, default=0.20,
                    help="sample duration in seconds (default 0.20 for a "
                         "stable loop region; TAD adds its own attack/decay)")
    ap.add_argument("--base-freq", type=float, default=261.626,
                    help="sample tonic in Hz (default C4)")
    ap.add_argument("--dump", action="store_true",
                    help="also print patch params to stdout")
    args = ap.parse_args()

    patches = extract_channel_patches(args.midi)
    if not patches:
        print(f"no AdLib patches found in {args.midi}", file=sys.stderr)
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    # Emit an index the per-song .terrificaudio can read to pick up the
    # actual sample base frequency (may be detuned slightly from base_freq
    # when integer-period rendering is in effect).
    import json
    index: list[dict] = []
    for ch in sorted(patches):
        patch = patches[ch]
        if args.dump:
            print(f"ch{ch}: {patch}")
        if ch == 9:
            # Render one sample per drum role. Each role has its own
            # band, duration, and synthesis strategy — see _DRUM_ROLES.
            for role in _DRUM_ROLES:
                buf = render_drum(patch, role=role)
                path = os.path.join(args.out_dir,
                                    f"{args.prefix}_drum_{role}.wav")
                write_wav(path, buf)
                index.append({
                    "name": f"{args.prefix}_drum_{role}",
                    "file": os.path.basename(path),
                    "kind": "drum",
                    "role": role,
                })
                print(f"  ch{ch} → {path}  (drum.{role})")
        else:
            buf, eff_freq = render_patch(
                patch, base_freq=args.base_freq, dur=args.dur)
            path = os.path.join(args.out_dir, f"{args.prefix}_ch{ch}.wav")
            write_wav(path, buf)
            conn = "FM" if patch.connection == 0 else "additive"
            tad_env = patch_to_tad_adsr(patch)
            index.append({
                "name": f"{args.prefix}_ch{ch}",
                "file": os.path.basename(path),
                "kind": conn,
                "freq": eff_freq,
                "mod_mult": patch.mod_mult,
                "car_mult": patch.car_mult,
                "feedback": patch.feedback,
                "tad_envelope": tad_env,
            })
            eg = "sust" if (patch.car_char_raw & 0x20) else "decay"
            print(f"  ch{ch} → {path}  ({conn}, freq={eff_freq:.3f} Hz, "
                  f"mod×{patch.mod_mult:g} car×{patch.car_mult:g}, FB={patch.feedback}) "
                  f"EG={eg} → `{tad_env}`")
    idx_path = os.path.join(args.out_dir, f"{args.prefix}_index.json")
    with open(idx_path, "w") as fh:
        json.dump(index, fh, indent=2)
    print(f"  index → {idx_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
