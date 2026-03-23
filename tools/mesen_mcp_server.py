#!/usr/bin/env python3
"""Mesen MCP Server — automates sym lookup, build, test execution, and screenshots.

Runs on Windows Python (NOT WSL). Calls into WSL only for make.
"""

import re
import struct
import subprocess
import zlib
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from paths import PROJECT_ROOT, DISTRIBUTION, windows_to_wsl

PROJECT = PROJECT_ROOT
SFC_DIR = DISTRIBUTION
SYM_FILE = PROJECT / "build" / "SuperMonkeyIsland.sym"
MESEN = PROJECT / "mesen" / "Mesen.exe"

mcp = FastMCP("mesen")


def _parse_sym_line(line: str):
    """Parse one sym file line into (bank, addr, name) or None."""
    m = re.match(r"^([0-9a-fA-F]+):([0-9a-fA-F]+)\s+(.+)$", line.strip())
    if not m:
        return None
    bank = int(m.group(1), 16)
    addr = int(m.group(2), 16)
    name = m.group(3)
    full = bank * 0x10000 + addr
    return bank, addr, name, full


@mcp.tool()
def lookup_symbol(pattern: str) -> str:
    """Search the sym file for symbols matching a regex pattern.

    Returns matching symbols with their addresses. ROM addresses include
    the $C0 bank prefix needed for Mesen exec callbacks.
    """
    if not SYM_FILE.exists():
        return f"ERROR: {SYM_FILE} not found. Run build_rom() first."

    results = []
    regex = re.compile(pattern, re.IGNORECASE)
    for line in SYM_FILE.read_text().split("\n"):
        parsed = _parse_sym_line(line)
        if not parsed:
            continue
        bank, addr, name, full = parsed
        if regex.search(name):
            # ROM addresses (banks $00-$3F) need $C0 prefix for Mesen exec callbacks
            if bank <= 0x3F:
                mesen_addr = 0xC00000 + full
                results.append(f"  {name}: ${full:06X} (Mesen: 0x{mesen_addr:06X})")
            else:
                results.append(f"  {name}: ${full:06X}")

    if not results:
        return f"No symbols matching '{pattern}' found."
    return f"Found {len(results)} match(es):\n" + "\n".join(results[:50])


@mcp.tool()
def lookup_symbols(symbols: list[str]) -> str:
    """Batch lookup multiple exact symbol names in the sym file.

    Returns each symbol's address with $C0 bank prefix for ROM addresses.
    Useful for updating all addresses in a Lua test script at once.
    """
    if not SYM_FILE.exists():
        return f"ERROR: {SYM_FILE} not found. Run build_rom() first."

    sym_map: dict[str, tuple[int, int, int]] = {}
    for line in SYM_FILE.read_text().split("\n"):
        parsed = _parse_sym_line(line)
        if not parsed:
            continue
        bank, addr, name, full = parsed
        sym_map[name] = (bank, addr, full)

    results = []
    for sym in symbols:
        if sym in sym_map:
            bank, addr, full = sym_map[sym]
            if bank <= 0x3F:
                mesen_addr = 0xC00000 + full
                results.append(f"  {sym}: ${full:06X} (Mesen: 0x{mesen_addr:06X})")
            else:
                results.append(f"  {sym}: ${full:06X}")
        else:
            # Try partial match
            matches = [n for n in sym_map if sym in n]
            if matches:
                results.append(f"  {sym}: NOT FOUND (did you mean: {', '.join(matches[:3])}?)")
            else:
                results.append(f"  {sym}: NOT FOUND")

    return "\n".join(results)


