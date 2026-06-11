#!/usr/bin/env python3
"""Render the Munt MT-32 ground-truth set for one SCUMM song.

  python tools/audio/mt32_make_refs.py data/scumm_extracted/rooms/<room>/sounds/soun_00N.bin \
      --out build/mt32_<song>

Produces, in --out:
  <stem>.mid              extracted MIDI (when input is a soun .bin)
  <stem>_reference.wav    full mix through Munt (MT-32 reverb on)
  solo_ch<N>.wav          one per MIDI channel with notes (reverb on)
  dry_prog<P>.wav         one 3s note per unique melodic program, reverb off
                          (sample-extraction source; note = channel median)
  dry_drum_n<K>.wav       one-shot per used rhythm-channel note, reverb off
  *_ev.txt                the mt32render event list behind each WAV

Event lists are "<ms> <hex midi bytes>" lines for tools/audio/mt32render.c
(built at /tmp/mt32render in WSL; MT-32 ROMs from --roms). iMUSE-internal
sysex (F0 7D) is dropped; channel voice messages and real Roland sysex pass
through. Timestamps honor every set_tempo via solo_ab.tick_to_seconds.
"""
import argparse
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from solo_ab import tick_to_seconds

REPO = Path(__file__).resolve().parents[2]
EXTRACT = Path.home() / '.claude/skills/midi-to-snes-mml/scripts/extract_scumm.py'
REVERB_OFF = 'F0 41 10 16 12 10 00 03 00 6D F7'
DRUM_CH = 9


def wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace('\\', '/')
    return f"/mnt/{s[0].lower()}{s[2:]}" if s[1] == ':' else s


def render(events_path: Path, wav_path: Path, roms: Path, tail_ms: int):
    cmd = ("exec /tmp/mt32render "
           f"{wsl_path(roms / 'MT32_CONTROL.ROM')} {wsl_path(roms / 'MT32_PCM.ROM')} "
           f"{wsl_path(events_path)} {wsl_path(wav_path)} {tail_ms}")
    r = subprocess.run(['wsl', '-e', 'bash', '-lc', cmd],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"mt32render failed for {events_path.name}:\n{r.stderr}")


def midi_events_ms(m):
    """[(ms, bytes, channel|None)] for every renderable message."""
    to_sec = tick_to_seconds(m)
    out = []
    for tr in m.tracks:
        t = 0
        for msg in tr:
            t += msg.time
            if msg.is_meta:
                continue
            if msg.type == 'sysex':
                data = bytes(msg.bytes())
                if len(data) > 1 and data[1] == 0x7D:   # iMUSE-internal
                    continue
                out.append((to_sec(t) * 1000, data, None))
            elif msg.type in ('note_on', 'note_off', 'control_change',
                              'program_change', 'pitchwheel', 'aftertouch',
                              'polytouch'):
                out.append((to_sec(t) * 1000, bytes(msg.bytes()), msg.channel))
    out.sort(key=lambda e: e[0])
    return out


def write_events(path: Path, events):
    lines = [f"{ms:.3f} " + ' '.join(f'{b:02X}' for b in data)
             for ms, data, _ in events]
    path.write_text('\n'.join(lines) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('input', type=Path, help='soun_*.bin or .mid')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--roms', type=Path,
                    default=Path('E:/x86box/roms/sound/mt32'))
    ap.add_argument('--tail-ms', type=int, default=2000)
    ap.add_argument('--skip-dry', action='store_true',
                    help='only the full + solo references')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem

    midi_path = args.input
    if args.input.suffix.lower() == '.bin':
        midi_path = args.out / f'{stem}.mid'
        r = subprocess.run([sys.executable, str(EXTRACT), str(args.input),
                            '-o', str(midi_path), '--variant', 'rol'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"extract_scumm failed:\n{r.stderr or r.stdout}")

    import mido
    m = mido.MidiFile(str(midi_path))
    events = midi_events_ms(m)

    # channel inventory
    notes_by_ch = defaultdict(list)
    prog_by_ch = {}
    for ms, data, ch in events:
        if ch is None:
            continue
        status = data[0] & 0xF0
        if status == 0x90 and data[2] > 0:
            notes_by_ch[ch].append(data[1])
        elif status == 0xC0 and ch not in prog_by_ch:
            prog_by_ch[ch] = data[1]

    # full reference
    ev_full = args.out / f'{stem}_full_ev.txt'
    write_events(ev_full, events)
    render(ev_full, args.out / f'{stem}_reference.wav', args.roms, args.tail_ms)
    print(f'{stem}_reference.wav  ({len(events)} events)')

    # per-channel solos (sysex kept: device setup applies to all)
    for ch in sorted(notes_by_ch):
        solo = [(ms, d, c) for ms, d, c in events if c is None or c == ch]
        ev = args.out / f'solo_ch{ch}_ev.txt'
        write_events(ev, solo)
        render(ev, args.out / f'solo_ch{ch}.wav', args.roms, args.tail_ms)
        prog = 'drums' if ch == DRUM_CH else f'prog{prog_by_ch.get(ch, "?")}'
        print(f'solo_ch{ch}.wav  ({len(notes_by_ch[ch])} notes, {prog})')

    if args.skip_dry:
        return

    # dry per-program sample sources (reverb off, 3s hold, channel median note)
    for ch, prog in sorted(prog_by_ch.items()):
        if ch == DRUM_CH or not notes_by_ch.get(ch):
            continue
        note = int(median(notes_by_ch[ch]))
        ev = args.out / f'dry_prog{prog}_ev.txt'
        ev.write_text('\n'.join([
            f'0 {REVERB_OFF}',
            f'0 C{ch:X} {prog:02X}',
            f'0 B{ch:X} 07 7F',
            f'0 B{ch:X} 0A 40',
            f'300 9{ch:X} {note:02X} 64',
            f'3300 8{ch:X} {note:02X} 40',
        ]) + '\n')
        render(ev, args.out / f'dry_prog{prog}.wav', args.roms, 1500)
        print(f'dry_prog{prog}.wav  (note {note})')

    # dry drum one-shots
    for note in sorted(set(notes_by_ch.get(DRUM_CH, []))):
        ev = args.out / f'dry_drum_n{note}_ev.txt'
        ev.write_text('\n'.join([
            f'0 {REVERB_OFF}',
            f'300 9{DRUM_CH:X} {note:02X} 7F',
            f'1300 8{DRUM_CH:X} {note:02X} 40',
        ]) + '\n')
        render(ev, args.out / f'dry_drum_n{note}.wav', args.roms, 1000)
        print(f'dry_drum_n{note}.wav')


if __name__ == '__main__':
    main()
