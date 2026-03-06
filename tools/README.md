# Tools Overview

This folder contains helper utilities for the SNES Super Monkey Island project — a native SCUMM v5 interpreter for The Secret of Monkey Island on Super Nintendo with MSU-1. Scripts are written in Python 3 (requires Pillow and NumPy); external dependencies such as WLA-DX and superfamiconv are included as pre-built binaries.

Install Python dependencies with `pip install -r requirements.txt`.

## Quick Reference Table

| Tool | Purpose |
| --- | --- |
| **SCUMM Resource Extraction & Conversion** | |
| `scumm_extract.py` | Extract all resources from MI1 CD Talkie data files (monkey.000 + monkey.001) |
| `scumm/` | SCUMM v5 parser package: crypto, chunks, index, resource, smap, palette, room_gfx, object_gfx, metadata, costume, charset, manifest |
| `snes_room_converter.py` | Convert extracted VGA room backgrounds to SNES Mode 1 tile format (palette, tileset, tilemap, column index) |
| `msu1_pack_rooms.py` | Pack all SNES room assets into a single .msu data file with dense room index for MSU-1 streaming |
| `msu1_pack_scripts.py` | Pack all SCUMM v5 script bytecode into MSU-1 data file (appends to room pack) |
| `scumm_costume_decoder.py` | Decode SCUMM v5 costume RLE data into per-frame indexed pixel images |
| `snes_costume_converter.py` | Convert decoded costume frames to SNES 4bpp sprite tiles + OAM layout |
| `scumm_opcode_audit.py` | Walk all 748 scripts, decode bytecode, report opcode coverage |
| `gen_dispatch_table.py` | Generate 256-entry 65816 opcode dispatch table from opcode map |
| `decode_script.py` | Disassemble SCUMM v5 bytecode from extracted script binary |
| `decode_boot_script.py` | Decode MI1 boot script (script 1) with opcode reference |
| `tiledpalettequant.py` | Standalone tile-aware palette optimizer for SNES graphics (extracted from snes_room_converter.py) |
| **Graphics & Assets (Makefile)** | |
| `animationWriter_sfc.py` | Convert PNG frames to SNES animation format via superfamiconv (sprites/backgrounds) |
| `animationWriter.py` | Animation writer using gracon.py (Makefile default) |
| `gfx_converter.py` | Unified wrapper for superfamiconv/gracon with consistent output naming |
| `gracon.py` | Python SNES graphics converter with tile deduplication (Makefile default) |
| `check_assets.py` | Validate sprite/background dimensions and transparency |
| **MSU-1 Audio** | |
| `msu1pcmwriter.py` | Validate WAV and prepend MSU-1 PCM header (44.1 kHz stereo 16-bit) |
| **Event Management** | |
| `create_event.py` | Generate boilerplate .h + .65816 for new Event classes |
| **FXPAK Pro Hardware** | |
| `fxpak_push.py` | Push ROM to FXPAK Pro via QUsb2Snes and boot it |
| `fxpak_debug.py` | Read SNES memory via QUsb2Snes: OOP stack, allocations, crash diagnostics |
| `fxpak_crash_dump.py` | Quick WRAM capture for post-crash analysis |
| **Testing & Automation** | |
| `mesen_mcp_server.py` | MCP server for Mesen automation: symbol lookup, build, test, screenshots |
| **Shared Helpers** | |
| `userOptions.py` | CLI option parser (used by gracon, animationWriter, msu1pcmwriter) |
| `paths.py` | Shared path resolution for Python tools |
| `revert_wladx.sh` | Downgrade WLA-DX from 9.5 to 9.3 (project requires 9.3) |
| **Bundled Binaries** | |
| `superfamiconv/` | Fast C++ SNES graphics converter (tiles, palettes, tilemaps) |
| `snesbrr-2006-12-13/` | BRR encoder/decoder for SNES audio samples |
| `wla-dx-9.5-svn/` | WLA-DX 9.3 assembler/linker for 65816/SPC700 (pre-built) |

## Graphics & Assets

### animationWriter_sfc.py

Converts PNG animation frames to SNES sprite/background animation format (`.animation` files with `SP` header) using superfamiconv for tile/palette conversion. Forces `-P 1` (single sub-palette) to prevent CGRAM overflow.

Called automatically by the makefile for `*.gfx_bg` and `*.gfx_sprite` directories.

### gfx_converter.py

Unified wrapper for superfamiconv or gracon.py with consistent output naming (`.palette`, `.tiles`, `.tilemap`).

```bash
python tools/gfx_converter.py --tool superfamiconv --input image.png --output-base output_name --bpp 4
```

