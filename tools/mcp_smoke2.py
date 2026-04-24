"""Phase 2 MCP smoke test.

Exercises the full tool set: pause/resume/run_frames/read_memory/
write_memory/take_screenshot/save_state/load_state. Builds on
mcp_smoke.py which only covered the initial 3 tools.
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
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        print(f">>> {line.decode().strip()}")
        self._sock.sendall(line)
        while b"\n" not in self._buf:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise EOFError
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        r = json.loads(line)
        print(f"<<< {json.dumps(r)[:180]}")
        return r

    def tool(self, name: str, args: dict | None = None) -> dict:
        r = self.call("tools/call", {"name": name, "arguments": args or {}})
        content = r.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return r


def wait_for(host: str, port: int, timeout: float = 15) -> socket.socket:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.create_connection((host, port), timeout=1)
            # Individual tool calls can take a few seconds (run_frames sleeps,
            # take_screenshot waits for file). Give them headroom.
            sock.settimeout(30)
            return sock
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(port)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7340)
    args = ap.parse_args()

    cmd = [str(MESEN), "--mcp", f"--mcp-port={args.port}", str(ROM)]
    print("launching:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(DIST),
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    time.sleep(2)
    try:
        sock = wait_for("127.0.0.1", args.port)
        c = McpClient(sock)

        c.call("initialize", {})
        tools = c.call("tools/list")["result"]["tools"]
        print(f"registered tools: {[t['name'] for t in tools]}")
        print(f"total: {len(tools)}")

        print("\n--- pause/resume/run_frames ---")
        c.tool("pause")
        state0 = c.tool("get_state")
        assert state0["isPaused"], state0
        r = c.tool("run_frames", {"count": 120})
        print(f"  run_frames result: {r}")
        state1 = c.tool("get_state")
        print(f"  after run_frames: {state1}")

        print("\n--- read / write memory ---")
        # SCUMM.newRoom at $7EF969. Write 33, read back.
        c.tool("write_memory", {
            "memoryType": "snesMemory",
            "address": 0x7EF969,
            "hex": "21",  # 33
        })
        rb = c.tool("read_memory", {
            "memoryType": "snesMemory",
            "address": 0x7EF967,
            "length": 4,
        })
        print(f"  read after write: {rb}")

        print("\n--- screenshot ---")
        sh = c.tool("take_screenshot")
        print(f"  screenshot: {sh.get('path')}")

        print("\n--- save / load state ---")
        state_path = str(DIST / "_mcp_smoke.mss")
        c.tool("save_state", {"path": state_path})
        c.tool("load_state", {"path": state_path})
        print(f"  state path: {state_path}")

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
            print("\n--- stderr ---")
            print(err)


if __name__ == "__main__":
    sys.exit(main())
