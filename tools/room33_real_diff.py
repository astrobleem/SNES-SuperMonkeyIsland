"""Compare CORRUPT (natural flow) vs CLEAN (direct poke) room-33 states
byte-for-byte across multiple memory regions.

Both runs end at currentRoom=33. The natural flow produces visible
corruption (horizontal stripes in the upper half); the poke path
renders cleanly. Both are paused before reads so emulation races can't
muddy the comparison.

Dump regions:
  - BG1 tile data  (VRAM $0000..$6FFF)
  - BG1 tilemap    (VRAM $3000..$3FFF)
  - BG2 tile data  (VRAM where applicable)
  - BG2 tilemap    (VRAM $5800..$5FFF for this project)
  - CGRAM (all 512 bytes)
  - OAM low + high tables
  - SCUMM scroll WRAM region $7F0000..$7F4FFF

Whichever region shows per-byte differences in BG1 tile data without a
corresponding tilemap diff is the smoking gun.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

ROOT = Path(__file__).resolve().parent.parent

BG1_TILES = ("snesVideoRam", 0x0000, 0x7000)
BG1_TMAP = ("snesVideoRam", 0x3000, 0x1000)
BG2_TMAP = ("snesVideoRam", 0x5800, 0x1000)
CGRAM = ("snesCgRam", 0x000, 0x200)
OAM = ("snesSpriteRam", 0x000, 0x220)
SCROLL_WRAM = ("snesMemory", 0x7F0000, 0x5000)


def dump_all(m: McpSession) -> dict[str, bytes]:
    m.pause()
    out = {}
    for name, region in [
        ("BG1_TILES", BG1_TILES),
        ("BG1_TMAP", BG1_TMAP),
        ("BG2_TMAP", BG2_TMAP),
        ("CGRAM", CGRAM),
        ("OAM", OAM),
        ("SCROLL_WRAM", SCROLL_WRAM),
    ]:
        mt, addr, length = region
        out[name] = m.read_memory(mt, addr, length)
    return out


def natural_run(m: McpSession) -> None:
    m.run_frames(300)
    for i in range(8):
        m.set_input(m.BTN_START, 40)
        m.set_input(0, 760)
        if m.read_u8(0x7EF967) == 33:
            m.set_input(0, 300)
            break


def poke_run(m: McpSession) -> None:
    # Advance to SCUMM ready
    for _ in range(30):
        m.run_frames(200)
        if m.read_u8(0x7EF967) != 0:
            break
    # 10 -> 38 -> 33 (large intermediate, same as corrupt via path)
    for room, x, y in [(38, 160, 100), (33, 346, 133)]:
        m.write_u8(0x7E890C, room)
        m.write_u16(0x7E890C + 2, x)
        m.write_u16(0x7E890C + 4, y)
        m.write_u8(0x7E890C + 11, 1)
        m.write_u8(0x7EF969, room)
        for _ in range(10):
            m.run_frames(30)
            if m.read_u8(0x7EF967) == room:
                break
        m.run_frames(300)
    m.run_frames(600)


def compare(corrupt: dict[str, bytes], clean: dict[str, bytes]) -> None:
    for name in corrupt:
        c = corrupt[name]
        k = clean[name]
        if len(c) != len(k):
            print(f"{name}: length differs ({len(c)} vs {len(k)})")
            continue
        diff_bytes = sum(1 for a, b in zip(c, k) if a != b)
        pct = 100.0 * diff_bytes / len(c) if len(c) else 0.0
        marker = "!!! " if diff_bytes > 0 else "    "
        print(f"{marker}{name:12s}  {diff_bytes:6d} / {len(c):6d} bytes differ  ({pct:4.1f}%)")


def main() -> int:
    out = ROOT / "distribution"

    print("=== CORRUPT run (natural flow) ===")
    with McpSession(port=7353, boot_wait=3.0, socket_timeout=180) as m:
        natural_run(m)
        corrupt = dump_all(m)
        corrupt_room = m.read_u8(0x7EF967)
        corrupt_cam = m.read_u16(0x7EFA1F)
        shot = m.take_screenshot()
        (out / "r33_diff_corrupt.png").write_bytes(Path(shot['path']).read_bytes())
        print(f"  curRoom={corrupt_room}, cameraX={corrupt_cam}")
    if corrupt_room != 33:
        print(f"  ABORT: natural run didn't reach room 33 (cur={corrupt_room})")
        return 1

    print("\n=== CLEAN run (direct poke via room 38) ===")
    with McpSession(port=7354, boot_wait=3.0, socket_timeout=180) as m:
        poke_run(m)
        clean = dump_all(m)
        clean_room = m.read_u8(0x7EF967)
        clean_cam = m.read_u16(0x7EFA1F)
        shot = m.take_screenshot()
        (out / "r33_diff_clean.png").write_bytes(Path(shot['path']).read_bytes())
        print(f"  curRoom={clean_room}, cameraX={clean_cam}")
    if clean_room != 33:
        print(f"  ABORT: poke run didn't reach room 33 (cur={clean_room})")
        return 1

    print("\n=== state diff (corrupt vs clean) ===")
    print(f"  cameraX: corrupt={corrupt_cam}  clean={clean_cam}")
    compare(corrupt, clean)
    print("\nShots: distribution/r33_diff_corrupt.png  distribution/r33_diff_clean.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
