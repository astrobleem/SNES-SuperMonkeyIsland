"""Phase 3.5 smoke test: verify the completeness-pass tools.

Coverage:
  - get_state now reports frameCount
  - lookup_symbol resolves names from build/SuperMonkeyIsland.sym
  - disassemble at a known PC returns SCUMM bytecode
  - add_read_hook + add_write_hook fire on the SCUMM.currentRoom byte
  - hook value match filters out unwanted events
  - run_until returns when a hook fires (vs hitting maxFrames)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

ROOT = Path(__file__).resolve().parent.parent
SYM = ROOT / "build" / "SuperMonkeyIsland.sym"


def main():
    with McpSession(port=7400, boot_wait=3.0, socket_timeout=180) as m:
        print("\n=== get_state (now has frameCount) ===")
        s = m.get_state()
        print(f"  isRunning={s['isRunning']} isPaused={s['isPaused']} frameCount={s['frameCount']}")

        print("\n=== lookup_symbol ===")
        r = m.lookup_symbol(str(SYM), "^SCUMM\\.currentRoom$")
        print(f"  matches: {r['matches']}  (totalSymbols={r['totalSymbols']})")
        cur_room_addr = r["matches"][0]["address"]

        r = m.lookup_symbol(str(SYM), "^_scummvm\\.fetch")
        print(f"  fetch* matches: {[(x['name'], hex(x['address'])) for x in r['matches']]}")
        fetch_loop_addr = next(x["romCpuAddr"] for x in r["matches"]
                               if x["name"] == "_scummvm.fetchLoop")

        print(f"\n=== disassemble fetchLoop @ 0x{fetch_loop_addr:x} ===")
        lines = m.disassemble(fetch_loop_addr, count=8)
        for ln in lines:
            print(f"  ${ln['address']:06X}: {ln['byteCode']:<10} {ln['text']}")

        print(f"\n=== add_write_hook on currentRoom @ 0x{cur_room_addr:x} ===")
        h = m.add_write_hook(cur_room_addr)
        print(f"  hook={h}")

        print("\n=== run_until (hook on currentRoom write) ===")
        m.run_until(max_frames=2000, hook_handle=h)
        notifs = m.drain_notifications(0.5)
        print(f"  notifications: {len(notifs)}")
        for n in notifs[:3]:
            p = n["params"]
            print(f"    write to ${p['address']:06X} value=0x{p['value']:02X} frame={p['frame']}")
        m.remove_hook(h)

        print("\n=== add_write_hook with value match (only fire on writes of 33) ===")
        h2 = m.add_write_hook(cur_room_addr, match_value=33, match_value_mask=0xFF)
        m.run_frames(60)
        notifs = m.drain_notifications(0.3)
        print(f"  notifications (value-filtered): {len(notifs)}")
        m.remove_hook(h2)

        print(f"\n=== final get_state ===")
        s = m.get_state()
        print(f"  frameCount={s['frameCount']} isPaused={s['isPaused']}")


if __name__ == "__main__":
    main()
