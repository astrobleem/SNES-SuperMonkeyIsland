"""T3 smoke: frame hook, reset_emulator, hook lifecycle on reset."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession


def main():
    with McpSession(port=7420, boot_wait=3.0, socket_timeout=60) as m:
        print("\n=== add_frame_hook (every frame) ===")
        h = m.add_frame_hook(every_n=1)
        print(f"  hook={h}")
        m.run_frames(10)
        notifs = m.drain_notifications(0.3)
        print(f"  notifications: {len(notifs)} (expected ~10 across 10 frames)")
        for n in notifs[:3]:
            print(f"    frame={n['params']['frame']}")
        m.remove_hook(h)

        print("\n=== add_frame_hook (every 4th frame, value-match filter) ===")
        h2 = m.add_frame_hook(every_n=4)
        m.run_frames(20)
        notifs2 = m.drain_notifications(0.3)
        print(f"  notifications: {len(notifs2)} (expected ~5 across 20 frames @ every-4)")
        m.remove_hook(h2)

        print("\n=== reset_emulator ===")
        before = m.get_state()
        m.reset_emulator()
        m.run_frames(30)
        after = m.get_state()
        print(f"  before reset: frame={before['frameCount']}")
        print(f"  after reset+30 frames: frame={after['frameCount']}")
        print(f"  reset rewound: {after['frameCount'] < before['frameCount']}")


if __name__ == "__main__":
    main()