**Palette rule:** `-palettes` must match the image's color count / 16 (e.g., 16 colors = `-palettes 1`).

### check_assets.py

Validates sprite and background image dimensions and transparency against specifications (arrows 32x32 transparent, backgrounds 256x224 opaque, etc.).

## MSU-1 Audio

### msu1pcmwriter.py

Validates WAV audio (must be stereo, 16-bit, 44.1 kHz RIFF PCM) and prepends the MSU-1 PCM header with optional loop point.

```bash
python3 msu1pcmwriter.py -infile audio/scene1.wav -outfile build/scene1-1.pcm -loopstart 0
```

## Event Management

### create_event.py

Generates boilerplate for new Event classes. Creates both `.h` (ZP struct, flags, properties) and `.65816` (init/play/kill methods with CLASS macro).

```bash
python tools/create_event.py Event.IntroScene
```

## FXPAK Pro Hardware

### fxpak_push.py

Pushes the built ROM to FXPAK Pro via QUsb2Snes websocket (localhost:23074) and boots it. Requires QUsb2Snes running and FXPAK connected via USB.

```bash
python tools/fxpak_push.py
```

### fxpak_debug.py

Reads SNES memory via QUsb2Snes for live debugging and crash analysis. Features include OOP stack dump (all 48 object slots), WRAM allocation tables, exception state readout, fingerprint mismatch diagnostics, and memory region inspection.

```bash
python tools/fxpak_debug.py
```

### fxpak_crash_dump.py

Quick WRAM capture from FXPAK for post-crash analysis. Dumps key memory regions (exception state, OOP stack, input registers) immediately after a crash.

```bash
python tools/fxpak_crash_dump.py
```

## Testing & Automation

### mesen_mcp_server.py

FastMCP-based automation server for Mesen 2 emulator. Provides tools for:
- `lookup_symbol` / `lookup_symbols` — auto-calculates `$C0xxxx` Mesen addresses from `.sym` file
- `build_rom` — trigger WSL build from Windows (incremental by default, `clean=True` for full rebuild)
- `run_test` — execute a Mesen Lua test script and return results
- `read_test_output` — read the output from the most recent test run
- `run_lua_snippet` — run arbitrary Lua in Mesen testrunner mode
- `take_screenshot` — capture emulator screen as PNG

Configured in `.mcp.json`. Runs on Windows Python, delegates to WSL for builds. All subprocess calls use `stdin=subprocess.DEVNULL` to prevent hanging.

## Bundled Binaries

### superfamiconv/

Fast C++ SNES graphics converter for tiles, palettes, and tilemaps. Primary converter for the build pipeline (~100x faster than gracon.py). Pre-built Linux binary included.

### snesbrr-2006-12-13/

BRR encoder/decoder for SNES audio samples with loop handling. Ships Windows `snesbrr.exe`; build from `src/` on Linux.

### wla-dx-9.5-svn/

WLA-DX 9.3 macro assembler/linker for 65816 and SPC700. Pre-built binaries included.

**Important:** Despite the directory name `9.5-svn`, the actual version is **9.3**. Version 9.4+ breaks the build.

## SCUMM v5 Resource Extractor

### scumm_extract.py

Extracts all game resources from The Secret of Monkey Island CD Talkie data files (`monkey.000` index + `monkey.001` data). Both files are XOR 0x69 encrypted. Parses the SCUMM v5 chunk format and exports:

- **86 room backgrounds** as full-color PNGs (320-1008px wide, 144-200px tall)
- **697 object images** as PNGs (per-state images from OBIM chunks)
- **86 room metadata** as JSON (walkboxes, scaling, color cycling, object lists)
- **187 global scripts** as raw bytecode (.bin)
- **138 sounds** as raw binary
- **123 costumes** as raw binary (full decode deferred to Phase 2)
- **5 charsets** as raw binary + font sheet PNGs
- **Palette data** per room (raw .bin + 16x16 swatch .png)
- **manifest.json** summarizing all extracted resources

```bash
# Full extraction
python tools/scumm_extract.py \
    --index data/monkeypacks/talkie/monkey.000 \
    --data data/monkeypacks/talkie/monkey.001 \
    --output data/scumm_extracted

# Extract specific rooms only
python tools/scumm_extract.py \
    --index data/monkeypacks/talkie/monkey.000 \
    --data data/monkeypacks/talkie/monkey.001 \
    --output data/scumm_extracted \
    --rooms 1,20,28

# Extract specific resource types only
python tools/scumm_extract.py \
    --index data/monkeypacks/talkie/monkey.000 \
    --data data/monkeypacks/talkie/monkey.001 \
    --output data/scumm_extracted \
    --types backgrounds,metadata

# Index-only mode (parse and dump index file without extracting resources)
python tools/scumm_extract.py \
    --index data/monkeypacks/talkie/monkey.000 \
    --data data/monkeypacks/talkie/monkey.001 \
    --output data/scumm_extracted \
    --index-only
```

