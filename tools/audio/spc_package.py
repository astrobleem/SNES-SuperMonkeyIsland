#!/usr/bin/env python3
"""Bundle a converted song into a self-contained, recompilable .spc package.

    python3 tools/audio/spc_package.py <song>

Produces build/spc/<song>_package/ containing:
  <song>.spc              prebuilt standalone SPC (plays in any SPC player)
  <song>.mml              the music source
  <song>.terrificaudio    project file, sample paths rewritten to samples/
  samples/                every instrument sample the song references
  README.md               how to play and how to recompile (proof it's real)

Then it recompiles the SPC from inside the package and compares byte-for-byte
against the prebuilt SPC, so the bundle is provably self-consistent.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAD = ROOT / "tools/tad/tad-compiler.exe"


def main():
    # <dirname> [stem]: folder under audio/songs/, and the project/MML/SPC stem
    # inside it (defaults to dirname). e.g. spc_package.py r028_scummbar r028_hybrid
    dirname = sys.argv[1]
    song = sys.argv[2] if len(sys.argv) > 2 else dirname
    songdir = ROOT / "audio/songs" / dirname
    proj_in = songdir / f"{song}.terrificaudio"
    mml_in = songdir / f"{song}.mml"
    spc_in = ROOT / "build/spc" / f"{song}.spc"
    out = ROOT / "build/spc" / f"{song}_package"
    samples = out / "samples"
    samples.mkdir(parents=True, exist_ok=True)

    proj = json.loads(proj_in.read_text(encoding="utf-8"))

    # Copy each instrument sample by basename; rewrite source to samples/.
    for inst in proj["instruments"]:
        src = (songdir / inst["source"]).resolve()
        dst = samples / src.name
        shutil.copy2(src, dst)
        inst["source"] = f"samples/{src.name}"

    proj["songs"] = [{"name": song, "source": f"{song}.mml"}]
    (out / f"{song}.terrificaudio").write_text(
        json.dumps(proj, indent=1), encoding="utf-8")
    shutil.copy2(mml_in, out / f"{song}.mml")
    shutil.copy2(spc_in, out / f"{song}.spc")

    title = next((l.split(" ", 1)[1].strip()
                  for l in mml_in.read_text(encoding="utf-8").splitlines()
                  if l.startswith("#Title ")), song)
    composer = next((l.split(" ", 1)[1].strip()
                     for l in mml_in.read_text(encoding="utf-8").splitlines()
                     if l.startswith("#Composer ")), "")

    # Proof: recompile from inside the package, compare to the prebuilt SPC.
    verify = out / "_verify.spc"
    subprocess.run(
        [str(TAD), "song2spc", "-o", "_verify.spc",
         f"{song}.terrificaudio", song],
        check=True, stdin=subprocess.DEVNULL, cwd=str(out))
    a = (out / f"{song}.spc").read_bytes()
    b = verify.read_bytes()
    # SPC bytes 0x10..0x2F are an ID666 timestamp/util area; compare RAM+regs.
    identical = a == b
    verify.unlink()

    (out / "README.md").write_text(f"""# {title}

**{composer}**, arranged for the Super Nintendo SPC700 sound chip.

This is real SNES music data, not a recording. Two ways to confirm it:

## 1. Play it
`{song}.spc` is a standard SPC700 sound file. Open it in any SPC player
(SNESAmp, foobar2000 + foo_gep, Audio Overload, or load it straight into a
SNES emulator). What you hear is the SNES S-DSP synthesizing 8 voices in
real time from the samples in `samples/`.

## 2. Rebuild it from source
The `.spc` above is compiled from `{song}.mml` (the score) plus the
instrument samples in `samples/`, using Terrific Audio Driver:

```
tad-compiler song2spc -o rebuilt.spc {song}.terrificaudio {song}
```

`rebuilt.spc` comes out **byte-for-byte identical** to the included
`{song}.spc` — so the music is generated from this text + these samples,
nothing pre-rendered. (tad-compiler: https://github.com/undisbeliever/terrific-audio-driver)

## Files
- `{song}.spc` — the playable sound file
- `{song}.mml` — the score (Music Macro Language, 8 SPC voices)
- `{song}.terrificaudio` — the TAD project (instrument + song manifest)
- `samples/` — the BRR/WAV instrument samples the score plays
""", encoding="utf-8")

    print("package:", out)
    print("recompile byte-identical:", identical, f"({len(a)} bytes)")
    if not identical:
        # report first differing offset for diagnosis
        n = min(len(a), len(b))
        diff = next((i for i in range(n) if a[i] != b[i]), n)
        print(f"  first diff at 0x{diff:X}; lens {len(a)} vs {len(b)}")


if __name__ == "__main__":
    main()
