"""Phase 3 MCP smoke test: event streaming via add_exec_hook.

Registers a hook on a frequently-hit PC, advances emulation, and reads
the notifications/mesen/hookFired messages the server pushes back.
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


class McpClient:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""
        self._id = 0
        self._notifications: list[dict] = []

    def _read_one(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise EOFError
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def call(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return the matching response, stashing any
        notifications that arrive in between."""
        self._id += 1
        msg_id = self._id
        m = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            m["params"] = params
        self._sock.sendall((json.dumps(m, separators=(",", ":")) + "\n").encode())
        while True:
            r = self._read_one()
            if "id" in r and r["id"] == msg_id:
                return r
            if "method" in r:
                self._notifications.append(r)
                continue

    def tool(self, name: str, args: dict | None = None):
        r = self.call("tools/call", {"name": name, "arguments": args or {}})
        c = r["result"].get("content", [])
        return json.loads(c[0]["text"]) if c else None

    def drain_notifications(self, timeout: float = 0.1) -> list[dict]:
        """Read any pending notifications without blocking long."""
        deadline = time.time() + timeout
        self._sock.settimeout(0.05)
        try:
            while time.time() < deadline:
                try:
                    r = self._read_one()
                except (socket.timeout, TimeoutError):
                    break
                if "method" in r:
                    self._notifications.append(r)
        finally:
            self._sock.settimeout(30)
        # Return the full accumulated list.
        got = self._notifications[:]
        self._notifications.clear()
        return got


def wait_for(host, port, timeout=15) -> socket.socket:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.settimeout(30)
            return s
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(port)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7380)
    args = ap.parse_args()

    cmd = [str(MESEN), "--mcp", f"--mcp-port={args.port}", str(ROM)]
    print("launching:", " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=str(ROOT / "distribution"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    time.sleep(2)
    try:
        sock = wait_for("127.0.0.1", args.port)
        c = McpClient(sock)

        c.call("initialize", {})

        print("\n--- add_exec_hook ---")
        # _play (main SCUMM loop) runs often. Hook it to get events.
        # If _play isn't at a fixed address across rebuilds, use any
        # frequently-hit PC; 0xC08000 is usually in CPU vector code.
        # _scummvm.fetchLoop at $C40026 runs every opcode dispatch — high
        # volume but scoped to 1 PC. Tight but guaranteed to hit.
        info = c.tool("add_exec_hook", {"address": 0xC40026, "endAddress": 0xC40026})
        print(f"  hook: {info}")
        handle = info["handle"]

        print("\n--- list_hooks ---")
        print(f"  {c.tool('list_hooks')}")

        print("\n--- hook_diag before ---")
        print(f"  {c.tool('hook_diag')}")

        print("\n--- run_frames(10), then drain notifications ---")
        c.tool("run_frames", {"count": 10})

        print("\n--- hook_diag after ---")
        print(f"  {c.tool('hook_diag')}")

        notifs = c.drain_notifications(0.3)
        print(f"  received {len(notifs)} notifications")
        if notifs:
            for n in notifs[:3]:
                print(f"    sample: {n}")
            addrs = sorted(set(n["params"]["address"] for n in notifs))
            print(f"  unique addresses: {addrs[:8]}{'...' if len(addrs) > 8 else ''}")

        print("\n--- remove_hook ---")
        print(f"  {c.tool('remove_hook', {'handle': handle})}")
        print(f"  list after: {c.tool('list_hooks')}")

        c.call("shutdown", {})
        sock.close()
        return 0
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read() if proc.stderr else ""
        if err:
            print("\n--- stderr ---\n", err)


if __name__ == "__main__":
    sys.exit(main())
