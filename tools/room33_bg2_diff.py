"""Compare BG2-related WRAM + HDMA state between corrupt & clean r33."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

FIELDS = {
    "hasBg2Mask": (0x7EFA94, 1),
    "HDMA_ENABLE": (0x7EFD6E, 1),
    "hdmaNbaChannel": (0x7EF972, 1),
    "hdmaScChannel": (0x7EF973, 1),
    "hdmaHofsChannel": (0x7EF974, 1),
    "bg2HofsHdmaTable": (0x7EF976, 10),
    "cameraX": (0x7EFA1F, 2),
    "xScrollBG1": (0x7EFD54, 2),
    "cutsceneNest": (0x7EF965, 1),
    "verbHdmaChannel": (0x7EF971, 1),
    "verbDirty": (0x7E9B4E, 1),
    "MainScreen": (0x7EFD47, 1),
}


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
    out = {}
    for name, (addr, length) in FIELDS.items():
        out[name] = m.read_memory("snesMemory", addr, length).hex()
    # Also DMA channel source addresses — channel N base address is $43x0..$43xF
    # These are registers, not WRAM. Let's skip those and trust the PpuState.
    return out


def main():
    with McpSession(port=7361, boot_wait=3.0, socket_timeout=180) as m:
        natural(m)
        corrupt = dump(m)
        # also dump ppu scanline so we know if both samples are at the same Y
        corrupt_ppu = m.get_ppu_state()
    with McpSession(port=7362, boot_wait=3.0, socket_timeout=180) as m:
        poke(m)
        clean = dump(m)
        clean_ppu = m.get_ppu_state()

    print(f"{'field':20s}  {'corrupt':>20s}  {'clean':>20s}  {'!':>1}")
    for k in FIELDS:
        c = corrupt[k]; q = clean[k]
        marker = "!" if c != q else " "
        print(f"{k:20s}  {c:>20s}  {q:>20s}  {marker}")
    print(f"\ncorrupt scanline={corrupt_ppu['scanline']}  clean scanline={clean_ppu['scanline']}")


if __name__ == "__main__":
    main()
