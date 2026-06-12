#!/usr/bin/env python3
"""Compile TAD group bins + the WLA-DX group tables include.

  python3 tools/audio/pack_groups.py

Reads build/audio/groups/manifest.json (from gen_groups.py). Per group:
  1. `tad-compiler check`  — the ARAM-fit authority; failure fails the build
  2. `tad-compiler common` — common audio data blob (samples, dir, SFX)
  3. `tad-compiler song`   — one blob per member song, in local-id order
  4. concatenate           → build/audio/groups/group<i>.bin
     (item 0 = common, item k = song with local id k, matching the id the
     TAD loader passes to the Tad_LoadAudioData callback)
All group bins concatenate into build/audio/groups/groups_blob.bin, which
rom_pack_data.py places at APPENDED OFFSET 0 (`--audio-blob`).

WHY THE APPENDED REGION: the music data (~450KB) cannot live in the linked
4MB image. The SA-1 Super MMC setup (boot.65816) keeps EXB=4/FXB=5-7 as
moving windows into the appended data, so linked ROM banks $20-$3F are NOT
reachable by plain long addressing at runtime, and banks $00-$1F have only
~100KB of slack. Letting the bins float superfree instead displaced other
sections into the unreachable range and broke the game wholesale (VM suite
114 failures). Appended block 0 (first 1MB) is permanently visible at CPU
banks $E0-$EF because EXB stays 4: room loads only reprogram FXB, and the
NMI ExcFontTiles remap restores EXB=4 before RTI. Room/script readers of
the same window are pure concurrent reads — no conflict.

Emits two includes (WLA-DX defines are per-object; the defines file is
safe to include from several .65816 files, the data file must be included
exactly once — tad_interface.h does):

build/audio/tad-groups.inc (defines only):
  - TAD_LOADER_SIZE / TAD_AUDIO_DRIVER_SIZE (+ offsets) parsed from the
    donor build/audio/tad-audio-data.asm — replaces the hand-kept defines
    in tad_interface.h
  - TAD_N_GROUPS, TAD_N_GLOBAL_SONGS, TAD_GSONG_<NAME> ordinal defines

build/audio/tad-groups-data.inc (one small section, pinned to TABLES_BANK
— a bank verified empty in the baseline image and reachable via the stable
DXB window):
  TadGroupRecs   per group: .dw table offset, .db item count (3B)
  TadSongMap     per ordinal: .db group, .db local id (2B)
  TadGroupTables per group: (items+1) x 3B NUMERIC window far addresses
                 ($E0 + blob offset); the +1 footer entry lets
                 Tad_LoadAudioData size the last item by lo16 subtraction
                 (modular math is safe for items < 64KB even across the
                 window's 64KB bank steps, which the transfer's
                 bank-overflow path already handles)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GROUPS_DIR = REPO / 'build/audio/groups'
MANIFEST = GROUPS_DIR / 'manifest.json'
DONOR_ASM = REPO / 'build/audio/tad-audio-data.asm'
OUT_INC = REPO / 'build/audio/tad-groups.inc'
OUT_DATA_INC = REPO / 'build/audio/tad-groups-data.inc'
TAD_COMPILER = REPO / 'tools/tad/tad-compiler.exe'

# The group tables (~250B) must be reachable by plain `lda.l`, i.e. live
# in linked ROM banks $00-$1F (stable CXB/DXB windows at $C0-$DF; banks
# $20-$3F sit behind the moving EXB/FXB windows). Bank $1F is completely
# empty in the baseline image.
TABLES_BANK = 0x1F

# Appended block 0 is visible at CPU banks $E0-$EF (EXB=4 steady state).
WINDOW_BASE_BANK = 0xE0
MAX_BLOB_SIZE = 0x100000        # appended block 0 = 1MB


def run(args):
    # Repo-relative path args + cwd: tad-compiler.exe runs via WSL interop
    # during make, where /mnt/e/... arguments would not resolve.
    r = subprocess.run([str(TAD_COMPILER)] + args, capture_output=True,
                       cwd=REPO)
    if r.returncode != 0:
        raise RuntimeError(f'tad-compiler {" ".join(args)} failed:\n'
                           f'{r.stderr.decode(errors="replace")}')
    return r.stdout


def donor_sizes():
    text = DONOR_ASM.read_text()
    loader = int(re.search(r'^Tad_Loader_SIZE = (\d+)', text, re.M)[1])
    driver = int(re.search(r'^Tad_AudioDriver_SIZE = (\d+)', text, re.M)[1])
    return loader, driver


def main():
    manifest = json.loads(MANIFEST.read_text())
    loader_size, driver_size = donor_sizes()

    ordinals = {}          # name -> ordinal
    group_layouts = []     # per group: item offsets WITHIN THE BLOB + end
    blob = bytearray()
    for g in manifest['groups']:
        project = g['project']      # repo-relative path string
        run(['check', project])

        blobs = [run(['common', '--stdout', project])]
        for s in g['songs']:
            assert s['local_id'] == len(blobs)
            blobs.append(run(['song', '--stdout', project, s['name']]))
            ordinals[s['name']] = s['ordinal']

        offsets = []
        for b in blobs:
            offsets.append(len(blob))
            blob += b
        group_layouts.append({'offsets': offsets, 'end': len(blob)})
        bin_path = REPO / g['bin']
        bin_path.write_bytes(b''.join(blobs))
        print(f'group{g["index"]}: check ok, {len(blobs)} items, '
              f'{group_layouts[-1]["end"] - offsets[0]}B')

    if len(blob) >= MAX_BLOB_SIZE:
        raise RuntimeError(f'groups blob {len(blob)}B exceeds appended '
                           f'block 0 (1MB); window addressing breaks')
    (GROUPS_DIR / 'groups_blob.bin').write_bytes(blob)

    n_groups = manifest['n_groups']
    n_songs = manifest['n_global_songs']
    by_ordinal = sorted(ordinals, key=lambda n: ordinals[n])
    assert len(by_ordinal) == n_songs

    song_map = {}          # ordinal -> (group index, local id)
    for g in manifest['groups']:
        for s in g['songs']:
            song_map[s['ordinal']] = (g['index'], s['local_id'])

    D = []
    D.append('; Auto-generated by tools/audio/pack_groups.py'
             ' -- do not hand-edit.')
    D.append('; Defines only -- safe to include from multiple .65816 files.')
    D.append('; Loader/driver sizes parsed from build/audio/'
             'tad-audio-data.asm (64tass-export donor).')
    D.append('')
    D.append(f'.define TAD_LOADER_SIZE         {loader_size}')
    D.append(f'.define TAD_AUDIO_DRIVER_SIZE   {driver_size}')
    D.append('.define TAD_LOADER_OFFSET       0')
    D.append(f'.define TAD_AUDIO_DRIVER_OFFSET {loader_size}')
    D.append('')
    D.append(f'.define TAD_N_GROUPS            {n_groups}')
    D.append(f'.define TAD_N_GLOBAL_SONGS      {n_songs}')
    for name in by_ordinal:
        D.append(f'.define TAD_GSONG_{name.upper():<22} {ordinals[name]}')
    D.append('')
    OUT_INC.write_text('\n'.join(D), encoding='ascii')

    def window_addr(off):
        return (off & 0xFFFF), WINDOW_BASE_BANK + (off >> 16)

    L = []
    L.append('; Auto-generated by tools/audio/pack_groups.py'
             ' -- do not hand-edit.')
    L.append('; Group tables -- include EXACTLY ONCE (tad_interface.h).')
    L.append('; Entries are NUMERIC window addresses into the appended')
    L.append('; groups_blob.bin at appended offset 0 (rom_pack_data.py')
    L.append('; --audio-blob), visible at CPU banks $E0+ via the EXB=4')
    L.append('; steady-state SA-1 window. See pack_groups.py for why.')
    L.append('')
    L.append(f'.bank {TABLES_BANK} slot 0')
    L.append('.base BSL')
    L.append('.section "TAD Group Tables" free')
    L.append('')
    L.append('; per group: .dw table offset within TadGroupTables,'
             ' .db item count')
    L.append('TadGroupRecs:')
    for g in manifest['groups']:
        gi = g['index']
        n_items = len(g['songs']) + 1
        L.append(f' .dw TadGroup{gi}_Table - TadGroupTables')
        L.append(f' .db {n_items}  ; group {gi}: '
                 + ', '.join(s['name'] for s in g['songs']))
    L.append('')
    L.append('; per global ordinal: .db group index, .db local song id')
    L.append('TadSongMap:')
    for o in range(n_songs):
        gi, local = song_map[o]
        L.append(f' .db {gi}, {local}  ; {o}: {by_ordinal[o]}')
    L.append('')
    L.append('; per group: (items+1) far-address entries, 3 bytes each;')
    L.append('; footer entry = end of group data, for sizing the last item')
    L.append('TadGroupTables:')
    for g, lay in zip(manifest['groups'], group_layouts):
        gi = g['index']
        L.append(f'TadGroup{gi}_Table:')
        names = ['common'] + [s['name'] for s in g['songs']]
        for off, name in zip(lay['offsets'], names):
            lo, bank = window_addr(off)
            L.append(f' .dw ${lo:04X}')
            L.append(f' .db ${bank:02X}  ; {name} (blob +{off})')
        lo, bank = window_addr(lay['end'])
        L.append(f' .dw ${lo:04X}')
        L.append(f' .db ${bank:02X}  ; footer')
    L.append('.ends')
    L.append('')

    OUT_DATA_INC.write_text('\n'.join(L), encoding='ascii')
    print(f'{n_groups} groups, {n_songs} songs -> '
          f'{OUT_INC.name} + {OUT_DATA_INC.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
