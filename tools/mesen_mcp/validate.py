"""Validation helpers for locating a usable Mesen MCP build."""
from __future__ import annotations

from pathlib import Path


class MesenBuildError(RuntimeError):
    """Raised when MESEN_EXE points at a build that cannot serve MCP."""


def _contains_marker(path: Path, marker: bytes) -> bool:
    try:
        return marker in path.read_bytes()
    except OSError as exc:
        raise MesenBuildError(f"could not read {path}: {exc}") from exc


def validate_mesen_build(mesen: Path | str) -> None:
    """Fail fast when MESEN_EXE points at an incomplete/stale build.

    Windows source builds produce a small apphost `Mesen.exe` beside
    `Mesen.dll` and `MesenCore.dll`. If either DLL is stale or missing,
    Mesen can exit before opening the MCP socket and the caller only sees
    a generic connection timeout.
    """
    exe = Path(mesen)
    if not exe.exists():
        raise MesenBuildError(f"Mesen.exe not found at {exe}")

    build_dir = exe.parent
    managed = build_dir / "Mesen.dll"
    native = build_dir / "MesenCore.dll"
    missing = [p.name for p in (managed, native) if not p.exists()]
    if missing:
        raise MesenBuildError(
            f"MESEN_EXE points at {exe}, but sibling file(s) are missing: "
            f"{', '.join(missing)}. Point MESEN_EXE at the build directory "
            "containing a matched Mesen.exe, Mesen.dll, and MesenCore.dll."
        )

    if not _contains_marker(managed, b"McpRunner"):
        raise MesenBuildError(
            f"{managed} does not appear to include the MCP runner. Rebuild "
            "the managed UI from the astrobleem/Mesen2 fork."
        )

    if not _contains_marker(native, b"McpDrainEvents"):
        raise MesenBuildError(
            f"{native} does not export the MCP hook bridge. Build the native "
            "solution as Release|x64, then rebuild the managed UI."
        )
