#!/usr/bin/env python3
"""Per-instrument A/B harness: TAD solo renders vs Munt MT-32 solo references.

  python tools/audio/solo_ab.py render [A B ...]   # build + capture TAD solos
  python tools/audio/solo_ab.py compare A          # numeric A/B for one voice

`render` filters the full generated MML down to one channel at a time,
compiles each with tad-compiler and captures real SPC output through the
Mesen testrunner (reusing verify.py's machinery). Reference WAVs are the
Munt solo renders produced by tools/audio scripts into build/mt32/.

`compare` picks exposed notes from the source MIDI for that voice and
reports attack/sustain/release envelope shape, sustain spectrum match and
pitch — ours vs reference — so instrument fixes can be judged one at a
time without mix confounds.
"""
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
SKILL = Path.home() / '.claude/skills/midi-to-snes-mml/scripts'
sys.path.insert(0, str(SKILL))

MML = REPO / 'build/verify_full_fixed/r010_mt32.mml'
PROJECT = REPO / 'audio/songs/r010_lucasarts/r010_lucasarts.terrificaudio'
OUT = REPO / 'build/solo_ab'

# channel letter -> (midi channels it plays, label)
CHANNELS = {
    'A': ((1,), 'lead flute (+low zone)'),
    'B': ((3,), 'organ top'),
    'C': ((3,), 'organ bottom'),
    'D': ((4,), 'marimba arp'),
    'E': ((5,), 'acoustic bass'),
    'F': ((2,), 'xylophone'),
    'G': ((7, 6), 'bottle + fantasia'),
    'H': ((9,), 'drums'),
}


def solo_mml(letter: str) -> Path:
    """Full MML filtered to one channel's body lines (directives kept)."""
    out_lines = []
    for line in MML.read_text().splitlines():
        first = line.split(' ', 1)[0] if line else ''
        if len(first) == 1 and 'A' <= first <= 'H':
            if first != letter:
                continue
        out_lines.append(line)
    p = OUT / f'solo_{letter}.mml'
    p.write_text('\n'.join(out_lines) + '\n')
    return p


def render(letters):
    from verify import (find_mesen, find_tad_compiler, compile_mml_to_spc,
                        run_mesen_dsp_capture)
    mesen, tad = find_mesen(), find_tad_compiler()
    OUT.mkdir(parents=True, exist_ok=True)
    for letter in letters:
        mml = solo_mml(letter)
        spc = OUT / f'solo_{letter}.spc'
        compile_mml_to_spc(mml, PROJECT, tad, spc)
        wav = OUT / f'tad_{letter}.wav'
        work = OUT / f'work_{letter}'
        work.mkdir(exist_ok=True)
        run_mesen_dsp_capture(spc, mesen, 97.0, work, audio_wav=wav)
        print(f"{letter}: {wav}")


def load(p):
    w = wave.open(str(p), 'rb')
    sr = w.getframerate()
    d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if w.getnchannels() == 2:
        d = d.reshape(-1, 2).mean(axis=1)
    w.close()
    return d.astype(np.float64), sr


def note_events(midi_chs):
    import mido
    MS = 2.0e6 / 480 / 1000
    m = mido.MidiFile(str(REPO / 'build/diag_compare/r010_rol.mid'))
    spans, active, t = [], {}, 0
    for msg in m.tracks[0]:
        t += msg.time
        ch = getattr(msg, 'channel', None)
        if ch not in midi_chs:
            continue
        sec = t * MS / 1000
        if msg.type == 'note_on' and msg.velocity > 0:
            active[(ch, msg.note)] = (sec, msg.velocity)
        elif msg.type in ('note_off', 'note_on') and (ch, msg.note) in active:
            on, vel = active.pop((ch, msg.note))
            spans.append((on, sec, msg.note, vel, ch))
    return sorted(spans)


def pick_exposed(spans, n=4):
    """Longest monophonic notes with the clearest gap before the next onset.

    Chord members are excluded: the Munt reference plays the channel's full
    polyphony while a TAD voice carries one reduction line, so only moments
    where the source channel is monophonic compare apples-to-apples."""
    scored = []
    for i, (on, off, note, vel, ch) in enumerate(spans):
        solo = all(off2 <= on or on2 >= off
                   for j, (on2, off2, *_ ) in enumerate(spans) if j != i)
        if not solo:
            continue
        nxt = spans[i + 1][0] if i + 1 < len(spans) else off + 5
        gap_after = nxt - off
        scored.append((min(off - on, 2.0) + min(gap_after, 1.0), (on, off, note, vel, ch)))
    scored.sort(reverse=True)
    out, used = [], []
    for _, ev in scored:
        if any(abs(ev[0] - u) < 1.0 for u in used):
            continue
        out.append(ev)
        used.append(ev[0])
        if len(out) >= n:
            break
    return sorted(out)


def refine_onset(d, sr, t_pred, search=0.8):
    """Locate the strongest energy rise within ±search of the predicted time."""
    hop = sr // 100
    a = max(0, int((t_pred - search) * sr))
    b = min(len(d), int((t_pred + search) * sr))
    seg = d[a:b]
    if len(seg) < 4 * hop:
        return t_pred
    e = np.array([np.sqrt((seg[i:i + hop] ** 2).mean() + 1e-9)
                  for i in range(0, len(seg) - hop, hop)])
    rise = e[2:] / (e[:-2] + e.max() * 0.01)
    i = int(np.argmax(rise)) + 2
    return a / sr + i * hop / sr


