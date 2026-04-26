#!/usr/bin/env python3
"""SMI Workflow MCP Server — project-scoped build / test / sym workflow.

This is *not* the generic Mesen-MCP toolchain — that lives in the
Mesen2 fork's --mcp mode and is exposed via tools/mesen_inproc_bridge.py
under the `mesen-inproc` namespace.

This server is the SuperMonkeyIsland project's own one-shot workflow:
  - build_rom / validate_rom (wsl make + bank-0 / BRK / boot smoke)
  - run_test / run_lua_snippet (Mesen --testrunner mode, one-shot Lua)
  - run_with_input (input injection via testrunner)
  - take_screenshot / crop_screenshot (testrunner one-shot capture)
  - visual_regression_check (PNG diff against reference)
  - lookup_symbol / lookup_symbols (WLA-DX .sym scan)

Why a separate server: testrunner mode runs Mesen for a fixed number of
frames and exits, which is incompatible with the long-lived TCP MCP
mode. Both serve different debugging needs.

Runs on Windows Python (NOT WSL). Calls into WSL only for make.
"""

import re
import struct
import subprocess
import zlib
from collections import Counter
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from paths import PROJECT_ROOT, DISTRIBUTION, windows_to_wsl

PROJECT = PROJECT_ROOT
SFC_DIR = DISTRIBUTION
SYM_FILE = PROJECT / "build" / "SuperMonkeyIsland.sym"
MESEN = PROJECT / "mesen" / "Mesen.exe"

mcp = FastMCP("smi-workflow")


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
        f'SuperMonkeyIsland.sfc {script_name} > out.txt 2>NUL'
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
        f'SuperMonkeyIsland.sfc _mcp_snippet.lua > out.txt 2>NUL'
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
def read_test_output(file: str = "out.txt", head: int | None = None) -> str:
    """Read previous test output from the distribution directory.

    Args:
        file: Output filename to read (default: out.txt).
        head: If specified, return only the first N characters.
    """
    path = SFC_DIR / file
    if not path.exists():
        return f"ERROR: {path} not found"
    text = path.read_text()
    return text[:head] if head is not None else text


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



def _pixels_to_png(width: int, height: int, pixels: list[int]) -> bytes:
    """Convert a list of ARGB pixel ints to PNG bytes (RGB, no alpha)."""
    argb_lines = []
    line_vals = []
    for p in pixels:
        line_vals.append(f"{p:08X}")
        if len(line_vals) >= 16:
            argb_lines.append(" ".join(line_vals))
            line_vals = []
    if line_vals:
        argb_lines.append(" ".join(line_vals))
    return _argb_to_png(width, height, argb_lines)


def _capture_frame(
    wait_frames: int,
    lua_preamble: str = "",
    timeout: int = 60,
) -> tuple[list[int], int, int]:
    """Run Mesen, capture a frame, and return (pixels, width, height).

    Raises RuntimeError on failure.
    """
    lua_code = _SCREENSHOT_LUA.replace("{target_frame}", str(wait_frames))
    lua_code = lua_code.replace("{lua_preamble}", lua_preamble)

    script_path = SFC_DIR / "_mcp_screenshot.lua"
    script_path.write_text(lua_code)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_screenshot.lua > out.txt 2>NUL'
    )

    try:
        subprocess.run(f'cmd.exe /c "{cmd}"', shell=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"MESEN TIMEOUT (>{timeout}s). Partial output:\n{_read_out_file(out_file)}")
    except Exception as e:
        raise RuntimeError(f"MESEN ERROR: {e}")

    output = _strip_uninit_lines(out_file.read_text())

    if "SCREENSHOT_ARGB_START" not in output or "SCREENSHOT_ARGB_END" not in output:
        if "SCREENSHOT_ERROR" in output:
            raise RuntimeError(output)
        raise RuntimeError(f"No screenshot data in output.\n\n{output[-1000:]}")

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

    # Strip SNES overscan padding
    if height == 239:
        top, bot = 7, 8
        pixels = pixels[top * width : len(pixels) - bot * width]
        height = 224
    elif height == 478:
        top, bot = 14, 16
        pixels = pixels[top * width : len(pixels) - bot * width]
        height = 448

    return pixels, width, height


