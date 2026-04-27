# SCUMM v5 Behavior Matrix

**Purpose:** track every place where ScummVM gates behavior on `_game.version`,
plus our SNES port's matching behavior. Maintain in lockstep with ScummVM
source so that any new divergence surfaces immediately.

ScummVM source: `E:/gh/scummvm/engines/scumm/`.

**Game version:** MI1 talkie = SCUMM v5. Anywhere ScummVM gates with
`version <= 4` should be SKIPPED in our port; `version >= 5` (or `>= 5 && <= 6`)
should be EXECUTED.

## Status legend

- ✅ **MATCHES**: our port follows the v5 path, verified against ScummVM source.
- ❌ **DIVERGES**: our port follows v4 (or undefined) behavior; needs fix.
- ⏳ **PARTIAL**: partial fix or workaround in place.
- ❓ **UNVERIFIED**: not yet checked.
- 🚫 **N/A**: irrelevant for our v5-only target (no v6/v7/v8 support needed).

---

## Opcode-level divergences

### `o5_loadRoomWithEgo` (`script_v5.cpp:1857`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Obj-walk-to teleport on entry | v5 happens inside `startScene` (room.cpp:223–237 + 257–263); v<=4 has it inline at script_v5.cpp:1881 | finalizeEgoSpawn does the teleport unconditionally (matches end-state) | ✅ |
| Walk to bytecode (x, y) if x != -1 | All versions | `finalizeEgoSpawn` calls `buildWalkPath_long` if x != -1 AND target != spawn AND BOXM has route (post 822e1ae) | ⏳ partial — BOXM-route fizzle works, but downstream chore/render BRKs at the snap-to-degenerate-walkbox state |

### `o5_putActor` (`script_v5.cpp` family `0x01/0x21/0x41/0x61/0x81/0xA1/0xC1/0xE1`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Sets actor (x, y) and clears moving | All versions | op_putActor | ✅ |
| Calls adjustActorPos (snap to walkbox AABB) implicitly via Actor::putActor | All versions | NOT CALLED — actor.x/y stays at raw values, only assignActorWalkboxes finds box | ❌ HIGH |
| Calls showActor when previously hidden | All versions | NOT CALLED | ❌ MEDIUM |
| Sets _egoPositioned when actor == VAR_EGO | All versions | ✅ | ✅ |
| `egoPositioned` walkbox snap (`adjustActorPos`) | All versions | Done in finalizeEgoSpawn after teleport (post 822e1ae) | ⏳ partial |

### `o5_actorOps` (`script_v5.cpp:404`)