def env_db(d, sr, t0, t1, hop_ms=10):
    hop = int(sr * hop_ms / 1000)
    seg = d[int(t0 * sr):int(t1 * sr)]
    e = np.array([np.sqrt((seg[i:i + hop] ** 2).mean() + 1e-9)
                  for i in range(0, max(len(seg) - hop, 1), hop)])
    return 20 * np.log10(e / (e.max() + 1e-9) + 1e-6)


def spectrum_profile(d, sr, t0, dur, f0):
    seg = d[int(t0 * sr):int((t0 + dur) * sr)]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    freqs = np.fft.rfftfreq(len(seg), 1 / sr)
    amps = []
    for h in range(1, 9):
        m = (freqs > f0 * h * 0.94) & (freqs < f0 * h * 1.06)
        amps.append(spec[m].max() if m.any() else 0.0)
    amps = np.array(amps)
    return amps / (amps.max() + 1e-9)


def detect_onsets(d, sr, thresh_ratio=4.0):
    hop = sr // 100
    e = np.array([np.sqrt((d[i:i + hop] ** 2).mean() + 1e-9)
                  for i in range(0, len(d) - hop, hop)])
    on = []
    for i in range(2, len(e)):
        if e[i] > thresh_ratio * (e[i - 2] + 1e-3) and e[i] > e.max() * 0.02:
            if not on or i * hop / sr - on[-1] > 0.08:
                on.append(i * hop / sr)
    return np.array(on)


def fit_alignment(midi_onsets, wav_onsets):
    """Fit t_wav = a * t_midi + b by maximizing matches within 60ms."""
    best = (1.0, 0.0, -1)
    for a in np.arange(0.96, 1.045, 0.002):
        for b in np.arange(-0.3, 0.31, 0.03):
            mapped = a * midi_onsets + b
            hits = sum(1 for t in mapped if np.min(np.abs(wav_onsets - t)) < 0.06) \
                if len(wav_onsets) else 0
            if hits > best[2]:
                best = (a, b, hits)
    return best


def kon_times(letter):
    """Exact note onsets for our render from the solo capture's DSP log."""
    import json as _json
    path = OUT / f'work_{letter}' / 'verify_dsp_out.txt'
    if not path.exists():
        return np.array([])
    NTSC = 60.0988
    out = []
    for line in path.read_text().splitlines():
        if not line.startswith('{'):
            continue
        try:
            e = _json.loads(line)
        except ValueError:
            continue
        if e.get('r') == 0x4C and e.get('v'):
            out.append(e.get('f', 0) / NTSC)
    return np.array(out)


def compare(letter):
    midi_chs, label = CHANNELS[letter]
    ours, osr = load(OUT / f'tad_{letter}.wav')
    refs = {ch: load(REPO / f'build/mt32/solo_ch{ch}.wav') for ch in midi_chs}
    spans = note_events(set(midi_chs))
    picks = pick_exposed(spans)
    midi_on = np.array([s[0] for s in spans])
    kons = kon_times(letter)
    # The SPC clock is shared by all channels — fit only the start offset.
    # (A free-scale fit goes unstable on dense channels where mono reduction
    # makes spurious nearest-neighbor matches plentiful.)
    diffs = [k - m for m in midi_on for k in kons[np.abs(kons - m) < 0.6]]
    bo = float(np.median(diffs)) if diffs else 0.0
    ho = sum(1 for m in midi_on
             if len(kons) and np.min(np.abs(kons - (m + bo))) < 0.06)
    print(f"=== {letter}: {label} ===")
    print(f"alignment ours(KON): offset {bo:+.2f}s ({ho}/{len(midi_on)} onsets)")
    for on_m, off_m, note, vel, ch in picks:
        ref, rsr = refs[ch]
        dur = off_m - on_m
        pred = on_m + bo
        near = kons[np.abs(kons - pred) < 0.5] if len(kons) else []
        on = float(near[np.argmin(np.abs(near - pred))]) if len(near) else pred
        ron = refine_onset(ref, rsr, on_m - 0.03)
        if on + dur > len(ours) / osr - 0.3 or ron + dur > len(ref) / rsr - 0.3:
            continue
        print(f"[ch{ch}]", end='')
        _compare_note(ours, osr, ref, rsr, on, on + dur, ron, ron + dur, note, vel)


def _compare_note(ours, osr, ref, rsr, on, off, ron, roff, note, vel):
    f0 = 440 * 2 ** ((note - 69) / 12)
    dur = off - on
    tail = min(1.0, max(dur, 0.3))
    print(f"\nnote {note} vel {vel} on {on:.2f}s len {dur:.2f}s "
          f"(env spans note + {tail:.1f}s past note-off)")
    for name, d, sr, t0, t1 in (('ref ', ref, rsr, ron, roff),
                                ('ours', ours, osr, on, off)):
        e = env_db(d, sr, t0 - 0.05, t1 + tail)
        marks = e[::max(1, len(e) // 16)][:16]
        print(f"  {name} env dB: " + ' '.join(f"{x:5.0f}" for x in marks))
    sus_off = min(0.3, dur / 2)
    sus_d = min(0.25, max(dur - sus_off, 0.1))
    pr = spectrum_profile(ref, rsr, ron + sus_off, sus_d, f0)
    po = spectrum_profile(ours, osr, on + sus_off, sus_d, f0)
    print(f"  ref  harm: " + ' '.join(f"{x:.2f}" for x in pr))
    print(f"  ours harm: " + ' '.join(f"{x:.2f}" for x in po) +
          f"   dist {np.abs(pr - po).sum():.2f}")


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'render'
    if mode == 'render':
        letters = sys.argv[2:] or list(CHANNELS)
        render(letters)
    elif mode == 'compare':
        compare(sys.argv[2])
