"""Dump OAM for corrupt vs clean room-33 states. The earlier CGRAM diff
pointed at palette 8 (first OAM palette) — suggesting the stripes are
sprites rendered in a broken palette, not BG tiles."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession


def natural(m):
    m.run_frames(300)
    for _ in range(8):
        m.set_input(m.BTN_START, 40)
        m.set_input(0, 760)
        if m.read_u8(0x7EF967) == 33:
            m.set_input(0, 300)
            break


def poke(m):
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


def dump_oam(m):
    m.pause()
    low = m.read_memory("snesSpriteRam", 0, 512)
    high = m.read_memory("snesSpriteRam", 512, 32)
    return low, high


def decode(low: bytes, high: bytes) -> list[dict]:
    sprites = []
    for i in range(128):
        e = low[i * 4 : i * 4 + 4]
        x_lo = e[0]
        y = e[1]
        tile = e[2]
        attr = e[3]
        # High table: 1 byte per 4 sprites, 2 bits per sprite
        hbyte = high[i // 4]
        shift = (i % 4) * 2
        hi_bits = (hbyte >> shift) & 0x03
        x_hi = hi_bits & 1
        size = (hi_bits >> 1) & 1
        x = x_lo | (x_hi << 8)
        if x >= 256:
            x -= 512
        palette = (attr >> 1) & 0x07
        priority = (attr >> 4) & 0x03
        hflip = (attr >> 6) & 1
        vflip = (attr >> 7) & 1
        tile_hi = attr & 1
        full_tile = tile | (tile_hi << 8)
        sprites.append({
            "i": i, "x": x, "y": y, "tile": full_tile,
            "pal": palette, "pri": priority,
            "hf": hflip, "vf": vflip, "size": size,
        })
    return sprites


def summarize(sprites: list[dict], label: str) -> None:
    visible = [s for s in sprites if s["y"] < 0xF0]
    print(f"[{label}] {len(visible)}/128 sprites on-screen")
    # Which palettes in use?
    from collections import Counter
    pal_counts = Counter(s["pal"] for s in visible)
    print(f"  palette usage: {dict(pal_counts)}")


def main():
    with McpSession(port=7357, boot_wait=3.0, socket_timeout=180) as m:
        natural(m)
        clow, chigh = dump_oam(m)
    with McpSession(port=7358, boot_wait=3.0, socket_timeout=180) as m:
        poke(m)
        klow, khigh = dump_oam(m)

    cs = decode(clow, chigh)
    ks = decode(klow, khigh)
    summarize(cs, "CORRUPT (natural)")
    summarize(ks, "CLEAN (poke)")

    # Per-sprite diff
    changed = 0
    for i in range(128):
        if cs[i] != ks[i]:
            changed += 1
            c = cs[i]; k = ks[i]
            # Only show non-boring diffs
            if c["y"] < 0xF0 or k["y"] < 0xF0:
                print(f"  sprite {i:3d}: corrupt={c}  clean={k}")
    print(f"\nTotal sprites differing: {changed}/128")


if __name__ == "__main__":
    main()
