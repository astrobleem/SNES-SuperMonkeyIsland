"""Audio MCP smoke: capture WAV, snapshot DSP, run analyzer."""
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpSession

ROOT = Path(__file__).resolve().parent.parent


def main():
    out = ROOT / "distribution" / "_mcp_audio_test.wav"
    out.unlink(missing_ok=True)

    with McpSession(port=7430, boot_wait=3.0, socket_timeout=60) as m:
        # Let the boot finish so audio engine is initialized
        m.run_frames(120)

        print(f"\n=== record_audio -> {out} ===")
        m.record_audio(out)
        m.run_frames(180)
        m.stop_audio()

        if not out.exists():
            print("ERROR: WAV not produced")
            return 1
        size = out.stat().st_size
        print(f"  WAV: {size} bytes")

        print("\n=== get_audio_state ===")
        s = m.get_audio_state()
        print(f"  SPC PC=${s['spc']['pc']:04X} cycle={s['spc']['cycle']}")
        print(f"  DSP main={s['dsp']['mainVolL']}/{s['dsp']['mainVolR']}  "
              f"keyOn=0x{s['dsp']['keyOn']:02X} flg=0x{s['dsp']['flg']:02X}")
        for v in s["voices"]:
            if v["volL"] != 0 or v["volR"] != 0 or v["envelope"] != 0:
                print(f"  voice {v['voice']}: vol={v['volL']:+d}/{v['volR']:+d} "
                      f"pitch={v['pitch']} env={v['envelope']} out={v['currentOutput']:+d} "
                      f"src={v['sampleSrc']} adsr={v['adsr1']:02X}/{v['adsr2']:02X}")

    print("\n=== audio_analyze.py on the WAV ===")
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "audio_analyze.py"), str(out)],
        capture_output=True, text=True
    )
    print(res.stdout)
    if res.returncode != 0:
        print("STDERR:", res.stderr)


if __name__ == "__main__":
    main()
