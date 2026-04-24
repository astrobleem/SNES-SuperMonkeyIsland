"""Smoke-test the in-Mesen MCP server over TCP (loopback).

Launches `Mesen.exe --mcp <ROM>`, waits for it to bind the port, connects
as a JSON-RPC client, sends initialize / tools/list / tools/call, prints
each response. Uses --mcp-port=N if an explicit port is passed.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MESEN = ROOT / "mesen" / "Mesen.exe"
ROM = ROOT / "distribution" / "SuperMonkeyIsland.sfc"
DIST = ROOT / "distribution"


class McpClient:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""
        self._next_id = 1

    def send(self, method: str, params: dict | None = None) -> dict:
        msg_id = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        print(f">>> {line.decode().strip()}")
        self._sock.sendall(line)
        return self.recv()

    def recv(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise EOFError("server closed connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        resp = json.loads(line.decode("utf-8"))
        print(f"<<< {json.dumps(resp)}")
        return resp


def wait_for_port(host: str, port: int, timeout: float) -> socket.socket:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            return socket.create_connection((host, port), timeout=1.0)
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise TimeoutError(f"could not connect to {host}:{port} within {timeout}s: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7333)
    ap.add_argument("--no-spawn", action="store_true",
                    help="skip launching Mesen; connect to an already-running instance")
    args = ap.parse_args()

    if not MESEN.exists():
        sys.exit(f"Mesen.exe not found: {MESEN}")
    if not ROM.exists():
        sys.exit(f"ROM not found: {ROM}")

    proc = None
    if not args.no_spawn:
        cmd = [str(MESEN), "--mcp", f"--mcp-port={args.port}", str(ROM)]
        print(f"launching: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=str(DIST),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Give Mesen a moment to boot + bind the port.
        time.sleep(2.0)

    try:
        sock = wait_for_port("127.0.0.1", args.port, timeout=15.0)
        print("connected")
        client = McpClient(sock)

        client.send("initialize", {})
        client.send("tools/list")
        client.send("tools/call", {"name": "ping", "arguments": {"hello": "world"}})
        client.send("tools/call", {"name": "get_state", "arguments": {}})
        client.send("tools/call", {
            "name": "read_memory",
            "arguments": {
                "memoryType": "snesMemory",
                "address": 0x7EF967,  # SCUMM.currentRoom
                "length": 4,
            },
        })
        client.send("shutdown", {})

        sock.close()
        return 0
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr = proc.stderr.read() if proc.stderr else ""
            if stderr:
                print(f"--- stderr ---\n{stderr}")


if __name__ == "__main__":
    sys.exit(main())
