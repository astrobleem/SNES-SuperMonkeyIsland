#!/usr/bin/env python3
"""One-shot: shrink instrument octave ranges to what each song's MML uses.

  python3 tools/audio/minimize_octaves.py

TAD's pitch table (max 256 entries) charges 12 entries per octave per
unique sample freq, and merged group projects union the ranges — slack
octaves in the per-song projects are what overflow the table. The compiler
is the authority on which notes the MML needs: probe-shrink each
instrument's first/last octave while the song still compiles, then write
the project back in place.

Run when adding new songs or after widening an MML's note range
(tad-compiler will fail loudly on a too-narrow range — bump the octave
in the project or rerun this tool).
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / 'tools/audio/groups_config.json'
TAD_COMPILER = REPO / 'tools/tad/tad-compiler.exe'


def song_compiles(proj: dict, project_dir: Path, song_name: str) -> bool:
    tmp = project_dir / '_minoct_tmp.terrificaudio'
    tmp.write_text(json.dumps(proj), encoding='utf-8')
    try:
        r = subprocess.run(
            [str(TAD_COMPILER), 'song', '--stdout',
             str(tmp.relative_to(REPO)), song_name],
            capture_output=True, cwd=REPO)
        return r.returncode == 0
    finally:
        tmp.unlink(missing_ok=True)


def minimize(proj: dict, project_dir: Path, song_name: str) -> int:
    shaved = 0
    for inst in proj['instruments']:
        for key, step in (('first_octave', 1), ('last_octave', -1)):
            while inst['first_octave'] < inst['last_octave']:
                inst[key] += step
                if song_compiles(proj, project_dir, song_name):
                    shaved += 1
                else:
                    inst[key] -= step
                    break
    return shaved


def main():
    cfg = json.loads(CONFIG.read_text())
    total = 0
    for name, s in sorted(cfg['songs'].items()):
        project = REPO / s['project']
        proj = json.loads(project.read_text())
        song_name = proj['songs'][0]['name']
        if not song_compiles(proj, project.parent, song_name):
            raise RuntimeError(f'{name}: does not compile before minimizing')
        shaved = minimize(proj, project.parent, song_name)
        if shaved:
            project.write_text(json.dumps(proj, indent=1) + '\n',
                               encoding='utf-8')
        total += shaved
        print(f'{name:<18} shaved {shaved} octave(s)'
              + ('' if shaved else ' (already tight)'))
    print(f'total: {total} octave(s) shaved')
    return 0


if __name__ == '__main__':
    sys.exit(main())
