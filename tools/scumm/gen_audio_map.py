"""Generate the SCUMM sound ID -> TAD audio map.

Walks data/scumm_extracted/sounds/*.bin, classifies each SOUN resource
(MIDI/SBL/ADL/stub), assigns TAD indices, and emits a WLA-DX .inc with
a 138-byte lookup table.

Byte encoding (SCUMM sound ID -> TAD dispatch):
    $FF         : no audio (stub or ADL-only — ADL can't run on SPC700)
    $00..$7F    : SFX index (use Tad_QueueSoundEffect)
    $80..$FE    : song index + $80 (use Tad_LoadSong)

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


def build_map(sound_dir: Path, tad_path: Path | None) -> tuple[list[int], list[tuple[int, str, int | None]]]:
    files = sorted(sound_dir.glob("soun_*.bin"))
    if not files:
        raise SystemExit(f"no sound files under {sound_dir}")

    sfx_overrides = read_tad_sfx_map(tad_path) if tad_path else {}

    entries: list[tuple[int, str, int | None]] = []  # (scumm_id, kind, tad_idx)
    table: list[int] = [NO_AUDIO] * 256  # oversize then trim at end
    next_song = 0
    max_id = -1

    for path in files:
        # stem looks like soun_NNN_roomMMM
        stem = path.stem
        try:
            sid = int(stem.split("_")[1])
        except (IndexError, ValueError):
            print(f"skip {path.name}: can't parse sound id", file=sys.stderr)
            continue
        max_id = max(max_id, sid)
        data = path.read_bytes()
        kind = classify(data)
        if kind == KIND_MIDI:
            if next_song > MAX_SONG_IDX:
                raise SystemExit(f"too many songs (>{MAX_SONG_IDX})")
            table[sid] = SONG_FLAG | next_song
            entries.append((sid, kind, next_song))
            next_song += 1
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

    if max_id < 0:
        raise SystemExit("no valid sound files found")
    # Pad the table to 256 entries so op_startSound et al can index any
    # 8-bit SCUMM ID without a bounds check. Entries beyond the last known
    # ID stay $FF (silent/no-audio) — matches the unused-ID semantics.
    return table[:256], entries


def emit_inc(out: Path, table: list[int], entries: list[tuple[int, str, int | None]]) -> None:
    n = len(table)
    songs = sum(1 for _, k, _ in entries if k == KIND_MIDI)
    sfx = sum(1 for _, k, _ in entries if k == KIND_SBL)
    stubs = sum(1 for _, k, _ in entries if k == KIND_STUB)
    adls = sum(1 for _, k, _ in entries if k == KIND_ADL)
    spks = sum(1 for _, k, _ in entries if k == KIND_SPK)

    lines: list[str] = []
    lines.append(";; Auto-generated by tools/scumm/gen_audio_map.py -- DO NOT EDIT.")
    lines.append(";;")
    lines.append(f";; SCUMM sound ID -> TAD dispatch ({n} entries).")
    lines.append(f";; Songs: {songs}  SFX: {sfx}  ADL-only: {adls}  SPK: {spks}  stubs: {stubs}")
    lines.append(";;")
    lines.append(";; Byte encoding:")
    lines.append(";;   $FF         : no audio (stub, AdLib-only, or PC Speaker)")
    lines.append(";;   $00..$7F    : SFX index (Tad_QueueSoundEffect)")
    lines.append(";;   $80..$FE    : song index | $80 (Tad_LoadSong)")
    lines.append("")
    lines.append('.section "ScummSoundMap" superfree')
    lines.append("ScummSoundMap:")

    # Emit 16 bytes per line with trailing comment showing ID range.
    for row_start in range(0, n, 16):
        row = table[row_start : row_start + 16]
        hex_vals = ", ".join(f"${b:02X}" for b in row)
        lines.append(f"  .db {hex_vals}  ; {row_start:3d}..{row_start + len(row) - 1:3d}")

    lines.append(".ends")
    lines.append("")

    # Manifest comment block at end for humans.
    lines.append(";; === Manifest ===")
    for sid, kind, tad in entries:
        if kind == KIND_MIDI:
            lines.append(f";; {sid:3d}  song {tad:3d}")
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
    args = ap.parse_args()

    table, entries = build_map(args.sound_dir, args.tad_project)
    emit_inc(args.output, table, entries)

    songs = sum(1 for _, k, _ in entries if k == KIND_MIDI)
    sfx_wired = sum(1 for _, k, idx in entries if k == KIND_SBL and idx is not None)
    sfx_silent = sum(1 for _, k, idx in entries if k == KIND_SBL and idx is None)
    adls = sum(1 for _, k, _ in entries if k == KIND_ADL)
    spks = sum(1 for _, k, _ in entries if k == KIND_SPK)
    stubs = sum(1 for _, k, _ in entries if k == KIND_STUB)
    print(f"wrote {args.output}")
    print(f"  {len(table)} entries | songs={songs} sfx_wired={sfx_wired} sfx_silent={sfx_silent} adl={adls} spk={spks} stub={stubs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
