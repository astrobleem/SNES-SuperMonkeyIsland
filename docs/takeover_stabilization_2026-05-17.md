# Takeover Stabilization - 2026-05-17

## Scope

Stabilize the dirty checkout before feature work. Do not land the mixed audio,
tool, or scratch changes. Do not push.

## Kept

- `src/object/scummvm/scummvm.65816`: Bug 85 flicker mitigation. It suppresses
  redundant `actorsDirty` costume reload work when `putActorInRoom` reissues a
  no-op room assignment, and skips palette/CHR DMA when a render slot's
  actor/costume pair is unchanged from the pre-clear snapshot.
- `CLAUDE.md`: current build, Mesen, VM-test, and architecture guidance.
- `AGENTS.md`: reconciled Mesen-MCP tool count and the old #23/#24/#25
  harness-deferral text with the current checkout.
- Historical root docs (`HANDOFF.md`, `TODO.md`, `stillbroken.txt`) were not
  deleted. The deletion attempt is preserved only in the quarantine stash.

## Quarantined

- Tracked mixed WIP was moved to `stash@{0}` with message
  `codex takeover quarantine mixed tracked WIP before stabilization`.
- That stash includes the audio/sample experiment, audio tooling edits,
  deleted root docs, deleted scratch scripts, and the original copies of the
  two files selectively restored for stabilization.
- Untracked local files remain uncommitted in the worktree. They include local
  skills/config, WLA binaries, audio sample candidates, extra data, scratch
  probes, and local tool prerequisites such as `tools/tad/`.

## Verification Results

- `python3 tests/run_vm_tests.py --build`: PASS, 178 passed / 0 failed.
- `python3 tests/integration/run_integration_tests.py`: PASS, 1 passed
  (`scumm_bar`, currentRoom 28).
- `python3 tools/rom_usage.py build/SuperMonkeyIsland.sym build/SuperMonkeyIsland.sfc`:
  PASS, Bank 0 at 46,782/65,536 bytes (71.4%).
- `git diff --check`: PASS. Git reported only CRLF conversion warnings for
  `AGENTS.md` and `CLAUDE.md`.
- Targeted Mesen room-38 black-band proof: PASS.
  - Route: boot 1500 frames, poke `SCUMM.newRoom=38`, wait for
    `SCUMM.currentRoom=38`, settle 360 frames, capture 12 screenshots every
    6 frames.
  - Manifest:
    `docs/proofs/takeover_2026-05-17/black_band_proof_manifest.json`
  - Contact sheet:
    `docs/proofs/takeover_2026-05-17/room38_black_band_contact_sheet.png`
  - MCP filmstrip:
    `docs/proofs/takeover_2026-05-17/room38_black_band_mcp_filmstrip.png`
  - Recorded final state: `currentRoom=38`, `actorsDirty=0`, `MainScreen=21`.
  - Automated row-darkness heuristic: max black-band run 0 rows in y=3..75.
  - Visual inspection: room art and fire animation are stable; no room-area
    black band is visible in either the contact sheet or MCP filmstrip.

## Open

- Dialog choice selection still blocks pirate conversations.
- Title-screen mountain cloud flicker is only partially mitigated.
- Save/load serialization, `palManipulate`, room-by-room verification, and
  MSU-1 voice acting remain future work.
- The quarantined audio experiment needs its own proof pass before landing.
