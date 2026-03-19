#!/usr/bin/env python3
"""bsnes MCP Server — automates sym lookup, build, test execution, and screenshots.

Uses the headless bsnes-test.exe testrunner (no Lua, CLI-based).
Runs on Windows Python (NOT WSL). Calls into WSL only for make.
"""

import json
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
BSNES_TEST = Path("E:/gh/bsnes/bsnes/out/bsnes-test.exe")
ROM_PATH = SFC_DIR / "SuperMonkeyIsland.sfc"

mcp = FastMCP("bsnes")


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

    Returns matching symbols with their addresses.
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
            results.append(f"  {name}: ${full:06X}")

    if not results:
        return f"No symbols matching '{pattern}' found."
    return f"Found {len(results)} match(es):\n" + "\n".join(results[:50])


@mcp.tool()
def lookup_symbols(symbols: list[str]) -> str:
    """Batch lookup multiple exact symbol names in the sym file."""
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
            results.append(f"  {sym}: ${full:06X}")
        else:
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

    try:
        ping = subprocess.run(
            ["wsl", "echo", "ok"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )
        if ping.returncode != 0 or "ok" not in ping.stdout:
            return f"BUILD ERROR: WSL not responsive (rc={ping.returncode})"
    except subprocess.TimeoutExpired:
        return "BUILD ERROR: WSL not responding (10s ping timeout)."
    except FileNotFoundError:
        return "BUILD ERROR: 'wsl' command not found."
    except Exception as e:
        return f"BUILD ERROR: WSL pre-flight failed: {e}"

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
        return f"BUILD TIMEOUT (>{timeout}s)."
    except Exception as e:
        return f"BUILD ERROR: {e}"


def _run_bsnes_test(args: list[str], timeout: int = 60) -> dict | str:
    """Run bsnes-test.exe with given args, return parsed JSON or error string."""
    if not BSNES_TEST.exists():
        return f"ERROR: bsnes-test.exe not found at {BSNES_TEST}"
    if not ROM_PATH.exists():
        return f"ERROR: ROM not found at {ROM_PATH}"

    cmd = [str(BSNES_TEST), str(ROM_PATH)] + args

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        stdout = result.stdout.strip()
        if not stdout:
            return f"ERROR: bsnes-test produced no output (rc={result.returncode}, stderr={result.stderr[:500]})"

        return json.loads(stdout)
    except json.JSONDecodeError as e:
        return f"ERROR: Failed to parse JSON: {e}\nOutput: {result.stdout[:1000]}"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT: bsnes-test exceeded {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _bmp_to_png(bmp_path: Path, png_path: Path) -> bool:
    """Convert BMP to PNG using minimal encoder (no Pillow dependency)."""
    data = bmp_path.read_bytes()
    if data[:2] != b"BM":
        return False

    offset = struct.unpack_from("<I", data, 10)[0]
    width = struct.unpack_from("<i", data, 18)[0]
    height = struct.unpack_from("<i", data, 22)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]

    # Handle top-down (negative height) BMPs
    top_down = height < 0
    height = abs(height)

    bytes_per_pixel = bpp // 8
    row_stride = width * bytes_per_pixel
    padded_stride = (row_stride + 3) & ~3

    # Build PNG raw scanlines (RGB, filter=0 per row)
    raw = bytearray()
    for y in range(height):
        if top_down:
            row_offset = offset + y * padded_stride
        else:
            row_offset = offset + (height - 1 - y) * padded_stride

        raw.append(0)  # filter byte
        for x in range(width):
            px_offset = row_offset + x * bytes_per_pixel
            b = data[px_offset]
            g = data[px_offset + 1]
            r = data[px_offset + 2]
            raw.extend((r, g, b))

    def png_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        chunk = chunk_type + chunk_data
        return struct.pack(">I", len(chunk_data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat_data = zlib.compress(bytes(raw))

    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", ihdr_data)
    png += png_chunk(b"IDAT", idat_data)
    png += png_chunk(b"IEND", b"")

    png_path.write_bytes(png)
    return True


@mcp.tool()
def take_screenshot(wait_frames: int = 800, timeout: int = 60) -> str:
    """Take a screenshot of the emulator at a specific frame.

    Args:
        wait_frames: Number of frames to run before capturing (default 800).
        timeout: Max seconds to wait for bsnes-test.
    """
    bmp_path = SFC_DIR / "bsnes_screenshot.bmp"
    png_path = SFC_DIR / "bsnes_screenshot.png"

    result = _run_bsnes_test(
        ["--frames", str(wait_frames), "--screenshot", str(bmp_path), "--watch-stp"],
        timeout=timeout,
    )

    if isinstance(result, str):
        return result

    if result.get("stp_detected"):
        msg = f"[CRASH DETECTED] STP at frame {result.get('stp_frame')}"
        if bmp_path.exists():
            if _bmp_to_png(bmp_path, png_path):
                msg += f"\nScreenshot (pre-crash): {png_path}"
        return msg

    if not bmp_path.exists():
        return f"ERROR: Screenshot BMP not created. JSON: {json.dumps(result)}"

    if _bmp_to_png(bmp_path, png_path):
        return f"Screenshot saved: {png_path} (frame {result.get('frames_run')})"
    return f"Screenshot BMP saved but PNG conversion failed: {bmp_path}"


@mcp.tool()
def run_test(
    frames: int = 500,
    read_mem: str = "",
    screenshot: bool = False,
    timeout: int = 60,
) -> str:
    """Run bsnes-test with custom options.

    Args:
        frames: Number of frames to run.
        read_mem: Memory read spec as 'ADDR:LEN' (hex), e.g. '7EEC12:1'.
                  Multiple reads separated by commas.
        screenshot: If True, capture a screenshot after running.
        timeout: Max seconds to wait.
    """
    args = ["--frames", str(frames), "--watch-stp"]

    if screenshot:
        bmp_path = SFC_DIR / "bsnes_test.bmp"
        args.extend(["--screenshot", str(bmp_path)])

    if read_mem:
        for spec in read_mem.split(","):
            spec = spec.strip()
            if spec:
                args.extend(["--read-mem", spec])

    result = _run_bsnes_test(args, timeout=timeout)

    if isinstance(result, str):
        return result

    lines = [f"Frames run: {result.get('frames_run')}"]

    if result.get("stp_detected"):
        lines.append(f"STP DETECTED at frame {result.get('stp_frame')}")
    else:
        lines.append("No crash detected")

    if result.get("memory"):
        lines.append("Memory:")
        for addr, val in result["memory"].items():
            lines.append(f"  ${addr}: {val}")

    cpu = result.get("cpu", {})
    if cpu:
        lines.append(f"CPU: PC=${cpu.get('pc','')} A=${cpu.get('a','')} "
                     f"X=${cpu.get('x','')} Y=${cpu.get('y','')} SP=${cpu.get('sp','')}")

    if screenshot and result.get("screenshot"):
        bmp = Path(result["screenshot"])
        png = bmp.with_suffix(".png")
        if bmp.exists() and _bmp_to_png(bmp, png):
            lines.append(f"Screenshot: {png}")

    return "\n".join(lines)


@mcp.tool()
def read_test_output(file: str = "bsnes_test_output.json") -> str:
    """Read previous bsnes test output from the distribution directory.

    Args:
        file: Output filename to read (default: bsnes_test_output.json).
    """
    path = SFC_DIR / file
    if not path.exists():
        return f"ERROR: {path} not found"
    return path.read_text()[-3000:]


def _run_boot_test() -> str:
    """Run the ROM in bsnes-test for 500 frames and check for STP."""
    result = _run_bsnes_test(
        ["--frames", "500", "--watch-stp"],
        timeout=60,
    )

    if isinstance(result, str):
        return f"Boot test: ERROR ({result})"

    if result.get("stp_detected"):
        return f"Boot test: CRASH -- STP at frame {result.get('stp_frame')}"

    frames = result.get("frames_run", 0)
    if frames >= 500:
        return "Boot test: PASS (500 frames, no STP hit)"

    return f"Boot test: INCOMPLETE ({frames} frames run)"


@mcp.tool()
def validate_rom(clean_build: bool = False) -> str:
    """Validate the ROM after build: bank 0 usage, BRK scan, and runtime boot test.

    Args:
        clean_build: If True, trigger a clean build first.

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

    lines = ["=== ROM Validation (bsnes) ==="]

    # Bank 0 usage check
    rom_data = rom_path.read_bytes()
    bank0 = rom_data[:0x8000]
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

    # Runtime boot test via bsnes-test
    lines.append(_run_boot_test())

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
