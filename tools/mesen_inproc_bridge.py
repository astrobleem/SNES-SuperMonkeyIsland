#!/usr/bin/env python3
"""Compat shim — delegates to mesen_mcp.bridge with this project's defaults.

The real bridge logic moved to tools/mesen_mcp/bridge.py. This shim:
  - Adds tools/ to sys.path so `import mesen_mcp` works when running
    from the project root.
  - Sets MESEN_EXE / MESEN_ROM / MESEN_CWD env-var defaults pointing at
    this project's mesen/ and distribution/ directories, but only if
    the caller hasn't already set them.

Other projects can either:
  1. Wire up tools/mesen_mcp/bridge.py directly via `mesen-mcp-bridge`
     (the console_script entry point), with MESEN_EXE/MESEN_ROM set in
     the .mcp.json env block, OR
  2. Write their own thin shim modeled on this one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# Project defaults — only fill in if the caller didn't already set the
# env var. That way other projects setting these vars take precedence.
os.environ.setdefault("MESEN_EXE", str(_ROOT / "mesen" / "Mesen.exe"))
os.environ.setdefault("MESEN_ROM", str(_ROOT / "distribution" / "SuperMonkeyIsland.sfc"))
os.environ.setdefault("MESEN_CWD", str(_ROOT / "distribution"))

# Make `import mesen_mcp` resolve to the in-tree package without
# requiring a pip install.
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mesen_mcp.bridge import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