def _check_uniform_frame(pixels: list[int], threshold: float = 0.95) -> bool:
    """Return True if >threshold fraction of pixels are the same RGB color."""
    if not pixels:
        return True
    # Convert to RGB (strip alpha)
    rgb_pixels = [(p >> 16) & 0xFF | ((p >> 8) & 0xFF) << 8 | (p & 0xFF) << 16
                  for p in pixels]
    most_common_count = Counter(rgb_pixels).most_common(1)[0][1]
    return most_common_count / len(rgb_pixels) > threshold


def _crop_pixels(
    pixels: list[int], src_w: int, src_h: int,
    x: int, y: int, w: int, h: int,
) -> list[int]:
    """Crop a pixel array to a sub-region. Clamps to bounds."""
    x = max(0, min(x, src_w))
    y = max(0, min(y, src_h))
    w = min(w, src_w - x)
    h = min(h, src_h - y)
    cropped = []
    for row in range(y, y + h):
        start = row * src_w + x
        cropped.extend(pixels[start : start + w])
    return cropped


def _upscale_pixels(
    pixels: list[int], w: int, h: int, scale: int,
) -> tuple[list[int], int, int]:
    """Nearest-neighbor upscale by integer factor."""
    if scale <= 1:
        return pixels, w, h
    new_w = w * scale
    new_h = h * scale
    out = []
    for row in range(h):
        row_pixels = pixels[row * w : (row + 1) * w]
        scaled_row = []
        for p in row_pixels:
            scaled_row.extend([p] * scale)
        for _ in range(scale):
            out.extend(scaled_row)
    return out, new_w, new_h


