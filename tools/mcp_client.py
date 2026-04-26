"""Compat shim — McpSession + SMI defaults on top of the mesen_mcp package.

The real implementation moved to tools/mesen_mcp/. This module:
  - Re-exports McpSession / McpError so existing
    `from mcp_client import McpSession` callers keep working.
  - Subclasses McpSession to fill in this project's default ROM and
    Mesen path so callers can still write `with McpSession() as m:`.

For any other project, use the package directly:

    from mesen_mcp import McpSession
    with McpSession.from_env() as m: ...

or with explicit paths:

    with McpSession(rom='game.sfc', mesen='/path/to/Mesen.exe') as m: ...
"""
from __future__ import annotations

from pathlib import Path

from mesen_mcp import McpError, McpSession as _McpSession

__all__ = ["McpSession", "McpError"]

_ROOT = Path(__file__).resolve().parent.parent
_MESEN_DEFAULT = _ROOT / "mesen" / "Mesen.exe"
_ROM_DEFAULT = _ROOT / "distribution" / "SuperMonkeyIsland.sfc"


class McpSession(_McpSession):
    """Project-default-aware McpSession.

    All keyword arguments from the base class are still accepted; rom
    and mesen become optional and default to this project's paths.
    """

    def __init__(
        self,
        port: int = 7350,
        rom: Path | str = _ROM_DEFAULT,
        mesen: Path | str = _MESEN_DEFAULT,
        cwd: Path | str | None = None,
        boot_wait: float = 2.0,
        socket_timeout: float = 30.0,
        stderr_log: Path | str | None = None,
    ) -> None:
        # Default cwd to distribution/ to match historical behavior.
        if cwd is None:
            cwd = _ROOT / "distribution"
        super().__init__(
            rom=rom, mesen=mesen, cwd=cwd, port=port,
            boot_wait=boot_wait, socket_timeout=socket_timeout,
            stderr_log=stderr_log,
        )