**Output directory structure:**
```
data/scumm_extracted/
  manifest.json
  index/
    maxs.json, room_names.json, directories.json, objects.json
  rooms/
    room_001_beach/
      background.png, palette.bin, palette.png, metadata.json
      objects/   (per-object PNGs)
      scripts/   (encd.bin, excd.bin, lscr_NNN.bin, scrp_NNN.bin)
      costumes/  (cost_NNN.bin)
      sounds/    (soun_NNN.bin)
  scripts/   (global scripts: scrp_NNN_roomNNN.bin)
  sounds/    (soun_NNN_roomNNN.bin)
  costumes/  (cost_NNN_roomNNN.bin)
  charsets/  (char_NNN_roomNNN.bin)
```

### scumm/ package modules

| Module | Purpose |
| --- | --- |
| `crypto.py` | XOR 0x69 decryption + DecryptedReader wrapper |
| `chunks.py` | Chunk reader (4-byte ASCII tag + 4-byte BE size) |
| `index.py` | Index file parser (RNAM room names, MAXS limits, DROO/DSCR/DSOU/DCOS/DCHR directories, DOBJ objects) |
| `resource.py` | Data file parser (LECF/LOFF/LFLF/ROOM structure) |
| `smap.py` | SMAP stripe decompression (all codecs: raw, BasicV, BasicH, MajMin, with/without transparency) |
| `palette.py` | CLUT 256-color palette parsing + swatch PNG rendering |
| `room_gfx.py` | Background extraction (RMIM/IM00/SMAP + CLUT → PNG) |
| `object_gfx.py` | Object image extraction (OBIM/IMHD/IM01+ → PNG) |
| `metadata.py` | Room metadata → JSON (RMHD, BOXD walkboxes, SCAL scaling, CYCL color cycling, OBCD objects, scripts) |
| `costume.py` | Costume raw binary extraction |
| `charset.py` | Charset raw binary extraction + font sheet PNG attempt |
| `manifest.py` | Resource manifest JSON generation |

## SNES Room Tile Converter

### snes_room_converter.py

Converts extracted VGA room background PNGs to SNES Mode 1 native tile format for MSU-1 streaming. Full Python pipeline with gracon-inspired lossy palette assignment, tile deduplication with flip detection, and column-major tilemap output.

```bash
# Single room
python tools/snes_room_converter.py \
    --input data/scumm_extracted/rooms/room_028_bar/background.png \
    --output data/snes_converted/rooms/

# All rooms (batch)
python tools/snes_room_converter.py \
    --input data/scumm_extracted/rooms/ \
    --output data/snes_converted/rooms/ \
    --verbose

# Specific rooms with verification images
python tools/snes_room_converter.py \
    --input data/scumm_extracted/rooms/ \
    --output data/snes_converted/rooms/ \
    --rooms 1,10,20,28 \
    --verify
```

**Pipeline per room:**
1. Load RGB PNG, convert to SNES BGR555 color space
2. Build 8 sub-palettes via global color reduction (median-cut merge nearest pairs, then partition)
3. Assign each 8x8 tile to best sub-palette, remap pixels to nearest palette color (lossy)
4. Deduplicate tiles with horizontal/vertical flip detection (hash-based O(1) lookup)
5. Encode binary outputs: `.pal`, `.chr`, `.map`, `.col`, `.hdr`

**Output per room** (in `data/snes_converted/rooms/`):

| File | Format | Description |
| --- | --- | --- |
| `room_NNN.pal` | 256 bytes (8x16x2 BGR555) | SNES CGRAM palette data |
| `room_NNN.chr` | Variable (32 bytes/tile) | 4bpp planar tileset, deduplicated |
| `room_NNN.map` | w_tiles x h_tiles x 2 bytes | Column-major tilemap (SNES tilemap words) |
| `room_NNN.col` | Variable | Column streaming index for scroll engine |
| `room_NNN.hdr` | 32 bytes | Room header (dimensions, sizes, offsets) |
| `room_NNN_verify.png` | PNG | Verification image (optional, `--verify`) |
| `manifest.json` | JSON | Batch conversion stats for all rooms |

**Key stats (all 86 rooms):** 0 failures, avg 732 tiles/room, avg 23.8% dedup, ~2 MB total tileset, 0.6s/room. 14 wide rooms (>320px) exceed the 1024-tile SNES tilemap limit and will need tile streaming.

