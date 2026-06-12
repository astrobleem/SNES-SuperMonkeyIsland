# MI1 Soundtrack Conversion — Status

Updated 2026-06-12. All 17 real songs are converted, compile clean, and are
**wired into the game**: 9 TAD sample-pool groups in ROM (SPC700 mode) plus
MSU-1 PCM streams of the Munt MT-32 references (MSU-1 mode). The title-screen
MUSIC row toggle (`SCUMM.musicMode`) switches the whole soundtrack between
backends and is verified in both directions (spectral-correlation captures:
MSU mode 0.991 vs MT-32 ref, TAD mode 0.986 vs SNES render).

Renders for ear verdicts: `build/eartest_batch/<song>.wav`
(regenerate: `tools/audio/` pipeline; group projects under `build/audio/groups/`).

## Per-song verdicts

| Song | SCUMM sid | Group | Verdict | Notes / next lever |
|---|---|---|---|---|
| r002_monkey1 | 1 | 4 | awaiting | opening theme |
| r010_lucasarts | 10 | 0 | **approved** | per-voice FM chairs; octave range minimized for pitch-table fit |
| r019_shdeck | 29 | 7 | awaiting | biggest song body (7.3KB) |
| r028_scummbar | 36 | 7 | **approved** | hybrid recipe (canonical project = r028 hybrid) |
| r034_highstreet | 46 | 7 | **iterating** | v4 = damnforest recipe + gentle organ pan 84 — awaiting A/B vs v3 |
| r038_lookout | 56 | 7 | awaiting | |
| r041_kitchen | 62 | 6 | awaiting | |
| r051_circus | 75 | 3 | awaiting | octave range minimized |
| r058_damnforest | 87 | 7 | awaiting | recipe donor for highstreet |
| r059_stans | 88 | 5 | awaiting | tempo runs +0.3% fast (MML tempo rounding); octave range minimized |
| r070_hellcliff | 98 | 8 | awaiting | credits music — the song the attract mode plays |
| r077_ghdeck | 115 | 2 | awaiting | lead stretched +12st to fit sample range — listen for strain |
| r078_church | 117 | 6 | awaiting | re-keyed sections — verify chord voicings |
| r083_dock4 | 123 | 1 | awaiting | |
| r083_dock5 | 124 | 1 | awaiting | |
| r085_melee | 126 | 2 | awaiting | drum kit trimmed to fit group budget |
| r096_part1 | 135 | 7 | awaiting | plays once in-game; loops in eartest |

General soft spot across kits: drum samples capped at 250ms — longest tails
(crash/ride) are truncated.

## Not converted (silent in SPC700 mode)

SCUMM sids **80, 91, 106** map to `$FF` (no TAD song). In MSU-1 mode
`op_startMusic` streams track = raw sid regardless of the map, so these three
could be covered by just rendering Munt references and running
`make msu-tracks` — no TAD conversion needed. 63 unregistered SFX are also
out of scope for this pass.

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
