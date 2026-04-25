"""Diff BG1 VRAM tile data between clean and corrupt room-33 renders.

Earlier investigation (in-tree task #11/#15) reproduced deterministic
corruption via tools/room_test.py: a transition through rooms with >~1000
unique tiles (1/5/10/20/25/38) breaks subsequent room-33 rendering. Every
probeable WRAM state was identical between the clean and corrupt cases;
the difference had to be in BG1 VRAM tile DATA, which Lua reads of
emu.memType.videoRam could not reliably dump.

Now with the in-Mesen MCP + pause, VRAM reads are deterministic. This
script:

  1. Boot Mesen fresh, wait for SCUMM VM ready.
  2. Checkpoint A (clean baseline): save state, poke room 33 directly,
     settle, pause, dump BG1 tile data (VRAM $0000..$6FFF = 28 KB).
  3. Rewind to the checkpoint, transit 10 -> 33 via a large room, settle,
     pause, dump the same VRAM range.
  4. Compare byte-for-byte. Report which 32-byte tile slots differ, whose
     tile-IDs the BG1 tilemap points at in each case, and screenshot both
     for eyeballing.

Anything that shows up as "same tilemap entry but different VRAM data at
that slot" is the smoking gun. Anything that shows "different tilemap
entries" means a different tile was allocated to that slot (ring-buffer
reuse) and the pixel data is expected to differ.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from mcp_client import McpSession

# SNES memory map constants.
CURRENT_ROOM = 0x7EF967
NEW_ROOM = 0x7EF969
RUNNING = 0x7EF941
ACTOR1 = 0x7E890C  # {room, costume, x, y, facing, ...}

# VRAM map (BG1 tile data lives at VRAM byte $0000..$6FFF; one 4bpp tile = 32 B).
BG1_TILE_BASE = 0x0000
BG1_TILE_BYTES = 0x7000  # 896 slots × 32 B
BG1_TILEMAP_BASE = 0x3000  # VRAM word $1800 = byte $3000


def wait_for_rom(m: McpSession, min_frames: int, max_ticks: int = 60) -> int:
    """Advance emulation until SCUMM is up and we've passed min_frames."""
    total = 0
    for _ in range(max_ticks):
        m.run_frames(200)
        total += 200
        if m.read_u16(RUNNING) != 0 and m.read_u8(CURRENT_ROOM) != 0 and total >= min_frames:
            return total
    raise RuntimeError(f"SCUMM not ready after {total} frames")


def poke_room(m: McpSession, room: int, x: int, y: int) -> None:
    """Seed actor 1 + request room change via WRAM poke."""
    m.write_u8(ACTOR1 + 0, room)          # actor 1 room
    m.write_u16(ACTOR1 + 2, x)            # x
    m.write_u16(ACTOR1 + 4, y)            # y
    m.write_u8(ACTOR1 + 11, 1)            # visible
    m.write_u8(NEW_ROOM, room)            # trigger processRoomChange


def wait_for_room(m: McpSession, target: int, max_batches: int = 10) -> None:
    for _ in range(max_batches):
        m.run_frames(30)
        if m.read_u8(CURRENT_ROOM) == target:
            return
    raise RuntimeError(f"room {target} never reached; curRoom={m.read_u8(CURRENT_ROOM)}")


def dump_bg1_vram(m: McpSession) -> bytes:
    # Pull in chunks (MCP caps length at 65536; fits in one request).
    m.pause()
    return m.read_memory("snesVideoRam", BG1_TILE_BASE, BG1_TILE_BYTES)


def dump_bg1_tilemap(m: McpSession) -> bytes:
    # 64 × 32 = 2048 tilemap entries × 2 bytes = 4096 B at $3000.
    m.pause()
    return m.read_memory("snesVideoRam", BG1_TILEMAP_BASE, 4096)


def scenario(m: McpSession, boot_min: int, via_room: int | None, label: str, shot_out: Path) -> dict:
    print(f"[{label}] waiting for ROM, boot_min={boot_min}")
    wait_for_rom(m, boot_min)
    if via_room is not None:
        print(f"[{label}] transit via room {via_room}")
        poke_room(m, via_room, 160, 100)
        wait_for_room(m, via_room)
        m.run_frames(300)
    print(f"[{label}] transit to room 33")
    poke_room(m, 33, 346, 133)
    wait_for_room(m, 33)
    m.run_frames(600)  # settle for render to stabilize
    print(f"[{label}] settled; dumping")

    tilemap = dump_bg1_tilemap(m)
    tiles = dump_bg1_vram(m)
    shot = m.take_screenshot()
    shot_path = Path(shot["path"])
    if shot_path.exists() and shot_path != shot_out:
        shot_out.write_bytes(shot_path.read_bytes())

    return {
        "label": label,
        "tilemap": tilemap,
        "tiles": tiles,
        "currentRoom": m.read_u8(CURRENT_ROOM),
        "cameraX": m.read_u16(0x7EFA1F),
        "shot": str(shot_out),
    }


def diff_tiles(clean: bytes, corrupt: bytes) -> list[int]:
    """Return slot indices whose 32-byte tile data differs."""
    diff = []
    n = min(len(clean), len(corrupt))
    slot_bytes = 32
    for slot in range(n // slot_bytes):
        base = slot * slot_bytes
        if clean[base:base + slot_bytes] != corrupt[base:base + slot_bytes]:
            diff.append(slot)
    return diff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7350)
    ap.add_argument("--boot-min", type=int, default=500)
    ap.add_argument("--via", type=int, default=10,
                    help="intermediate room for the corrupt case (default 10)")
    ap.add_argument("--out-dir", default="distribution")
    args = ap.parse_args()

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Run A: clean load (direct 10 -> 33).
    with McpSession(port=args.port, boot_wait=3.0) as m:
        clean = scenario(m, args.boot_min, None, "clean",
                         out_dir / "r33_clean.png")

    # Run B: corrupt load (10 -> via -> 33).
    with McpSession(port=args.port + 1, boot_wait=3.0) as m:
        corrupt = scenario(m, args.boot_min, args.via, f"corrupt_via{args.via}",
                           out_dir / f"r33_corrupt_via{args.via}.png")

    print()
    print(f"clean cameraX={clean['cameraX']}  shot={clean['shot']}")
    print(f"corrupt cameraX={corrupt['cameraX']}  shot={corrupt['shot']}")
    print()

    tilemap_same = clean["tilemap"] == corrupt["tilemap"]
    print(f"BG1 tilemap bytes equal: {tilemap_same}  ({len(clean['tilemap'])} bytes)")
    diff_slots = diff_tiles(clean["tiles"], corrupt["tiles"])
    print(f"BG1 VRAM tile slots that differ: {len(diff_slots)} / {len(clean['tiles']) // 32}")
    if diff_slots:
        print(f"  first 20 differing slots: {diff_slots[:20]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
