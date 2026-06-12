#!/usr/bin/env python3
"""Bin-pack the converted songs into TAD sample-pool groups.

  python3 tools/audio/gen_groups.py

Reads tools/audio/groups_config.json (canonical per-song projects), unions
each song's BRR sample bytes, and greedily packs songs into groups. Shared
samples (organ, bass, drum kit) make co-grouping nearly free, so packing is
by MARGINAL cost: largest song first, placed where the union grows least.

Placement is validated by `tad-compiler check` on the tentative merged
project (the ARAM authority — it also catches non-byte limits like the
256-entry pitch table). The byte budget in groups_config.json is only a
fast pre-filter that keeps the number of check runs small. Per-song
instrument octave ranges are assumed tight (tools/audio/minimize_octaves.py
shrinks them in place; rerun it when adding songs).

Emits:
  build/audio/groups/group<i>.terrificaudio   merged project per group
  build/audio/groups/manifest.json            groups, ordinals, SCUMM sids

Group 0 is always the group containing r010_lucasarts (the title theme) so
boot/title playback needs no group switch. Global song ordinals are assigned
in sorted song-name order, independent of grouping, so regrouping never
renumbers ScummSoundMap.

The SFX block (instruments named sfx_*, sound_effects list, sound_effect_file)
is copied verbatim from audio/smi.terrificaudio into EVERY group so SFX
indices stay stable no matter which group is resident.
"""
import json
import math
import subprocess
import sys
import wave
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / 'tools/audio/groups_config.json'
SMI_PROJECT = REPO / 'audio/smi.terrificaudio'
OUT_DIR = REPO / 'build/audio/groups'
SOUND_DIR = REPO / 'data/scumm_extracted/sounds'
ROOMS_DIR = REPO / 'data/scumm_extracted/rooms'
TAD_COMPILER = REPO / 'tools/tad/tad-compiler.exe'


def brr_bytes(source: Path) -> int:
    """BRR size of a sample source (WAV gets encoded 16 samples -> 9 bytes)."""
    if source.suffix.lower() == '.brr':
        return source.stat().st_size - 2          # minus AMK loop header
    with wave.open(str(source)) as w:
        return math.ceil(w.getnframes() / 16) * 9