## MSU-1 Room Data Packer

### msu1_pack_rooms.py

Packs all SNES-converted room assets into a single `.msu` data file with a dense room index for O(1) lookup by the 65816 engine via MSU-1 registers. Reads room binaries and `manifest.json` from `snes_room_converter.py` output.

```bash
# Basic pack
python tools/msu1_pack_rooms.py \
    --input data/snes_converted/rooms/ \
    --output distribution/SuperMonkeyIsland.msu

# With verification and verbose output
python tools/msu1_pack_rooms.py \
    --input data/snes_converted/rooms/ \
    --output distribution/SuperMonkeyIsland.msu \
    --verify --verbose
```

**File format:**
- 256-byte header with `S-MSU1` magic, title, section offsets, and placeholders for future resource types (scripts, costumes, sounds, charsets)
- Dense room index table (100 entries × 8 bytes) — room ID maps directly to entry offset for O(1) lookup
- Room data blocks (512-byte aligned for SD2SNES sector reads), each containing hdr+pal+chr+map+col in engine load order

**Verification (`--verify`):** Reads back every room from the packed `.msu` file and compares byte-for-byte against source files. Also validates magic, title, null entries for missing room IDs, and 512-byte alignment of all data block offsets.

**Output:** `distribution/SuperMonkeyIsland.msu` (~2.52 MB for 86 rooms)

## MSU-1 Script Packer

### msu1_pack_scripts.py

Appends SCUMM v5 script bytecode to an existing MSU-1 data pack (`.msu` file created by `msu1_pack_rooms.py`). Builds indexed sections for both global scripts and per-room scripts (ENCD, EXCD, LSCR).

```bash
# Basic pack (appends to existing .msu)
python tools/msu1_pack_scripts.py

# With verification and verbose output
python tools/msu1_pack_scripts.py --verify --verbose
```

**File format** (appended after room data, 512-byte aligned):
- Script section header (32 bytes) with `SCPT` magic
- Global script index (187 slots x 8 bytes) — offset + size per script
- Room script index (100 slots x 8 bytes) — offset + size per room
- Global script data (individually seekable by script number)
- Room script blocks (per-room: block header + ENCD + EXCD + LSCRs)

**Important:** `msu1_pack_rooms.py` recreates the `.msu` from scratch — must re-run `msu1_pack_scripts.py` after any room repack.

**Output:** Appends ~380 KB to `distribution/SuperMonkeyIsland.msu` (total: 2.89 MB)

## SCUMM Costume Tools

### scumm_costume_decoder.py

Decodes SCUMM v5 costume RLE binary data (extracted by `scumm_extract.py`) into per-frame indexed pixel images. Handles format 0x58 (16-color) with column-by-column RLE compression and cross-column run carry (runs span column boundaries).

```bash
# List all animations and pictures in a costume
python tools/scumm_costume_decoder.py \
    --costume data/scumm_extracted/costumes/cost_001_room002.bin \
    --list

# Decode specific frame with verification PNG
python tools/scumm_costume_decoder.py \
    --costume data/scumm_extracted/costumes/cost_001_room002.bin \
    --anim 1 --frame 0 --verify \
    --palette data/scumm_extracted/rooms/room_020_main-beac/palette.bin

# Decode all pictures and save binary + PNG
python tools/scumm_costume_decoder.py \
    --costume data/scumm_extracted/costumes/cost_017_room020.bin \
    --all --verify \
    --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \
    --output data/snes_converted/costumes/
```

**Output per frame:** indexed pixel array (width x height uint8), optional verification PNG using room palette.

### snes_costume_converter.py

Converts decoded SCUMM costume frames to SNES-native sprite data: 4bpp planar CHR tiles (32 bytes/tile), BGR555 palette (32 bytes = 16 colors), and OAM layout table with per-tile (dx, dy, tile_id, attr) entries.

```bash
# Convert Guybrush's standing-south frame
python tools/snes_costume_converter.py \
    --costume data/scumm_extracted/costumes/cost_001_room002.bin \
    --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \
    --anim 4 --frame 0 \
    --output data/snes_converted/costumes/cost_001

# Convert all pictures with verification
python tools/snes_costume_converter.py \
    --costume data/scumm_extracted/costumes/cost_017_room020.bin \
    --palette data/scumm_extracted/rooms/room_001_beach/palette.bin \
    --all --verify \
    --output data/snes_converted/costumes/cost_017
```

**Output per frame:** `.chr` (4bpp tiles), `.pal` (BGR555 palette), `.oam` (layout table), optional verification PNG.