| Sub-op | v5 param count | Our port | Status |
|--------|---------------|----------|--------|
| 0x11 (`scale`) | v5: TWO bytes (scaleX, scaleY); v4: ONE byte | UNVERIFIED — audit flagged this as need-to-verify (#2 HIGH) | ❓ |
| 0x09 (frame) | All versions: byte | per-actor frame override (task #61 done) | ✅ |
| 0x16 (init) | All versions: no params | ✅ | ✅ |

### `o5_walkActorTo` / `o5_walkActorToActor` / `o5_walkActorToObject`

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Calls Actor::startWalkActor (= adjustXYToBeInBox + walk pump start) | All versions | buildWalkPath_long called direct; does NOT call adjustXYToBeInBox | ❌ HIGH |

### `o5_findObject` / `findObject` (`object.cpp:553`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Skip via `kObjectClassUntouchable` class bit | v5 always | uses `objectState == 0` instead | ❌ HIGH (ticket #50) |
| Skip if obj_nr < 1 | All versions | UNVERIFIED | ❓ |

### `o5_setClass` / `o5_ifClassOfIs` (`script_v5.cpp:565`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Class indexing (1-based stored as bit class-1) | All versions | ✅ (fixed earlier per memory) | ✅ |
| Class storage capacity (32 classes) | All versions | LIMITED — 16-bit array can't round-trip class 17–32 (already known divergence in `project_vm_test_harness.md`) | ⏳ blocked on WRAM |

---

## Actor-system divergences (`actor.cpp`)

### `Actor::startWalkActor` (`actor.cpp:850`)

| Step | ScummVM v5 | Our port | Status |
|------|-----------|----------|--------|
| isInCurrentRoom + version >= 7 early return | v7+ | 🚫 | 🚫 |
| `abr = adjustXYToBeInBox(destX, destY)` | v5 (`else` branch line 862) | NOT CALLED — buildWalkPath does findBox + AABB-only snap when destBox == $FF | ❌ HIGH |
| isInCurrentRoom + v <= 6 early teleport | v5 | UNVERIFIED — our walk pump may not check this | ❓ |
| `checkXYInBoxBounds(_walkdata.destbox, abr.x, abr.y)` reuse | v5 | NOT CHECKED | ❌ MEDIUM |
| Already-at-target early return (`_pos.x == abr.x && _pos.y == abr.y`) | v5 | UNVERIFIED — buildWalkPath may handle | ❓ |
| MF_NEW_LEG walk start | v5: `(_moving & MF_IN_LEG) | MF_NEW_LEG` | UNVERIFIED — may simplify to just MF_NEW_LEG | ❓ |

### `Actor::walkActor` (walk pump, `actor.cpp:945`)

| Step | ScummVM v5 | Our port | Status |
|------|-----------|----------|--------|
| `getNextBox(_walkbox, destbox) < 0 → destbox=walkbox + MF_LAST_LEG` | All versions | `buildWalkPath_long` directs to `_bwp.directPath` instead, ignoring BOXM-no-route | ❌ HIGH (recently mitigated for FES-only via 822e1ae route-fizzle pre-check, but core buildWalkPath still wrong for other callers) |
| `setBox(_walkdata.curbox)` per-leg | All versions | UNVERIFIED | ❓ |
| `calcMovementFactor` v3/v4 vs v5 | v5 simpler | UNVERIFIED — audit gap | ❓ |

### `Actor::adjustXYToBeInBox` (`actor.cpp:1983`)

| Step | ScummVM v5 | Our port (`_bwp.adjustToBox`) | Status |
|------|-----------|-------------------------------|--------|
| Three-pass threshold {30, 80, 0} | v5 | SINGLE PASS, no threshold (single best-distance scan) | ❌ MEDIUM |
| `inBoxQuickReject` early-out at threshold | v5 | NOT IMPLEMENTED | ⏳ minor performance |
| Backwards iteration (numBoxes-1 down to firstValidBox) | v5 | FORWARD iteration (1..count-1) | ❌ MEDIUM (different tie-breaks) |
| `getClosestPtOnBox` (true edge-distance) | v5 | AABB-clamp distance only | ❌ MEDIUM (less accurate for non-axis-aligned quads) |
| Init `abr.x/y` to `dstX/Y` not `_lastValidX/Y` | v5 | ✅ (we don't have v4 path) | ✅ |
| `_lastValidX/Y` tracking | All versions (used by v3-4 quirk + v5 set when box found) | NOT TRACKED | ❌ MEDIUM (impacts no-best-found case) |

### `Actor::adjustActorPos` (`actor.cpp:2090`)

| Step | ScummVM | Our port | Status |
|------|---------|----------|--------|
| `adjustXYToBeInBox(_pos.x, _pos.y)` snap | All versions | `scummvm.snapPointToWalkbox_long` (post 822e1ae, FES caller only) | ⏳ partial — only used in FES, not in op_putActor |
| Apply snap to `_pos.x/y` | All versions | ✅ in FES caller | ⏳ |
| `_walkdata.destbox = abr.box` | All versions | UNVERIFIED — we may not write a destbox field | ❓ |
| `setBox(abr.box)` (sets `_walkbox`) | All versions | UNVERIFIED — we have actorWalkBox separate from snap output | ❓ |
| `stopActorMoving()` | All versions | ✅ via `stz moving` | ✅ |
| `cost.soundCounter/soundPos = 0` | All versions | UNVERIFIED | ❓ |
| Box-flag-based turnToDirection | All versions | UNVERIFIED | ❓ |

### `Actor::faceToObject` (`actor.cpp:1660`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Direction encoding (90/270 compass for v5+, 4-dir for v4-) | v5 | UNVERIFIED — task may not be implemented | ❓ |

### `Actor::setDirection` (`actor.cpp:1418`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Walkbox flag specdir override | v5 conditional on `!_ignoreBoxes` | UNVERIFIED | ❓ |

### `Actor::hideActor` / `showActor` / putActor visibility transition

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| `startScene` hides all actors (room.cpp:111-113) | All versions | NOT IMPLEMENTED — actors carry visibility from previous scene | ❌ MEDIUM (but may interact with how scripts re-show; tested removal regressed boot) |
| `startScene` `showActors()` after entry script | v5 | NOT IMPLEMENTED | ❓ |
| `Actor::putActor` showActor when transitioning hidden→in-room | All versions | NOT IMPLEMENTED in op_putActor | ❌ MEDIUM |
| `Actor::putActor` hideActor when leaving current room | All versions | NOT IMPLEMENTED in op_putActor | ❓ |

---

## Box-system divergences (`boxes.cpp`)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| `getBox` v4 OOB-clamp workaround | v <= 4 | 🚫 | 🚫 |
| `BOXM` matrix routing in `getNextBox` | All versions | ✅ scummvm.getNextBox uses BOXM | ✅ |
| `pointInBox` scanline scan | All versions | ✅ scummvm.pointInBox | ✅ |
| `checkXYInBoxBounds` for v5 walk | v5 | UNVERIFIED — our walk doesn't currently check this | ❓ |

---

## Costume / chore / SCAL divergences (`costume.cpp`, `actor.cpp` chore)

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| Costume frame data layout (v5 spec) | v5 | ✅ chore engine (per `project_chore_engine_status.md`) | ✅ |
| `_walkFrame=2 _standFrame=3` | v5 | ✅ (per `feedback_scumm_v5_anim_frames.md`) | ✅ |
| Stand-anim seed when actor stops moving | All versions | ⏳ — task #48 in_progress; may be the BRK source for stationary actor at room 33 entry | ⏳ |
| SCAL slot interpretation (4 slots × {s1, y1, s2, y2}) | v5 | ✅ extractor + tick engine | ✅ |
| SCAL out-of-y-range clamp behavior | v5 | UNVERIFIED — y=75 is below slot 0 y1=76 | ❓ |

---

## VAR / system divergences

| Behavior | ScummVM gate | Our port | Status |
|----------|--------------|----------|--------|
| `VAR_CURSORSTATE` / `VAR_USERPUT` set in endScene | v >= 4 | Always (we are v5) | ✅ |
| `VAR_NEW_ROOM` / `VAR_ROOM` updates | All versions | ✅ | ✅ |
| `VAR_WALKTO_OBJ` set during loadRoomWithEgo | All versions | UNVERIFIED — we may not set this | ❓ |

---

## Audit gaps (couldn't verify in 2026-04-26 round)

- **Walk pump step calculation** (`calcMovementFactor`): ScummVM v3/v4 use
  different step math than v5. Our walk advancer (`_walk.advance` /
  scummvm walk pump) hasn't been compared line-by-line to v5.
- **Cycle-accurate animation timing**: chore engine ticks each frame.
  ScummVM has actor.startAnimActor and frame-counter behavior; ours
  may diverge in edge cases.
- **Actor `cost.*` fields** (soundCounter, soundPos, frame, anim): we
  don't track all of these per-actor; some are global. May cause
  desync between sound and animation.
- **`needRedraw` / `needBgReset` flags**: all-actors-renderdirty pattern.
- **Box flag `kBoxLocked` / `kBoxPlayerOnly`**: pathfinding flags we
  may not honor.

---

## Remediation order (HIGH first)

1. ❌ HIGH: `op_putActor` calls `adjustActorPos` (walkbox snap +
   showActor). Without this, `op_putActor` is missing the implicit
   visibility/position-snap that ScummVM does for free. Likely fixes
   the "Guybrush parked at degenerate box → renderer BRK" regression
   from 822e1ae.
2. ❌ HIGH: `startWalkActor` calls `adjustXYToBeInBox` for v5.
   Required before walk pump kicks off so `_walkdata.dest` is always
   inside a real box.
3. ❌ HIGH: `findObject` visibility check via
   `kObjectClassUntouchable` (ticket #50).
4. ❌ HIGH: `op_actorOps` sub 0x11 (`scale`): verify v5 takes 2
   bytes (scaleX, scaleY).
5. ❌ MEDIUM: `_bwp.adjustToBox` three-pass threshold + backwards
   iteration + true edge-distance.
6. ❌ MEDIUM: `Actor::putActor` visibility transitions
   (showActor/hideActor on room match change).
7. ❓ verify gaps: walk pump step math, faceToObject direction
   encoding, walkbox flag specdir override.

---

## Test investment (per `feedback_test_coverage_gaps.md`)

- Fixture test: load `data/snes_converted/rooms/room_033.box`, run
  `buildWalkPath_long` from (9, 76) to (346, 133), assert pathLen == 0
  (BOXM has no route box 1 → box 9). This single test catches the
  "auto-walk to dock middle" cheat.
- Reference snapshot test: drive room 33 entry, capture `actors.x/y`
  and `actors.moving` at frames {entry+1, +5, +10, +50}, assert
  matches ScummVM-derived expected values.
- Visual regression CI: automate `tools/visual_compare.py` against
  reference video at known timestamps.
