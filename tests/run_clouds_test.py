#!/usr/bin/env python3
"""Run the clouds-behind-logo visual regression test in Mesen testrunner mode.

Stages tests/clouds_behind_logo.lua into distribution/ (next to the ROM) and runs
it. Exits 0 on PASS, non-zero otherwise. Windows Python (uses cmd.exe + the Windows
Mesen build), same convention as tests/run_vm_tests.py.

    python tests/run_clouds_test.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT      = Path(__file__).resolve().parents[1]
ROM_PATH  = ROOT / "distribution" / "SuperMonkeyIsland.sfc"
MESEN_EXE = Path(os.environ.get("SMI_MESEN", ROOT / "mesen" / "Mesen.exe"))
SCRIPT    = ROOT / "tests" / "clouds_behind_logo.lua"
HARD_TIMEOUT_SEC = 120


def main():
    if not ROM_PATH.exists():
        print(f"[clouds_test] missing ROM: {ROM_PATH}", file=sys.stderr)
        return 1
    if not MESEN_EXE.exists():
        print(f"[clouds_test] missing Mesen: {MESEN_EXE}", file=sys.stderr)
        return 1

    staged = ROOT / "distribution" / "clouds_behind_logo.lua"
    staged.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    out_file = ROOT / "distribution" / "clouds_test_out.txt"
    if out_file.exists():
        out_file.unlink()

    cmd = (
        f'cd /d "{ROOT / "distribution"}" && '
        f'"{MESEN_EXE}" --testrunner SuperMonkeyIsland.sfc '
        f'clouds_behind_logo.lua > clouds_test_out.txt 2>&1'
    )
    try:
        subprocess.run(f'cmd.exe /c "{cmd}"', shell=True,
                       timeout=HARD_TIMEOUT_SEC, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print(f"[clouds_test] hard timeout {HARD_TIMEOUT_SEC}s", file=sys.stderr)

    log = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    sys.stdout.write(log)

    if "##CLOUDS_BEHIND_DONE##" not in log:
        print("[clouds_test] sentinel not seen — testrunner didn't complete",
              file=sys.stderr)
        return 2
    return 0 if "RESULT: PASS" in log else 1


if __name__ == "__main__":
    sys.exit(main())
