#!/usr/bin/env python3
"""SCUMM VM test runner.

Runs tests/scumm_vm/test_runner.lua against the current build of the ROM
under Mesen's testrunner mode. Exits 0 on all-pass, non-zero on failure.

Usage:
    python3 tests/run_vm_tests.py
    python3 tests/run_vm_tests.py --build  # make first
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[1]
ROM_PATH   = ROOT / "distribution" / "SuperMonkeyIsland.sfc"
MESEN_EXE  = Path(os.environ.get("SMI_MESEN", ROOT / "mesen" / "Mesen.exe"))
SCRIPT     = ROOT / "tests" / "scumm_vm" / "test_runner.lua"
SYM_PATH   = ROOT / "build" / "SuperMonkeyIsland.sym"
OUT        = ROOT / "tests" / "_last_run.txt"

# WRAM addresses shift whenever a ramsection changes size, so the H.SYM
# constants in test_runner.lua are re-resolved from the .sym file at stage
# time. The values checked into the lua are documentation defaults only.
LUA_SYMBOLS = {
    "SCUMM_actors_base":   "SCUMM.actors",
    "SCUMM_slots_base":    "SCUMM.slots",
    "SCUMM_currentRoom":   "SCUMM.currentRoom",
    "SCUMM_newRoom":       "SCUMM.newRoom",
    "SCUMM_globalVars":    "SCUMM.globalVars",
    "SCUMM_pendingEgoObj": "SCUMM.pendingEgoObj",
    "SCUMM_pendingEgoX":   "SCUMM.pendingEgoX",
    "SCUMM_pendingEgoY":   "SCUMM.pendingEgoY",
    "SCUMM_egoPositioned": "SCUMM.egoPositioned",
    "SCUMM_bitVars":       "SCUMM.bitVars",
}


def load_wram_symbols():
    """Parse the wlalink sym file; return {name: address} for 24-bit labels."""
    table = {}
    for line in SYM_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and ":" in parts[0]:
            addr = parts[0].split(":")[1]
            if len(addr) == 6:          # WRAM label (ROM labels are 4-digit)
                table[parts[1]] = int(addr, 16)
    return table


def resolve_lua_addresses(text):
    syms = load_wram_symbols()

    # Lint first: every bank-$7E literal must be in one of the substituted
    # conventions (an H.SYM table entry or a `local <PREFIX>_xxx = 0x...`
    # declaration), otherwise it silently goes stale on the next WRAM shift.
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("--")[0]
        if (re.search(r"0x7[Ee][0-9A-Fa-f]{4}", code)
                and not re.match(r"\s*(?:SCUMM|Mesen)_\w+\s*=", code)
                and not re.match(r"\s*local\s+(?:SCUMM|Mesen|GLOBAL)_\w+\s*=", code)):
            print(f"[run_vm_tests] hardcoded $7E address at test_runner.lua:{i}: "
                  f"{line.strip()} — use a `local SCUMM_xxx = 0x...` declaration",
                  file=sys.stderr)
            sys.exit(2)

    for lua_key, sym_name in LUA_SYMBOLS.items():
        addr = syms.get(sym_name)
        if addr is None:
            print(f"[run_vm_tests] symbol not in sym file: {sym_name}", file=sys.stderr)
            sys.exit(2)
        text, n = re.subn(rf"({lua_key}\s*=\s*)0x[0-9A-Fa-f]+",
                          lambda m, a=addr: f"{m.group(1)}0x{a:06X}", text)
        if n != 1:
            print(f"[run_vm_tests] expected 1 occurrence of {lua_key}, found {n}",
                  file=sys.stderr)
            sys.exit(2)

    # Second pass: per-test `local SCUMM_xxx = 0x7E....` declarations map
    # mechanically to engine symbols (SCUMM_foo -> SCUMM.foo, Mesen_foo -> foo,
    # GLOBAL_room_cameraX -> GLOBAL.room.cameraX). Bank $7F addresses are
    # fixed-layout defines and stay literal.
    def sub_local(m):
        name = m.group(2)
        if name.startswith("SCUMM_"):
            sym_name = name.replace("SCUMM_", "SCUMM.", 1)
        elif name.startswith("Mesen_"):
            sym_name = name.replace("Mesen_", "", 1)
        else:                                   # GLOBAL_*
            sym_name = name.replace("_", ".")
        addr = syms.get(sym_name)
        if addr is None:
            print(f"[run_vm_tests] no symbol for lua local {name} ({sym_name})",
                  file=sys.stderr)
            sys.exit(2)
        return f"{m.group(1)}0x{addr:06X}"

    text = re.sub(r"(local\s+((?:SCUMM|Mesen|GLOBAL)_\w+)\s*=\s*)0x7[Ee][0-9A-Fa-f]{4}",
                  sub_local, text)
    return text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true",
                   help="run `make` before testing")
    args = p.parse_args()

    if args.build:
        rc = subprocess.run(
            ["wsl", "-e", "bash", "-lc",
             f"cd /mnt/{str(ROOT)[0].lower()}/{str(ROOT)[3:].replace(chr(92),'/')} && make"],
            check=False
        ).returncode
        if rc != 0:
            print("[run_vm_tests] build failed", file=sys.stderr)
            return rc

    if not ROM_PATH.exists():
        print(f"[run_vm_tests] missing ROM: {ROM_PATH}", file=sys.stderr)
        return 1
    if not MESEN_EXE.exists():
        print(f"[run_vm_tests] missing Mesen: {MESEN_EXE}", file=sys.stderr)
        return 1

    # Stage the test script into distribution/ so Mesen's testrunner picks
    # it up by simple filename (matches the MCP run_test convention).
    # H.SYM addresses are re-resolved from the current sym file on the way.
    staged = ROOT / "distribution" / "vm_test_runner.lua"
    staged.write_text(resolve_lua_addresses(SCRIPT.read_text()), encoding="utf-8")
    out_file = ROOT / "distribution" / "vm_test_out.txt"
    if out_file.exists():
        out_file.unlink()

    HARD_TIMEOUT_SEC = 240
    cmd = (
        f'cd /d "{ROOT / "distribution"}" && '
        f'"{MESEN_EXE}" --testrunner SuperMonkeyIsland.sfc '
        f'vm_test_runner.lua > vm_test_out.txt 2>&1'
    )
    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"', shell=True, timeout=HARD_TIMEOUT_SEC,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        print(f"[run_vm_tests] hard timeout {HARD_TIMEOUT_SEC}s", file=sys.stderr)

    log = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    OUT.write_text(log)
    sys.stdout.write(log)

    if "##VM_TESTS_DONE##" not in log:
        print("[run_vm_tests] sentinel not seen — harness didn't complete",
              file=sys.stderr)
        return 2

    summary_lines = [l for l in log.splitlines()
                     if "[harness]" in l and "passed" in l]
    if summary_lines:
        last = summary_lines[-1]
        try:
            parts = last.split()
            passed = int(parts[1])
            failed = int(parts[3])
            print(f"\n[run_vm_tests] {passed} passed, {failed} failed")
            return 0 if failed == 0 else 1
        except Exception:
            pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
