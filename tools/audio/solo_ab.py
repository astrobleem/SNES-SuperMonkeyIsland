#!/usr/bin/env python3
"""Per-instrument A/B harness: TAD solo renders vs Munt MT-32 solo references.

  python tools/audio/solo_ab.py render [A B ...]   # build + capture TAD solos
  python tools/audio/solo_ab.py compare A          # numeric A/B for one voice

All song-specific configuration comes from the conversion plan
(--plan, default r010): the SPC-voice -> MIDI-channel map is derived from
plan["assignments"], the MIDI tempo map is read from the source .mid, and
the remaining paths default to the r010 layout:

  --mml      build/verify_full_fixed/r010_mt32.mml   (converted MML)
  --midi     audio/songs/<plan dir>/<plan stem>.mid  (conversion source)
  --project  <plan dir>/<plan stem>.terrificaudio
  --ref-dir  build/mt32      (Munt refs: solo_ch<N>.wav per MIDI channel)
  --out      build/solo_ab   (captures + DSP logs)

`render` filters the full generated MML down to one channel at a time,
compiles each with tad-compiler and captures real SPC output through the
Mesen testrunner (reusing verify.py's machinery).

`compare` picks exposed notes from the source MIDI for that voice and
reports attack/sustain/release envelope shape, sustain spectrum match and
pitch — ours vs reference — so instrument fixes can be judged one at a
time without mix confounds.
"""
import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
SKILL = Path.home() / '.claude/skills/midi-to-snes-mml/scripts'
sys.path.insert(0, str(SKILL))

DEFAULT_PLAN = REPO / 'audio/songs/r010_lucasarts/r010_lucasarts.plan.json'
DEFAULT_MML = REPO / 'build/verify_full_fixed/r010_mt32.mml'

# Set by parse_args() / load_config(); module-level so balance.py can reuse.
PLAN = MML = MIDI = PROJECT = REF_DIR = OUT = None
CHANNELS: dict = {}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('mode', choices=['render', 'compare'])
    p.add_argument('letters', nargs='*', help='channel letters (default: all)')
    p.add_argument('--plan', type=Path, default=DEFAULT_PLAN)
    p.add_argument('--mml', type=Path, default=DEFAULT_MML)
    p.add_argument('--midi', type=Path, default=None,
                   help='conversion source .mid (default: <plan stem>.mid)')
    p.add_argument('--project', type=Path, default=None,
                   help='.terrificaudio (default: <plan stem>.terrificaudio)')
    p.add_argument('--ref-dir', type=Path, default=REPO / 'build/mt32',
                   help='Munt reference dir with solo_ch<N>.wav files')
    p.add_argument('--out', type=Path, default=REPO / 'build/solo_ab')
    return p.parse_args(argv)


def load_config(args):
    """Resolve paths + derive the voice->channel map from the plan."""
    global PLAN, MML, MIDI, PROJECT, REF_DIR, OUT, CHANNELS
    PLAN = json.loads(Path(args.plan).read_text())
    MML = Path(args.mml)
    stem = Path(args.plan).name.replace('.plan.json', '')
    MIDI = Path(args.midi) if args.midi else Path(args.plan).parent / f'{stem}.mid'
    PROJECT = (Path(args.project) if args.project
               else Path(args.plan).parent / f'{stem}.terrificaudio')
    REF_DIR = Path(args.ref_dir)
    OUT = Path(args.out)

    CHANNELS = {}
    for a in PLAN['assignments']:
        letter = chr(ord('A') + a['spc'] - 1)
        chs = [a['midi_ch']]
        # A disjoint merge makes the voice genuinely two-channel; a fill
        # merge only borrows scraps, so probes stay on the primary channel.
        if a.get('merge_ch') is not None and a.get('merge_mode') != 'fill':
            chs.append(a['merge_ch'])
        label = f"{a.get('role', '?')} ch{'+'.join(str(c) for c in chs)}"
        CHANNELS[letter] = (tuple(chs), label)


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
    dur = song_duration() + 2.0
    for letter in letters:
        mml = solo_mml(letter)
        spc = OUT / f'solo_{letter}.spc'
        compile_mml_to_spc(mml, PROJECT, tad, spc)
        wav = OUT / f'tad_{letter}.wav'
        work = OUT / f'work_{letter}'
        work.mkdir(exist_ok=True)
        run_mesen_dsp_capture(spc, mesen, dur, work, audio_wav=wav)
        print(f"{letter}: {wav}")


def load(p):
    w = wave.open(str(p), 'rb')
    sr = w.getframerate()
    d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if w.getnchannels() == 2:
        d = d.reshape(-1, 2).mean(axis=1)
    w.close()
    return d.astype(np.float64), sr


def tick_to_seconds(m):
    """Absolute-tick -> seconds converter honoring every set_tempo event."""
    tempos = []
    for tr in m.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.type == 'set_tempo':
                tempos.append((t, msg.tempo))
    tempos.sort()
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))
    segs, sec = [], 0.0
    for i, (tick, tempo) in enumerate(tempos):
        if i > 0:
            sec += ((tick - tempos[i - 1][0]) * tempos[i - 1][1]
                    / m.ticks_per_beat / 1e6)
        segs.append((tick, sec, tempo))

    def to_sec(tick):
        for st, ss, tempo in reversed(segs):
            if st <= tick:
                return ss + (tick - st) * tempo / m.ticks_per_beat / 1e6
        return 0.0
    return to_sec


def note_events(midi_chs):
    """(on_s, off_s, note, vel, ch) spans for the given MIDI channels."""
    import mido
    m = mido.MidiFile(str(MIDI))
    to_sec = tick_to_seconds(m)
    spans = []
    for tr in m.tracks:
        t, active = 0, {}
        for msg in tr:
            t += msg.time
            ch = getattr(msg, 'channel', None)
            if ch not in midi_chs:
                continue
            sec = to_sec(t)
            if msg.type == 'note_on' and msg.velocity > 0:
                active[(ch, msg.note)] = (sec, msg.velocity)
            elif msg.type in ('note_off', 'note_on') and (ch, msg.note) in active:
                on, vel = active.pop((ch, msg.note))
                spans.append((on, sec, msg.note, vel, ch))
    return sorted(spans)


def song_duration():
    """Length of the source MIDI in seconds (all channels)."""
    spans = note_events(set(range(16)))
    return max(s[1] for s in spans) if spans else 90.0


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


def kon_times(letter):
    """Exact note onsets for our render from the solo capture's DSP log."""
    path = OUT / f'work_{letter}' / 'verify_dsp_out.txt'
    if not path.exists():
        return np.array([])
    NTSC = 60.0988
    out = []
    for line in path.read_text().splitlines():
        if not line.startswith('{'):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('r') == 0x4C and e.get('v'):
            out.append(e.get('f', 0) / NTSC)
    return np.array(out)


def compare(letter):
    midi_chs, label = CHANNELS[letter]
    ours, osr = load(OUT / f'tad_{letter}.wav')
    refs = {ch: load(REF_DIR / f'solo_ch{ch}.wav') for ch in midi_chs}
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
    args = parse_args()
    load_config(args)
    if args.mode == 'render':
        render(args.letters or list(CHANNELS))
    elif args.mode == 'compare':
        for letter in (args.letters or list(CHANNELS)):
            compare(letter)
