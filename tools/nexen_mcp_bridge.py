#!/usr/bin/env python3
"""Stdio bridge for the Nexen TCP MCP runner.

The MCP-capable Nexen fork exposes newline-delimited JSON-RPC on a localhost
TCP port. Codex-style MCP clients expect stdio, so this script launches Nexen
headlessly and proxies bytes between stdio and the TCP socket.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NEXEN = Path(r"E:\gh\Mesen2-mcp-server\bin\win-x64\Release\Nexen.exe")
DEFAULT_NEXEN_DLL = DEFAULT_NEXEN.with_suffix(".dll")
PUBLISH_NEXEN = Path(r"E:\gh\Mesen2-mcp-server\build\publish-mcp\Nexen.exe")
FALLBACK_NEXEN = Path(r"E:\gh\Mesen2\publish_out\Mesen.exe")
CAPTURE_DIR = PROJECT_ROOT / "build" / "nexen-captures"
LOCAL_DOTNET = PROJECT_ROOT / "tools" / "_dotnet" / "dotnet.exe"


def _eprint(message: str) -> None:
    print(f"nexen-mcp-bridge: {message}", file=sys.stderr, flush=True)


def _path_from_env(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).expanduser()


def _resolve_nexen() -> tuple[Path, list[str], Path]:
    env_nexen = _path_from_env("NEXEN_EXE")
    if env_nexen and env_nexen.exists():
        return env_nexen, [str(env_nexen)], env_nexen

    if LOCAL_DOTNET.exists() and DEFAULT_NEXEN_DLL.exists():
        return DEFAULT_NEXEN_DLL, [str(LOCAL_DOTNET), str(DEFAULT_NEXEN_DLL)], DEFAULT_NEXEN_DLL

    candidates = [PUBLISH_NEXEN, DEFAULT_NEXEN, FALLBACK_NEXEN]
    for candidate in candidates:
        if candidate.exists():
            return candidate, [str(candidate)], candidate
    raise SystemExit(
        "NEXEN_EXE is not set and no local Nexen.exe was found. "
        f"Tried {DEFAULT_NEXEN}, {PUBLISH_NEXEN}, and {FALLBACK_NEXEN}."
    )


def _resolve_rom() -> Path:
    env_rom = _path_from_env("NEXEN_ROM")
    candidates = [
        env_rom,
        PROJECT_ROOT / "distribution" / "SuperMonkeyIsland.sfc",
        PROJECT_ROOT / "build" / "SuperMonkeyIsland.sfc",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise SystemExit(
        "NEXEN_ROM is not set and no SuperMonkeyIsland.sfc was found under the "
        "Game Garden build, distribution, or legacy build paths."
    )


def _choose_port() -> int:
    env_port = os.environ.get("NEXEN_MCP_PORT")
    if env_port:
        return int(env_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _native_search_dirs(nexen: Path) -> list[Path]:
    dirs = [nexen.parent, nexen.parent / "Dependencies"]
    for parent in nexen.parents:
        if parent.name.lower() in {"mesen2-mcp-server", "mesen2"}:
            dirs.extend(
                [
                    parent / "bin" / "win-x64" / "Release",
                    parent / "bin" / "win-x64" / "Release" / "Dependencies",
                    parent / "build" / "publish-mcp",
                ]
            )
            break

    seen: set[str] = set()
    existing: list[Path] = []
    for path in dirs:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            existing.append(path)
    return existing


def _connect(port: int, process: subprocess.Popen[bytes]) -> socket.socket:
    timeout = float(os.environ.get("NEXEN_MCP_START_TIMEOUT", "30"))
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"Nexen exited before MCP was reachable: {process.returncode}")
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.25)
            sock.settimeout(None)
            return sock
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise SystemExit(f"Timed out connecting to Nexen MCP on 127.0.0.1:{port}: {last_error}")


def _pipe_stdin_to_socket(
    sock: socket.socket,
    stop: threading.Event,
    stdin_closed: threading.Event,
) -> None:
    try:
        while not stop.is_set():
            chunk = sys.stdin.buffer.readline()
            if not chunk:
                break
            sock.sendall(chunk)
    finally:
        stdin_closed.set()
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _pipe_socket_to_stdout(sock: socket.socket, stop: threading.Event) -> None:
    try:
        with sock.makefile("rb") as reader:
            while not stop.is_set():
                chunk = reader.readline()
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
    finally:
        stop.set()


def main() -> int:
    nexen, nexen_cmd, native_root = _resolve_nexen()
    rom = _resolve_rom()
    port = _choose_port()

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        *nexen_cmd,
        "--mcp",
        f"--mcp-port={port}",
        "--Preferences.OverrideScreenshotFolder=true",
        f"--Preferences.ScreenshotFolder={CAPTURE_DIR}",
        str(rom),
    ]
    _eprint(f"launching {' '.join(cmd)}")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    env = os.environ.copy()
    env["NEXEN_MCP_CAPTURE_DIR"] = str(CAPTURE_DIR)
    env["PATH"] = (
        os.pathsep.join(str(path) for path in _native_search_dirs(native_root))
        + os.pathsep
        + env.get("PATH", "")
    )

    process = subprocess.Popen(
        cmd,
        cwd=str(rom.parent),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
        creationflags=creationflags,
    )

    sock: socket.socket | None = None
    terminated_nexen = False
    try:
        sock = _connect(port, process)
        stop = threading.Event()
        stdin_closed = threading.Event()
        stdin_thread = threading.Thread(
            target=_pipe_stdin_to_socket,
            args=(sock, stop, stdin_closed),
            name="stdin-to-nexen",
            daemon=True,
        )
        stdout_thread = threading.Thread(
            target=_pipe_socket_to_stdout,
            args=(sock, stop),
            name="nexen-to-stdout",
            daemon=True,
        )
        stdin_thread.start()
        stdout_thread.start()
        while not stop.is_set():
            if process.poll() is not None:
                stop.set()
                break
            if stdin_closed.is_set():
                time.sleep(float(os.environ.get("NEXEN_MCP_EOF_DRAIN_SECONDS", "1.0")))
                stop.set()
                break
            time.sleep(0.05)
        stdin_thread.join(timeout=1.0)
        stdout_thread.join(timeout=1.0)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if process.poll() is None:
            terminated_nexen = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    if terminated_nexen:
        return 0
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
