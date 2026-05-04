# SNES Super Monkey Island

A native SCUMM v5 interpreter for *The Secret of Monkey Island* on the Super Nintendo, using MSU-1 for asset streaming.

| | |
|:---:|:---:|
| ![Beach Objects](screenshots/room01_objects.png) | ![SCUMM Bar](screenshots/room28_scumm_bar.png) |
| Beach with OCHR object rendering (rocks, shoreline) | SCUMM Bar background |
| ![Verb Bar](screenshots/room1_verb_bar.png) | ![Guybrush Scaled](screenshots/guybrush_scaled.png) |
| Full verb bar with HDMA palette split | Guybrush scaled down near rocks via SA-1 CC Type 2 |
| ![Melee Town](screenshots/room35_town.png) | ![Moonlit Dock](screenshots/room33_dock.png) |
| ![Governor's Mansion](screenshots/room53_mansion.png) | ![Monkey Head](screenshots/room69_monkey_head.png) |
| ![Monkey Island](screenshots/room12_monkey_island.png) | ![LeChuck's Lair](screenshots/room65_hell.png) |

## Architecture

- **Language**: 65816 assembly with a custom OOP framework
- **Platform**: SNES + MSU-1 (SD2SNES / FXPAK Pro), SA-1 co-processor
- **Target**: MI1 VGA CD Talkie (`monkey.000` / `monkey.001`)
- **Input**: SNES Mouse (primary), joypad with virtual cursor (fallback)
- **Audio**: SPC700 native chip music + SFX via [Terrific Audio Driver](https://github.com/undisbeliever/terrific-audio-driver), MSU-1 reserved for voice acting
- **Assembler**: WLA-DX v9.3 (v9.4+ breaks the build)
- **ROM**: 4MB HiROM (SA-1 directly addressable)
- **Engine base**: Forked from Super Dragon's Lair Arcade (SNES MSU-1)

## Approach

Following the GBAGI model (Brian Provinciano's native AGI interpreter for GBA): a purpose-built, hardware-native interpreter that reads original game data files. Not a ScummVM port.

The SNES ROM is just the engine. All game assets live in an MSU-1 data pack generated offline from the user's own MI1 data files (`monkey.000` / `monkey.001`).

MSU-1 provides unlimited storage (4GB addressable) with on-demand streaming. VRAM and WRAM act as live caches backed by MSU-1, the same proven architecture used by the Super Dragon's Lair SNES port for continuous FMV playback.

## Build

Build runs under WSL with WLA-DX v9.3:

```bash
# Standard build (clean + build)
wsl -e bash -c "cd /mnt/e/gh/SNES-SuperMonkeyIsland && make clean && make"

# Output: build/SuperMonkeyIsland.sfc (also copied to distribution/)
```

`distribution/SuperMonkeyIsland.msu` must remain in place — the MSU-1 boot handshake checks for it on real hardware and emulators even though room/script data has been moved into ROM.

## Testing

Two harnesses cover different layers:

| Harness | Purpose |
|---------|---------|
| `tests/run_vm_tests.py` | **178 unit tests** — inject synthetic SCUMM bytecode into the script cache, assert WRAM. Catches opcode-semantic regressions. |
| `tests/integration/run_integration_tests.py` | Gameplay-grade — boots the ROM, lets the intro run, drives state pokes / clicks, asserts visible result. Catches scenarios unit tests miss. |

Both run inside Mesen 2's `--testrunner` mode against the current build.

## Offline Pipeline Tools

The `tools/` directory contains Python tools that convert MI1 data into SNES-native format and drive emulator-based debugging:

### Asset pipeline
| Tool | Purpose |
|------|---------|
| `scumm_extract.py` | Extract all MI1 resources (rooms, scripts, costumes, sounds, charsets) |
| `scumm_costume_decoder.py` | Decode SCUMM v5 costume RLE data into indexed pixel arrays |
| `snes_costume_converter.py` | Convert decoded costumes to SNES 4bpp sprite tiles + OAM layout |
| `convert_all_costumes.py` | Batch-convert all 123 MI1 costumes (119 with valid frames → ROM) |
| `costume_transparency_editor.py` | Hue-aware sprite background stripping + manual export/import |
| `snes_room_converter.py` | Convert room backgrounds to SNES 4bpp tilesets + tilemaps with z-plane BG2 mask generation |
| `msu1_pack_rooms.py` / `msu1_pack_scripts.py` | Pack rooms + script bytecode into MSU-1 data file |
| `rom_pack_data.py` | Append rooms + scripts into the linked ROM (current shipping path) |
| `scumm_opcode_audit.py` | Walk all 748 script files, decode bytecode, report opcode coverage |
| `gen_dispatch_table.py` | Generate 256-entry 65816 opcode dispatch table from Python opcode map |
| `gen_costume_rom.py` | Emit `costume_data.inc` (CHR + OAM + DCOS lookup tables) for assembly |

### Audio pipeline
| Tool | Purpose |
|------|---------|
| `tad/tad-compiler.exe` | Terrific Audio Driver compiler — MML + WAV → SPC700 binary blob |
| `audio/extract_sf2_samples.py` | SoundFont sample extraction for instrument banks |
| `audio/gen_instrument_samples.py` | AdLib FM → SNES BRR sample generation |
| `audio/register_sfx.py` | Register WAV-based SFX in the TAD project |

### Hardware / debugging
| Tool | Purpose |
|------|---------|
| `fxpak_push.py` | Push ROM to FXPAK Pro via QUsb2Snes |
| `fxpak_debug.py` | Live WRAM inspector for FXPAK Pro debugging |
| `fxpak_crash_dump.py` | Post-crash memory dump from FXPAK Pro |
| `smi_workflow_server.py` | MCP server (`smi-workflow`) — build, validate, run_test, run_with_input, screenshot, sym lookup, step_until_pc |
| `mesen_inproc_bridge.py` | MCP server (`mesen-inproc`) — long-lived Mesen 2 debugger: state, hooks, render, audio (46 tools) |
| `rom_usage.py` | Bank 0 occupancy report (run after every change — bank-0 overflow is silent) |

## Reusable Modules

The `tools/scumm/` package contains reusable SCUMM v5 modules:

| Module | Purpose |
|--------|---------|
| `opcodes_v5.py` | Complete 256-entry opcode table with variable-length parameter decoders |
| `chunks.py` / `index.py` / `resource.py` | LECF chunk-tree parsing, index file, resource dispatch |
| `costume.py` | SCUMM v5 costume container parsing |
| `room_gfx.py` / `object_gfx.py` / `zplane.py` | Room background, OBIM/OCHR, and z-plane bitmap decoding |
| `palette.py` / `cycle.py` | VGA palette extraction, color-cycle metadata |
| `smap.py` | Strip-image (SMAP) decoding for room backgrounds |
| `charset.py` / `manifest.py` / `metadata.py` | Charset decode, asset manifest, room/object metadata |

## Legal Model

Engine distributed separately from game data (like GBAGI). Users supply their own copy of Monkey Island.

## Status

**Phases 0–2 complete, Phase 3 in progress.** SCUMM v5 interpreter + actor system + scaling + verb/dialog/walkbox systems are running. The boot chain reaches LucasArts logo → credits → title card → opening cutscene → controllable gameplay on the lookout. ScummVM-parity port of the walking pump (multi-leg `walkActor`, `buildWalkPath` with BOXM fizzle, `Camera::moveCamera` dead zone) has landed and is regression-tested.

### Rendering Pipeline
- All 86 MI1 rooms extracted, converted, and shipped (in-ROM via `rom_pack_data.py`; MSU-1 boot handshake retained)
- 896-slot VRAM tile cache with random-access streaming + background column refresh on scroll reversal
- Smooth horizontal scrolling, **NMI tile transfer via DMA channel 7** (20+ tiles per VBlank)
- **OCHR object rendering** — `setState` triggers tile-overlay apply/remove with instant forced-blank redraw, deferred brightness restore until ENCD + OCHR patches are applied
- **BG2 z-plane pixel-level masking** — runtime BG2 tilemap + tile-base HDMA switching so actors clip behind pillars / foreground geometry per pixel, not per tile boundary

### Actor System + SA-1 Hardware Scaling
The actor scaling system went through a notable evolution. Early prototypes explored a SuperFX chip approach for real-time sprite scaling, but the SuperFX's limited throughput couldn't handle multi-actor scenes at 60fps. The solution: **SA-1 co-processor with Character Conversion Type 2** — a hardware-assisted bitmap-to-tile converter that the SA-1 provides but almost no commercial game ever used.

The pipeline: body + head costume tiles are composited into a BW-RAM pixel buffer, nearest-neighbor scaled to the target size, then CC Type 2 converts the scaled bitmap back to SNES 4bpp tile format in SA-1 I-RAM. The SNES CPU DMAs the result to VRAM. Non-blocking: the SA-1 runs the scaler asynchronously while the SNES CPU continues game logic. Results are cached per-actor until the animation frame or scale factor changes.

### Costumes
- All **123 MI1 costumes** converted (119 with valid frames, 4 empty → fallback)
- 1.7 MB total costume data in ROM superfree sections (1.5 MB CHR + 208 KB OAM)
- ScummVM-parity chore engine (`scummvm_chore.65816`) drives all per-costume animation tables — legacy hardcoded walk/head cycle tables removed

### Interpreter
- All 105 SCUMM v5 base opcodes implemented (103 used by MI1)
- 25 concurrent script slots, 44 KB bytecode cache in bank `$7F`
- Cutscene system: `beginOverride`/`endOverride` + `freezeScripts` + `cursorCommand` (sub-ops 1–14)
- Camera: `actorFollowCamera`, `panCameraTo`, `setCameraAt`, ScummVM-strip dead zone in `moveCamera`
- Walking: ScummVM-port multi-leg pathfinding (`walkData` WRAM struct, per-leg dispatch on waypoint arrival, BOXM-route fizzle)
- Copy-protection short-circuited at the script level (no hand-rolled patch)
- Verb table driven by MI1 script (`verbOps`), not hardcoded defaults

### Additional Systems
- **Verb bar** — 10 MI1 verbs on BG2 with HDMA palette split, yellow highlight on hover
- **Dialog** — BG3 text overlay, per-actor talk colors, auto-timed display, sentence line on BG2 verb row
- **Walkbox pathfinding** — full SCUMM v5 walkbox + BOXM matrix; ScummVM `Actor::startWalkActor` semantics
- **Object interaction** — `findObject` AABB with `kObjectClassUntouchable` skip, `doSentence`/`startObject` execution via OBCD VERB pipeline
- **Audio** — Terrific Audio Driver v0.2.0 on SPC700; AdLib FM → SNES BRR sample pipeline; first real songs: LucasArts logo (`r010_lucasarts`), SCUMM Bar theme (`soun_scummbar`), SUPERGUYBRUSH theme
- **BW-RAM infrastructure** — save header + first-boot init, CGRAM shadow, `darkenPalette`, `setPalColor`
- **SA-1 co-processor** — CC Type 2 sprite scaling, BW-RAM composite buffer, non-blocking pipeline

### Test Coverage
- **178 unit tests** in `tests/run_vm_tests.py` covering opcode semantics (signed comparisons, expression evaluator, actor getters, walk pump, etc.)
- Gameplay-grade integration runner in `tests/integration/run_integration_tests.py` covering bugs that slipped through unit tests (invisible spawn, moonwalking, can't-enter SCUMM bar, old-man-on-campfire)
- 11 ScummVM-spec divergences caught and fixed against the new harness; tracked in `docs/v5_behavior_matrix.md`

### Open Frontier
- **Dialog choice selection** — choices render but no d-pad+A cursor-highlight-and-select. Blocks pirate conversations.
- **Title-screen mountain cloud flicker** — partially mitigated; not fully resolved.
- **Save/load serialization** — BW-RAM map + boot init done; serializing VM state into a slot is next.
- **`palManipulate`** — gradual palette transitions (sunsets, lighting fades).
- **Room-by-room gameplay verification** — 86 rooms, plus puzzle logic (insult sword fighting, Herman Toothrot, Governor's mansion).
- **MSU-1 voice acting** — pipeline reserved, not yet wired.
