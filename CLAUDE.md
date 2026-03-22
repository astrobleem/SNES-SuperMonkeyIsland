# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SNES Super Monkey Island — a native SCUMM v5 interpreter for The Secret of Monkey Island on the Super Nintendo, targeting real NTSC SNES hardware with MSU-1 on SD2SNES/FXPAK Pro. Written in 65816 assembly with a custom OOP framework. Engine forked from SuperDragonsLairArcade.

## Assembly Delegation

**Never write 65816 assembly directly — always delegate to the snes-65816-dev agent.** Use the Agent tool with the `.claude/agents/snes-65816-dev.md` agent for all 65816 assembly work including writing, reviewing, debugging, and optimizing assembly code.

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

**Emulator testing (manual):**
```bat
:: ROM MUST load from distribution/ where .msu/.pcm files live
cmd.exe /c "cd /d E:\gh\SNES-SuperMonkeyIsland\distribution && E:\gh\SNES-SuperMonkeyIsland\mesen\Mesen.exe --testrunner SuperMonkeyIsland.sfc script.lua > out.txt 2>&1"
```

## Bank 0 Management

Bank 0 is stabilized at ~82-88% (~27-28KB/32KB). **Check BEFORE and AFTER every code change:**

```bash
wsl -e bash -lc "cd /mnt/e/gh/SNES-SuperMonkeyIsland && python3 tools/rom_usage.py build/SuperMonkeyIsland.sym build/SuperMonkeyIsland.sfc"
```

- Bank 0 overflow is **silent and catastrophic** — WLA-DX reshuffles superfree sections without error, breaking section co-location (TAD, local labels) → mysterious crashes
- OOP methods must stay in bank 0 but keep them thin — move heavy logic to `superfree` sections, call via `jsl`/`rtl`
- ~30 superfree sections are pinned to banks 2-5 (see `bank_stabilization.md` in memory)

## Architecture

### Memory Map
- HiROM+FastROM, 16 banks x 64KB = 1MB ROM
- Slot 0: $0000-$FFFF (ROM), Slot 1: $7E2000 (Work RAM), Slot 2: zero page
- PBR=$C0 at runtime (bank $C0 mirrors $00 with full $0000-$FFFF ROM access)
- Checksum values hardcoded in header — "Invalid Checksum" in emulators is expected

### OOP System (`src/core/oop.65816`)
Custom object system with 48 concurrent object slots. Each object has init/play/kill methods, a direct page (ZP) allocation, and properties bitmask.

**Key macros** (defined in `src/config/macros.inc`):
- `CLASS name method1 method2...` — defines a class with method table
- `METHOD name` — defines an instance method
- `NEW class.CLS.PTR hashPtr args...` — creates object instance, stores hash pointer
- `CALL class.method.MTD hashPtr args...` — dispatches method call via hash pointer
- `TRIGGER_ERROR E_code` — expands to `pea E_code; jsr core.error.trigger` (fatal, calls stp)

**Object properties** (`src/config/globals.inc`):
- `isScript=$0001`, `isChapter=$0002`, `isEvent=$0004`, `isHdma=$0008`, `isSerializable=$1000`
- `killOthers` uses bitmask AND matching — ALL requested bits must be present

**Singleton objects**: Brightness, ScummVM have `OBJECT.FLAGS.Singleton`. Creating a singleton that already exists returns the existing instance WITHOUT calling init again.

### Script System (`src/object/script/`)
Scripts are 65816 code that runs synchronously during init (via `bra _play`) until the first `jsr SavePC`, then resumes one iteration per frame. Key macros: `SCRIPT`, `DIE`, `SavePC`, `WAIT`.

**Script ZP layout** (96 bytes total):
- iteratorStruct (28 bytes, offset 0) — self, properties, target, index, count, sort fields
- scriptStruct (4 bytes, offset 28) — timestamp, initAddress
- vars (28 bytes, offset 32) — _tmp[16], currPC, buffFlags, buffBank, buffA/X/Y, buffStack
- hashPtr (36 bytes, offset 60) — 9 hash pointers x 4 bytes each (id, count, pntr)

**Hash pointer access**: `hashPtr.N` is 1-indexed. `hashPtr.1` = offset 60, `hashPtr.N` = offset 60 + (N-1)*4.

### SCUMM v5 Interpreter (`src/object/scummvm/`)
- 105 dispatch entries, 25 concurrent script slots, 800 global vars, 2048 bit vars
- 44KB script cache in bank $7F ($7F:5000-$7F:FFFF) with MSU-1 on-demand loading
- Room script loading: ENCD/EXCD/LSCR on room change, global script reload on cache flush
- Actor system: 16B struct x 256, 19 opcodes, walking animation, walkbox pathfinding

**SCUMM v5 parameter encoding** (critical for opcode implementation):
- `getVarOrDirectByte(mask)`: flag SET in opcode → getVar (2-byte var ref), flag CLEAR → fetchByte (1-byte literal)
- `getVarOrDirectWord(mask)`: flag SET → getVar (2 bytes), flag CLEAR → fetchWord (2 bytes)
- Our 65816 uses `beq` (branch when zero = flag CLEAR → literal path). **`beq` is CORRECT.**
- `ifClassOfIs`/`setClass`: `{aux_byte, word_value}* + $FF` terminator
- Vararg ops (print sub-ops, verbOps): byte+word pairs terminated by $FF byte

### Game Flow
```
boot.65816 → main.script → msu1 splash → losers/credits → title_screen → level1 stub
```

