# MI1 Soundtrack Conversion — Status

Updated 2026-07-04. All 17 real songs are converted, compile clean, and are
**wired into the game**: 9 TAD sample-pool groups in ROM (SPC700 mode) plus
MSU-1 PCM streams of the Munt MT-32 references (MSU-1 mode). The title-screen
MUSIC row toggle (`SCUMM.musicMode`) switches the whole soundtrack between
backends and is verified in both directions (spectral-correlation captures:
MSU mode 0.991 vs MT-32 ref, TAD mode 0.986 vs SNES render).

**2026-07-04 SOUND-ID MAP CORRECTED.** The extractor used to number global
sounds by disk order; the runtime ids scripts pass to startSound come from the
index DSOU directory and are DIFFERENT. Every "SCUMM sid" below carried the
wrong number (the music cues really live at ids 151–168), which is why the
engine-side room→song autoplay hack existed and why the intro lookout blasted
the wrong theme. Extraction, `ScummSoundMap`, MSU track filenames, and the SFX
registry now all use real DSOU ids; the autoplay hack is deleted — the
Ultimate Talkie's scripts start/stop every theme themselves through the
var-251 cue system (see "How UT music actually works" below).

Renders for ear verdicts: `build/eartest_batch/<song>.wav`
(regenerate: `tools/audio/` pipeline; group projects under `build/audio/groups/`).

## Per-song verdicts

sid = real DSOU sound id (pre-2026-07 docs used the old disk-order id).

| Song | SCUMM sid | old id | Group | Verdict | Notes / next lever |
|---|---|---|---|---|---|
| r002_monkey1 | 164 | 1 | 4 | awaiting | Monkey Island rooms theme (ENCD cue via var251=164) |
| r010_lucasarts | 167 | 10 | 0 | **approved** | title/credits medley — plays ONCE now (loop stripped to match reference; iMUSE would even marker-shorten it) |
| r019_shdeck | 165 | 29 | 7 | awaiting | biggest song body (7.3KB) |
| r028_scummbar | 158 | 36 | 7 | **approved** | hybrid recipe (canonical project = r028 hybrid) |
| r034_highstreet | 153 | 46 | 7 | **iterating** | v4 = damnforest recipe + gentle organ pan 84 — awaiting A/B vs v3. Also the Mêlée-area ambient cue (jail/streets/lookout ENCDs) |
| r038_lookout | 160 | 56 | 7 | awaiting | NOT dead after all — the gameplay lookout cue (lscr_200/203) |
| r041_kitchen | 151 | 62 | 6 | awaiting | |
| r051_circus | 161 | 75 | 3 | awaiting | octave range minimized |
| r058_damnforest | 152 | 87 | 7 | awaiting | recipe donor for highstreet |
| r059_stans | 168 | 88 | 5 | awaiting | tempo runs +0.3% fast (MML tempo rounding); octave range minimized |
| r070_hellcliff | 162 | 98 | 8 | awaiting | the grand lookout/intro pan-down theme (scripts 120/121) — "hellcliff" is a storage-room misnomer |
| r077_ghdeck | 159 | 115 | 2 | awaiting | lead stretched +12st to fit sample range — listen for strain |
| r078_church | 154 | 117 | 6 | awaiting | re-keyed sections — verify chord voicings |
| r083_dock4 | 157 | 123 | 1 | awaiting | |
| r083_dock5 | 166 | 124 | 1 | awaiting | |
| r085_melee | 155 | 126 | 2 | awaiting | drum kit trimmed to fit group budget |
| r096_part1 | 156 | 135 | 7 | awaiting | plays once in-game; loops in eartest |

General soft spot across kits: drum samples capped at 250ms — longest tails
(crash/ride) are truncated.

## How UT music actually works (the var-251 cue system)

- Boot runs script 176: clears bitvar498, fires `startSound(99)` (a silent
  stub) and probes `isSoundRunning(99)` to detect the optional SE-audio
  install. Stub sounds are `$FE` in `ScummSoundMap` and are never primed, so
  the probe fails and **bitvar498 stays 0 = MIDI-cue mode** (the mode the
  reference video runs in).
- Every music-bearing script picks a cue id into **var 251** (bitvar498
  selects SE stub vs real MIDI id — e.g. title = 110/167, bar = 101/158,
  melee = 112/155) and calls `startSound(var251)`; exits/scene cuts call
  `stopSound(...)`. `op_stopSound` now stops the mapped song
  (`Tad_StopGlobalSong` — no-op unless that ordinal is the loaded one).
- The intro lookout (old man + fire) is **canonically silent**: room 38's
  ENCD fires sound 98, which is a 248-byte AdLib-only micro-cue — nothing on
  an MT-32 setup. The wrong-song bug was the old id map pointing 98 at the
  162 medley.
- Credits/music sync in the original uses iMUSE marker queues
  (`soundKludge` + script 32 + vars 250–258). We stub `soundKludge`, so no
  marker sync: known deviation, revisit only if credits pacing needs it.

## Not converted (silent in SPC700 mode)

Unconverted MIDI sounds map to `$FF` (primed for pacing, no audio); silent
stub SOUNs map to `$FE` (never primed — see probe note above). In MSU-1 mode
`op_startMusic` streams track = raw sid regardless of the map, so leftover
MIDI music could be covered by rendering Munt references and running
`make msu-tracks` — no TAD conversion needed. Most SFX (SBLs) are still
unregistered; only real sid 172 (old 99) is wired as a TAD SFX. The intro
ambience (fire crackle at the lookout, e.g. the 44KB SBLs at real sids 4/5)
is a candidate roster for the next SFX pass.

## Engine notes / known follow-ups

- **Group-switch latency**: changing groups reloads common audio data
  (~35-42KB) at 256 B/frame ≈ 3-7s of silence. Knob: `Tad_SetTransferSize`
  (up to ~800 B/frame during loading) if it bothers anyone in practice.
- **Paused-resume edge** (TAD mode): `op_stopMusic` sends PAUSE;
  `op_startMusic` with the *same* song ordinal is a no-op (global-ordinal
  compare), so the song stays paused. Parity with the old
  `Tad_LoadSongIfChanged` behavior — fix only if a real script hits it.
- **MUSIC toggle semantics** (verified 2026-06-12): both `op_startMusic` and
  `op_startSound`'s song path branch on `SCUMM.musicMode`; each backend
  silences the other on song start/stop (mode switches mid-game can't leave
  the old backend looping). `musicMode` is zeroed once in `core.boot.init`
  (defaults to SPC700) — it is *not* part of `scummvm.init`, so a VM rebuild
  doesn't wipe the user's title-screen choice. All accesses use **long
  addressing** — DB is not $7E during opcode execution; the old `lda.w`
  reads silently read ROM (this masked the uninitialized variable for ages).
- **MSU-1 PCMs are not committed**: `make msu-tracks` regenerates
  `distribution/SuperMonkeyIsland-<sid>.pcm` from `build/mt32_*/` Munt
  references (WSL + MT-32 ROMs required). Resample is linear-interp
  32→44.1kHz — swap for polyphase if anyone hears zipper noise.
- **SA-1 placement sensitivity** (repo-wide): linked ROM banks $20-$3F are
  NOT readable via `lda.l` at runtime (EXB/FXB moving windows). The group
  bins live in *appended block 0* (CPU $E0-$EF, stable EXB=4); the group
  tables are pinned to bank $1F. Anything superfree that drifts into
  $20-$3F dies silently — see `tools/audio/pack_groups.py` docstring.
