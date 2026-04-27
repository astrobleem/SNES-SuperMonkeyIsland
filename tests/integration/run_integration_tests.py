#!/usr/bin/env python3
"""SCUMM VM integration test runner.

The unit-grade harness in `tests/run_vm_tests.py` exercises opcode
semantics in isolation by injecting bytecode into a frozen test slot.
That can't catch gameplay-grade bugs — Bug 1 (invisible spawn), Bug 2
(moonwalking), Bug 3 (cannot enter SCUMM bar), Bug 4 (old man on
campfire) all slipped through 178 unit tests.

This runner is the second pass: it boots the ROM normally, lets MI1's
intro run, then drives game-state pokes / clicks and asserts the
visible result. Each case may produce a verification screenshot.

Run:
    python3 tests/integration/run_integration_tests.py
    python3 tests/integration/run_integration_tests.py --case scumm_bar
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT      = Path(__file__).resolve().parents[2]
ROM_PATH  = ROOT / "distribution" / "SuperMonkeyIsland.sfc"
MESEN_EXE = ROOT / "mesen" / "Mesen.exe"
DIST      = ROOT / "distribution"


def run_mesen_lua(script_text: str, timeout_sec: int = 90) -> str:
    """Run Mesen testrunner with the given Lua script; return stdout."""
    staged = DIST / "integration_runner.lua"
    staged.write_text(script_text, encoding="utf-8")
    out_file = DIST / "integration_out.txt"
    if out_file.exists():
        out_file.unlink()
    cmd = (
        f'cd /d "{DIST}" && "{MESEN_EXE}" --testrunner SuperMonkeyIsland.sfc '
        f'integration_runner.lua > integration_out.txt 2>&1'
    )
    try:
        subprocess.run(f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout_sec,
                       stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"[runner] hard timeout {timeout_sec}s"
    return out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def case_scumm_bar() -> tuple[bool, str]:
    """Bug 3 verification: room 28 (SCUMM bar interior) is reachable.

    Boots past the intro (frame 1500), pokes newRoom=28, waits for
    processRoomChange to take effect, asserts currentRoom == 28.
    Verification screenshot of room 28 is captured separately via
    `mcp__smi-workflow__take_screenshot` (the lua → ARGB → PNG path) —
    Mesen's own Lua API has no `emu.saveScreenshot`.
    """
    lua = r'''
local NEW_ROOM = 0x7EF8E9
local CUR_ROOM = 0x7EF8E7
local fc = 0
local state = "boot"
local poked = 0
local target = 28

emu.addEventCallback(function()
  fc = fc + 1
  if state == "boot" and fc > 1500 then
    emu.write(NEW_ROOM, target, emu.memType.snesMemory)
    state = "poked"
    poked = fc
  elseif state == "poked" then
    local cr = emu.read(CUR_ROOM, emu.memType.snesMemory)
    if cr == target then
      state = "settling"
      print(string.format("[runner] f=%d ROOM %d LOADED", fc, target))
    elseif fc - poked > 300 then
      print(string.format("[runner] f=%d TIMEOUT cr=%d", fc, cr))
      print("##INTEGRATION_FAIL##")
      emu.stop(1)
    end
  elseif state == "settling" then
    -- Settle 30 frames so processRoomChange + initial fade complete.
    if fc >= poked + 60 then
      local cr = emu.read(CUR_ROOM, emu.memType.snesMemory)
      print(string.format("[result] currentRoom=%d expected=%d", cr, target))
      if cr == target then
        print("##INTEGRATION_PASS##")
        emu.stop(0)
      else
        print("##INTEGRATION_FAIL##")
        emu.stop(1)
      end
    end
  end
end, emu.eventType.endFrame)
'''
    out = run_mesen_lua(lua, timeout_sec=60)
    passed = "##INTEGRATION_PASS##" in out
    return passed, out


CASES = {
    "scumm_bar": case_scumm_bar,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=list(CASES.keys()) + ["all"], default="all")
    args = p.parse_args()

    if not ROM_PATH.exists() or not MESEN_EXE.exists():
        print(f"[runner] missing ROM ({ROM_PATH}) or Mesen ({MESEN_EXE})",
              file=sys.stderr)
        return 1

    cases = list(CASES.items()) if args.case == "all" else [(args.case, CASES[args.case])]
    failures = []
    for name, fn in cases:
        print(f"[runner] === case: {name} ===")
        ok, out = fn()
        for line in out.splitlines():
            if line.startswith("[runner]") or line.startswith("[result]"):
                print(f"  {line}")
        status = "PASS" if ok else "FAIL"
        print(f"[runner] {name}: {status}")
        if not ok:
            failures.append(name)

    if failures:
        print(f"\n[runner] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print(f"\n[runner] {len(cases)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
