# Session 23 Handoff (2026-04-21)

## What happened
Investigated drawObject renderer — discovered sparkle is costume-driven (not drawObject), clouds are costume-driven (not palette cycling). Fixed multiple rendering bugs. Added OAM priority rotation for sprite overflow management.

## Key discoveries
1. **Sparkle = costume #111 (Cost005)** — actors placed by lscr_203/204, animated by chore engine. NOT drawObject. Objects 110-114 are transparent click targets.
2. **Clouds = costume #59 (Cost004)** — 2 actors placed by lscr_202, scrolled left. 66-94 OAM entries per frame, exceeding 128 hardware limit → flicker.
3. **Campfire junk = oversized head** — cost_060 had 143x143 "head" pic (limb 1) being rendered by default head cycle fallback even though the campfire never activates limb 1. 6688 bytes of CHR DMA stomped BG tile VRAM.
4. **TODO.md palette mapping was wrong** — no setPalColor or CYCL calls exist for room 10 clouds. The entire diagnosis was incorrect.

## Changes made (not yet committed)

### OAM priority rotation (`nmi.65816`, `oam.h`, `oam.65816`)
- Added `GLOBAL.oamRotation` counter
- After DMA, sets $2103 bit 7 (priority rotation) with rotating base index
- Advances by 4 sprites/frame, wraps at 128
- Distributes scanline overflow dropout evenly

### Z-clip → OAM priority (`scummvm.65816:_ri.buildOam`)
- Actors with `actorZClip == 1` (set by `alwaysZClip(#1)`) render at OAM priority 0 (behind all BG)
- Normal actors stay at priority 2
- Fixes clouds appearing in front of "The Secret of Monkey Island" title

### Head suppression for headless costumes (`scummvm.65816:_ri.headGot`)
- When chore engine drives body (chorePic slot 0 != $FF):
  - If limb 1 was never decoded (choreCurpos == $FFFF): suppress head (e.g. campfire)
  - If limb 1 was decoded but chorePic still $FF (e.g. Guybrush walk with 0x79 cmd): keep default head cycle
  - If limb 1 has explicit chorePic: use it
- Fixes 143x143 head DMA corrupting BG VRAM, dirty rectangles, upper-left junk

### East/west direction fix (`scummvm_chore.65816:_caa.cmdSetDir`)
- `oldDirToNewDir` had east/west swapped (dir 0 → east, dir 1 → west)
- Fixed to match ScummVM: dir 0 → west (270), dir 1 → east (90)

### Walk facing re-seed (`scummvm.65816:_ua.setFacing`)
- When facing changes during walking, re-calls `animateActor_impl` with walkFrame=2
- Previously, walk animation was seeded once at start and never updated for direction changes
- Fixes Guybrush always facing south during walking

### TODO.md updated
- Sparkle resolved — costume-driven, not drawObject
- Cloud flicker documented as OAM overflow with mitigation
- Removed incorrect palette mapping diagnosis

## What works now
- Campfire animates without dirty rectangles or upper-left junk
- Cloud sprites render behind title text (z-clip priority)
- Cloud/sprite dropout distributes evenly (OAM rotation)
- Guybrush should turn to face walk direction
- Sparkle confirmed working on LucasFilm logo (frame 400)
- Intro progresses through room 33 to room 38 (lookout gameplay)

## Additional fixes after initial handoff
- **Z-clip flag clobber** — `pla` after `cmp` in z-clip priority code clobbered flags. Actors in render slot 0 (palette bits=0, like Guybrush) got priority 0 = invisible behind BG. Fixed by storing palette bits to WRAM instead of stack.
- **OAM race condition** — `core.oam.play` cleared OAM buffer AND set `oamUploadFlag=1` before renderActors wrote sprites. NMI could upload empty buffer → all sprites vanish for 1 frame (campfire blank frame). Fixed by removing premature flag set; renderActors sets it after populating buffer.
- **actorOps.init clears zClip** — prevents stale zClip values from prior room making actors invisible.

## What's left
1. **Cloud vertical cutoff** (task #78) — OAM overflow. Cloud costume needs 66-94 entries × 2 actors = 132-188, exceeding 128 limit. Needs 16x16 sprite conversion or MaxTile SA-1 buffer.
2. **Walk facing re-seed** (task #73) — attempted but reverted (clobbers scratch registers). Guybrush doesn't change directional walk animation on turns.
3. **Room 33 stall** (task #64) — may be resolved. Needs focused test.
4. **drawObject multi-state** — doors/props with multiple OBIM states. Not needed for intro scenes.
5. **Legacy cleanup** — tasks #74/#75 (delete static walk/head cycle tables, remove actorAnimFrame/Timer WRAM). Low priority.

## Files touched
- `src/core/nmi.65816` — OAM priority rotation after DMA
- `src/core/oam.h` — GLOBAL.oamRotation variable
- `src/core/oam.65816` — init oamRotation to 0
- `src/object/scummvm/scummvm.65816` — z-clip priority, head suppression logic, walk facing re-seed
- `src/object/scummvm/scummvm_chore.65816` — east/west direction fix
- `tools/gen_costume_rom.py` — reverted oversized head filter (all data preserved)
- `TODO.md` — updated with correct diagnoses
