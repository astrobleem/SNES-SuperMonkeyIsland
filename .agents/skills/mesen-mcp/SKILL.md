---
name: mesen-mcp
description: Drive the Mesen-MCP emulator (Mesen2 fork in E:/gh/Mesen2 with a built-in MCP server) over a TCP socket to play, debug, and analyze SNES/NES/GB/etc. ROMs programmatically. Use whenever the task involves running this project's ROM, inspecting emulator state (CPU/PPU/SPC/DMA), recording audio or video, setting watchpoints (exec/read/write/frame hooks), capturing screenshots, or comparing two reproduction paths byte-for-byte.
---

# Mesen-MCP skill

The project has a fork of Mesen2 (at `E:/gh/Mesen2`, branch `hirom-gsu-support`) that exposes the emulator as an MCP-style JSON-RPC server. The deployed binaries live at `E:/gh/SNES-SuperMonkeyIsland/mesen/`. A Python client wrapper is at `tools/mcp_client.py` and an offline FFT analyzer for captured WAVs is at `tools/audio_analyze.py`.

Before reading further, look at `E:/gh/Mesen2/AGENTS.md` — it's the single most useful 5-minute read. Everything below is the project-flavored quickstart.

## When to reach for this

| Task | Tool |
|---|---|
| "Boot the ROM and screenshot at frame N" | `room_test.py` (legacy) or McpSession.run_frames + take_screenshot |
| "What's currentRoom right now?" | `read_memory` at SCUMM.currentRoom address |
| "Hook _rvbc and watch cutsceneNest" | `add_exec_hook` on lookup_symbol result |
| "Why does this render fail?" | dump CGRAM / OAM / VRAM / DMA into both states and diff |
| "Is the song actually playing?" | `record_audio` + `audio_analyze.py --ref reference.wav` |

## Canonical session

```python
from pathlib import Path
import sys
sys.path.insert(0, 'E:/gh/SNES-SuperMonkeyIsland/tools')
from mcp_client import McpSession

with McpSession(port=7400, boot_wait=3.0, socket_timeout=120) as m:
    # advance past splash
    m.run_frames(500)

    # find a SCUMM symbol from the build's sym file
    sym = m.lookup_symbol(
        str(Path('E:/gh/SNES-SuperMonkeyIsland/build/SuperMonkeyIsland.sym')),
        '^_scummvm\\.fetchLoop$',
    )
    addr = sym['matches'][0]['romCpuAddr']

    # watch its execution
    h = m.add_exec_hook(addr)
    m.run_frames(60)
    print(f"fetchLoop hit {len(m.drain_notifications())} times")
    m.remove_hook(h)

    # snapshot rendering state
    m.pause()
    print(m.get_ppu_state()['mainScreenLayers'])
    print(m.read_memory('snesCgRam', 0, 32).hex())
```

## SCUMM constants worth memorizing

```
SCUMM.currentRoom        $7EF967  (1 byte)
SCUMM.newRoom            $7EF969  (1 byte)
SCUMM.cutsceneNest       $7EF965  (1 byte)
SCUMM.actors.1           $7E890C  (16-byte struct: room, costume, x, y, ...)
GLOBAL.room.cameraX      $7EFA1F  (2 bytes)
GLOBAL.room.hasBg2Mask   $7EFA94  (1 byte)
xScrollBG1               $7EFD54  (2 bytes)
yScrollBG1               $7EFD56  (2 bytes)
MainScreen               $7EFD47  (1 byte; mirrors $212C TMAIN)
```

Use `lookup_symbol(SYM_FILE, '^name$')` for any others — addresses shift on every rebuild, so don't cache them in code.

## Mode selection

`McpSession` defaults to spawning a new Mesen instance. If you want to attach to one that's already running (faster iteration during a session), `socket.create_connection` directly.

## Hard-won pitfalls

1. **Always pause before a multi-call inspection.** Otherwise emulation advances between your reads and you compare two unrelated frames.

2. **Drain stderr in a thread.** `subprocess.PIPE` on stderr without a drainer fills up after ~12k uninit-read warnings, blocks the server's logger, and deadlocks initialize. `McpSession` handles this; if you roll your own client, mimic the pattern.

3. **`run_frames` is approximate.** At MaximumSpeed flag the emulator races through more frames than the requested 60Hz wall-clock duration. Use `m.get_state()['frameCount']` for ground truth.

4. **Hooks on hot addresses flood the socket.** `add_exec_hook(0xC40026)` on `_scummvm.fetchLoop` fires hundreds of times per frame. Filter with `match_value` + `match_value_mask`, or scope to a tight address range.

5. **Audio is only captured if the song is actually playing.** A silent WAV is a real observation, not a bug. Cross-check with `get_audio_state()` — `keyOn != 0` or any voice with `envelope > 0` means actual playback.

## Where the tools live

- Server: `E:/gh/Mesen2/UI/Utilities/Mcp/McpTools.cs` (every tool's args + impl)
- Hot path: `E:/gh/Mesen2/Core/Mcp/McpHookManager.cpp` (hook event emit)
- Client: `E:/gh/SNES-SuperMonkeyIsland/tools/mcp_client.py` (typed wrapper)
- Audio analyzer: `E:/gh/SNES-SuperMonkeyIsland/tools/audio_analyze.py`
- Smoke tests (good copy-paste examples): `tools/mcp_smoke{,2,3,4,5,6,7}.py`
- Existing diagnostic suite: `tools/room33_*.py` (state-diff between two boot paths)

## Adding a new tool

If a workflow you need isn't in the 30 existing tools:

1. Add an entry in `Descriptions` list at top of `McpTools.cs` (with input schema)
2. Add the tool name → handler mapping in `McpServer.BuildToolTable()`
3. Implement the handler method on `McpTools` (most are 5–30 lines, see existing as template)
4. Rebuild: MSBuild for `Core,InteropDLL` (only if you touched C++), then `dotnet build UI/UI.csproj -c Release`
5. Deploy: `cp E:/bin/win-x64/Release/Mesen.dll E:/gh/SNES-SuperMonkeyIsland/mesen/Mesen.dll`

Most extensions need only step 1, 2, 3, and the dotnet build. Touching C++ is rare.

## Build / deploy reminders

```bash
# Mesen.exe is WinExe — kill any running instance before redeploying the DLL
powershell.exe -c "Get-Process Mesen -ErrorAction SilentlyContinue | Stop-Process -Force"

# C++ side
"/c/Program Files/Microsoft Visual Studio/18/Community/MSBuild/Current/Bin/MSBuild.exe" \
  E:/gh/Mesen2/Mesen.sln -p:Configuration=Release -p:Platform=x64 -t:Core,InteropDLL -m

# C# side
cd E:/gh/Mesen2 && dotnet build UI/UI.csproj -c Release

# Deploy
cp E:/bin/win-x64/Release/Mesen.dll        E:/gh/SNES-SuperMonkeyIsland/mesen/
cp E:/gh/Mesen2/bin/win-x64/Release/MesenCore.dll E:/gh/SNES-SuperMonkeyIsland/mesen/
```

The two output paths are deliberate — `dotnet build` writes UI into `E:/bin`, MSBuild writes Core/InteropDLL into `E:/gh/Mesen2/bin`.