### MSU-1 Video/Audio
The MSU-1 streaming engine from SuperDragonsLairArcade is preserved. Chapter/event system for video playback is intact. MSU-1 data files (.msu, .pcm) live in `distribution/` and are generated by offline pipeline tools (`tools/msu1_pack_rooms.py`, `tools/msu1_pack_scripts.py`).

### Audio Engine — TAD v0.2.0
Terrific Audio Driver for SPC700. Source: `src/object/audio/tad_interface.{h,65816}`, project: `audio/smi.terrificaudio`. SCUMM sound opcodes wired to TAD (startSound, startMusic, stopMusic, stopSound). TAD code + audio data pinned to bank 2.

## Critical Pitfalls

### WLA-DX Assembler
- **`.def` cannot redefine** — `.def X Y` then `.def X Z` → second SILENTLY IGNORED. Use `.redefine`.
- **`_` prefix = local labels** — invisible across compilation units (.o files) AND across `.section` boundaries within the same file.
- **`.ACCU`/`.INDEX` at branch targets** — WLA-DX tracks M/X flags linearly, NOT by control flow. Every branch-target label in mixed-width code MUST have `.ACCU N`/`.INDEX N` directives. Missing → phantom `$00` (BRK) bytes from wrong-width immediates. Use `validate_rom` MCP tool after each build.
- **`.base BSL` required** for HiROM superfree sections (without it, addresses < $8000 read WRAM).
- **Anonymous labels** — `+`, `++`, `+++` are DISTINCT tiers; must be at column 0.
- **Parentheses** = indirect addressing, not grouping. `sta.b (EXPR & $ff)` → STA indirect.
- **`^`** = bank byte, NOT XOR. Use `~` for XOR.
- **Macro calls** need leading whitespace (column 0 → treated as label).
- **Section limit** — max ~512 sections per compilation unit.
- **WRAM address arithmetic** — `SCUMM.foo + 2` evaluates to 24-bit ($7Exxxx+2), out of 16-bit range. Use separate labels.
- **`.bank` bleed-through from headers** — if a `.h` file ends with `.bank N` (N!=0), it bleeds into the including file. Add explicit `.bank 0 slot 0 / .base BSL` before class sections.

### 65816 CPU
- **PHA/PLA width must match processor mode** — `pha` pushes 2 bytes when M=0, 1 byte when M=1. Mismatched `pla` → 1-byte stack misalignment → corrupted return address.
- **16-bit `lda` on `db` fields** — reads 2 bytes. Mask with `and #$00FF` or use `sep #$20`.
- **No `long,Y`** — only X can index long addresses.
- **Stack-relative in subroutines** — reading `OBJECT.CALL.ARG.N,s` from `jsr`-called subroutine: add +2 for extra return address.
- **`oopCreateNoPtr` = $FFFF** — null pointer for hash system. Never use hash pntr=0 (matches OopStack slot 0).

### SNES Hardware
- **Event kill methods** delegating to `Event.template.kill` MUST use `jmp`, not `jsr`.
- **`core.nmi.stop` zeros ScreenBrightness** — save/restore around force-blank DMA.
- **VRAM DMA safety** — disable BOTH NMI (`core.nmi.stop`) AND IRQ (`sei`).
- **HDMA runs during forced blanking** — only `HDMAEN` ($420C) = 0 stops it. NMI re-enables HDMA every VBlank.
- **NMI/IRQ PBR guards must use `bcs`**, not `beq` — interrupts can fire in any ROM bank ($C0-$C5).
- **`_scummvm.readVariable` clobbers `SCUMM.scratch2`** — push operands to stack before calling.

## Key Files

| File | Purpose |
|------|---------|
| `src/config/macros.inc` | All macros: CLASS, METHOD, NEW, CALL, SCRIPT, EVENT, etc. |
| `src/config/globals.inc` | Object properties, flags, global enums |
| `src/config/structs.inc` | Data structures: iteratorStruct, animationStruct, eventStruct |
| `src/core/oop.65816` | Object creation, singleton handling, method dispatch |
| `src/core/oop.h` | OBJID enum, OopClassLut (class registration) |
| `src/core/error.h` | Error code enum |
| `src/core/boot.65816` | Entry point, main loop, interrupt vectors |
| `src/object/script/script.h` | Script class definition, hash pointer defaults |
| `src/object/scummvm/scummvm.65816` | SCUMM v5 interpreter: scheduler, opcodes, script cache, room transitions |
| `src/object/scummvm/scummvm.h` | SCUMM constants, slot struct, WRAM layout, cache config |
| `src/object/room/room.65816` | Room loader: MSU-1 seek, index lookup, tileset/tilemap/palette DMA |
| `src/object/actor/actor.65816` | Actor rendering, costumes, walking, multi-actor OAM |
| `src/object/audio/tad_interface.65816` | Terrific Audio Driver — SPC700 init, transfer, per-frame processing |
| `tools/rom_usage.py` | Bank 0 usage verification — run after every build |
| `tools/brk_scanner.py` | Post-build BRK opcode scanner (baseline ~19-46, varies with packing) |
| `tools/mesen_mcp_server.py` | MCP server: build, validate, screenshot, test, symbol lookup |
| `tools/fxpak_push.py` | Push ROM to FXPAK Pro via QUsb2Snes |
| `tools/fxpak_debug.py` | Live WRAM inspector for FXPAK Pro debugging |
| `tools/fxpak_crash_dump.py` | Post-crash memory dump from FXPAK Pro |
| `build/SuperMonkeyIsland.sym` | Symbol table — addresses shift every rebuild |
