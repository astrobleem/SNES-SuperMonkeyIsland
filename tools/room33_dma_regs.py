"""Dump DMA channel source addresses for each of the 8 DMA channels in
both corrupt and clean room 33 states. If the HDMA source for the TMAIN
channel points at different table bytes, that tells us which table is
active and whether a stale one got stuck."""
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


def dump_dma(m):
    m.pause()
    # DMA registers at $4300..$437F; 8 channels × 16 bytes.
    # $43x2: source low, $43x3: source mid, $43x4: source bank.
    out = {}
    for ch in range(8):
        base = 0x4300 + ch * 0x10
        regs = m.read_memory("snesMemory", base, 11)
        out[ch] = {
            "control": regs[0],
            "target": regs[1],         # B bus ($21xx low byte)
            "src": (regs[4] << 16) | (regs[3] << 8) | regs[2],
            "count/addr": (regs[6] << 8) | regs[5],
            "indirect_bank": regs[7],
            "table_addr": (regs[9] << 8) | regs[8],
            "line_counter": regs[10],
        }
    return out


def main():
    with McpSession(port=7363, boot_wait=3.0, socket_timeout=180) as m:
        natural(m)
        corrupt = dump_dma(m)
    with McpSession(port=7364, boot_wait=3.0, socket_timeout=180) as m:
        poke(m)
        clean = dump_dma(m)

    for ch in range(8):
        c = corrupt[ch]; q = clean[ch]
        diff = c != q
        marker = "!!!" if diff else "   "
        print(f"{marker} ch{ch}  corrupt={c}")
        print(f"        clean  ={q}")


if __name__ == "__main__":
    main()
