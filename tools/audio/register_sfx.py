"""Idempotently register MI1 SFX in smi.terrificaudio + sound-effects.txt.

Reads a manifest file listing SCUMM sound IDs to register, then ensures:

    audio/smi.terrificaudio      : each ID has `sfx_soun_NNN` instrument +
                                   appears in the `sound_effects` list.
    audio/sfx/sound-effects.txt  : each ID has a `=== soun_NNN ===` stanza.
    audio/samples/sfx/*.wav      : each ID's WAV file exists (auto-convert
                                   from SBL if missing).

The map generator (tools/scumm/gen_audio_map.py) picks up the ordering of
the `sound_effects` list and emits a matching ScummSoundMap automatically.

Manifest format: one SCUMM ID per line, plus optional `#` comments.
    # intro SFX
    8
    99

Running this script is idempotent — re-running with the same manifest is
a no-op (except the sound-effects.txt regen, which is deterministic).

Audio RAM budget: each SFX BRR-compresses to ~56% of its PCM size. Keep
the manifest short until we have budget telemetry; audio RAM is 64 KB and
the Tier-1 instruments already consume ~6 KB.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAD_PROJECT = ROOT / "audio" / "smi.terrificaudio"
SFX_TXT = ROOT / "audio" / "sfx" / "sound-effects.txt"
SFX_WAV_DIR = ROOT / "audio" / "samples" / "sfx"
SOUN_DIR = ROOT / "data" / "scumm_extracted" / "sounds"
SBL_CONVERTER = ROOT / "tools" / "scumm" / "sbl_to_wav.py"
DEFAULT_MANIFEST = ROOT / "audio" / "registered_sfx.txt"


# Standard SFX instrument template. The SBL VOC rate is embedded in the WAV,
# so `freq: 261.63` + `first_octave/last_octave: 4` means "play note c4 to get
# native-rate playback" — consistent with the sound-effects.txt snippet below.
SFX_TEMPLATE: dict = {
    "freq": 261.63,
    "loop": "dupe_block_hack_filter_1",
    "loop_setting": 2,
    "evaluator": "default",
    "ignore_gaussian_overflow": False,
    "first_octave": 4,
    "last_octave": 4,
    "envelope": "adsr 15 7 2 0",
}


def load_manifest(path: Path) -> list[int]:
    if not path.exists():
        return []
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        ids.append(int(line))
    return ids


def find_soun_file(scumm_id: int) -> Path | None:
    matches = sorted(SOUN_DIR.glob(f"soun_{scumm_id:03d}_*.bin"))
    return matches[0] if matches else None


def ensure_wav(scumm_id: int, soun_path: Path) -> Path | None:
    # sbl_to_wav writes `<stem>.wav` — mirror the input filename.
    wav_path = SFX_WAV_DIR / f"{soun_path.stem}.wav"
    if wav_path.exists():
        return wav_path
    SFX_WAV_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["python3", str(SBL_CONVERTER), str(soun_path), "--outdir", str(SFX_WAV_DIR)],
        check=True,
    )
    return wav_path if wav_path.exists() else None


def wav_rel_source(wav_path: Path) -> str:
    # smi.terrificaudio source paths are relative to its own directory.
    return str(wav_path.relative_to(TAD_PROJECT.parent)).replace("\\", "/")


def update_tad_project(scumm_ids: list[int], wavs: dict[int, Path]) -> None:
    data = json.loads(TAD_PROJECT.read_text(encoding="utf-8"))
    instruments: list[dict] = data.setdefault("instruments", [])
    sfx_list: list[str] = data.setdefault("sound_effects", [])

    # Index existing instrument names.
    have_inst = {inst["name"] for inst in instruments}
    have_sfx = set(sfx_list)

    for sid in scumm_ids:
        inst_name = f"sfx_soun_{sid:03d}"
        sfx_name = f"soun_{sid:03d}"
        if inst_name not in have_inst:
            wav = wavs[sid]
            instruments.append({
                "name": inst_name,
                "source": wav_rel_source(wav),
                **SFX_TEMPLATE,
                "comment": f"MI1 SOUN {sid:03d}",
            })
            have_inst.add(inst_name)
        if sfx_name not in have_sfx:
            # Insert before any test_* names so real SFX indices stay contiguous.
            insert_at = len(sfx_list)
            for i, name in enumerate(sfx_list):
                if name.startswith("test_"):
                    insert_at = i
                    break
            sfx_list.insert(insert_at, sfx_name)
            have_sfx.add(sfx_name)

    TAD_PROJECT.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


_STANZA_RE = re.compile(r"^=== (\w+) ===\s*$", re.MULTILINE)


def regenerate_sfx_txt(scumm_ids: list[int]) -> None:
    # Read the existing file so we preserve any non-SOUN stanzas (like test_beep).
    existing = SFX_TXT.read_text(encoding="utf-8") if SFX_TXT.exists() else ""
    stanzas: dict[str, str] = {}
    # Split into stanzas by header regex.
    parts = _STANZA_RE.split(existing)
    # parts structure: [preamble, name1, body1, name2, body2, ...]
    for i in range(1, len(parts), 2):
        name = parts[i]
        body = parts[i + 1].strip("\n")
        stanzas[name] = body

    # Ensure a stanza exists for each soun_NNN id.
    for sid in scumm_ids:
        sfx_name = f"soun_{sid:03d}"
        if sfx_name in stanzas:
            continue
        # Use set_instrument_and_gain with fixed gain F127 so the SFX plays
        # at full volume regardless of the instrument's ADSR sustain level.
        stanzas[sfx_name] = (
            f"    set_instrument_and_gain sfx_{sfx_name} F127\n"
            f"    play_note c4 48"
        )

    # Emit: soun_* stanzas in sorted SCUMM-id order, then any other stanzas
    # (test_*, etc.) afterward.
    out_lines: list[str] = []
    soun_names = sorted(n for n in stanzas if _SFX_ID_RE.match(n))
    other_names = [n for n in stanzas if not _SFX_ID_RE.match(n)]
    for name in soun_names + other_names:
        out_lines.append(f"=== {name} ===\n{stanzas[name]}\n")
    SFX_TXT.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


_SFX_ID_RE = re.compile(r"^soun_\d+$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = ap.parse_args()

    ids = load_manifest(args.manifest)
    if not ids:
        print(f"no ids in manifest {args.manifest}", file=sys.stderr)
        return 1

    wavs: dict[int, Path] = {}
    for sid in ids:
        soun = find_soun_file(sid)
        if not soun:
            print(f"skip {sid}: no soun_{sid:03d}_*.bin", file=sys.stderr)
            continue
        wav = ensure_wav(sid, soun)
        if not wav:
            print(f"skip {sid}: conversion failed", file=sys.stderr)
            continue
        wavs[sid] = wav

    ready = [sid for sid in ids if sid in wavs]
    update_tad_project(ready, wavs)
    regenerate_sfx_txt(ready)

    print(f"registered {len(ready)} SFX: {ready}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
