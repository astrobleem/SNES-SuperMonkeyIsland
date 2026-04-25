"""Drive Mesen through the *natural* intro path via MCP set_input — same
way a player reaches room 33 (press START through cutscenes, wait for the
scripts to transition rooms organically). Screenshot at the frame where
corruption was reported to live, so we can look at it.

If this produces the horizontal-stripe pattern, the bug is real and
reproducible via MCP, and we can bisect it from there without going back
to Lua.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "distribution" / "r33_natural_mcp.png"


def main():
    with McpSession(port=7352, boot_wait=3.0, socket_timeout=180) as m:
        # Let the boot sequence initialize (LucasArts splash etc).
        m.run_frames(300)

        # Press START repeatedly during intro windows. The Lua `run_with_input`
        # pattern was to hold start for 60 frames every ~800 frames; same
        # cadence here.
        burst_frames = 40
        gap_frames = 760
        total_bursts = 16  # covers ~13000 frames = most of the intro + Part One

        for i in range(total_bursts):
            m.set_input(m.BTN_START, burst_frames)
            m.set_input(0, gap_frames)
            cur = m.read_u8(0x7EF967)
            nxt = m.read_u8(0x7EF969)
            print(f"  burst {i+1}/{total_bursts}: curRoom={cur} newRoom={nxt}")
            if cur == 33 and i >= 4:
                # Give the scripts a couple seconds to settle post-transition
                m.set_input(0, 300)
                break

        m.pause()
        final_room = m.read_u8(0x7EF967)
        print(f"final curRoom={final_room}")
        shot = m.take_screenshot()
        OUT.write_bytes(Path(shot['path']).read_bytes())
        print(f"saved: {OUT}")


if __name__ == "__main__":
    main()
