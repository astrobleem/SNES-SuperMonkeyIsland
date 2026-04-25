"""Take the same room-33 screenshot via MCP without pausing first.
If we see corruption here, it's a genuine rendering bug. If it's clean,
then the earlier 'corruption' was an artifact of the Lua-based screenshot
path (emu.getScreenBuffer mid-render)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

with McpSession(port=7351, boot_wait=3.0) as m:
    # Advance to SCUMM ready
    for _ in range(30):
        m.run_frames(200)
        if m.read_u8(0x7EF967) != 0:
            break

    # Transit 10 -> 38 (large) -> 33
    for room in (38, 33):
        m.write_u8(0x7E890C, room)
        if room == 33:
            m.write_u16(0x7E890C + 2, 346)
            m.write_u16(0x7E890C + 4, 133)
        m.write_u8(0x7E890C + 11, 1)
        m.write_u8(0x7EF969, room)
        for _ in range(10):
            m.run_frames(30)
            if m.read_u8(0x7EF967) == room:
                break
        m.run_frames(300)

    # Settle
    m.run_frames(600)

    # Take shot WITHOUT pausing first (emulator running at max speed)
    m.resume()
    time.sleep(0.2)
    shot = m.take_screenshot()
    Path('distribution/r33_live_unpaused.png').write_bytes(Path(shot['path']).read_bytes())
    print(f"live unpaused shot: distribution/r33_live_unpaused.png")

    # Now pause and shoot again
    m.pause()
    shot2 = m.take_screenshot()
    Path('distribution/r33_paused.png').write_bytes(Path(shot2['path']).read_bytes())
    print(f"paused shot: distribution/r33_paused.png")
