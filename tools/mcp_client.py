"""Lightweight MCP client for the in-Mesen server.

Usage:
    with McpSession(port=7350, rom=ROM) as m:
        m.pause()
        m.run_frames(500)
        room = m.read_u8(0x7EF967)
        print(f'currentRoom={room}')
        m.write_hex(0x7EF969, '21')
        m.run_frames(60)
        shot = m.take_screenshot()
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_MESEN = _ROOT / "mesen" / "Mesen.exe"
_ROM_DEFAULT = _ROOT / "distribution" / "SuperMonkeyIsland.sfc"


class McpError(RuntimeError):
    pass


class McpSession:
    """Spawn Mesen --mcp, connect, expose a typed tool API."""

    def __init__(
        self,
        port: int = 7350,
        rom: Path | str = _ROM_DEFAULT,
        mesen: Path | str = _MESEN,
        boot_wait: float = 2.0,
        socket_timeout: float = 30.0,
        stderr_log: Path | str | None = None,
    ) -> None:
        self._port = port
        self._rom = str(rom)
        self._mesen = str(mesen)
        self._boot_wait = boot_wait
        self._socket_timeout = socket_timeout
        self._stderr_log = stderr_log
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._buf = b""
        self._next_id = 1
        self._notifications: list[dict] = []

    def __enter__(self) -> "McpSession":
        self._proc = subprocess.Popen(
            [self._mesen, "--mcp", f"--mcp-port={self._port}", self._rom],
            cwd=str(_ROOT / "distribution"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(self._boot_wait)
        self._sock = self._connect()
        self.call("initialize", {})
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._sock is not None:
                try:
                    self.call("shutdown", {})
                except Exception:
                    pass
                self._sock.close()
        finally:
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                if self._stderr_log and self._proc.stderr:
                    Path(self._stderr_log).write_text(self._proc.stderr.read() or "")

    # --- raw JSON-RPC ----------------------------------------------------

    def _connect(self) -> socket.socket:
        deadline = time.time() + 15
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                s = socket.create_connection(("127.0.0.1", self._port), timeout=1)
                s.settimeout(self._socket_timeout)
                return s
            except OSError as e:
                last_err = e
                time.sleep(0.2)
        raise McpError(f"connect to 127.0.0.1:{self._port} timed out: {last_err}")

    def call(self, method: str, params: dict | None = None) -> dict:
        """Send a request, stash any notifications that arrive while we
        wait for the matching response, return the response."""
        if self._sock is None:
            raise McpError("session not open")
        msg_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._sock.sendall((json.dumps(msg, separators=(",", ":")) + "\n").encode())
        while True:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise McpError("server closed connection mid-call")
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            resp = json.loads(line)
            if "id" in resp and resp["id"] == msg_id:
                if "error" in resp:
                    raise McpError(f"{method}: {resp['error']}")
                return resp
            if "method" in resp:
                self._notifications.append(resp)
                continue
            # Out-of-order response ID — drop it and keep waiting.

    def tool(self, name: str, args: dict | None = None) -> Any:
        resp = self.call("tools/call", {"name": name, "arguments": args or {}})
        content = resp["result"].get("content", [])
        if not content:
            return None
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # --- typed tool wrappers --------------------------------------------

    def ping(self, **kwargs) -> dict:
        return self.tool("ping", kwargs or {})

    def get_state(self) -> dict:
        return self.tool("get_state")

    def pause(self) -> dict:
        return self.tool("pause")

    def resume(self) -> dict:
        return self.tool("resume")

    def run_frames(self, count: int) -> dict:
        return self.tool("run_frames", {"count": count})

    def read_memory(self, mem_type: str, address: int, length: int) -> bytes:
        r = self.tool("read_memory", {
            "memoryType": mem_type,
            "address": address,
            "length": length,
        })
        return bytes.fromhex(r["hex"])

    def read_u8(self, address: int, mem_type: str = "snesMemory") -> int:
        return self.read_memory(mem_type, address, 1)[0]

    def read_u16(self, address: int, mem_type: str = "snesMemory") -> int:
        b = self.read_memory(mem_type, address, 2)
        return b[0] | (b[1] << 8)

    def write_hex(self, address: int, hex_bytes: str, mem_type: str = "snesMemory") -> dict:
        return self.tool("write_memory", {
            "memoryType": mem_type,
            "address": address,
            "hex": hex_bytes,
        })

    def write_u8(self, address: int, value: int, mem_type: str = "snesMemory") -> dict:
        return self.write_hex(address, f"{value & 0xFF:02x}", mem_type)

    def write_u16(self, address: int, value: int, mem_type: str = "snesMemory") -> dict:
        return self.write_hex(address, f"{value & 0xFF:02x}{(value >> 8) & 0xFF:02x}", mem_type)

    def take_screenshot(self, format: str = "path") -> dict:
        return self.tool("take_screenshot", {"format": format})

    def save_state(self, path: str | Path) -> dict:
        return self.tool("save_state", {"path": str(path)})

    def load_state(self, path: str | Path) -> dict:
        return self.tool("load_state", {"path": str(path)})

    # Button bitmask constants for set_input.
    BTN_A, BTN_B = 0x001, 0x002
    BTN_SELECT, BTN_START = 0x004, 0x008
    BTN_UP, BTN_DOWN, BTN_LEFT, BTN_RIGHT = 0x010, 0x020, 0x040, 0x080
    BTN_X, BTN_L, BTN_R, BTN_Y = 0x100, 0x200, 0x400, 0x800

    def set_input(self, buttons: int, frames: int, port: int = 0) -> dict:
        return self.tool("set_input", {
            "port": port,
            "buttons": buttons,
            "frames": frames,
        })

    def get_ppu_state(self) -> dict:
        return self.tool("get_ppu_state")

    # --- hooks -----------------------------------------------------------

    def add_exec_hook(self, address: int, end_address: int | None = None,
                      cpu_type: str = "Snes") -> int:
        """Register an exec hook; returns the handle. Every time the CPU
        executes an instruction whose PC is in [address, end_address],
        the server pushes a notifications/mesen/hookFired message on the
        socket. Call drain_notifications() to collect them."""
        args: dict = {"address": address, "cpuType": cpu_type}
        if end_address is not None:
            args["endAddress"] = end_address
        return self.tool("add_exec_hook", args)["handle"]

    def remove_hook(self, handle: int) -> bool:
        return bool(self.tool("remove_hook", {"handle": handle})["removed"])

    def list_hooks(self) -> list[dict]:
        return self.tool("list_hooks")["hooks"]

    def hook_diag(self) -> dict:
        return self.tool("hook_diag")

    def drain_notifications(self, timeout: float = 0.1) -> list[dict]:
        """Read any pending notifications without blocking long. MCP
        notifications have no `id`, so call() stashes them in
        self._notifications; this empties that queue plus any in the
        socket buffer."""
        got = list(self._notifications)
        self._notifications.clear()
        if self._sock is None:
            return got
        deadline = time.time() + timeout
        self._sock.settimeout(0.05)
        try:
            while time.time() < deadline:
                try:
                    while b"\n" not in self._buf:
                        chunk = self._sock.recv(65536)
                        if not chunk:
                            return got
                        self._buf += chunk
                except (socket.timeout, TimeoutError):
                    break
                line, self._buf = self._buf.split(b"\n", 1)
                r = json.loads(line)
                if "method" in r:
                    got.append(r)
        finally:
            self._sock.settimeout(self._socket_timeout)
        return got
