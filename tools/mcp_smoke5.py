"""T2 smoke: crop_screenshot, save/load slot, read_dma_state."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession


def main():
    with McpSession(port=7410, boot_wait=3.0, socket_timeout=60) as m:
        print("\n=== save_state_slot 0, run_frames, load_state_slot 0 ===")
        m.run_frames(60)
        before_state = m.get_state()
        print(f"  before save: frame={before_state['frameCount']}")
        m.save_state_slot(0)
        m.run_frames(120)
        mid_state = m.get_state()
        print(f"  after run_frames(120): frame={mid_state['frameCount']}")
        m.load_state_slot(0)
        after_state = m.get_state()
        print(f"  after load slot 0: frame={after_state['frameCount']}")
        # Frame counter should rewind close to before_state['frameCount']
        assert after_state["frameCount"] <= mid_state["frameCount"], "load_state should rewind"

        print("\n=== crop_screenshot ===")
        # Top 96 px (sky region) of the 256x224 SNES screen
        crop = m.crop_screenshot(0, 0, 256, 96)
        print(f"  cropped: {crop}")
        crop2 = m.crop_screenshot(64, 100, 32, 32, format="base64")
        print(f"  inline: {crop2['width']}x{crop2['height']}, {crop2['bytes']} bytes")

        print("\n=== read_dma_state ===")
        chans = m.read_dma_state()
        for c in chans:
            if c["control"] != 0xFF:  # uninitialized = 0xFF
                print(f"  ch{c['channel']}: target={c['targetReg']} src=${c['aBusAddr']:06X} "
                      f"ctrl=${c['control']:02X} count={c['count']}")


if __name__ == "__main__":
    main()
