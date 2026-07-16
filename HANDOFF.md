# HANDOFF — SNES Super Monkey Island (2026-07-05)

Written by the outgoing agent for whoever picks this up next. Read CLAUDE.md
and AGENTS.md first; this file is the *state + roadmap*, those are the *rules
+ tooling*. The persistent memory index (auto-loaded each session) points back
here.

## Who you're working for

Chad. Terse delegation; judges by artifacts, not narration. Never end with a
menu of options. Never ask permission for routine ops (build/test/commit).
**Never claim a rendering or audio bug is fixed without proof** — screenshot or
recorded audio, captured via the Mesen MCP tools. His ear is the oracle for
music: two conversions are approved (r010, r028), the rest await HIS verdict —
do not self-approve audio. When a "verified" data map contradicts what he
hears, the map is wrong (this happened; see the DSOU lesson below).

## Where the game stands

Boot → MSU-1 splash → title screen → full intro cutscene (music, CD speech,
credits paced to the talkie) → lookout old-man dialogue → Part One card
(room 96) → **player control at the dock (room 33)**. Dialog choices work.
SCUMM Bar rooms load. VM regression suite: 182/182. Bank 0: 87.3%.

Recent landings (all proven, see commit messages for evidence):
- `be7fe15` (2026-07-12) dialog text owns CGRAM 29-31 — killed the title-logo
  palette hijack. The NMI white-force on CGRAM[29] recolored every room pixel
  sharing the slot while a dialog line was up: rectangular holes through the
  MONKEY ISLAND logo on EVERY intro credit card (self-healing between cards —
  which is why single screenshots kept "validating" a broken title screen and
  Chad had to re-report it), white blocks in the mountain pan, junk stripe
  under credit text. Converter now carves art row 1 c13-c15; _ears.renderEnd
  writes scummTalkColorLUT[dialogColor] into shadow word 29. Credits render
  per-card SCUMM colors matching the talkie. REQUIRES reconverted
  data/snes_converted — a stale checkout resurrects the bug.
- `89dd1c3` converter merges manifest.json instead of rewriting — a --rooms
  subset run used to wipe every other room's entry and silently break the
  next ROM pack.
- `0b32238` nested startScript + iMUSE beat clock — intro paced to the talkie
- `f787d8d` CD-talkie speech backend — voice via MSU-1, music stays on SPC700
- `ff414a2` objUntouchable Y-clobber fix — THE mega root cause (see below)
- `0c3bf88` sound-id map rebuilt on DSOU ids — fixed every music-mapping bug

## Immediate loose ends (small, do these first)

1. **BRK-scan baseline is stale**: `validate_rom` reports 38 vs baseline 35.
   The 3 new are data-table false positives (`ScummSoundMap+257`,
   `TadGroupRecs+0`, `TadSongMap+2` — banks $09/$1F, non-executable). Bump the
   baseline in `tools/smi_workflow_server.py` so real regressions stay visible.
