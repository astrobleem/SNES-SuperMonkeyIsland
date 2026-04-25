# Agent guide for SNES-SuperMonkeyIsland

Project-specific quickstart for AI coding agents (and humans pretending to be
one). For codebase conventions and build commands, read `CLAUDE.md` first.
This file documents the **Mesen-MCP harness** — the highest-leverage tool in
the project, and the one most agents reach for wrong on first try.

## TL;DR

The project ships a patched Mesen2 emulator (`mesen/Mesen.exe`) with a
built-in MCP server. The fork's source + protocol docs live in a separate
repo (path varies by checkout; conventionally cloned as a sibling
directory). All you typically need is `tools/mcp_client.py` and
`mesen/Mesen.exe` from this repo. From Python:

```python
import sys; sys.path.insert(0, 'tools')
from mcp_client import McpSession

with McpSession(rom='distribution/SuperMonkeyIsland.sfc') as m:
    m.run_frames(500)
    state = m.get_state()         # frame counter, paused state
    m.pause()                      # always before multi-call inspection
    print(m.read_memory('snesMemory', 0x7EF967, 1).hex())  # currentRoom
```

Full tool surface (30 tools): `read_memory`, `write_memory`,
`add_exec_hook` / `add_read_hook` / `add_write_hook` / `add_frame_hook`,
`take_screenshot`, `crop_screenshot`, `record_audio`, `get_audio_state`,
`save_state_slot` / `load_state_slot`, `lookup_symbol`, `disassemble`,
`run_until`, `read_dma_state`, `get_ppu_state`, `reset_emulator` …
The Mesen-MCP fork's `AGENTS.md` is the canonical protocol doc; if you
have its source checked out as a sibling directory, look there. Otherwise
read it on GitHub (see `mesen/README.txt` for the repo URL pinned to
this build).

## When to reach for what

| Situation | First tool to try |
|---|---|
| "Boot to a known room, screenshot it" | `tools/room_test.py 33 -x 346 -y 133` (existing harness) |
| "Inspect SCUMM state mid-flow" | McpSession + `pause` + `read_memory` at SCUMM symbols |
| "Why does this differ between two boot paths?" | `tools/room33_real_diff.py` style state-diff (CGRAM, OAM, VRAM, DMA) |
| "When does this function execute?" | `add_exec_hook` on the lookup_symbol address |
| "Is the song right?" | `record_audio` then `tools/audio_analyze.py /path/to.wav --ref reference.wav` |
| "Drive the natural intro flow" | `set_input(BTN_START, 40)` in a loop with `set_input(0, 760)` between bursts |

## Project SCUMM constants worth knowing

```
SCUMM.currentRoom        $7EF967  byte
SCUMM.newRoom            $7EF969  byte
SCUMM.cutsceneNest       $7EF965  byte
SCUMM.actors.1           $7E890C  16-byte struct: {room, costume, x, y, facing, elev, moving, vis, ...}
GLOBAL.room.cameraX      $7EFA1F  word
GLOBAL.room.hasBg2Mask   $7EFA94  byte
xScrollBG1               $7EFD54  word
yScrollBG1               $7EFD56  word
MainScreen               $7EFD47  byte (mirrors $212C TMAIN)
```

Addresses shift on every rebuild. Use `lookup_symbol(SYM_FILE, '^name$')` for
anything not in this list. Never hard-code addresses you got from a prior
session.

## The five-minute checklist before doing real work

1. **Always pause before a multi-call inspection.** Two `read_memory` calls
   50ms apart see different states because the emulator runs at max speed
   between them. Either `m.pause()` first, or wrap reads in a single
   `with-pause` block.

2. **`run_frames(N)` is wall-clock approximate.** At MaximumSpeed flag,
   the emulator runs many more than N frames in N×16ms. If you need an
   exact frame count, use `m.get_state()['frameCount']` for ground truth
   or compose with `run_until(maxFrames=N)`.

