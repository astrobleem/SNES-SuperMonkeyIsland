# SCUMM VM Tests

Executable test coverage for the SCUMM v5 VM, so we can spot regressions
without playing through 85 rooms by hand. Each test injects a tiny
synthetic bytecode sequence into the script cache, lets the SCUMM
scheduler run it for a frame or two, and asserts the resulting WRAM state.

## Layout

- `scumm_vm/test_runner.lua` — single-file Lua harness + tests.
  Mesen's testrunner disables `dofile`/`require`, so the harness is
  inlined. Add new tests at the bottom of the `TESTS` table.
- `run_vm_tests.py` — Python wrapper that stages the script into
  `distribution/` and runs it via Mesen's testrunner.

## Running

```bash
python3 tests/run_vm_tests.py            # use the current build
python3 tests/run_vm_tests.py --build    # rebuild first
```

The runner exits 0 when every test passes, non-zero otherwise. Lua's
`emu.log` output (per-test PASS/FAIL lines + a summary) is captured to
`tests/_last_run.txt` and echoed to stdout.

## Adding a test

In `scumm_vm/test_runner.lua`, write a Lua function that:

1. Pre-clears any state the test depends on (`H.wr8 / H.wr16`).
2. Calls `H.run_bytecode({...})` with the bytecode terminating in `$A0`
   (`stopObjectCode`). The harness writes the bytes into the test cache
   region (`$7F:F000`) and primes the test slot (last slot, index 23).
3. Asserts on resulting WRAM via `H.assert_eq(...)`.

Append the test to the `TESTS` table.

## Patterns + gotchas (learned from building this)

- **Cache offset wrap.** `slot.cachePtr` is added to `$7F:6400` with
  16-bit wrap. Test bytecode at `$7F:F000` therefore needs cache offset
  `$8C00` (= `$F000 - $6400`), NOT `$AC00` (which wraps into the walkbox
  buffer at `$7F:1000`).
- **Use vars, not actors, as path-detection signals.** MI1's chore
  engine and per-frame actor update touch low-numbered actor coords
  every frame regardless of slot freezing. Use a high-numbered global
  var (e.g. Var[210], Var[211]) with a unique sentinel value to detect
  which branch of a conditional ran. Actors >= 16 are also reasonably
  safe (out of `SCUMM_WALK_ACTORS` range).
- **Test ordering.** Tests with side effects (room change, LSCR spawn)
  destabilize subsequent tests. Run them last in the `TESTS` table, or
  restore state before returning. `op_loadRoom` test uses
  `saved_current` + writes `newRoom = currentRoom` to suppress
  `processRoomChange` on the next frame.
- **Other slots get frozen.** The harness sets `freezeCount = $FF` on
  every slot except the test slot for the duration of `run_bytecode`,
  then restores. This is what keeps MI1's gameplay scripts from
  rewriting test state mid-run.
- **No `dofile` / `require` / `io`.** Mesen's `--testrunner` runs Lua
  with I/O disabled. Don't try to split the harness across files.
- **No `emu.frameAdvance` in script body.** Mesen Lua is callback-only.
  The harness wraps `run_all` in a coroutine resumed from an
  `endFrame` callback; tests `coroutine.yield()` to wait a frame
  (wrapped as `H.wait_frames(N)` and inside `H.run_bytecode`).
- **`emu.stop()` is unreliable from callbacks.** Set a `stop_requested`
  flag in the callback and call `emu.stop()` on the *following* frame.
  The Python runner also kills the Mesen process after seeing
  `##VM_TESTS_DONE##` on stdout as a belt-and-suspenders.
- **Field offsets aren't structural — verify.** `actor.initFrame` is at
  `+12`, not `+8`. Use `lookup_symbol` (or the symfile directly) for
  any new field you reference. Sample offsets:
  - `actor.room=+0`, `costume=+1`, `x=+2`, `y=+4`, `facing=+6`,
    `elevation=+8`, `moving=+10`, `visible=+11`, `initFrame=+12`,
    `scalex=+13`, `talkColor=+14`.
- **Hex address arithmetic in Python:** `python3 -c "print(hex(N))"`.
  Several harness bugs were "I converted decimal to hex by hand and got
  it wrong." The `lookup_symbol` MCP returns decimal — convert it.

## Why this exists

Several rounds of "is opcode X implemented?" / "is it implemented
correctly?" wasted hours each. The right answer is automated coverage.
Each opcode's contract should have at least one test pinning down what
it writes (and what it leaves alone). Bug reports become "the test for
opcode Y now fails" instead of "Guybrush is in the wrong spot in room
33." Walk the dispatch table, write a test per row.