2. **VAR_MUSIC_TIMER is render-rate coupled.** var14 ticks once per VM play
   tick, and the play tick follows the frame rate. The intro's first ~6 s run
   at 60 fps, so beats 1–3 of the beat clock fire 2× fast (~1.5 s early),
   self-correcting when the frame rate drops to 30. Correct fix if Chad wants
   exact pacing: tick var14 at a fixed 30 Hz (every other frame at 60 fps),
   then re-verify with `distribution/test_intro_pacing.lua` — but FIRST
   re-resolve its hardcoded WRAM addresses from the current .sym (any
   ramsection change shifts them; this is the #1 test footgun).
3. **Re-examine the "Bug 85" workaround in `op_putActorInRoom`**
   (scummvm.65816). Its comment claims background scripts re-issue
   putActorInRoom every tick — that was actually the lscr_200 wedge caused by
   the Y-clobber, now fixed. The black-band workaround may be removable dead
   weight. Verify room 38 renders identically after removal (screenshot).
4. **Y-discipline audit**: sweep every helper called from `op_*` handlers for
   Y preservation — Y holds the SCUMM PC during opcode dispatch. The
   objUntouchable class of bug (helper trashes Y → PC rewinds → script
   corruption) is the most damaging failure mode this codebase has had.
5. **Re-profile room 38**: the ~10 fps / pathfinding-45% measurement was taken
   WITH the lscr_200 wedge burning cycles. Numbers are stale; re-measure
   before optimizing anything.
6. **Title-screen mountain cloud flicker** — carried item, partially mitigated
   (sparkle suppressed during cutscenes, gated on cameraX=344). Not resolved.
7. **Lookout ambience**: the fire-crackle SBLs (real sids ~4/5) are not yet
   registered as TAD SFX — the old-man scene is canonically silent music-wise,
   but the reference has ambience.
8. **soundKludge is minimal by design**: only `(256, sid, 7|8)` =
   getBeatIndex/getTick is implemented (that's all script 152 needs). Other
   iMUSE forms (markers/triggers → VAR_SOUNDRESULT) are consumed as no-ops.
   Implement additional forms ONLY when a specific script needs them — check
   `docs/v5_behavior_matrix.md` and ScummVM source (`E:\gh\scummvm`) per form.

## Known open bugs (diagnosed 2026-07-04, mostly unverified since)

Full detail in snes-secret-plan.md "Current Frontier (2026-07-04, second
session)" → "New bugs surfaced by the fix". Summary, roughly by user impact:

1. ~~FF 07 insert-string-slot escape~~ DONE (`6e9884a`) — title cards render
   text. NOTE: this fixed only the missing card text; the title-screen logo
   corruption was the separate CGRAM[29] hijack, fixed 2026-07-12 (`be7fe15`).
   Still open from that family: the Testers credit card renders box glyphs
   between names and wraps mid-word ("W ayne") — the inserted slot string
   carries control bytes (< $20) that `_ears.emitChar` renders as garbage
   tiles instead of treating as newlines. Fix in the FF07 detour's
   newline/control handling. Evidence: distribution/fix_f6237.png.
2. **Dock (room 33) entry**: camera parked at x=880 with Guybrush at x=9
   (the scenic bar→ego pan likely doesn't run — possibly another
   soundKludge/marker sync, now easier with the beat clock landed), and the
   BG column streamer leaves the right half of the screen BLACK after a
   camera JUMP (streaming only fills VRAM on pans). Exits work; the SCUMM
   bar door (obj 428) still needs a direct click test after the camera fix.
3. **Dialog text renders garbled/overlapping** in the old-man dialogue
   (letters collide; readable but wrong).
4. **Walk-behind masking not applying in room 38** — ZP01 exists in the
   extraction but Guybrush draws fully in front of the fire wall; check
   whether ZP01 reaches BG2 outside the verb area + sprite-vs-BG2 priority.
5. **Verb UI stays visible during cutscenes** (lscr_203 issues userput off;
   verb bar keeps rendering).
6. **Stale duplicate Guybrush sprite** during the room-38 walk-in.
7. ~~"THE" of "THE SECRET OF" hidden by cloud actors at the title~~ FIXED
   2026-07-13 (`8bb3fe5`). TWO separate causes, don't conflate them:
   (a) `cd5efd5` gated op_putActor's walkbox snap on actorIgnoreBoxes so the
   clouds actually DRIFT (they were parking at the box edge). Necessary but
   NOT sufficient — a drifting cloud still drew IN FRONT of "THE".
   (b) `8bb3fe5` is the real layering fix: the clouds must pass BEHIND the
   logo. The logo is object 109, whose OBIM carries a ZP01 z-plane marking
   it foreground — and our object extractor DROPPED every object z-plane
   (decoded SMAP only). So the logo text had no foreground mask and the
   priority-2 cloud sprites drew over it. Fix: extract object ZP01s; bake
   z-planed (static, single-state) objects into the room bg + foreground so
   BG2 priority-1 masks the sprites. Also gives correct walk-behind masking
   for object z-planes game-wide (was entirely absent). Proven: frame 1654 a
   cloud sits over "THE" and the text is fully in front (fg_full_1654.png).
   LESSON: "clouds move" ≠ "clouds behind text" — verify the actual visual,
   and check every extraction layer for silently-dropped data (cf. DSOU).
8. **Room 38 play tick was ~15 Hz** (walk feels slow) — measured WITH the
   now-fixed lscr_200 wedge; re-measure first.

## Music pipeline status

- 9 TAD song groups wired in-game + MSU-1/TAD toggle, both verified.
- **15 songs converted, awaiting Chad's ear** — resume doc:
  `audio/songs/STATUS.md`. r010 (title medley, non-looping, hand-stripped
  loop markers — do NOT regenerate) and r028 are approved.
- `64ths.txt` (repo root) is Chad's written spec for the next converter
  improvement: quantizer grid 16th → 32nd default with 64th option and a
  grid-tolerance snap parameter, explicitly NO triplet/swing heuristics.
  Implement in the MIDI→MML tooling; calibrate exactly as the file describes.
- `audio/songs/r028_scummbar/r028_phantasia.*` is a WIP alternate arrangement.

## Release-ready roadmap (the big arcs, in order)

1. **Prove progression past the dock.** Nothing beyond room 33 has been
   exercised. Drive the game chapter by chapter with `run_with_input`
   (frame-scheduled button injection): the three trials, the SCUMM Bar,
   the ship purchase, Monkey Island, the ending. Every new room/script will
   surface opcode and resource gaps — fix them one at a time, add a VM
   regression test per fix (`tests/scumm_vm/test_runner.lua`).
2. **Save/load completeness** — the current save layout omits: bit variables,
   full per-slot script state (offsets/delay/freezeCount/cutsceneOverride/
   localVars), the cutscene stack, objectClass bitmasks, verb state, actor
   palette overrides, in-progress walk paths, camera state, room scroll
   limits, scale slots. (snes-secret-plan.md §5.7 + Known Gaps item 11.)
3. **Entry/exit script dual layer** — ScummVM runs VAR_ENTRY_SCRIPT (var 28)
   → ENCD → VAR_ENTRY_SCRIPT2 (var 29) on entry, and vars 30/31 around EXCD.
   We only run ENCD/EXCD; later chapters set those vars (item 15).
4. **decodeParseString sub-opcode completeness** (item 12) — text layout
   correctness across the whole game.
5. **320→256 coordinate audit** (item 17) — findObject/actorFromPos/drawBox/
   print positions/roomScroll each need the transform applied exactly once.
6. **Performance** — after re-profiling room 38: pathfinding is the known
   hotspot; the SA-1 offload pattern from renderActors (CMD=3 mailbox) is the
   template for moving more work off the 65816.
7. **Audio completeness** — remaining song approvals, SFX registration sweep
   (registered_sfx.txt), verify CD speech in later chapters (voice_table.bin
   already covers all 4393 talkie lines).
8. **Real-hardware validation** — SD2SNES/FXPAK Pro on NTSC hardware: MSU-1
   handshake, FastROM timing, SA-1 paths. Emulator-proven ≠ release-ready.
9. **Turnkey user pipeline** (legal model: we ship engine + tools, user
   supplies their own MI1 copy). The pieces exist — scumm_extract.py,
   converters, rom_pack_data.py, build_speech_msu.py, gen_msu_tracks — but
   need a one-command wrapper: GOG/Steam install dir in → .sfc + .msu +
   .pcm set out, plus a README for non-developers. Commercial game data must
   never enter the repo (see .gitignore).

## Hard-won operational knowledge

- `validate_rom` after EVERY build — bank 0 overflow is silent.
- Any WRAM ramsection resize shifts bank-$7E addresses; Lua tests must resolve
  addresses from `build/SuperMonkeyIsland.sym`, never hardcode.
- Worktree gotcha: `distribution/SuperMonkeyIsland.sfc` in a worktree is a
  HARDLINK to main's — a worktree build clobbers the main checkout's ROM while
  the .sym files diverge. Rebuild before trusting either.
- `distribution/SuperMonkeyIsland.msu` must exist or the ROM won't boot.
- Long-lived emulator instances (nexen / mesen-inproc) do NOT reload the ROM
  on reset_emulator — after a rebuild, verify loaded PRG bytes against the
  file or use smi-workflow's per-call testrunner for current-ROM captures.
- Room 36 conversion fails ("ubyte format requires 0 <= number <= 255",
  pre-existing since ~March) — the pack ships March-stale room 36 data,
  which still has art in CGRAM 29-31 (palette hijack would show THERE only).
- Time-varying rendering bugs need a filmstrip across the whole sequence;
  a single screenshot between credit cards is how the title bug kept getting
  falsely closed.
- Two MCP servers: `smi-workflow` (one-shot build/validate/test/screenshot) and
  `mesen-inproc` (long-lived memory/exec hooks, audio capture). AGENTS.md is
  the harness reference. The read-hook fetch-trace technique (hook the script
  cache, fetch addresses = PC trajectory) is how the Y-clobber was found —
  reach for it whenever a script "mysteriously" misbehaves.
- To verify which MSU voice/track played, WRITE-hook MSU_TRACK ($2004/5)
  during free-run — do not read scratch WRAM after the fact.
- The DSOU lesson: when extraction-layer id assignment is wrong, every
  downstream "proof" validates a self-consistent lie. Audit the id-assignment
  layer first when data contradicts observed behavior.
