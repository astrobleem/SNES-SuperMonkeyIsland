"""Break down the CGRAM difference between corrupt-flow and clean-poke
room-33 states at palette / color granularity. 29 bytes differ; that's
~15 colors across potentially multiple subpalettes. Want to know which."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession


def natural_to_room_33(m: McpSession) -> None:
    m.run_frames(300)
    for _ in range(8):
        m.set_input(m.BTN_START, 40)
        m.set_input(0, 760)
        if m.read_u8(0x7EF967) == 33:
            m.set_input(0, 300)
            break


def poke_to_room_33(m: McpSession) -> None:
    for _ in range(30):
        m.run_frames(200)
        if m.read_u8(0x7EF967) != 0:
            break
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


def dump_cgram(m: McpSession) -> bytes:
    m.pause()
    return m.read_memory("snesCgRam", 0, 512)


def bgr555_to_rgb(word: int) -> tuple[int, int, int]:
    r = (word & 0x1F) << 3
    g = ((word >> 5) & 0x1F) << 3
    b = ((word >> 10) & 0x1F) << 3
    return (r, g, b)


def main() -> int:
    print("corrupt run")
    with McpSession(port=7355, boot_wait=3.0, socket_timeout=180) as m:
        natural_to_room_33(m)
        corrupt = dump_cgram(m)

    print("clean run")
    with McpSession(port=7356, boot_wait=3.0, socket_timeout=180) as m:
        poke_to_room_33(m)
        clean = dump_cgram(m)

    # 16 palettes of 16 colors (2 bytes each) = 512 bytes.
    print("\npalette-by-palette diff summary:")
    for pal in range(16):
        pal_diff = 0
        for c in range(16):
            off = pal * 32 + c * 2
            if corrupt[off:off+2] != clean[off:off+2]:
                pal_diff += 1
        tag = "!!!" if pal_diff > 0 else "   "
        print(f"  {tag} pal {pal:2d}: {pal_diff}/16 colors differ")

    # For palettes 0..5 (typically BG art in this game), dump the exact color
    # differences so we can see WHICH colors changed.
    print("\ndetailed diff for pal 0..5 (BG art):")
    for pal in range(6):
        for c in range(16):
            off = pal * 32 + c * 2
            cw = corrupt[off] | (corrupt[off+1] << 8)
            kw = clean[off]   | (clean[off+1]   << 8)
            if cw != kw:
                cr = bgr555_to_rgb(cw)
                kr = bgr555_to_rgb(kw)
                print(f"  pal{pal}.color{c:2d}: corrupt=0x{cw:04x}{cr} clean=0x{kw:04x}{kr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
