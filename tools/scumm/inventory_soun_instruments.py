"""Inventory GM program usage across MI1 SOUN files.

Walks data/scumm_extracted/sounds/, identifies MIDI (SOU+ROL) vs SFX (SOU+SBL)
vs stub (24-byte) vs unknown, and for each MIDI extracts the embedded SMF then
enumerates which GM programs (per channel) are used.

Output: per-song program list + aggregate program frequency across intro-critical
rooms and the full corpus.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import mido

SOUND_DIR = Path(__file__).resolve().parents[2] / "data" / "scumm_extracted" / "sounds"

# Room IDs that make up the intro sequence. See CLAUDE.md game-flow + session
# status memories: logo -> vista -> lookout -> campfire -> ...
INTRO_ROOMS = {10, 11, 72, 38}

GM_NAMES = {
    0: "Grand Piano", 1: "Bright Piano", 2: "EP", 4: "Honky-Tonk",
    8: "Celesta", 9: "Glockenspiel", 10: "Music Box", 11: "Vibraphone",
    13: "Xylophone", 14: "Tubular Bells",
    16: "Drawbar Organ", 17: "Percussive Organ", 19: "Church Organ",
    24: "Nylon Guitar", 25: "Steel Guitar", 26: "Jazz Guitar", 27: "Clean Gtr",
    29: "Overdrive Gtr", 30: "Distortion Gtr",
    32: "Acoustic Bass", 33: "Fingered Bass", 34: "Picked Bass", 35: "Fretless",
    38: "Synth Bass 1",
    40: "Violin", 41: "Viola", 42: "Cello", 43: "Contrabass",
    46: "Harp", 47: "Timpani",
    48: "String Ens 1", 49: "String Ens 2", 50: "Synth Strings",
    52: "Choir Aahs", 55: "Orch Hit",
    56: "Trumpet", 57: "Trombone", 58: "Tuba", 60: "French Horn",
    61: "Brass Section",
    64: "Soprano Sax", 65: "Alto Sax", 66: "Tenor Sax", 67: "Baritone Sax",
    68: "Oboe", 69: "English Horn", 71: "Clarinet",
    72: "Piccolo", 73: "Flute", 74: "Recorder", 75: "Pan Flute",
    76: "Blown Bottle", 77: "Shakuhachi", 78: "Whistle", 79: "Ocarina",
    80: "Square Lead", 81: "Saw Lead",
    88: "New Age Pad", 89: "Warm Pad", 91: "Polysynth", 95: "Sweep",
    96: "Rain FX", 97: "Soundtrack FX", 98: "Crystal", 99: "Atmosphere",
    100: "Brightness", 101: "Goblins", 102: "Echoes", 103: "Sci-Fi",
    104: "Sitar", 105: "Banjo", 106: "Shamisen", 107: "Koto", 108: "Kalimba",
    109: "Bagpipe", 110: "Fiddle", 111: "Shanai",
    112: "Tinkle Bell", 113: "Agogo", 114: "Steel Drums", 115: "Woodblock",
    116: "Taiko", 117: "Melodic Tom", 118: "Synth Drum", 119: "Reverse Cymbal",
    120: "Gtr Fret Noise", 121: "Breath Noise",
    127: "Gunshot",
}


def classify(data: bytes) -> str:
    if len(data) < 12:
        return "stub"
    if data[:4] != b"SOU ":
        return "unknown"
    if data[8:12] == b"ROL ":
        return "midi"
    if data[8:12] == b"SBL ":
        return "sfx"
    return "unknown"


def extract_midi(data: bytes) -> bytes | None:
    idx = data.find(b"MThd")
    if idx < 0:
        return None
    return data[idx:]


def scan_programs(midi_bytes: bytes) -> dict[int, set[int]]:
    """Return {channel: {program, ...}} by walking ALL tracks."""
    out: dict[int, set[int]] = defaultdict(set)
    # Save to a temp path since mido wants a file or stream
    import io
    mf = mido.MidiFile(file=io.BytesIO(midi_bytes))
    active: dict[int, int] = {}  # channel -> current program
    for track in mf.tracks:
        for msg in track:
            if msg.type == "program_change":
                active[msg.channel] = msg.program
                out[msg.channel].add(msg.program)
            elif msg.type == "note_on" and msg.velocity > 0:
                # If channel played a note with no explicit program yet,
                # record as GM 0 (grand piano) -- the GM default.
                if msg.channel not in active and msg.channel != 9:
                    active[msg.channel] = 0
                    out[msg.channel].add(0)
    return dict(out)


def main() -> int:
    files = sorted(SOUND_DIR.glob("soun_*.bin"))
    if not files:
        print(f"no sound files under {SOUND_DIR}", file=sys.stderr)
        return 1

    kinds = Counter()
    intro_songs: list[tuple[str, dict[int, set[int]]]] = []
    all_programs: Counter[int] = Counter()
    intro_programs: Counter[int] = Counter()
    perc_rooms: set[int] = set()
    intro_perc_rooms: set[int] = set()

    for path in files:
        data = path.read_bytes()
        kind = classify(data)
        kinds[kind] += 1
        stem = path.stem  # soun_NNN_roomMMM
        try:
            room_id = int(stem.split("_room")[-1])
        except ValueError:
            room_id = -1
        if kind != "midi":
            continue
        midi = extract_midi(data)
        if not midi:
            continue
        try:
            progs = scan_programs(midi)
        except Exception as e:
            print(f"{stem}: parse error -- {e}", file=sys.stderr)
            continue
        # record drum channel usage
        if 9 in progs or any(msg_ch == 9 for msg_ch in progs):
            pass  # set below once we inspect
        uses_drum = scan_has_drum_channel(midi)
        if uses_drum:
            perc_rooms.add(room_id)
        for ch, progset in progs.items():
            for p in progset:
                all_programs[p] += 1
        if room_id in INTRO_ROOMS:
            intro_songs.append((stem, progs))
            for ch, progset in progs.items():
                for p in progset:
                    intro_programs[p] += 1
            if uses_drum:
                intro_perc_rooms.add(room_id)

    print("=== File breakdown ===")
    for k, v in kinds.most_common():
        print(f"  {k}: {v}")
    print()

    print(f"=== Intro-critical songs (rooms {sorted(INTRO_ROOMS)}) ===")
    for stem, progs in intro_songs:
        prog_list = sorted({p for s in progs.values() for p in s})
        prog_fmt = ", ".join(f"@{p:3d} {GM_NAMES.get(p, '?')}" for p in prog_list)
        drum = "DRUM " if any(ch == 9 for ch in progs) else "     "
        print(f"  {stem} {drum}-> {prog_fmt}")
    print()

    print(f"=== Intro program frequency (distinct songs using each) ===")
    for prog, cnt in intro_programs.most_common():
        print(f"  @{prog:3d} {GM_NAMES.get(prog, '?'):25s} ({cnt} song{'s' if cnt != 1 else ''})")
    print()

    print(f"=== Full-corpus program frequency (top 20) ===")
    for prog, cnt in all_programs.most_common(20):
        print(f"  @{prog:3d} {GM_NAMES.get(prog, '?'):25s} ({cnt} song{'s' if cnt != 1 else ''})")
    print()

    print(f"=== Intro rooms with drum channel: {sorted(intro_perc_rooms)} ===")
    print(f"=== Total rooms with drum channel: {len(perc_rooms)} ===")
    return 0


def scan_has_drum_channel(midi_bytes: bytes) -> bool:
    import io
    mf = mido.MidiFile(file=io.BytesIO(midi_bytes))
    for track in mf.tracks:
        for msg in track:
            if msg.type == "note_on" and msg.velocity > 0 and msg.channel == 9:
                return True
    return False


if __name__ == "__main__":
    sys.exit(main())