@mcp.tool()
def build_rom(clean: bool = False) -> str:
    """Build the ROM via WSL make. Returns build output and refreshes .sym file.

    Args:
        clean: If True, runs 'make clean && make'. If False (default), runs
               incremental 'make' (~1s). Use clean=True only when needed.
    """
    wsl_project = windows_to_wsl(str(PROJECT))

    # Pre-flight: verify WSL is responsive (fail fast instead of hanging)
    # CRITICAL: stdin=DEVNULL prevents WSL from inheriting the MCP server's
    # piped stdin, which causes WSL to hang indefinitely on Windows.
    try:
        ping = subprocess.run(
            ["wsl", "echo", "ok"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )
        if ping.returncode != 0 or "ok" not in ping.stdout:
            return f"BUILD ERROR: WSL not responsive (rc={ping.returncode}, out={ping.stdout.strip()!r})"
    except subprocess.TimeoutExpired:
        return "BUILD ERROR: WSL not responding (10s ping timeout). Is WSL running?"
    except FileNotFoundError:
        return "BUILD ERROR: 'wsl' command not found. Is WSL installed?"
    except Exception as e:
        return f"BUILD ERROR: WSL pre-flight failed: {e}"

    # Build command — no shell=True, call wsl directly with list args
    make_cmd = "make clean && make" if clean else "make"
    timeout = 60 if clean else 30

    try:
        result = subprocess.run(
            ["wsl", "bash", "-c", f"cd {wsl_project} && {make_cmd}"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout + result.stderr

        if result.returncode == 0:
            rom = PROJECT / "build" / "SuperMonkeyIsland.sfc"
            sym = PROJECT / "build" / "SuperMonkeyIsland.sym"
            if rom.exists():
                size_kb = rom.stat().st_size // 1024
                sym_note = " + .sym updated" if sym.exists() else " (WARNING: .sym missing)"
                return f"BUILD SUCCESS ({size_kb} KB ROM{sym_note})\n\n{output[-2000:]}"
            return f"BUILD WARNING: make returned 0 but ROM not found\n\n{output[-2000:]}"
        return f"BUILD FAILED (exit code {result.returncode})\n\n{output[-2000:]}"
    except subprocess.TimeoutExpired:
        return f"BUILD TIMEOUT (>{timeout}s). WSL was responsive but make hung."
    except Exception as e:
        return f"BUILD ERROR: {e}"


@mcp.tool()
def run_test(script_name: str, timeout: int = 120) -> str:
    """Execute a Mesen testrunner Lua script and return the output.

    Args:
        script_name: Lua script filename (e.g. 'test_room_load.lua').
                     Must exist in the distribution directory.
        timeout: Max seconds to wait for Mesen to finish.
    """
    script_path = SFC_DIR / script_name
    if not script_path.exists():
        available = sorted(f.name for f in SFC_DIR.glob("*.lua"))
        msg = f"ERROR: {script_name} not found in {SFC_DIR}"
        if available:
            msg += f"\n\nAvailable scripts:\n" + "\n".join(f"  {f}" for f in available)
        return msg

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc {script_name} > out.txt 2>&1'
    )

    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"MESEN TIMEOUT (>{timeout}s). Partial output:\n{_read_out_file(out_file)}"
    except Exception as e:
        return f"MESEN ERROR: {e}"

    output = _read_out_file(out_file)

    # Parse result
    if "PASS" in output:
        status = "PASS"
    elif "FAIL" in output:
        status = "FAIL"
    elif "TIMEOUT" in output:
        status = "TIMEOUT"
    else:
        status = "INCONCLUSIVE"

    return f"Result: {status}\n\n{output}"


@mcp.tool()
def run_lua_snippet(lua_code: str, timeout: int = 60) -> str:
    """Write ad-hoc Lua code to a temp file and run it in Mesen testrunner.

    Args:
        lua_code: The Lua script contents to execute.
        timeout: Max seconds to wait.
    """
    script_path = SFC_DIR / "_mcp_snippet.lua"
    script_path.write_text(lua_code)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_snippet.lua > out.txt 2>&1'
    )

    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"MESEN TIMEOUT (>{timeout}s). Partial output:\n{_read_out_file(out_file)}"
    except Exception as e:
        return f"MESEN ERROR: {e}"

    return _read_out_file(out_file)


@mcp.tool()
def read_test_output(file: str = "out.txt") -> str:
    """Read previous test output from the distribution directory.

    Args:
        file: Output filename to read (default: out.txt).
    """
    path = SFC_DIR / file
    if not path.exists():
        return f"ERROR: {path} not found"
    return path.read_text()[-3000:]


def _argb_to_png(width: int, height: int, argb_lines: list[str]) -> bytes:
    """Convert ARGB hex pixel data (from getScreenBuffer) to a PNG file."""
    # Parse all ARGB values from the hex lines
    pixels = []
    for line in argb_lines:
        for hex_val in line.strip().split():
            pixels.append(int(hex_val, 16))

    if len(pixels) != width * height:
        raise ValueError(f"Expected {width*height} pixels, got {len(pixels)}")

    # Build raw RGBA scanlines with filter byte 0 (None) per row
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter byte
        for x in range(width):
            argb = pixels[y * width + x]
            r = (argb >> 16) & 0xFF
            g = (argb >> 8) & 0xFF
            b = argb & 0xFF
            raw.extend((r, g, b))

    # Minimal PNG encoder
    def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat_data = zlib.compress(bytes(raw))

    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", ihdr_data)
    png += png_chunk(b"IDAT", idat_data)
    png += png_chunk(b"IEND", b"")
    return png



# Lua screenshot script template.
# Uses getScreenBuffer() ARGB array (reliable across Mesen versions).
_SCREENSHOT_LUA = r"""
local TARGET_FRAME = {target_frame}
local screenshotDone = false

{lua_preamble}

emu.addEventCallback(function()
    if screenshotDone then return end
    local frame = emu.getState()["ppu.frameCount"]
    if frame < TARGET_FRAME then return end
    screenshotDone = true

    local buf = emu.getScreenBuffer()
    if not buf or #buf == 0 then
        print("SCREENSHOT_ERROR: no screen buffer available")
        emu.stop()
        return
    end
    local size = emu.getScreenSize()
    local w = size.width or 256
    local h = size.height or 224

    print(string.format("SCREENSHOT_ARGB_START %d %d", w, h))
    local line = {}
    for i = 1, #buf do
        line[#line + 1] = string.format("%08X", buf[i])
        if #line >= 16 then
            print(table.concat(line, " "))
            line = {}
        end
    end
    if #line > 0 then
        print(table.concat(line, " "))
    end
    print("SCREENSHOT_ARGB_END")
    emu.stop()
end, emu.eventType.endFrame)
"""


@mcp.tool()
def take_screenshot(
    wait_frames: int = 800,
    lua_preamble: str = "",
    timeout: int = 60,
) -> str:
    """Take a screenshot of the emulator at a specific PPU frame.

    Runs a Lua script in Mesen that waits until the target frame, captures the
    screen buffer, converts to PNG, and saves to the distribution directory.

    Args:
        wait_frames: PPU frame number at which to capture (default 800 = after boot + SCUMM init).
        lua_preamble: Optional Lua code inserted before the screenshot logic
                      (e.g., input injection for room cycling).
        timeout: Max seconds to wait for Mesen.

    Returns:
        Path to the saved PNG file, or an error message.
    """
    effective_preamble = lua_preamble

    lua_code = _SCREENSHOT_LUA.replace("{target_frame}", str(wait_frames))
    lua_code = lua_code.replace("{lua_preamble}", effective_preamble)

    script_path = SFC_DIR / "_mcp_screenshot.lua"
    script_path.write_text(lua_code)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_screenshot.lua > out.txt 2>&1'
    )

    try:
        subprocess.run(f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"MESEN TIMEOUT (>{timeout}s). Partial output:\n{_read_out_file(out_file)}"
    except Exception as e:
        return f"MESEN ERROR: {e}"

    output = out_file.read_text()
    png_path = SFC_DIR / "screenshot.png"

    # Parse ARGB pixel buffer from Lua output
    if "SCREENSHOT_ARGB_START" in output and "SCREENSHOT_ARGB_END" in output:
        header_line = output[output.index("SCREENSHOT_ARGB_START"):].split("\n")[0]
        parts = header_line.split()
        width = int(parts[1]) if len(parts) > 1 else 256
        height = int(parts[2]) if len(parts) > 2 else 224

        start = output.index("SCREENSHOT_ARGB_START")
        start = output.index("\n", start) + 1  # skip header line
        end = output.index("SCREENSHOT_ARGB_END")
        argb_text = output[start:end].strip().split("\n")

        try:
            # Parse ARGB pixels for crash detection before PNG conversion
            pixels = []
            for line in argb_text:
                for hex_val in line.strip().split():
                    pixels.append(int(hex_val, 16))

            # Strip SNES overscan padding rows from PPU buffer.
            # SnesPpu::SendFrame() zeroes top 7 / bottom 8 rows of the
            # 239-line buffer for 224-line mode games.  The Lua API
            # returns the raw buffer, so we crop to visible content.
            if height == 239:
                top, bot = 7, 8
                pixels = pixels[top * width : len(pixels) - bot * width]
                height = 224
            elif height == 478:          # hi-res interlace (doubled)
                top, bot = 14, 16
                pixels = pixels[top * width : len(pixels) - bot * width]
                height = 448

            # Regenerate argb_text from (possibly cropped) pixels
            argb_text = []
            line_vals = []
            for p in pixels:
                line_vals.append(f"{p:08X}")
                if len(line_vals) >= 16:
                    argb_text.append(" ".join(line_vals))
                    line_vals = []
            if line_vals:
                argb_text.append(" ".join(line_vals))

            png_bytes = _argb_to_png(width, height, argb_text)
            png_path.write_bytes(png_bytes)
            msg = f"Screenshot saved: {png_path} ({len(png_bytes)} bytes, from ARGB buffer)"

            # Check for magenta error screen
            if _check_magenta_crash(pixels, width):
                msg += "\n[CRASH DETECTED -- magenta error screen]"

            return msg
        except Exception as e:
            return f"ERROR converting ARGB to PNG: {e}"

    # Neither strategy produced output
    if "SCREENSHOT_ERROR" in output:
        return output
    return f"INCONCLUSIVE — no screenshot data in output.\n\n{output[-1000:]}"


def _read_out_file(path: Path) -> str:
    """Safely read an output file, returning last 3000 chars."""
    try:
        return path.read_text()[-3000:]
    except Exception:
        return "(could not read output file)"


def _lookup_sym_address(sym_path: Path, symbol_name: str) -> int | None:
    """Look up a symbol's Mesen exec-callback address from the sym file.

    Returns the address with $C0 bank prefix for ROM symbols, or None.
    """
    for line in sym_path.read_text().split("\n"):
        parsed = _parse_sym_line(line)
        if not parsed:
            continue
        bank, addr, name, full = parsed
        if name == symbol_name:
            if bank <= 0x3F:
                return 0xC00000 + full
            return full
    return None


def _lookup_wram_offset(sym_path: Path, symbol_name: str) -> int | None:
    """Look up a WRAM symbol's offset from $7E:0000.

    WLA-DX sym files store WRAM addresses as bank=0000 with the full 24-bit
    SNES address in the addr field (e.g. 0000:7EED63).  _lookup_sym_address
    would incorrectly add a $C0 ROM prefix to these — use this instead.
    """
    for line in sym_path.read_text().split("\n"):
        parsed = _parse_sym_line(line)
        if not parsed:
            continue
        _bank, addr, name, _full = parsed
        if name == symbol_name:
            if addr >= 0x7E0000:
                return addr - 0x7E0000
            return addr
    return None


def _check_magenta_crash(argb_pixels: list[int], width: int) -> bool:
    """Check if the screen shows the magenta error screen.

    Samples pixels from the top-left area. The error screen has a bright
    magenta/pink backdrop (R>200, G<100, B<100 in the SNES palette).
    """
    height = len(argb_pixels) // width if width > 0 else 0
    if height == 0:
        return False
    sample_count = 0
    magenta_count = 0
    # Sample from multiple screen regions (top rows may be blank overscan)
    for y_off in (height // 4, height // 3, height // 2, height * 2 // 3):
        for x_off in (2, width // 4, width // 2, width * 3 // 4, width - 3):
            idx = y_off * width + x_off
            if idx >= len(argb_pixels):
                continue
            argb = argb_pixels[idx]
            r = (argb >> 16) & 0xFF
            g = (argb >> 8) & 0xFF
            b = argb & 0xFF
            sample_count += 1
            if r > 200 and g < 100 and b > 200:
                magenta_count += 1
    return sample_count > 0 and magenta_count > sample_count // 2


_BOOT_TEST_LUA = r"""
local errorHit = false
emu.addMemoryCallback(function()
    if errorHit then return end
    errorHit = true
    local frame = emu.getState()["ppu.frameCount"]
    print("BOOT_CRASH frame=" .. frame)
    emu.stop()
end, emu.callbackType.exec, {error_trigger_addr})

emu.addEventCallback(function()
    local frame = emu.getState()["ppu.frameCount"]
    if frame >= 500 then
        print("BOOT_OK")
        emu.stop()
    end
end, emu.eventType.endFrame)
"""


def _run_boot_test(sym_path: Path) -> str:
    """Run the ROM in Mesen for 500 frames and check for error handler hit."""
    error_addr = _lookup_sym_address(sym_path, "core.error.trigger")
    if error_addr is None:
        return "Boot test: SKIP (core.error.trigger not found in sym file)"

    lua_code = _BOOT_TEST_LUA.replace(
        "{error_trigger_addr}", f"0x{error_addr:06X}"
    )

    script_path = SFC_DIR / "_mcp_boot_test.lua"
    script_path.write_text(lua_code)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_boot_test.lua > out.txt 2>&1'
    )

    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"', shell=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "Boot test: TIMEOUT (>60s)"
    except Exception as e:
        return f"Boot test: ERROR ({e})"

    output = _read_out_file(out_file)
    if "BOOT_CRASH" in output:
        return f"Boot test: CRASH -- {output.strip()}"
    if "BOOT_OK" in output:
        return "Boot test: PASS (500 frames, no error handler hit)"
    return f"Boot test: INCONCLUSIVE -- {output[-200:]}"


@mcp.tool()
def validate_rom(clean_build: bool = False) -> str:
    """Validate the ROM after build: bank 0 usage, BRK scan, and runtime boot test.

    Args:
        clean_build: If True, trigger a clean build first. Default False (validate existing ROM).

    Returns:
        Combined validation report with bank 0 usage, BRK scan, and boot test results.
    """
    if clean_build:
        build_result = build_rom(clean=True)
        if "BUILD FAILED" in build_result or "BUILD ERROR" in build_result:
            return build_result

    rom_path = PROJECT / "build" / "SuperMonkeyIsland.sfc"
    sym_path = PROJECT / "build" / "SuperMonkeyIsland.sym"

    if not rom_path.exists():
        return "ERROR: ROM not found. Run build_rom() first."
    if not sym_path.exists():
        return "ERROR: sym file not found. Run build_rom() first."

    lines = ["=== ROM Validation ==="]

    # Bank 0 usage check
    rom_data = rom_path.read_bytes()
    bank0 = rom_data[:0x8000]  # HiROM bank 0 = first 32KB
    used = sum(1 for b in bank0 if b != 0)
    total = len(bank0)
    pct = used * 100.0 / total
    status = "CRITICAL" if pct > 95 else "WARNING" if pct > 90 else "OK"
    lines.append(f"Bank 0: {used:,}/{total:,} ({pct:.1f}%) -- {status}")

    # BRK scan
    from brk_scanner import scan_rom
    result = scan_rom(str(sym_path), str(rom_path))
    hit_count = len(result.hits)
    baseline = 15

    if hit_count > baseline:
        new_hits = hit_count - baseline
        lines.append(f"BRK scan: WARNING -- {hit_count} detected "
                     f"({new_hits} above baseline of {baseline}, "
                     f"{result.regions_scanned} regions scanned)")
        for hit in result.hits:
            ctx_hex = ' '.join(f'{b:02X}' for b in hit.context_bytes)
            lines.append(f"  BRK at ${hit.snes_bank:02X}:{hit.snes_offset:04X} "
                        f"near {hit.nearest_symbol}+{hit.symbol_distance} "
                        f"ctx: [{ctx_hex}]")
    else:
        lines.append(f"BRK scan: CLEAN ({hit_count} detected, baseline {baseline}, "
                     f"{result.regions_scanned} regions scanned)")

    # Runtime boot test
    lines.append(_run_boot_test(sym_path))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Button name -> SNES JOY1L bitmask mapping
# ---------------------------------------------------------------------------
_BUTTON_MAP = {
    "b": 0x8000, "y": 0x4000, "select": 0x2000, "start": 0x1000,
    "up": 0x0800, "down": 0x0400, "left": 0x0200, "right": 0x0100,
    "a": 0x0080, "x": 0x0040, "l": 0x0020, "r": 0x0010,
}


def _parse_buttons(button_str: str) -> int:
    """Parse a button string like 'right+a' into a 16-bit bitmask."""
    mask = 0
    for name in button_str.lower().split("+"):
        name = name.strip()
        if name in _BUTTON_MAP:
            mask |= _BUTTON_MAP[name]
    return mask


# Lua template for run_with_input.
# Placeholders: {hook_addr}, {press_wram}, {trigger_wram}, {old_wram},
#               {schedule_entries}, {user_lua}, {screenshot_logic}, {stop_frame}
_RUN_WITH_INPUT_LUA = r"""
-- Helpers available to user lua_code
local rd8 = function(a) return emu.read(a, emu.memType.snesWorkRam, false) end
local rd16 = function(a)
  return emu.read(a, emu.memType.snesWorkRam, false)
       + emu.read(a+1, emu.memType.snesWorkRam, false) * 256
end
local rd16s = function(a)
  local v = rd16(a)
  if v >= 32768 then v = v - 65536 end
  return v
end
local frameCount = 0

-- Input schedule: list of {startFrame, endFrame, buttonMask}
local _schedule = {
{schedule_entries}
}

local _lastButtons = 0

local function _wr16(addr, val)
  emu.write(addr, val & 0xFF, emu.memType.snesWorkRam)
  emu.write(addr + 1, (val >> 8) & 0xFF, emu.memType.snesWorkRam)
end

-- Hook the RTS of _checkInputDevice to override press/trigger after HW read
emu.addMemoryCallback(function()
  local frame = emu.getState()["ppu.frameCount"]
  local buttons = 0
  for _, entry in ipairs(_schedule) do
    if frame >= entry[1] and frame <= entry[2] then
      buttons = buttons | entry[3]
    end
  end

  if buttons ~= 0 then
    _wr16({press_wram}, buttons)
    -- trigger = newly pressed (not held from last frame)
    local trig = buttons & (~_lastButtons & 0xFFFF)
    _wr16({trigger_wram}, trig)
    _wr16({old_wram}, buttons)
  end
  _lastButtons = buttons
end, emu.callbackType.exec, {hook_addr})

-- Update frameCount FIRST (registered before user code so it fires first)
emu.addEventCallback(function()
  frameCount = emu.getState()["ppu.frameCount"]
end, emu.eventType.endFrame)

-- User-supplied Lua code
{user_lua}

-- Screenshot + stop logic
local _screenshotDone = false
emu.addEventCallback(function()
{screenshot_logic}
  if frameCount >= {stop_frame} then
    emu.stop()
  end
end, emu.eventType.endFrame)
"""

_SCREENSHOT_CAPTURE_LUA = r"""
  if not _screenshotDone and frameCount >= {screenshot_frame} then
    _screenshotDone = true
    local buf = emu.getScreenBuffer()
    if buf and #buf > 0 then
      local size = emu.getScreenSize()
      local w = size.width or 256
      local h = size.height or 224
      print(string.format("SCREENSHOT_ARGB_START %d %d", w, h))
      local line = {}
      for i = 1, #buf do
        line[#line + 1] = string.format("%08X", buf[i])
        if #line >= 16 then
          print(table.concat(line, " "))
          line = {}
        end
      end
      if #line > 0 then
        print(table.concat(line, " "))
      end
      print("SCREENSHOT_ARGB_END")
    end
  end
"""


@mcp.tool()
def run_with_input(
    input_schedule: list[dict],
    lua_code: str = "",
    screenshot_frame: int = 0,
    stop_frame: int = 0,
    timeout: int = 120,
) -> str:
    """Run the ROM with scheduled input injection and optional screenshot.

    Hooks _checkInputDevice RTS to inject button presses at specific frame
    ranges.  Auto-resolves hook and WRAM addresses from the sym file.

    Args:
        input_schedule: List of {"frames": [start, end], "buttons": "right+a"}.
                        Button names: a, b, x, y, l, r, up, down, left, right,
                        start, select.  Combine with '+'.
        lua_code: Optional Lua code with access to rd8/rd16/rd16s/frameCount
                  globals.  Runs inside the endFrame callback context.
        screenshot_frame: If >0, capture a screenshot at this frame.
        stop_frame: Frame at which to stop.  Defaults to screenshot_frame+10,
                    or last scheduled frame+200 if no screenshot.
        timeout: Max seconds to wait for Mesen.

    Returns:
        Printed output from the Lua script.  If screenshot_frame is set,
        returns the saved PNG path (same as take_screenshot).
    """
    sym_path = SYM_FILE
    if not sym_path.exists():
        return "ERROR: sym file not found. Run build_rom() first."

    # Resolve addresses from sym file
    check_input_addr = _lookup_sym_address(sym_path, "_checkInputDevice")
    if check_input_addr is None:
        return "ERROR: _checkInputDevice not found in sym file."
    hook_addr = check_input_addr + 0x1E  # RTS at end of function

    # Look up WRAM offsets (from $7E:0000) — NOT _lookup_sym_address which
    # adds a $C0 ROM prefix that corrupts WRAM addresses.
    press_off = _lookup_wram_offset(sym_path, "inputDevice.press")
    trigger_off = _lookup_wram_offset(sym_path, "inputDevice.trigger")
    old_off = _lookup_wram_offset(sym_path, "inputDevice.old")
    if None in (press_off, trigger_off, old_off):
        return "ERROR: inputDevice.press/trigger/old not found in sym file."

    # Parse schedule into Lua table entries
    schedule_lines = []
    last_frame = 0
    for entry in input_schedule:
        frames = entry.get("frames", [0, 0])
        buttons = entry.get("buttons", "")
        mask = _parse_buttons(buttons)
        if mask == 0:
            continue
        start_f, end_f = int(frames[0]), int(frames[1])
        schedule_lines.append(f"  {{{start_f}, {end_f}, 0x{mask:04X}}},")
        last_frame = max(last_frame, end_f)

    # Compute stop frame
    if stop_frame <= 0:
        if screenshot_frame > 0:
            stop_frame = screenshot_frame + 10
        else:
            stop_frame = last_frame + 200

    # Build screenshot logic
    if screenshot_frame > 0:
        ss_lua = _SCREENSHOT_CAPTURE_LUA.replace(
            "{screenshot_frame}", str(screenshot_frame)
        )
    else:
        ss_lua = ""

    # Assemble final Lua
    lua = _RUN_WITH_INPUT_LUA
    lua = lua.replace("{hook_addr}", f"0x{hook_addr:06X}")
    lua = lua.replace("{press_wram}", f"0x{press_off:04X}")
    lua = lua.replace("{trigger_wram}", f"0x{trigger_off:04X}")
    lua = lua.replace("{old_wram}", f"0x{old_off:04X}")
    lua = lua.replace("{schedule_entries}", "\n".join(schedule_lines))
    lua = lua.replace("{user_lua}", lua_code)
    lua = lua.replace("{screenshot_logic}", ss_lua)
    lua = lua.replace("{stop_frame}", str(stop_frame))

    # Write and execute
    script_path = SFC_DIR / "_mcp_run_with_input.lua"
    script_path.write_text(lua)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_run_with_input.lua > out.txt 2>&1'
    )

    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"MESEN TIMEOUT (>{timeout}s). Partial output:\n{_read_out_file(out_file)}"
    except Exception as e:
        return f"MESEN ERROR: {e}"

    # Read full output when screenshot expected (pixel data can be >500KB);
    # truncated read otherwise to keep MCP responses small.
    if screenshot_frame > 0:
        try:
            output = out_file.read_text()
        except Exception:
            output = ""
    else:
        output = _read_out_file(out_file)

    # If screenshot was requested, extract PNG same as take_screenshot
    if screenshot_frame > 0 and "SCREENSHOT_ARGB_START" in output and "SCREENSHOT_ARGB_END" in output:
        png_path = SFC_DIR / "screenshot.png"
        try:
            header_line = output[output.index("SCREENSHOT_ARGB_START"):].split("\n")[0]
            parts = header_line.split()
            width = int(parts[1]) if len(parts) > 1 else 256
            height = int(parts[2]) if len(parts) > 2 else 224

            start = output.index("SCREENSHOT_ARGB_START")
            start = output.index("\n", start) + 1
            end = output.index("SCREENSHOT_ARGB_END")
            argb_text = output[start:end].strip().split("\n")

            pixels = []
            for line in argb_text:
                for hex_val in line.strip().split():
                    pixels.append(int(hex_val, 16))

            # Strip overscan
            if height == 239:
                top, bot = 7, 8
                pixels = pixels[top * width : len(pixels) - bot * width]
                height = 224
            elif height == 478:
                top, bot = 14, 16
                pixels = pixels[top * width : len(pixels) - bot * width]
                height = 448

            argb_text = []
            line_vals = []
            for p in pixels:
                line_vals.append(f"{p:08X}")
                if len(line_vals) >= 16:
                    argb_text.append(" ".join(line_vals))
                    line_vals = []
            if line_vals:
                argb_text.append(" ".join(line_vals))

            png_bytes = _argb_to_png(width, height, argb_text)
            png_path.write_bytes(png_bytes)
            msg = f"Screenshot saved: {png_path} ({len(png_bytes)} bytes)\n\n"

            if _check_magenta_crash(pixels, width):
                msg += "[CRASH DETECTED -- magenta error screen]\n\n"

            # Append non-screenshot output
            clean_output = output[:output.index("SCREENSHOT_ARGB_START")].strip()
            if clean_output:
                msg += clean_output
            return msg
        except Exception as e:
            return f"ERROR converting screenshot: {e}\n\nRaw output:\n{output[-2000:]}"

    return output


if __name__ == "__main__":
    mcp.run()
