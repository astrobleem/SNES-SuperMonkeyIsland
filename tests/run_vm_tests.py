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
import subprocess
import sys
from pathlib import Path

ROOT       = Path(__file__).resolve().parents[1]
ROM_PATH   = ROOT / "distribution" / "SuperMonkeyIsland.sfc"
MESEN_EXE  = ROOT / "mesen" / "Mesen.exe"
SCRIPT     = ROOT / "tests" / "scumm_vm" / "test_runner.lua"
OUT        = ROOT / "tests" / "_last_run.txt"


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
    staged = ROOT / "distribution" / "vm_test_runner.lua"
    staged.write_text(SCRIPT.read_text(), encoding="utf-8")
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
