"""Quick audition helper for a per-song bundle.

Given a slug (e.g. `r010_lucasarts`), runs `tad-compiler song2spc` against
`audio/songs/<slug>/<slug>.terrificaudio` + `<slug>.mml` and drops an .spc
next to the MML. Play with any SPC player (SNESAmp, SPC-player, Mesen's SPC
file-open, VLC with libspc, etc.).

Usage:
    python3 tools/audition_song.py r010_lucasarts
    python3 tools/audition_song.py r010_lucasarts --open     # launches the .spc
    python3 tools/audition_song.py --list                    # shows available slugs

Exits 0 on success. Prints the .spc path on stdout so shell one-liners can
pipe it (e.g. `vlc "$(python tools/audition_song.py r010 | tail -1)"`).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SONGS_DIR = ROOT / "audio" / "songs"

# Windows binary; the underlying tools/ folder is the SMI convention.
DEFAULT_TAD = ROOT / "tools" / "tad" / "tad-compiler.exe"


def list_bundles() -> list[str]:
    """Return sorted slugs that have a `.terrificaudio` + `.mml` pair."""
    out = []
    if not SONGS_DIR.is_dir():
        return out
    for child in sorted(SONGS_DIR.iterdir()):
        if not child.is_dir():
            continue
        slug = child.name
        tad = child / f"{slug}.terrificaudio"
        mml = child / f"{slug}.mml"
        if tad.is_file() and mml.is_file():
            out.append(slug)
    return out


def find_tad_compiler(override: str | None) -> Path | None:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env = os.environ.get("TAD_COMPILER")
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_TAD)
    for c in candidates:
        if c.is_file():
            return c
    return None


def audition(slug: str, tad: Path, open_it: bool) -> int:
    bundle = SONGS_DIR / slug
    tad_proj = bundle / f"{slug}.terrificaudio"
    mml = bundle / f"{slug}.mml"
    spc = bundle / f"{slug}.spc"

    if not tad_proj.is_file():
        print(f"ERROR: no project at {tad_proj}", file=sys.stderr)
        return 2
    if not mml.is_file():
        print(f"ERROR: no MML at {mml}", file=sys.stderr)
        return 2

    cmd = [str(tad), "song2spc", str(tad_proj), str(mml), "-o", str(spc)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        print(f"ERROR: tad-compiler timed out", file=sys.stderr)
        return 2

    if result.returncode != 0:
        err = (result.stderr or result.stdout or b"").decode("utf-8", "replace")
        print(f"tad-compiler song2spc failed ({result.returncode}):", file=sys.stderr)
        print(err, file=sys.stderr)
        return 1

    if not spc.is_file():
        print(f"ERROR: tad-compiler exited 0 but did not produce {spc}",
              file=sys.stderr)
        return 2

    size = spc.stat().st_size
    print(f"OK: {spc}", file=sys.stderr)
    print(f"    {size} bytes", file=sys.stderr)

    if open_it:
        # Windows: `start ""` opens with the default .spc handler.
        # If no handler is registered, the user sees a file-association prompt.
        if sys.platform.startswith("win") or os.name == "nt":
            os.startfile(str(spc))  # type: ignore[attr-defined]
        else:
            # Fallback: xdg-open / open (macOS)
            opener = shutil.which("xdg-open") or shutil.which("open")
            if opener:
                subprocess.Popen([opener, str(spc)])
            else:
                print("No opener found; play manually.", file=sys.stderr)

    print(spc)  # stdout: the path, for scripting
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug", nargs="?", help="Song slug (directory under audio/songs/)")
    ap.add_argument("--list", action="store_true",
                    help="List available bundles and exit")
    ap.add_argument("--open", dest="open_it", action="store_true",
                    help="After compile, launch the .spc with the system default player")
    ap.add_argument("--tad-compiler", dest="tad_compiler",
                    help="Path to tad-compiler.exe (auto-detected if omitted)")
    args = ap.parse_args()

    if args.list:
        bundles = list_bundles()
        if not bundles:
            print(f"No bundles under {SONGS_DIR}", file=sys.stderr)
            return 1
        for slug in bundles:
            print(slug)
        return 0

    if not args.slug:
        ap.error("slug is required (or use --list)")

    tad = find_tad_compiler(args.tad_compiler)
    if tad is None:
        print("ERROR: tad-compiler.exe not found. Set $TAD_COMPILER or pass "
              "--tad-compiler.", file=sys.stderr)
        return 2

    return audition(args.slug, tad, args.open_it)


if __name__ == "__main__":
    sys.exit(main())
