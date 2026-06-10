#!/usr/bin/env python3
"""Extract TAD-ready instrument WAVs from dry Munt (MT-32) note renders.

Input: build/mt32/dry_<name>.wav — mono-mixable stereo 32kHz renders of one
note per instrument (300ms preroll, note at 300ms, long hold).

Per instrument we keep [attack .. attack+loop] where the loop region holds
exactly k waveform cycles in a 16-aligned sample count (the whole signal is
micro-resampled by <0.5% to make that exact), so sustained notes loop as a
pure tone instead of an inharmonic comb. Declared freq is then exact by
construction: k * 32000 / loop_len. The renders are 32kHz = the SNES DSP
BRR rate, so no rate correction is needed anywhere.

A one-cycle crossfade at the loop seam blends chorused patches (organ,
fantasia) so the wrap doesn't click.
"""
import math
import os
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(REPO, 'build/mt32')
OUT_DIR = os.path.join(REPO, 'audio/samples/instruments')
NOTE_MS = 300  # note-on time inside the render

# name -> (midi_note, f0_hint, attack_ms, loop_len)
# loop_len % 16 == 0; the cycle count k = round(loop_len / measured_period)
# is derived per file, so the resample factor stays within ~1% and
# timbre/formants are untouched.
INSTRUMENTS = {
    'lead_flute': (72, 524.8, 200, 1280),
    # dedicated low zone for the lead's intro-drone register (notes 38-45) —
    # the C5 sample stretched -32st smears its attack and formants
    'lead_low':   (40, 82.4,  150, 1552),
    # organ loop must span ONE full tremolo cycle (~218ms = 72 pitch cycles):
    # a 100ms loop froze a fraction of the tremolo and pulsed at 10Hz
    'organ':      (64, 330.2, 150, 6976),
    'bottle':     (76, 661.6, 150, 1600),
    'fantasia':   (70, 467.3, 250, 1920),
    # sampled at the range center (ch4 plays 48-83 folded) so down-pitched
    # strikes keep their transient sharpness
    'marimba':    (66, 371.2, 200, 1280),
    'xylophone':  (76, 669.5, 200, 960),
    'acoubass':   (36, 65.0,  150, 6400),
}


def load_mono(path):
    w = wave.open(path, 'rb')
    sr = w.getframerate()
    d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    if w.getnchannels() == 2:
        d = d.reshape(-1, 2).mean(axis=1)
    w.close()
    assert sr == 32000, f"{path}: expected 32kHz render, got {sr}"
    return d, sr


def save_wav(path, data, sr):
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(np.clip(np.round(data), -32768, 32767).astype(np.int16).tobytes())
    w.close()


def measure_period(d, sr, f0_hint):
    seg = d - d.mean()
    ac = np.correlate(seg, seg, 'full')[len(seg) - 1:]
    lo = max(2, int(sr / (f0_hint * 1.3)))
    hi = min(len(ac) - 2, int(sr / (f0_hint * 0.7)))
    i = lo + int(np.argmax(ac[lo:hi]))
    a, b, c = ac[i - 1], ac[i], ac[i + 1]
    delta = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    return i + delta


def fft_resample(d, new_len):
    spec = np.fft.rfft(d)
    out_bins = new_len // 2 + 1
    if out_bins <= len(spec):
        spec = spec[:out_bins]
    else:
        spec = np.pad(spec, (0, out_bins - len(spec)))
    return np.fft.irfft(spec, new_len) * (new_len / len(d))


# one-shot percussion: no loop — trimmed at the decay floor (2% of peak)
# capped at max_ms, 5ms fade, padded to a 16-sample multiple.
DRUMS = {
    'kick':      100,
    'rim':       120,
    'bongo':     150,
    'conga_med': 150,
    'conga_hi':  100,
    'claves':    100,
}


def extract_drums():
    total = 0
    for name, max_ms in DRUMS.items():
        d, sr = load_mono(os.path.join(SRC_DIR, f'dry_drum_{name}.wav'))
        note_at = NOTE_MS * sr // 1000
        body = d[note_at:]
        peak = np.abs(body).max()
        onset = max(0, int(np.argmax(np.abs(body) > 0.02 * peak)) - 16)
        body = body[onset:]
        # decay floor: last index above 2% of peak (10ms-smoothed)
        hop = sr // 100
        env = np.array([np.abs(body[i:i + hop]).max() for i in range(0, len(body) - hop, hop)])
        above = np.nonzero(env > 0.02 * env.max())[0]
        end = min((above[-1] + 1) * hop if len(above) else len(body), int(max_ms * sr / 1000))
        end = (end // 16) * 16
        out = body[:end].copy()
        fade = int(0.005 * sr)
        out[-fade:] *= np.linspace(1, 0, fade)
        out *= 28000.0 / np.abs(out).max()
        save_wav(os.path.join(OUT_DIR, f'mt32_drum_{name}.wav'), out, sr)
        brr = (end // 16) * 9
        total += brr
        print(f"mt32_drum_{name:<10} len {end:>5} ({end * 1000 // sr}ms) brr {brr:>5}B")
    print(f"drum BRR total ~{total} bytes")


def main():
    if '--drums' in sys.argv:
        extract_drums()
        return 0
    total_brr = 0
    for name, (note, f0_hint, attack_ms, loop_len) in INSTRUMENTS.items():
        d, sr = load_mono(os.path.join(SRC_DIR, f'dry_{name}.wav'))
        note_at = NOTE_MS * sr // 1000
        # onset: first sample after note-on above 2% of peak, minus a 32-sample lead-in
        body = d[note_at:]
        peak = np.abs(body).max()
        onset = note_at + max(0, int(np.argmax(np.abs(body) > 0.02 * peak)) - 32)

        # measure the period over the region that becomes the loop
        attack_len = (int(attack_ms * sr / 1000) // 16) * 16
        steady0 = onset + attack_len
        period = measure_period(d[steady0:steady0 + sr], sr, f0_hint)
        k = int(round(loop_len / period))
        factor = loop_len / (k * period)
        if not 0.98 < factor < 1.02:
            print(f"{name}: factor {factor:.4f} out of bounds (period {period:.2f})", file=sys.stderr)
            return 1

        keep = d[onset:]
        keep = fft_resample(keep, int(round(len(keep) * factor)))
        total = attack_len + loop_len
        out = keep[:total].copy()

        # one-cycle crossfade at the loop seam: end of file must continue
        # into loop start (= sample[attack_len]) without a step
        cyc = int(round(loop_len / k))
        ramp = np.linspace(0.0, 1.0, cyc)
        pre = out[attack_len - cyc:attack_len]          # audio entering the loop
        out[total - cyc:] = out[total - cyc:] * (1 - ramp) + pre * ramp

        # normalize to common headroom; mix balance comes from MML velocities
        out *= 28000.0 / np.abs(out).max()

        dst = os.path.join(OUT_DIR, f'mt32_{name}.wav')
        save_wav(dst, out, sr)
        freq = k * 32000.0 / loop_len
        brr = (total // 16) * 9
        total_brr += brr
        print(f"mt32_{name:<12} note {note} period {period:7.3f} factor {factor:.5f} "
              f"total {total:>6} loop_start {attack_len:>5} loop {loop_len:>5} ({k} cyc) "
              f"freq {freq:8.3f} brr {brr:>5}B")
    print(f"melodic BRR total ~{total_brr} bytes")
    return 0


if __name__ == '__main__':
    sys.exit(main())