3. **Drain stderr.** Mesen emits hundreds of `[CPU] Uninitialized memory
   read: ...` warnings during the first ~12k frames. If you spawn Mesen
   with `subprocess.PIPE` for stderr and don't drain it, the pipe fills,
   blocks Mesen's logger, and deadlocks `initialize`. The provided
   `McpSession` does this for you; copy that pattern if you roll your
   own.

4. **Filter hot hooks.** `add_exec_hook(0xC40026)` on `_scummvm.fetchLoop`
   fires hundreds of times per frame. Use `match_value` + `match_value_mask`
   for value filtering, or `add_frame_hook(every_n=N)` for periodic
   pulses, instead of trying to drain a flood of notifications.

5. **A silent WAV is a real observation, not a tool failure.**
   Cross-check with `get_audio_state()` — `keyOn != 0` or any voice with
   `envelope > 0` means actual playback. If both are zero, the song
   isn't playing yet (game-state issue, not MCP).

## Existing diagnostics that already use this

| Script | What it does |
|---|---|
| `tools/room_test.py` | Parametric room loader: pokes SCUMM newRoom, settles, screenshot. Original Lua-based, still useful. |
| `tools/room33_natural_flow.py` | Drives the real intro path with `set_input` + START spam. Reproduces the original room-33 corruption bug. |
| `tools/room33_real_diff.py` | Captures CGRAM, OAM, BG1 tilemap+tiles, BG2, scroll WRAM into both clean and corrupt paths and diffs. |
| `tools/room33_ppu_diff.py` | Compares full PPU state (layer enables, scroll, window mask). |
| `tools/room33_dma_regs.py` | Decodes all 8 DMA channels' control/source/target. |
| `tools/room33_bg2_diff.py` | Targeted WRAM dump of BG2-related state. |
| `tools/audio_analyze.py` | Offline FFT on a captured WAV; per-band dB delta vs reference. |
| `tools/mcp_smoke{,2,...,7}.py` | Smoke tests; good copy-paste templates for new MCP-driven tooling. |

## Adding a new MCP tool

If you need something the existing 30 tools don't cover, the diff is small.
Inside the Mesen-MCP fork checkout (path varies — adjust to wherever you
cloned it):

1. `UI/Utilities/Mcp/McpTools.cs` — add a `Descriptions` entry (with input
   schema) and a handler method.
2. Same directory, `McpServer.cs` `BuildToolTable()` — map the tool name to
   the handler.
3. `dotnet build UI/UI.csproj -c Release` — usually that's it.
4. Copy the fresh `Mesen.dll` from the build output into this project's
   `mesen/` directory. Kill any running Mesen first or the copy fails
   with "Device or resource busy".

C++ side (`Core/Mcp/`, `InteropDLL/DebugApiWrapper.cpp`) only needs touching
for new emulator-thread hot paths or new C-ABI exports. Most extensions are
pure C# additions.

## Tracked deferrals

Things the harness deliberately doesn't do yet, with TODO tasks:

- **Movie record/play** for replayable boot paths instead of START-spam
  every test (#23). Would cut smoke-test runtime from ~30s to ~3s.
- **Frame-exact stepping** (greenzone-style) instead of wall-clock
  `run_frames` (#24).
- **Pansy metadata import** alongside WLA-DX `.sym` (#25).

If your task hits one of these, surface it; don't paper over it with a
brittle workaround.

## Pointers

In this project:

- `tools/mcp_client.py` — Python client; the typed wrapper most code in
  this project uses.
- `tools/mcp_smoke{,2,...,7}.py` — runnable copy-paste templates.
- `tools/audio_analyze.py` — offline FFT analyzer for captured WAVs.
- `mesen/Mesen.exe` — the patched emulator binary.

In the Mesen-MCP fork (separate repo, clone path varies):

- `AGENTS.md` — full protocol details + workflow patterns.
- `UI/Utilities/Mcp/McpTools.cs` — every tool's argument parsing + impl.
  Read this when in doubt about argument shape.
- `Core/Mcp/McpHookManager.{h,cpp}` — hot-path event manager.