def song_data_bytes(project: Path, song_name: str) -> int:
    # Repo-relative args + cwd: tad-compiler.exe runs via WSL interop
    # during make, where /mnt/e/... arguments would not resolve.
    r = subprocess.run([str(TAD_COMPILER), 'song', '--stdout',
                        str(project.relative_to(REPO)), song_name],
                       capture_output=True, cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError(f'tad-compiler song {project} {song_name}: '
                           f'{r.stderr.decode(errors="replace")[:300]}')
    return len(r.stdout)


def tad_check(project: Path) -> tuple[bool, str]:
    r = subprocess.run([str(TAD_COMPILER), 'check',
                        str(project.relative_to(REPO))],
                       capture_output=True, cwd=REPO)
    return r.returncode == 0, r.stderr.decode(errors='replace')


def resolve_source(project: Path, source: str) -> Path:
    return (project.parent / source).resolve()


def rel_source(path) -> str:
    """Path relative to a project file in build/audio/groups/."""
    return '../../../' + str(Path(path).relative_to(REPO)).replace('\\', '/')


def scumm_sids(room_glob: str, local: str) -> list[int]:
    """Global SCUMM sound IDs whose content matches this room-local bin."""
    rooms = [d for d in ROOMS_DIR.iterdir()
             if fnmatch(d.name, room_glob) and (d / 'sounds' / local).is_file()]
    if len(rooms) != 1:
        raise RuntimeError(f'room glob {room_glob} matched {rooms}')
    data = (rooms[0] / 'sounds' / local).read_bytes()
    sids = []
    for f in sorted(SOUND_DIR.glob('soun_*_room*.bin')):
        if f.read_bytes() == data:
            sids.append(int(f.name.split('_')[1]))
    if not sids:
        raise RuntimeError(f'no global sound matches {rooms[0].name}/{local}')
    return sids


def merge_instrument(merged: dict, entry: dict, song: str):
    name = entry['name']
    if name not in merged:
        e = dict(entry)
        e['_songs'] = [song]
        merged[name] = e
        return
    m = merged[name]
    for k in ('source', 'freq', 'loop', 'envelope', 'evaluator',
              'loop_setting'):
        if entry.get(k) != m.get(k):
            raise RuntimeError(
                f'instrument {name}: {k} conflict between {m["_songs"]} '
                f'({m.get(k)}) and {song} ({entry.get(k)})')
    m['first_octave'] = min(m['first_octave'], entry['first_octave'])
    m['last_octave'] = max(m['last_octave'], entry['last_octave'])
    m['_songs'].append(song)


def project_dict(smi: dict, sfx_instruments: list, songs: dict,
                 members: list, insts: dict, comment: str) -> dict:
    """Merged group project; members must already be in local-id order."""
    instruments = []
    for e in sorted(insts.values(), key=lambda e: e['name']):
        e = dict(e)
        e.pop('_songs')
        e['source'] = rel_source(e['source'])
        instruments.append(e)
    for e in sfx_instruments:
        e = dict(e)
        e['source'] = rel_source(resolve_source(SMI_PROJECT, e['source']))
        instruments.append(e)
    return {
        '_about': {'file_type': 'Terrific Audio Driver project file',
                   'version': '0.2.0-beta.2',
                   '_comment': comment},
        'instruments': instruments,
        'samples': [],
        'default_sfx_flags': smi.get('default_sfx_flags',
                                     {'one_channel': True,
                                      'interruptible': True}),
        'high_priority_sound_effects':
            smi.get('high_priority_sound_effects', []),
        'sound_effects': smi.get('sound_effects', []),
        'low_priority_sound_effects':
            smi.get('low_priority_sound_effects', []),
        'sound_effect_file': rel_source(
            resolve_source(SMI_PROJECT, smi['sound_effect_file'])),
        'songs': [{'name': n, 'source': rel_source(songs[n]['mml'])}
                  for n in members],
    }


def write_project(path: Path, project: dict):
    path.write_text(json.dumps(project, indent=1) + '\n', encoding='utf-8')


def main():
    cfg = json.loads(CONFIG.read_text())
    budget = int(cfg.get('group_fit_budget', 55000))

    smi = json.loads(SMI_PROJECT.read_text())
    sfx_instruments = [i for i in smi['instruments']
                       if i['name'].startswith('sfx_')]
    sfx_bytes = sum(brr_bytes(resolve_source(SMI_PROJECT, i['source']))
                    for i in sfx_instruments)

    songs = {}
    for name, s in sorted(cfg['songs'].items()):
        project = REPO / s['project']
        proj = json.loads(project.read_text())
        if len(proj['songs']) != 1:
            raise RuntimeError(f'{project}: expected exactly 1 song')
        tad_song = proj['songs'][0]['name']
        insts = {}
        for e in proj['instruments']:
            merge_instrument(insts, {**e, 'source': str(
                resolve_source(project, e['source']))}, name)
        sample_sets = {e['source'] for e in insts.values()}
        songs[name] = {
            'project': project,
            'tad_song': tad_song,
            'mml': project.parent / proj['songs'][0]['source'],
            'instruments': insts,
            'sample_bytes': sum(brr_bytes(Path(p)) for p in sample_sets),
            'song_bytes': song_data_bytes(project, tad_song),
            'scumm_sids': scumm_sids(s['room'], s['local']),
        }
        print(f"{name:<18} samples {songs[name]['sample_bytes']:>6}B  "
              f"song {songs[name]['song_bytes']:>6}B  "
              f"sids {songs[name]['scumm_sids']}")

    ordinals = {n: i for i, n in enumerate(sorted(songs))}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_proj = OUT_DIR / '_check_tmp.terrificaudio'

    def check_members(members: list, insts: dict) -> bool:
        write_project(tmp_proj, project_dict(
            smi, sfx_instruments, songs,
            sorted(members, key=lambda n: ordinals[n]), insts,
            'packing feasibility probe'))
        ok, _ = tad_check(tmp_proj)
        return ok

    # --- greedy marginal-cost packing, tad-compiler check as the gate ---
    groups = []   # each: {'songs': [name], 'sources': {path: bytes}, 'insts'}
    order = sorted(songs, key=lambda n: -songs[n]['sample_bytes'])
    n_checks = 0
    for name in order:
        s = songs[name]
        s_sources = {e['source'] for e in s['instruments'].values()}
        # candidates passing the cheap byte pre-filter, cheapest first
        candidates = []
        for g in groups:
            added = sum(brr_bytes(Path(p)) for p in s_sources
                        if p not in g['sources'])
            union = sum(g['sources'].values()) + added
            max_song = max(s['song_bytes'],
                           *(songs[n]['song_bytes'] for n in g['songs']))
            if union + sfx_bytes + max_song > budget:
                continue
            try:
                test = {k: dict(v) for k, v in g['insts'].items()}
                for e in s['instruments'].values():
                    merge_instrument(test, e, name)
            except RuntimeError:
                continue
            candidates.append((added, g, test))
        placed = None
        for added, g, test in sorted(candidates, key=lambda c: c[0]):
            n_checks += 1
            if check_members(g['songs'] + [name], test):
                placed, merged = g, test
                break
        if placed is None:
            merged = {}
            for e in s['instruments'].values():
                merge_instrument(merged, e, name)
            n_checks += 1
            if not check_members([name], merged):
                ok, err = tad_check(tmp_proj)
                raise RuntimeError(f'{name} fails tad-compiler check even '
                                   f'as a singleton group:\n{err}')
            placed = {'songs': [], 'sources': {}}
            groups.append(placed)
        placed['songs'].append(name)
        placed['insts'] = merged
        for p in s_sources:
            placed['sources'].setdefault(p, brr_bytes(Path(p)))
    tmp_proj.unlink(missing_ok=True)
    print(f'packed with {n_checks} tad-compiler check runs')

    # group 0 = the group holding the title theme
    groups.sort(key=lambda g: 'r010_lucasarts' not in g['songs'])

    manifest = {'n_groups': len(groups), 'n_global_songs': len(songs),
                'groups': [], 'sfx_bytes': sfx_bytes}
    for gi, g in enumerate(groups):
        members = sorted(g['songs'], key=lambda n: ordinals[n])
        ppath = OUT_DIR / f'group{gi}.terrificaudio'
        write_project(ppath, project_dict(
            smi, sfx_instruments, songs, members, g['insts'],
            f'Sample-pool group {gi} (generated by gen_groups.py): '
            + ', '.join(members)))
        sample_total = sum(g['sources'].values())
        manifest['groups'].append({
            'index': gi,
            'project': str(ppath.relative_to(REPO)).replace('\\', '/'),
            'bin': f'build/audio/groups/group{gi}.bin',
            'sample_bytes': sample_total,
            'max_song_bytes': max(songs[n]['song_bytes'] for n in members),
            'songs': [{'name': n,
                       'local_id': li + 1,
                       'ordinal': ordinals[n],
                       'scumm_sids': songs[n]['scumm_sids'],
                       'song_bytes': songs[n]['song_bytes']}
                      for li, n in enumerate(members)],
        })
        print(f'group{gi}: {len(members)} songs, samples '
              f'{sample_total}B + sfx {sfx_bytes}B, '
              f'max song {manifest["groups"][-1]["max_song_bytes"]}B  '
              f'[{", ".join(members)}]')

    (OUT_DIR / 'manifest.json').write_text(
        json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    print(f'{len(groups)} groups -> {OUT_DIR / "manifest.json"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