def _png_to_rgb(png_path: Path) -> tuple[list[tuple[int, int, int]], int, int]:
    """Read a PNG file and return ([(r,g,b), ...], width, height).

    Supports 8-bit RGB and RGBA PNGs with filter type 0 (None) or basic
    filters. Uses zlib to decompress IDAT chunks.
    """
    data = png_path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a valid PNG file: {png_path}")

    # Parse chunks
    pos = 8
    ihdr = None
    idat_chunks = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        chunk_type = data[pos+4:pos+8]
        chunk_data = data[pos+8:pos+8+length]
        pos += 12 + length  # 4 len + 4 type + data + 4 crc

        if chunk_type == b"IHDR":
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if ihdr is None:
        raise ValueError("No IHDR chunk found")

    width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])
    if bit_depth != 8:
        raise ValueError(f"Unsupported bit depth: {bit_depth}")

    if color_type == 2:    # RGB
        bpp = 3
    elif color_type == 6:  # RGBA
        bpp = 4
    else:
        raise ValueError(f"Unsupported color type: {color_type}")

    raw = zlib.decompress(b"".join(idat_chunks))

    # Reconstruct scanlines (with PNG filtering)
    stride = width * bpp
    pixels = []
    prev_row = bytes(stride)

    for y in range(height):
        offset = y * (stride + 1)
        filter_type = raw[offset]
        scanline = bytearray(raw[offset + 1 : offset + 1 + stride])

        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                scanline[i] = (scanline[i] + scanline[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                scanline[i] = (scanline[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = scanline[i - bpp] if i >= bpp else 0
                scanline[i] = (scanline[i] + (left + prev_row[i]) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = scanline[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                up_left = prev_row[i - bpp] if i >= bpp else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                if pa <= pb and pa <= pc:
                    pred = left
                elif pb <= pc:
                    pred = up
                else:
                    pred = up_left
                scanline[i] = (scanline[i] + pred) & 0xFF

        prev_row = bytes(scanline)

        for x in range(width):
            r = scanline[x * bpp]
            g = scanline[x * bpp + 1]
            b = scanline[x * bpp + 2]
            pixels.append((r, g, b))

    return pixels, width, height


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
    output_name: str = "screenshot.png",
    timeout: int = 60,
) -> str:
    """Take a screenshot of the emulator at a specific PPU frame.

    Runs a Lua script in Mesen that waits until the target frame, captures the
    screen buffer, converts to PNG, and saves to the distribution directory.

    Args:
        wait_frames: PPU frame number at which to capture (default 800 = after boot + SCUMM init).
        lua_preamble: Optional Lua code inserted before the screenshot logic
                      (e.g., input injection for room cycling).
        output_name: Output filename (default: screenshot.png). Saved in distribution/.
        timeout: Max seconds to wait for Mesen.

    Returns:
        Path to the saved PNG file, or an error message.
    """
    png_path = SFC_DIR / output_name

    for attempt in range(2):
        try:
            pixels, width, height = _capture_frame(wait_frames, lua_preamble, timeout)
        except RuntimeError as e:
            return str(e)

        try:
            png_bytes = _pixels_to_png(width, height, pixels)

            # Retry once if file is suspiciously small (black frame bug)
            if len(png_bytes) < 500 and attempt == 0:
                continue

            png_path.write_bytes(png_bytes)
            msg = f"Screenshot saved: {png_path} ({len(png_bytes)} bytes, from ARGB buffer)"

            # Check for magenta error screen
            if _check_magenta_crash(pixels, width):
                msg += "\n[CRASH DETECTED -- magenta error screen]"

            # Check for uniform/blank frame
            if _check_uniform_frame(pixels):
                msg += "\n[WARNING: frame appears blank or uniform -- possible timing issue]"

            return msg
        except Exception as e:
            return f"ERROR converting ARGB to PNG: {e}"

    # Both attempts produced tiny files
    png_path.write_bytes(png_bytes)
    return f"Screenshot saved: {png_path} ({len(png_bytes)} bytes) [WARNING: file unusually small, possible black frame]"


@mcp.tool()
def crop_screenshot(
    wait_frames: int = 800,
    x: int = 0,
    y: int = 0,
    width: int = 256,
    height: int = 224,
    scale: int = 1,
    output_name: str = "crop.png",
    lua_preamble: str = "",
    timeout: int = 60,
) -> str:
    """Take a screenshot, crop to a pixel region, optionally upscale, and save.

    Useful for zooming in on specific screen areas (e.g., verb bar boundary,
    palette bleed, tile seams).

    Args:
        wait_frames: PPU frame to capture at (default 800).
        x: Left edge of crop region in pixels.
        y: Top edge of crop region in pixels.
        width: Width of crop region in pixels.
        height: Height of crop region in pixels.
        scale: Nearest-neighbor upscale factor (1 = no upscale).
        output_name: Output filename (saved in distribution/).
        lua_preamble: Optional Lua code inserted before screenshot logic.
        timeout: Max seconds to wait for Mesen.

    Returns:
        Path to saved PNG and dimensions, or error message.
    """
    try:
        pixels, src_w, src_h = _capture_frame(wait_frames, lua_preamble, timeout)
    except RuntimeError as e:
        return str(e)

    try:
        # Crop
        cropped = _crop_pixels(pixels, src_w, src_h, x, y, width, height)
        crop_w = min(width, src_w - max(0, x))
        crop_h = min(height, src_h - max(0, y))

        # Upscale
        final_pixels, final_w, final_h = _upscale_pixels(cropped, crop_w, crop_h, scale)

        png_bytes = _pixels_to_png(final_w, final_h, final_pixels)
        png_path = SFC_DIR / output_name
        png_path.write_bytes(png_bytes)
        return (
            f"Cropped screenshot saved: {png_path}\n"
            f"  Source: {src_w}x{src_h}, Region: ({x},{y}) {crop_w}x{crop_h}, "
            f"Scale: {scale}x, Final: {final_w}x{final_h}, Size: {len(png_bytes)} bytes"
        )
    except Exception as e:
        return f"ERROR in crop_screenshot: {e}"


@mcp.tool()
def visual_regression_check(
    reference_path: str,
    wait_frames: int = 800,
    region: list[int] | None = None,
    tolerance: int = 0,
    output_name: str = "diff.png",
    lua_preamble: str = "",
    timeout: int = 60,
) -> str:
    """Compare a new screenshot against a reference image. Report pixel differences.

    Takes a fresh screenshot and compares it pixel-by-pixel against a reference
    PNG. Generates a diff image highlighting differences in red.

    Args:
        reference_path: Path to the reference PNG image.
        wait_frames: PPU frame to capture at (default 800).
        region: Optional [x, y, w, h] crop region to compare. None = full frame.
        tolerance: Per-channel tolerance for color matching (0 = exact match).
        output_name: Filename for the diff image (saved in distribution/).
        lua_preamble: Optional Lua code inserted before screenshot logic.
        timeout: Max seconds to wait for Mesen.

    Returns:
        PASS/FAIL verdict, diff stats, and path to diff image.
    """
    ref_path = Path(reference_path)
    if not ref_path.exists():
        return f"ERROR: Reference image not found: {ref_path}"

    # Load reference image
    try:
        ref_pixels, ref_w, ref_h = _png_to_rgb(ref_path)
    except Exception as e:
        return f"ERROR reading reference PNG: {e}"

    # Capture new frame
    try:
        new_argb, new_w, new_h = _capture_frame(wait_frames, lua_preamble, timeout)
    except RuntimeError as e:
        return str(e)

    # Convert ARGB ints to (r,g,b) tuples
    new_pixels = [
        ((p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF)
        for p in new_argb
    ]

    # Apply region crop if specified
    if region is not None and len(region) == 4:
        rx, ry, rw, rh = region

        # Crop reference
        ref_cropped = []
        for row in range(ry, min(ry + rh, ref_h)):
            for col in range(rx, min(rx + rw, ref_w)):
                ref_cropped.append(ref_pixels[row * ref_w + col])
        ref_pixels = ref_cropped
        ref_cw = min(rw, ref_w - rx)
        ref_ch = min(rh, ref_h - ry)

        # Crop new
        new_cropped = []
        for row in range(ry, min(ry + rh, new_h)):
            for col in range(rx, min(rx + rw, new_w)):
                new_cropped.append(new_pixels[row * new_w + col])
        new_pixels = new_cropped
        new_cw = min(rw, new_w - rx)
        new_ch = min(rh, new_h - ry)

        cmp_w, cmp_h = min(ref_cw, new_cw), min(ref_ch, new_ch)
    else:
        cmp_w, cmp_h = min(ref_w, new_w), min(ref_h, new_h)

    if len(ref_pixels) == 0 or len(new_pixels) == 0:
        return "ERROR: One or both images have zero pixels after cropping."

    # Compare pixel-by-pixel and build diff image
    total_pixels = min(len(ref_pixels), len(new_pixels))
    diff_count = 0
    diff_argb = []

    for i in range(total_pixels):
        rr, rg, rb = ref_pixels[i]
        nr, ng, nb = new_pixels[i]

        if (abs(rr - nr) <= tolerance and
            abs(rg - ng) <= tolerance and
            abs(rb - nb) <= tolerance):
            # Match — dim the pixel (25% brightness)
            dr = rr // 4
            dg = rg // 4
            db = rb // 4
            diff_argb.append(0xFF000000 | (dr << 16) | (dg << 8) | db)
        else:
            # Difference — highlight in red
            diff_count += 1
            diff_argb.append(0xFFFF0000)

    pct = (diff_count / total_pixels * 100) if total_pixels > 0 else 0
    verdict = "PASS" if diff_count == 0 else "FAIL"

    # Save diff image
    try:
        png_bytes = _pixels_to_png(cmp_w, cmp_h, diff_argb)
        diff_path = SFC_DIR / output_name
        diff_path.write_bytes(png_bytes)
    except Exception as e:
        return f"ERROR generating diff image: {e}"

    return (
        f"{verdict}: {diff_count}/{total_pixels} pixels differ ({pct:.2f}%)\n"
        f"  Reference: {ref_path} ({ref_w}x{ref_h})\n"
        f"  Captured: {new_w}x{new_h}\n"
        f"  Compared region: {cmp_w}x{cmp_h}\n"
        f"  Tolerance: {tolerance}\n"
        f"  Diff image: {diff_path}"
    )


def _strip_uninit_lines(text: str) -> str:
    """Remove Mesen '[CPU] Uninitialized memory read' noise from output."""
    return "\n".join(
        line for line in text.split("\n")
        if not line.startswith("[CPU] Uninitialized memory read")
    )


def _extract_capture_blocks(text: str, prefix: str) -> tuple[str, list[str]]:
    """Pull all CAPTURE_ARGB_START/END blocks out of `text`, write each
    as a separate PNG file under SFC_DIR, return (residual_text, summaries).

    Block format from _RUN_WITH_INPUT_LUA:
        CAPTURE_ARGB_START <index> <width> <height>
        <ARGB hex words, 16 per line>
        CAPTURE_ARGB_END <index>

    Each saved as `<prefix>_NNN.png` (NNN = 3-digit 1-based index).
    """
    summaries: list[str] = []
    out_lines: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("CAPTURE_ARGB_START"):
            parts = line.split()
            try:
                idx = int(parts[1])
                w = int(parts[2])
                h = int(parts[3])
            except (IndexError, ValueError):
                out_lines.append(line)
                i += 1
                continue
            # Collect ARGB lines until END marker.
            argb_lines: list[str] = []
            j = i + 1
            end_marker = f"CAPTURE_ARGB_END {idx}"
            while j < len(lines) and lines[j].strip() != end_marker:
                argb_lines.append(lines[j])
                j += 1
            # Skip past END line (or just stop if EOF).
            try:
                pixels: list[int] = []
                for argb_line in argb_lines:
                    for hex_val in argb_line.strip().split():
                        if hex_val:
                            pixels.append(int(hex_val, 16))
                # Strip overscan rows (matches take_screenshot path).
                actual_h = h
                if h == 239:
                    pixels = pixels[7 * w : len(pixels) - 8 * w]
                    actual_h = 224
                elif h == 478:
                    pixels = pixels[14 * w : len(pixels) - 16 * w]
                    actual_h = 448
                argb_text: list[str] = []
                line_vals: list[str] = []
                for p in pixels:
                    line_vals.append(f"{p:08X}")
                    if len(line_vals) >= 16:
                        argb_text.append(" ".join(line_vals))
                        line_vals = []
                if line_vals:
                    argb_text.append(" ".join(line_vals))
                png_bytes = _argb_to_png(w, actual_h, argb_text)
                png_path = SFC_DIR / f"{prefix}_{idx:03d}.png"
                png_path.write_bytes(png_bytes)
                summaries.append(f"{png_path} ({len(png_bytes)} bytes)")
            except Exception as e:
                summaries.append(f"<capture {idx} failed: {e}>")
            i = j + 1
            continue
        out_lines.append(line)
        i += 1
    return "\n".join(out_lines), summaries


def _dedupe_consecutive(text: str) -> str:
    """Collapse runs of identical consecutive lines.

    Mesen prints a "Registered memory callback from $X to $Y" line for
    every addMemoryCallback registration. If the user puts the
    registration in lua_code (which runs every frame) instead of
    lua_init (one-shot), the output gets flooded with thousands of
    identical lines and overflows the MCP token limit. This collapser
    is a safety net — same line repeated more than 3× becomes
    "<line>  [x N]" so a flooded run still returns useful output.
    """
    lines = text.split("\n")
    if not lines:
        return text
    out: list[str] = []
    prev = None
    count = 0
    for line in lines:
        if line == prev:
            count += 1
        else:
            if prev is not None:
                if count > 3:
                    out.append(f"{prev}  [x {count}]")
                else:
                    out.extend([prev] * count)
            prev = line
            count = 1
    if prev is not None:
        if count > 3:
            out.append(f"{prev}  [x {count}]")
        else:
            out.extend([prev] * count)
    return "\n".join(out)


def _read_out_file(path: Path, head: int | None = None) -> str:
    """Safely read an output file. Returns full contents, or first `head` chars if specified."""
    try:
        text = _strip_uninit_lines(path.read_text())
        return text[:head] if head is not None else text
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
        f'SuperMonkeyIsland.sfc _mcp_boot_test.lua > out.txt 2>NUL'
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
#               {schedule_entries}, {lua_init}, {user_lua}, {screenshot_logic},
#               {stop_frame}
_RUN_WITH_INPUT_LUA = r"""
-- Helpers available to user lua_code AND lua_init.
-- Use emu.memType.snesMemory so callers pass the full $7Exxxx CPU address
-- (matching what lookup_symbol returns). Reading through the SNES bus
-- routes through SnesMemoryManager::Peek, which short-circuits $7E/$7F
-- direct to _workRam — so SA-1 ROMs get correct WRAM bytes too. Passing
-- a $7Exxxx address to memType.snesWorkRam silently misreads (it expects
-- a 0..$1FFFF offset into the 128KB WRAM array, not a CPU address).
local function rd8(a) return emu.read(a, emu.memType.snesMemory, false) end
local function rd16(a)
  return emu.read(a, emu.memType.snesMemory, false)
       + emu.read(a+1, emu.memType.snesMemory, false) * 256
end
local function rd16s(a)
  local v = rd16(a)
  if v >= 32768 then v = v - 65536 end
  return v
end

-- CPU + PPU snapshot helpers. emu.getState() returns a FLAT dictionary
-- with dotted keys ("cpu.a", not nested cpu.a) — these wrap that surprise.
-- Use inside hook callbacks or every-frame lua_code.
local function cpu()
  local s = emu.getState()
  return {
    a  = s["cpu.a"],  x  = s["cpu.x"],  y  = s["cpu.y"],
    pc = s["cpu.pc"], k  = s["cpu.k"],  sp = s["cpu.sp"],
    p  = s["cpu.p"],  db = s["cpu.db"], d  = s["cpu.d"],
  }
end
local function ppu()
  local s = emu.getState()
  return {
    frameCount = s["ppu.frameCount"],
    cycle      = s["ppu.cycle"],
    scanline   = s["ppu.scanline"],
  }
end

-- Address-formatting convenience for trace prints.
local function fmtA(v) return string.format("$%04X", v & 0xFFFF) end
local function fmt24(v) return string.format("$%06X", v & 0xFFFFFF) end

local frameCount = 0

-- Input schedule: list of {startFrame, endFrame, buttonMask}
local _schedule = {
{schedule_entries}
}

local _lastButtons = 0

local function _wr16(addr, val)
  -- Internal-only. The press_wram / trigger_wram placeholders are
  -- pre-stripped to 0..$1FFFF offsets by Python's _lookup_wram_offset,
  -- so memType.snesWorkRam is correct here. Do NOT switch to snesMemory
  -- without converting addr to a $7Exxxx CPU form.
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

-- One-shot init: runs ONCE, before the frame loop starts. Use this for
-- emu.addMemoryCallback / emu.addEventCallback registrations that must
-- not fire every frame. Anything in lua_code (below) runs every frame.
{lua_init}

-- Update frameCount FIRST (registered before user code so it fires first)
emu.addEventCallback(function()
  frameCount = emu.getState()["ppu.frameCount"]
end, emu.eventType.endFrame)

-- User-supplied Lua code (runs each frame in endFrame callback)
emu.addEventCallback(function()
{user_lua}
end, emu.eventType.endFrame)

-- Multi-frame capture (capture_frames=[...]). Empty list → no-op.
local _captureFramesList = { {capture_frames_list} }
local _captureFramesDone = {}
emu.addEventCallback(function()
  for i, f in ipairs(_captureFramesList) do
    if not _captureFramesDone[i] and frameCount >= f then
      _captureFramesDone[i] = true
      local buf = emu.getScreenBuffer()
      if buf and #buf > 0 then
        local size = emu.getScreenSize()
        local w = size.width or 256
        local h = size.height or 224
        print(string.format("CAPTURE_ARGB_START %d %d %d", i, w, h))
        local line = {}
        for j = 1, #buf do
          line[#line + 1] = string.format("%08X", buf[j])
          if #line >= 16 then
            print(table.concat(line, " "))
            line = {}
          end
        end
        if #line > 0 then
          print(table.concat(line, " "))
        end
        print(string.format("CAPTURE_ARGB_END %d", i))
      end
    end
  end
end, emu.eventType.endFrame)

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
    lua_init: str = "",
    screenshot_frame: int = 0,
    capture_frames: list[int] | None = None,
    capture_prefix: str = "capture",
    stop_frame: int = 0,
    timeout: int = 120,
) -> str:
    """Run the ROM with scheduled input injection and optional screenshot.

    Hooks _checkInputDevice RTS to inject button presses at specific frame
    ranges. Auto-resolves hook and WRAM addresses from the sym file.

    Args:
        input_schedule: List of {"frames": [start, end], "buttons": "right+a"}.
                        Button names: a, b, x, y, l, r, up, down, left, right,
                        start, select. Combine with '+'.
        lua_code: Optional Lua code that runs EVERY FRAME inside an
                  emu.eventType.endFrame callback. Has access to
                  rd8/rd16/rd16s/cpu()/ppu()/fmtA/fmt24 helpers and the
                  `frameCount` global. Use this for per-frame state probes
                  ("if frameCount == 2200 then print(...) end").
        lua_init: Optional Lua code that runs EXACTLY ONCE before the frame
                  loop starts. Use this for emu.addMemoryCallback /
                  emu.addEventCallback registrations — putting them in
                  lua_code re-registers every frame and floods the output
                  with "Registered memory callback" lines (and creates
                  duplicate hooks).
        screenshot_frame: If >0, capture a screenshot at this frame.
        capture_frames: If non-empty, capture a screenshot at each frame in
                  the list within ONE Mesen run. Useful for filmstrip /
                  visual_compare workflows that want N frames without N
                  Mesen invocations. Each PNG is named
                  `<capture_prefix>_NNN.png` (NNN = 1-based index, 3-digit).
        capture_prefix: Filename prefix for capture_frames PNGs. Default
                  "capture" produces capture_001.png, capture_002.png, ...
        stop_frame: Frame at which to stop. Defaults to (max of
                    screenshot_frame, last capture_frame, last input frame)
                    + 10..200 padding.
        timeout: Max seconds to wait for Mesen.

    Returns:
        Printed output from the Lua script. If screenshot_frame is set,
        returns the saved PNG path (same as take_screenshot). Mesen's
        boilerplate "Registered memory callback" lines are deduplicated
        in the returned text — same line repeated N times collapses to
        "<line>  [x N]".

    Reading register state at hook time:
        Inside an emu.addMemoryCallback (registered via lua_init), use the
        cpu() helper for a typed snapshot: `local r = cpu(); print(r.a, r.pc)`.
        emu.getState() returns a FLAT dict with dotted keys ("cpu.a") — not
        nested tables. cpu()/ppu() wrap that surprise.
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

    # Normalize capture_frames into a sorted unique list; reject huge sets
    # (every captured frame writes ~400KB of ARGB to stdout).
    capture_list: list[int] = sorted(set(int(f) for f in (capture_frames or []) if int(f) > 0))
    if len(capture_list) > 16:
        return f"ERROR: capture_frames has {len(capture_list)} entries; max 16 per call."

    # Compute stop frame: pad past whichever capture/screenshot fires last.
    if stop_frame <= 0:
        latest = max(
            screenshot_frame,
            capture_list[-1] if capture_list else 0,
            last_frame,
        )
        if latest == 0:
            stop_frame = 200
        elif latest == last_frame and screenshot_frame == 0 and not capture_list:
            stop_frame = last_frame + 200
        else:
            stop_frame = latest + 10

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
    lua = lua.replace("{lua_init}", lua_init)
    lua = lua.replace("{user_lua}", lua_code)
    lua = lua.replace("{screenshot_logic}", ss_lua)
    lua = lua.replace("{capture_frames_list}", ", ".join(str(f) for f in capture_list))
    lua = lua.replace("{stop_frame}", str(stop_frame))

    # Write and execute
    script_path = SFC_DIR / "_mcp_run_with_input.lua"
    script_path.write_text(lua)

    out_file = SFC_DIR / "out.txt"
    cmd = (
        f'cd /d "{SFC_DIR}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _mcp_run_with_input.lua > out.txt 2>NUL'
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

    # Read full output when screenshot or capture_frames expected (pixel
    # data can be >500KB per frame); truncated read otherwise to keep
    # MCP responses small.
    if screenshot_frame > 0 or capture_list:
        try:
            output = _strip_uninit_lines(out_file.read_text())
        except Exception:
            output = ""
    else:
        output = _read_out_file(out_file)

    # Extract any CAPTURE_ARGB blocks into per-index PNG files. Strip
    # them from `output` so the dedup/return path sees a clean text body.
    capture_results: list[str] = []
    if capture_list:
        output, capture_results = _extract_capture_blocks(output, capture_prefix)

    # Collapse repeated lines (e.g. Mesen's "Registered memory callback"
    # spam if the user accidentally puts addMemoryCallback in lua_code
    # instead of lua_init).
    output = _dedupe_consecutive(output)

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
            if capture_results:
                msg += "\n\nCaptures (capture_frames):\n  " + "\n  ".join(capture_results)
            return msg
        except Exception as e:
            return f"ERROR converting screenshot: {e}\n\nRaw output:\n{output[-2000:]}"

    if capture_results:
        head = "Captures (capture_frames):\n  " + "\n  ".join(capture_results)
        return head + ("\n\n" + output if output.strip() else "")
    return output


if __name__ == "__main__":
    mcp.run()
