"""Generate the SCUMM sound ID -> TAD audio map.

Walks data/scumm_extracted/sounds/*.bin, classifies each SOUN resource
(MIDI/SBL/ADL/stub), assigns TAD indices, and emits a WLA-DX .inc with
a 138-byte lookup table.

Byte encoding (SCUMM sound ID -> TAD dispatch):
    $FF         : no audio (stub, ADL-only, or MIDI not yet converted)
    $00..$7F    : SFX index (use Tad_QueueSoundEffect)
    $80..$FE    : global song ordinal + $80 (use Tad_LoadGlobalSong)

Song ordinals come from the groups manifest (tools/audio/gen_groups.py):
each converted song lists every SCUMM sound ID whose content matches it,
so shared songs map all their IDs to one ordinal. MIDI sounds without a
converted song are explicitly $FF (silent).

Output:
    build/audio/scumm_sound_map.inc — WLA-DX section with ScummSoundMap label

The table is keyed by SCUMM sound ID, which matches the extraction order
in `data/scumm_extracted/sounds/soun_NNN_roomMMM.bin` (NNN = sound ID).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOUND_DIR = ROOT / "data" / "scumm_extracted" / "sounds"
DEFAULT_OUT = ROOT / "build" / "audio" / "scumm_sound_map.inc"
DEFAULT_TAD = ROOT / "audio" / "smi.terrificaudio"
DEFAULT_GROUPS = ROOT / "build" / "audio" / "groups" / "manifest.json"

_SFX_NAME_RE = re.compile(r"^soun_(\d+)$")

KIND_STUB = "stub"
KIND_MIDI = "midi"
KIND_SBL = "sbl"
KIND_ADL = "adl"
KIND_SPK = "spk"
KIND_UNKNOWN = "?"

SONG_FLAG = 0x80
NO_AUDIO = 0xFF
MAX_SONG_IDX = 0x7F
MAX_SFX_IDX = 0x7F


def classify(data: bytes) -> str:
    # Anything not wrapped in a recognizable SOU+<type> container is a stub —
    # SCUMM SOUN directory entries can be 24-byte placeholders with no audio.
    if len(data) < 12 or data[:4] != b"SOU ":
        return KIND_STUB
    tag = data[8:12]
    if tag == b"ROL ":
        return KIND_MIDI
    if tag == b"SBL ":
        return KIND_SBL
    if tag == b"ADL ":
        return KIND_ADL
    if tag == b"SPK ":
        return KIND_SPK
    return KIND_UNKNOWN


def read_tad_sfx_map(tad_path: Path) -> dict[int, int]:
    """Return {scumm_id: tad_sfx_index} for every `soun_NNN` entry in the
    smi.terrificaudio `sound_effects` list, in declaration order."""
    data = json.loads(tad_path.read_text(encoding="utf-8"))
    order: list[str] = data.get("sound_effects", [])
    # TAD also scans high_priority/low_priority pools; include them for
    # completeness if anyone wires MI1 SFX into those tiers later.
    # Index ordering: high_priority first, then normal, then low_priority.
    full: list[str] = (
        data.get("high_priority_sound_effects", [])
        + order
        + data.get("low_priority_sound_effects", [])
    )
    out: dict[int, int] = {}
    for tad_idx, name in enumerate(full):
        m = _SFX_NAME_RE.match(name)
        if m:
            out[int(m.group(1))] = tad_idx
    return out


def read_groups_song_map(manifest_path: Path) -> dict[int, int]:
    """Return {scumm_id: global_song_ordinal} from the groups manifest."""
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: dict[int, int] = {}
    for g in m["groups"]:
        for s in g["songs"]:
            for sid in s["scumm_sids"]:
                out[sid] = s["ordinal"]
    return out


def build_map(sound_dir: Path, tad_path: Path | None,
              groups_manifest: Path) -> tuple[list[int], list[int], list[tuple[int, str, int | None]]]:
    files = sorted(sound_dir.glob("soun_*.bin"))
    if not files:
        raise SystemExit(f"no sound files under {sound_dir}")

    sfx_overrides = read_tad_sfx_map(tad_path) if tad_path else {}
    song_map = read_groups_song_map(groups_manifest)

    entries: list[tuple[int, str, int | None]] = []  # (scumm_id, kind, tad_idx)
    table: list[int] = [NO_AUDIO] * 256  # oversize then trim at end
    # RoomSongMap: room number -> the SCUMM sound ID of that room's music
    # (a song-flagged ID), or $FF for rooms with no converted music. The
    # engine plays this song on room entry so each room gets its theme
    # without relying on the (stubbed) script-side iMUSE triggers. Files are
    # processed in sorted order, so the lowest sound ID wins if a room has
    # more than one song resource.
    room_song: list[int] = [NO_AUDIO] * 256
    max_id = -1

    # Pre-scan every room's ENCD entry-script for startSound(immediate) opcodes
    # (SCUMM v5 0x1C <id>) -- the authority for which room actually plays which
    # song. Used below to OVERRIDE the filename base binding where a room
    # explicitly starts a song (fixes storage!=play rooms like 38<-sound 98).
    rooms_dir = ROOT / "data" / "scumm_extracted" / "rooms"
    encd_claims: dict[int, list[int]] = {}   # room -> startSound immediate ids, in order
    if rooms_dir.is_dir():
        for d in sorted(rooms_dir.iterdir()):
            m = re.match(r"room_(\d+)", d.name)
            if not m:
                continue
            encd = d / "scripts" / "encd.bin"
            if not encd.is_file():
                continue
            script = encd.read_bytes()
            ids = [script[i + 1] for i in range(len(script) - 1) if script[i] == 0x1C]
            if ids:
                encd_claims[int(m.group(1))] = ids

    for path in files:
        # stem looks like soun_NNN_roomMMM
        stem = path.stem
        try:
            sid = int(stem.split("_")[1])
        except (IndexError, ValueError):
            print(f"skip {path.name}: can't parse sound id", file=sys.stderr)
            continue
        room = None
        m_room = re.search(r"room(\d+)", stem)
        if m_room:
            room = int(m_room.group(1))
        max_id = max(max_id, sid)
        data = path.read_bytes()
        kind = classify(data)
        if kind == KIND_MIDI:
            ordinal = song_map.get(sid)
            if ordinal is None:
                # MIDI exists but no converted song yet -> silent
                entries.append((sid, kind, None))
                continue
            if ordinal > MAX_SONG_IDX:
                raise SystemExit(f"song ordinal {ordinal} exceeds {MAX_SONG_IDX}")
            table[sid] = SONG_FLAG | ordinal
            # BASE binding: bind a room to a song FILED under it (lowest ID wins).
            # Heuristic fallback for rooms whose music is triggered by non-ENCD
            # scripts (e.g. the intro/room 10 logo+theme) and so have no ENCD
            # startSound. Correct for storage==play rooms; the ENCD pass below
            # overrides storage!=play rooms (e.g. room 38).
            if room is not None and 0 <= room < 256 and room_song[room] == NO_AUDIO:
                room_song[room] = sid
            entries.append((sid, kind, ordinal))
        elif kind == KIND_SBL:
            tad_idx = sfx_overrides.get(sid)
            if tad_idx is None:
                # SBL exists but no matching soun_NNN entry in TAD yet -> silent
                entries.append((sid, kind, None))
                continue
            if tad_idx > MAX_SFX_IDX:
                raise SystemExit(f"SFX index {tad_idx} for scumm id {sid} exceeds max {MAX_SFX_IDX}")
            table[sid] = tad_idx
            entries.append((sid, kind, tad_idx))
        else:
            entries.append((sid, kind, None))

    # --- ENCD startSound OVERRIDE (authoritative where present) ---
    # ENCD startSound OVERRIDE (authoritative): bind each room to the first ENCD
    # startSound that resolves to a converted song, overriding the filename base.
    # See tools/scumm/decode_sound.py --encd-scan for the cross-check.
    for room, ids in encd_claims.items():
        if not (0 <= room < 256):
            continue
        for sid in ids:
            if 0 <= sid < 256 and table[sid] != NO_AUDIO and (table[sid] & SONG_FLAG):
                room_song[room] = sid             # first converted-song startSound wins
                break

    if max_id < 0:
        raise SystemExit("no valid sound files found")
    # Pad the table to 256 entries so op_startSound et al can index any
    # 8-bit SCUMM ID without a bounds check. Entries beyond the last known
    # ID stay $FF (silent/no-audio) — matches the unused-ID semantics.
    return table[:256], room_song[:256], entries


def emit_inc(out: Path, table: list[int], room_song: list[int],
             entries: list[tuple[int, str, int | None]]) -> None:
    n = len(table)
    songs = sum(1 for _, k, t in entries if k == KIND_MIDI and t is not None)
    midi_silent = sum(1 for _, k, t in entries if k == KIND_MIDI and t is None)
    sfx = sum(1 for _, k, _ in entries if k == KIND_SBL)
    stubs = sum(1 for _, k, _ in entries if k == KIND_STUB)
    adls = sum(1 for _, k, _ in entries if k == KIND_ADL)
    spks = sum(1 for _, k, _ in entries if k == KIND_SPK)

    lines: list[str] = []
    lines.append(";; Auto-generated by tools/scumm/gen_audio_map.py -- DO NOT EDIT.")
    lines.append(";;")
    lines.append(f";; SCUMM sound ID -> TAD dispatch ({n} entries).")
    lines.append(f";; Songs: {songs}  unconverted MIDI: {midi_silent}  SFX: {sfx}  ADL-only: {adls}  SPK: {spks}  stubs: {stubs}")
    lines.append(";;")
    lines.append(";; Byte encoding:")
    lines.append(";;   $FF         : no audio (stub, AdLib-only, PC Speaker, or unconverted MIDI)")
    lines.append(";;   $00..$7F    : SFX index (Tad_QueueSoundEffect)")
    lines.append(";;   $80..$FE    : global song ordinal | $80 (Tad_LoadGlobalSong)")
    lines.append("")
    room_songs = sum(1 for v in room_song if v != NO_AUDIO)
    lines.append('.section "ScummSoundMap" superfree')
    lines.append("ScummSoundMap:")

    # Emit 16 bytes per line with trailing comment showing ID range.
    for row_start in range(0, n, 16):
        row = table[row_start : row_start + 16]
        hex_vals = ", ".join(f"${b:02X}" for b in row)
        lines.append(f"  .db {hex_vals}  ; {row_start:3d}..{row_start + len(row) - 1:3d}")

    lines.append("")
    lines.append(f"  ;; RoomSongMap: room number -> that room's music sound ID")
    lines.append(f"  ;; ({len(room_song)} entries, {room_songs} rooms with music). The engine plays")
    lines.append(f"  ;; RoomSongMap[currentRoom] on room entry; $FF = leave the current song")
    lines.append(f"  ;; playing (room has no converted music of its own). Kept in the SAME")
    lines.append(f"  ;; section as ScummSoundMap so it shares that runtime-readable bank --")
    lines.append(f"  ;; a standalone superfree section can drift into banks $20-$3F which are")
    lines.append(f"  ;; not readable via lda.l at runtime (SA-1 window).")
    lines.append("RoomSongMap:")
    for row_start in range(0, len(room_song), 16):
        row = room_song[row_start : row_start + 16]
        hex_vals = ", ".join(f"${b:02X}" for b in row)
        lines.append(f"  .db {hex_vals}  ; {row_start:3d}..{row_start + len(row) - 1:3d}")

    lines.append(".ends")
    lines.append("")

    # Manifest comment block at end for humans.
    lines.append(";; === Manifest ===")
    room_lines = [f";; room {r:3d} -> sound {s:3d}" for r, s in enumerate(room_song) if s != NO_AUDIO]
    if room_lines:
        lines.append(";; --- RoomSongMap ---")
        lines.extend(room_lines)
        lines.append(";; --- ScummSoundMap ---")
    for sid, kind, tad in entries:
        if kind == KIND_MIDI and tad is not None:
            lines.append(f";; {sid:3d}  song {tad:3d}")
        elif kind == KIND_MIDI:
            lines.append(f";; {sid:3d}  MIDI (unconverted — silent)")
        elif kind == KIND_SBL and tad is not None:
            lines.append(f";; {sid:3d}   sfx {tad:3d}")
        elif kind == KIND_SBL:
            lines.append(f";; {sid:3d}   sfx (unregistered — silent until added to smi.terrificaudio)")
        elif kind == KIND_ADL:
            lines.append(f";; {sid:3d}   ADL (unplayable — silent)")
        elif kind == KIND_SPK:
            lines.append(f";; {sid:3d}   SPK (unplayable — silent)")
        else:
            lines.append(f";; {sid:3d}  stub")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sound-dir", type=Path, default=SOUND_DIR)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tad-project", type=Path, default=DEFAULT_TAD,
                    help="path to smi.terrificaudio; SFX indices are taken from its "
                    "sound_effects list (entries named soun_NNN map to SCUMM id NNN).")
    ap.add_argument("--groups-manifest", type=Path, default=DEFAULT_GROUPS,
                    help="groups manifest from tools/audio/gen_groups.py; song "
                    "ordinals and SCUMM-id coverage are taken from it.")
    args = ap.parse_args()

    if not args.groups_manifest.is_file():
        raise SystemExit(f"groups manifest not found: {args.groups_manifest} "
                         "(run tools/audio/gen_groups.py first)")

    table, room_song, entries = build_map(args.sound_dir, args.tad_project,
                                           args.groups_manifest)
    emit_inc(args.output, table, room_song, entries)

    songs = sum(1 for _, k, t in entries if k == KIND_MIDI and t is not None)
    midi_silent = sum(1 for _, k, t in entries if k == KIND_MIDI and t is None)
    sfx_wired = sum(1 for _, k, idx in entries if k == KIND_SBL and idx is not None)
    sfx_silent = sum(1 for _, k, idx in entries if k == KIND_SBL and idx is None)
    adls = sum(1 for _, k, _ in entries if k == KIND_ADL)
    spks = sum(1 for _, k, _ in entries if k == KIND_SPK)
    stubs = sum(1 for _, k, _ in entries if k == KIND_STUB)
    print(f"wrote {args.output}")
    print(f"  {len(table)} entries | songs={songs} midi_silent={midi_silent} "
          f"sfx_wired={sfx_wired} sfx_silent={sfx_silent} adl={adls} spk={spks} stub={stubs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
