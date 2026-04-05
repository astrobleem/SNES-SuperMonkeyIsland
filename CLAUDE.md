# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SNES Super Monkey Island — a native SCUMM v5 interpreter for The Secret of Monkey Island on the Super Nintendo, targeting real NTSC SNES hardware with MSU-1 on SD2SNES/FXPAK Pro. Written in 65816 assembly with a custom OOP framework. Engine forked from SuperDragonsLairArcade.

## Assembly

Write 65816 assembly directly. The `snes-65816-dev` and `scumm-reference` agents no longer exist.

## Agent

**Aramis** (`.claude/agents/mesen-debugger.md`) — Mesen 2 debugging: Lua tests, WRAM inspection, frame analysis. Use the Agent tool with `subagent_type: "mesen-debugger"` for complex runtime diagnostics. You can write simple Lua snippets directly via `run_lua_snippet`, but delegate thorough debugging sessions to Aramis.

## Build Commands

Build runs under WSL. The project uses WLA-DX v9.3 assembler (v9.4+ breaks the build).

```bash
# Standard build (clean + build)
wsl -e bash -lc "cd /mnt/e/gh/SNES-SuperMonkeyIsland && make clean && make"

# Fast rebuild (skip clean if only .65816/.script files changed)
wsl -e bash -lc "cd /mnt/e/gh/SNES-SuperMonkeyIsland && make"

# Build output: build/SuperMonkeyIsland.sfc (also copied to distribution/SuperMonkeyIsland.sfc)
```

**Build warnings that are normal:**
- `DIRECTIVE_ERROR` about redefined `__init`/`__play`/`__kill` — from CLASS macro in event files
- `DISCARD` messages — unused event sections stripped by `-d` linker flag

**TAD audio**: `tools/tad/tad-compiler.exe` compiles `audio/smi.terrificaudio` → `build/audio/tad-audio-data.bin`. Triggered automatically by make.

## Testing & Validation

The Mesen 2 MCP server (`.mcp.json` → `tools/mesen_mcp_server.py`) provides automated testing:

- **`build_rom`** — Incremental build (~1s). Uses `stdin=subprocess.DEVNULL` to avoid hanging.
- **`validate_rom`** — Bank 0 usage check + BRK scan + 500-frame boot test with crash detection. Run after every build.
- **`take_screenshot`** — Renders N frames, captures screenshot. Auto-detects magenta error screen → `[CRASH DETECTED]`.
- **`run_test`** — Run a Lua test script in Mesen testrunner mode.
- **`run_with_input`** — Input injection tests. Takes `input_schedule` (list of `{"frames": [start, end], "buttons": "right+a"}`). Auto-resolves controller hook from sym file.
- **`lookup_symbol`/`lookup_symbols`** — Look up addresses from the sym file.
- **`crop_screenshot`** — Takes screenshot then crops to a region with optional zoom. For targeted UI checks.
- **`visual_regression_check`** — Compares current screenshot against a reference image.

**Emulator testing (manual):**
```bat
:: ROM MUST load from distribution/ where .msu/.pcm files live
cmd.exe /c "cd /d E:\gh\SNES-SuperMonkeyIsland\distribution && E:\gh\SNES-SuperMonkeyIsland\mesen\Mesen.exe --testrunner SuperMonkeyIsland.sfc script.lua > out.txt 2>&1"
```

## Bank 0 Management

**Check BEFORE and AFTER every code change** via `validate_rom` MCP tool or:

```bash
wsl -e bash -lc "cd /mnt/e/gh/SNES-SuperMonkeyIsland && python3 tools/rom_usage.py build/SuperMonkeyIsland.sym build/SuperMonkeyIsland.sfc"
```

Bank 0 overflow is silent — WLA-DX reshuffles sections without error.

## Architecture (High-Level)

- **HiROM+FastROM**, 16 banks x 64KB = 1MB ROM, PBR=$C0 at runtime
- **OOP System**: 48 concurrent object slots, init/play/kill methods, direct page allocation
- **SCUMM v5 Interpreter**: 105 dispatch entries, 25 script slots, 800 global vars, 2048 bit vars, 44KB script cache in bank $7F
- **Actor System**: 16B struct x 256, 19 opcodes, walking animation, walkbox pathfinding
- **MSU-1**: Room/script data streaming from .msu files in `distribution/`
- **Audio**: TAD v0.2.0 (SPC700), pinned to bank 2

### Game Flow
```
boot.65816 → main.script → msu1 splash → losers/credits → title_screen → level1 stub
```

## Key Files

| File | Purpose |
|------|---------|
| `src/object/scummvm/scummvm.65816` | SCUMM v5 interpreter: scheduler, opcodes, script cache, room transitions |
| `src/object/scummvm/scummvm.h` | SCUMM constants, slot struct, WRAM layout, cache config |
| `src/object/room/room.65816` | Room loader: MSU-1 seek, index lookup, tileset/tilemap/palette DMA |
| `src/object/actor/actor.65816` | Actor rendering, costumes, walking, multi-actor OAM |
| `src/object/audio/tad_interface.65816` | Terrific Audio Driver — SPC700 init, transfer, per-frame processing |
| `src/config/macros.inc` | All macros: CLASS, METHOD, NEW, CALL, SCRIPT, EVENT, etc. |
| `src/config/globals.inc` | Object properties, flags, global enums |
| `src/core/oop.65816` | Object creation, singleton handling, method dispatch |
| `src/core/boot.65816` | Entry point, main loop, interrupt vectors |
| `tools/rom_usage.py` | Bank 0 usage verification — run after every build |
| `tools/mesen_mcp_server.py` | MCP server: build, validate, screenshot, test, symbol lookup |
| `build/SuperMonkeyIsland.sym` | Symbol table — addresses shift every rebuild |