## SCUMM Bytecode Analysis

### scumm_opcode_audit.py

Walks all 748 extracted script files, decodes every opcode using the variable-length parameter decoders from `scumm/opcodes_v5.py`, and reports coverage: which of the 105 base opcodes MI1 actually uses.

```bash
python tools/scumm_opcode_audit.py
```

**Output:** Console summary + `data/scumm_extracted/opcode_audit.json` with:
- Per-opcode usage counts and frequency percentages
- Per-script-type breakdowns (SCRP, ENCD, EXCD, LSCR)
- Unused opcodes list
- Category distribution (control flow, arithmetic, actor, etc.)
- **Result:** 103/105 opcodes used, 30,066 total opcode instances, 0 decode errors

### gen_dispatch_table.py

Generates a 256-entry 65816 assembly dispatch table (`.dw` directives) from the Python opcode map. Each entry points to the handler label for that opcode byte; unimplemented opcodes point to `op_stub`.

```bash
python tools/gen_dispatch_table.py
```

**Output:** `src/object/scummvm/scummvm_dispatch_table.inc` — included by `scummvm.65816` for `jsr (table,x)` dispatch.

### decode_script.py

Disassembles SCUMM v5 bytecode from an extracted script binary, showing opcode names, parameters, variable references, and jump targets in human-readable format.

```bash
python tools/decode_script.py data/scumm_extracted/scripts/scrp_001_room002.bin
```

**Output:** Annotated bytecode listing with opcode addresses, decoded parameters, and variable names.

### decode_boot_script.py

Specialized decoder for MI1's boot script (script 1, 42 bytes). Hardcodes the raw bytecode and decodes it with known global variable name annotations. Useful as a reference for the interpreter's initial execution path.

```bash
python tools/decode_boot_script.py
```

### scumm/opcodes_v5.py

Complete 256-entry SCUMM v5 opcode table with variable-length parameter decoders. Maps each byte value to its base opcode name and provides a decoder function that consumes the correct number of parameter bytes from a stream. Reused by `scumm_opcode_audit.py`, `decode_script.py`, and `gen_dispatch_table.py`.

Key exports:
- `OPCODE_MAP`: dict mapping byte (0-255) → `(base_name, decoder_fn)`
- `BASE_OPCODES`: set of 105 unique base opcode names
- `OPCODE_CATEGORIES`: dict mapping category → set of opcode names
- `UNIQUE_OPCODES`: dict mapping base_name → list of byte values

## Tile-Aware Palette Optimizer

### tiledpalettequant.py

Standalone tile-aware palette optimizer for SNES graphics. Jointly assigns tiles to sub-palettes and optimizes palette colors using iterative k-means, minimizing per-tile quantization error with perceptual color weighting. Based on [tiledpalettequant by Rilden](https://github.com/rilden/tiledpalettequant).

```bash
# Basic usage — optimize palettes for a PNG image
python tools/tiledpalettequant.py input.png -o output.pal

# Custom palette configuration
python tools/tiledpalettequant.py input.png -o output.pal \
    --palettes 8 --colors 16 --transparent 000000

# JSON output with tile assignments
python tools/tiledpalettequant.py input.png --json output.json

# Verification PNG showing palette-reduced result
python tools/tiledpalettequant.py input.png -o output.pal --verify

# Verbose mode with per-tile info
python tools/tiledpalettequant.py input.png -o output.pal -v
```

**CLI options:**
- `--output, -o` — Output .pal file path (SNES CGRAM format: N x colors x 2 bytes BGR555)
- `--json` — Output JSON with palettes and tile assignments
- `--palettes, -p` — Number of sub-palettes (default: 8)
- `--colors, -c` — Colors per sub-palette (default: 16, index 0 = transparent)
- `--transparent COLOR` — Transparent color in hex (default: 000000)
- `--tile-size N` — Tile size in pixels (default: 8)
- `--verbose, -v` — Print per-tile assignment info
- `--verify` — Save verification PNG showing palette-reduced output

**Library interface:** Import `build_palettes_tileaware()` directly for use in other tools (used by `snes_room_converter.py`).

**Dependencies:** NumPy (required), Pillow (for CLI PNG I/O only).

## Platform Notes

- Python tooling requires **Python 3.10+** with Pillow and NumPy (`pip install -r requirements.txt`)
- Build tools run under **WSL** (Ubuntu); FXPAK tools run on **Windows Python**
- `mesen_mcp_server.py` runs on Windows Python, delegates WSL for builds
- Audio scripts use the standard library (`wave`); no external encoders required
