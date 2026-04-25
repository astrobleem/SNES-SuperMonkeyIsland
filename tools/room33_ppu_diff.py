"""Compare PPU state between corrupt and clean room 33. BG1 tile data,
tilemap, and sprite state were identical; palette 8 (OAM) differed; CGRAM
colors mostly differed in pal 8. Screen shows upper-half BLACK in corrupt
case only. Prime suspect: main-screen layer enable mask or window mask
changed between the two states."""
import json
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


def dump(m):
    m.pause()
    return m.get_ppu_state()


def main():
    with McpSession(port=7359, boot_wait=3.0, socket_timeout=180) as m:
        natural(m)
        corrupt = dump(m)
    with McpSession(port=7360, boot_wait=3.0, socket_timeout=180) as m:
        poke(m)
        clean = dump(m)

    keys = ["forcedBlank", "brightness", "bgMode", "mainScreenLayers",
            "subScreenLayers", "mainScreenWindowMask", "subScreenWindowMask",
            "mosaicSize", "mosaicEnabled"]
    print(f"{'key':25s}  {'corrupt':>15s}  {'clean':>15s}  {'!':>1}")
    for k in keys:
        c = corrupt[k]; q = clean[k]
        marker = "!" if c != q else " "
        print(f"{k:25s}  {str(c):>15s}  {str(q):>15s}  {marker}")
    for i in range(4):
        print(f"\nLayer {i}:")
        lc = corrupt["layers"][i]; lq = clean["layers"][i]
        for key in lc:
            a = lc[key]; b = lq[key]
            marker = "!" if a != b else " "
            print(f"  {key:15s}  corrupt={a}  clean={b}  {marker}")


if __name__ == "__main__":
    main()
