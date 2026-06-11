#!/usr/bin/env python3
"""Auto-extract TAD instrument WAVs from dry Munt renders (batch, no hand-tuning).

  python tools/audio/auto_extract_mt32.py --jobs jobs.json --out audio/samples/instruments

jobs.json: [{"name": "p107", "dry": "build/mt32_kitchen/dry_prog107.wav",
             "note": 70}, ...]  -- melodic jobs; "note" is the rendered MIDI note
            {"name": "drum_n42", "dry": ".../dry_drum_n42.wav", "drum": true}

Automates the two decisions extract_mt32_samples.py takes by hand:

* percussive vs sustained: the 10ms peak envelope at ~1s vs its early peak.
  Percussive patches keep strike + a short bright loop (the DSP envelope
  shapes the ring); sustained patches loop a steady-state window.
* loop length: for sustained patches, the envelope's autocorrelation finds
  any baked-in tremolo/chorus AM period, and the loop is sized to span one
  full modulation cycle (rounded to whole pitch cycles, 16-aligned, capped
  at 8000 samples) -- the r010 organ lesson, automated.

The pitch-exactness machinery (measure period, micro-resample so the loop
holds exactly k cycles in a 16-aligned count, one-cycle seam crossfade,
freq = k*32000/loop_len) is reused from extract_mt32_samples.py.

Writes mt32_<name>.wav per job plus extract_report.json (freq, loop_setting,
mode, suggested envelope, modulation period found) for project-file assembly.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_mt32_samples import load_mono, save_wav, measure_period, fft_resample

NOTE_MS = 300
RATE = 32000


def envelope(d, sr, hop_ms=10):
    hop = sr * hop_ms // 1000
    n = len(d) // hop
    return np.array([np.abs(d[i*hop:(i+1)*hop]).max() for i in range(n)]), hop


def extract_drum(d, sr, name, out_dir, max_ms=250, freq=65.406):
    note_at = NOTE_MS * sr // 1000
    body = d[note_at:]
    peak = np.abs(body).max()
    if peak < 100:      # silent on the MT-32 (cf. rhythm note 82 lesson)
        return {"name": f"mt32_{name}", "mode": "silent", "brr_bytes": 0,
                "envelope": None, "freq": None}
    onset = max(0, int(np.argmax(np.abs(body) > 0.02 * peak)) - 16)
    body = body[onset:]
    hop = sr // 100
    env = np.array([np.abs(body[i:i+hop]).max() for i in range(0, len(body)-hop, hop)])
    above = np.nonzero(env > 0.02 * env.max())[0]
    end = (above[-1] + 1) * hop if len(above) else len(body)
    end = min(int(end), int(max_ms / 1000 * sr))
    end = (end // 16) * 16
    out = body[:end].copy()
    fade = int(0.005 * sr)
    out[-fade:] *= np.linspace(1, 0, fade)
    out *= 28000.0 / np.abs(out).max()
    save_wav(str(out_dir / f'mt32_{name}.wav'), out, sr)
    return {"name": f"mt32_{name}", "mode": "oneshot", "samples": int(end),
            "ms": int(end * 1000 // sr), "brr_bytes": int(end // 16 * 9),
            "envelope": "gain F127", "freq": float(freq)}


def extract_melodic(d, sr, name, note, out_dir):
    f0_hint = 440.0 * 2 ** ((note - 69) / 12)
    note_at = NOTE_MS * sr // 1000
    body = d[note_at:]
    peak = np.abs(body).max()
    onset_rel = max(0, int(np.argmax(np.abs(body) > 0.02 * peak)) - 32)
    onset = note_at + onset_rel

    env, hop = envelope(d[onset:], sr)
    early = env[:30].max() if len(env) >= 30 else env.max()
    sus_idx = slice(80, 120)            # 0.8-1.2s after onset
    sustained = len(env) > 120 and env[sus_idx].mean() > 0.25 * early

    def clamped_period(seg):
        """Autocorr period, but the MT-32 played the note we asked for --
        when the measurement disagrees with the hint by >1.5 st it locked a
        non-fundamental peak; trust the hint."""
        p = measure_period(seg, sr, f0_hint)
        if abs(12 * math.log2((sr / p) / f0_hint)) > 1.5:
            return sr / f0_hint
        return p

    if not sustained:
        # percussive: strike + short bright loop; DSP envelope shapes the ring
        attack_ms = 50
        attack_len = (int(attack_ms * sr / 1000) // 16) * 16
        steady0 = onset + attack_len
        period = clamped_period(d[steady0:steady0 + sr // 2])
        k = max(4, int(round(1200 / period)))
        loop_len = int(round(k * period / 16)) * 16
        mode, mod_ms, env_sug = "percussive", None, "adsr 15 2 0 0"
    else:
        # attack: first settle point of the envelope after 100ms
        attack_ms = 150
        for t in range(10, 40):
            if t + 10 < len(env) and abs(float(env[t+10]) - float(env[t])) < 0.05 * early:
                attack_ms = max(100, t * 10)
                break
        attack_len = (int(attack_ms * sr / 1000) // 16) * 16
        steady0 = onset + attack_len
        period = clamped_period(d[steady0:steady0 + sr])
        # modulation search: envelope autocorr over the sustain, lags 40-500ms
        sus_env = env[attack_ms // 10: attack_ms // 10 + 200].astype(np.float64)
        mod_ms = None
        if len(sus_env) > 60:
            x = sus_env - sus_env.mean()
            if x.std() > 0.01 * sus_env.mean():
                ac = np.correlate(x, x, 'full')[len(x)-1:]
                if ac[0] > 0:
                    ac /= ac[0]
                    lo, hi = 4, min(len(ac) - 1, 50)
                    i = lo + int(np.argmax(ac[lo:hi]))
                    if ac[i] > 0.25:
                        mod_ms = i * 10
        if mod_ms:
            mod_smp = mod_ms * sr // 1000
            k = max(8, int(math.ceil(mod_smp / period)))
        else:
            k = max(8, int(round(2400 / period)))
        loop_len = int(round(k * period / 16)) * 16
        while loop_len > 8000 and k > 8:        # BRR budget cap
            k -= max(1, k // 8)
            loop_len = int(round(k * period / 16)) * 16
        mode, env_sug = "sustained", "adsr 15 1 7 0"

    k = int(round(loop_len / period))
    factor = loop_len / (k * period)
    if not 0.98 < factor < 1.02:
        raise RuntimeError(f"{name}: resample factor {factor:.4f} out of bounds")

    keep = d[onset:]
    keep = fft_resample(keep, int(round(len(keep) * factor)))
    total = attack_len + loop_len
    out = keep[:total].copy()
    cyc = int(round(loop_len / k))
    ramp = np.linspace(0.0, 1.0, cyc)
    pre = out[attack_len - cyc:attack_len]
    out[total - cyc:] = out[total - cyc:] * (1 - ramp) + pre * ramp
    out *= 28000.0 / np.abs(out).max()
    save_wav(str(out_dir / f'mt32_{name}.wav'), out, sr)

    freq = k * 32000.0 / loop_len
    return {"name": f"mt32_{name}", "mode": mode, "note": int(note),
            "freq": round(float(freq), 3), "loop_setting": int(attack_len),
            "loop_len": int(loop_len), "cycles": int(k),
            "total_samples": int(total), "brr_bytes": int(total // 16 * 9),
            "mod_period_ms": int(mod_ms) if mod_ms else None,
            "envelope": env_sug}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=Path, required=True)
    ap.add_argument('--out', type=Path,
                    default=Path('audio/samples/instruments'))
    ap.add_argument('--report', type=Path, default=None)
    args = ap.parse_args()
    jobs = json.loads(args.jobs.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    report = []
    for job in jobs:
        d, sr = load_mono(job['dry'])
        if job.get('drum'):
            r = extract_drum(d, sr, job['name'], args.out)
        elif job.get('oneshot_pitched'):
            # single-pitch percussive chair (e.g. melodic tom): full ring,
            # no loop, freq = the rendered note so MML plays it 1:1
            f0 = 440.0 * 2 ** ((job['note'] - 69) / 12)
            r = extract_drum(d, sr, job['name'], args.out,
                             max_ms=job.get('max_ms', 400), freq=f0)
            r['note'] = job['note']
        else:
            r = extract_melodic(d, sr, job['name'], job['note'], args.out)
        report.append(r)
        print(f"{r['name']:<22} {r['mode']:<10} freq={r.get('freq')} "
              f"loop={r.get('loop_len')} brr={r['brr_bytes']}B "
              f"mod={r.get('mod_period_ms')}ms env={r['envelope']}")
    rp = args.report or args.jobs.with_name(args.jobs.stem + '_report.json')
    rp.write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    total = sum(r['brr_bytes'] for r in report)
    print(f"total BRR ~{total // 1024}KB across {len(report)} samples")
    return 0


if __name__ == '__main__':
    sys.exit(main())
