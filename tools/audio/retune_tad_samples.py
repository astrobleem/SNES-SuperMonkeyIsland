#!/usr/bin/env python3
"""Retune r010 instrument WAVs for TAD's actual playback model.

Two systemic bugs fixed here (measured 2026-06-10 on the r010 render):

1. The SPC700 DSP consumes BRR at 32000 Hz/register-0x1000 and tad-compiler
   does NOT resample source WAVs. Our samples are 22050 Hz WAVs whose
   `freq` was measured at file rate, so every melodic voice rendered
   +6.45 st sharp (32000/22050 = 1.45125). Fix: declared freq must be the
   frequency the sample produces AT 32 kHz playback = f0_at_filerate x
   32000/filerate.

2. Short BRR loops with a non-integer number of waveform cycles turn every
   sustained note into an inharmonic comb (spacing = consumption_rate /
   loop_len) -- the "squeal". Fix: micro-resample each looped sample so the
   loop region holds exactly k cycles in a 16-aligned sample count, then
   freq is exact by construction: k x 32000 / loop_len.

Drums are deliberately untouched: noise hits ear-tuned in the current
registers; changing their declared freq would change their sound.
"""
import json
import math
import os
import sys
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT = os.path.join(REPO, 'audio/songs/r010_lucasarts/r010_lucasarts.terrificaudio')
RATE_RATIO = 32000.0 / 22050.0

# (instrument, k cycles in loop, loop_len samples). loop_len % 16 == 0 and
# loop_len ~= k * measured_period so the resample factor stays within ~2%.
LOOP_FIXES = {
    'r010_fm_ch2': (3, 64),     # atmosphere_h, period ~20.99
    'r010_fm_ch4': (3, 128),    # arp_poly72,   period ~42.95
    'r010_fm_ch5': (2, 448),    # acobass,      period ~225.09
    'r010_fm_ch7': (10, 336),   # violn76_hi,   period ~33.59
}

# Instruments that keep their WAV (long loop or no loop) but need the
# rate-ratio freq correction. Drums excluded on purpose.
FREQ_ONLY = ('r010_fm_ch1', 'r010_fm_ch3')


def load_wav(path):
    w = wave.open(path, 'rb')
    sr, n = w.getframerate(), w.getnframes()
    d = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64)
    if w.getnchannels() == 2:
        d = d.reshape(-1, 2).mean(axis=1)
    w.close()
    return d, sr


def save_wav(path, data, sr):
    w = wave.open(path, 'wb')
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(np.clip(np.round(data), -32768, 32767).astype(np.int16).tobytes())
    w.close()


def measure_period(d, sr, freq_hint):
    """Waveform period in samples via autocorrelation + parabolic refinement."""
    seg = d[len(d) // 2:]
    seg = seg - seg.mean()
    ac = np.correlate(seg, seg, 'full')[len(seg) - 1:]
    lo = max(2, int(sr / (freq_hint * 1.3)))
    hi = min(len(ac) - 2, int(sr / (freq_hint * 0.7)))
    i = lo + int(np.argmax(ac[lo:hi]))
    a, b, c = ac[i - 1], ac[i], ac[i + 1]
    delta = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    return i + delta


def fft_resample(d, new_len):
    """Whole-signal sinc resample via rfft truncation/zero-pad."""
    spec = np.fft.rfft(d)
    out_bins = new_len // 2 + 1
    if out_bins <= len(spec):
        spec = spec[:out_bins]
    else:
        spec = np.pad(spec, (0, out_bins - len(spec)))
    return np.fft.irfft(spec, new_len) * (new_len / len(d))


def main():
    proj = json.load(open(PROJECT))
    proj_dir = os.path.dirname(PROJECT)

    for inst in proj['instruments']:
        name = inst['name']
        if name in FREQ_ONLY:
            old = inst['freq']
            inst['freq'] = round(old * RATE_RATIO, 2)
            print(f"{name:<14} freq {old:8.2f} -> {inst['freq']:8.2f} (rate ratio only)")
            continue
        if name not in LOOP_FIXES:
            print(f"{name:<14} untouched")
            continue

        k, loop_len = LOOP_FIXES[name]
        src = os.path.normpath(os.path.join(proj_dir, inst['source']))
        d, sr = load_wav(src)
        # current declared freqs follow the old at-file-rate convention, so
        # they are a valid search hint for the autocorrelation band
        period = measure_period(d, sr, inst['freq'])
        if not (0.9 < loop_len / (k * period) < 1.1):
            print(f"{name}: period {period:.2f} inconsistent with k={k}, loop={loop_len}", file=sys.stderr)
            return 1

        factor = loop_len / (k * period)          # time-axis scale
        new_len = int(round(len(d) * factor)) // 16 * 16
        out = fft_resample(d, int(round(len(d) * factor)))[:new_len]
        loop_start = new_len - loop_len
        assert loop_start % 16 == 0 and loop_start > 0

        base, _ = os.path.splitext(src)
        dst = base + '_tuned.wav'
        save_wav(dst, out, sr)

        new_freq = round(k * 32000.0 / loop_len, 3)
        old_freq, old_loop = inst['freq'], inst.get('loop_setting')
        inst['source'] = os.path.relpath(dst, proj_dir).replace(os.sep, '/')
        inst['freq'] = new_freq
        inst['loop_setting'] = loop_start
        print(f"{name:<14} period {period:7.3f} factor {factor:.5f} "
              f"len {len(d)} -> {new_len}  loop {old_loop} -> {loop_start} (len {loop_len}, {k} cyc)  "
              f"freq {old_freq:8.2f} -> {new_freq:8.2f}")

    json.dump(proj, open(PROJECT, 'w'), indent=2)
    print(f"updated {PROJECT}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
