#!/usr/bin/env python3
"""Generate MSU-1 PCM tracks from the Munt MT-32 reference renders.

  python3 tools/audio/gen_msu_tracks.py

The title-screen MUSIC row toggles SCUMM.musicMode between SPC700 (TAD)
and MSU-1. The MSU-1 branch of op_startMusic plays track number = raw
SCUMM sound ID, so for every converted song this writes
distribution/SuperMonkeyIsland-<sid>.pcm for each SCUMM sound ID the song
covers (from the groups manifest). Missing .pcm files fail to graceful
silence, so this generator is OPTIONAL (manual `make msu-tracks`) — it
needs the Munt reference WAVs under build/mt32_*/ (tools/audio/
mt32_make_refs.py regenerates those; WSL + MT-32 ROMs required).

Audio path: 32kHz stereo Munt render -> linear resample to 44.1kHz ->
tools/msu1pcmwriter.py (MSU1 magic + loopstart 0 = loop whole track).
Room music loops; part1 also loops but its script transitions away.
"""
import json
import struct
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / 'build/audio/groups/manifest.json'
DIST = REPO / 'distribution'
PCMWRITER = REPO / 'tools/msu1pcmwriter.py'

# groups-manifest song name -> Munt reference directory under build/
REF_DIR_OVERRIDES = {'r010_lucasarts': 'mt32_r010_check'}


def ref_wav(song_name: str) -> Path:
    dirname = REF_DIR_OVERRIDES.get(
        song_name, 'mt32_' + song_name.split('_', 1)[1])
    d = REPO / 'build' / dirname
    refs = sorted(d.glob('*_reference.wav'))
    if len(refs) != 1:
        raise RuntimeError(f'{song_name}: expected 1 reference in {d}, '
                           f'found {[r.name for r in refs]}')
    return refs[0]


def resample_to_44k1(src: Path, dst: Path):
    """32kHz (or any rate) stereo 16-bit -> 44.1kHz, linear interpolation."""
    import numpy as np
    with wave.open(str(src)) as w:
        rate, ch, width, n = (w.getframerate(), w.getnchannels(),
                              w.getsampwidth(), w.getnframes())
        if width != 2:
            raise RuntimeError(f'{src}: not 16-bit')
        data = np.frombuffer(w.readframes(n), dtype=np.int16)
    data = data.reshape(-1, ch).astype(np.float64)
    if ch == 1:
        data = np.repeat(data, 2, axis=1)
    out_n = int(round(n * 44100 / rate))
    src_pos = np.arange(out_n) * (rate / 44100.0)
    idx = np.arange(n)
    out = np.empty((out_n, 2))
    for c in range(2):
        out[:, c] = np.interp(src_pos, idx, data[:, c])
    out = np.clip(np.round(out), -32768, 32767).astype(np.int16)
    with wave.open(str(dst), 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(out.tobytes())


def main():
    manifest = json.loads(MANIFEST.read_text())
    tmp = REPO / 'build/audio/_msu_resample.wav'
    written = 0
    for g in manifest['groups']:
        for s in g['songs']:
            src = ref_wav(s['name'])
            resample_to_44k1(src, tmp)
            for sid in s['scumm_sids']:
                out = DIST / f'SuperMonkeyIsland-{sid}.pcm'
                r = subprocess.run(
                    [sys.executable, str(PCMWRITER),
                     '-loopstart', '0',
                     '-infile', str(tmp), '-outfile', str(out)],
                    capture_output=True)
                if r.returncode != 0:
                    raise RuntimeError(f'{s["name"]} sid {sid}: '
                                       f'{r.stderr.decode(errors="replace")}')
                written += 1
                print(f'{s["name"]:<18} -> {out.name} '
                      f'({out.stat().st_size//1024}KB)')
    tmp.unlink(missing_ok=True)
    print(f'{written} MSU-1 PCM tracks written to {DIST}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
