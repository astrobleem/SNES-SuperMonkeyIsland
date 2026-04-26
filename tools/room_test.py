"""Room load test harness -- load any room in the SCUMM engine via WRAM poke,
let processRoomChange run the real flow, settle, dump state + screenshot.

Usage:
    python3 tools/room_test.py 33
    python3 tools/room_test.py 33 -x 346 -y 133
    python3 tools/room_test.py 38 --settle 300 --out dock_check.png

Every run regenerates a Lua harness from the current build/SuperMonkeyIsland.sym
so addresses survive rebuilds. Output lands in distribution/:
    _room_harness.lua       generated script
    room_harness_out.txt    stdout (includes [harness] state lines)
    <out>.png               screenshot at settle point
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "distribution"
MESEN = ROOT / "mesen" / "Mesen.exe"
SYM = ROOT / "build" / "SuperMonkeyIsland.sym"


def _parse_sym(pattern: str) -> int | None:
    """Return the $xxxxxx hex address of the first symbol matching pattern
    (exact name match, not regex). Reads build sym file."""
    # sym format: "BBBB:OOOOOO NAME" where bank is 4 digits and offset is 1-6
    # digits. WRAM symbols already encode the $7E bank in the offset
    # (e.g. "0000:7ef941 SCUMM.running"), so the returned address is just
    # int(offset, 16) -- drop the bank prefix.
    rx = re.compile(
        r"^\s*[0-9A-Fa-f]{4}:([0-9A-Fa-f]+)\s+" + re.escape(pattern) + r"\s*$"
    )
    for line in SYM.read_text().splitlines():
        m = rx.match(line)
        if m:
            return int(m.group(1), 16)
    return None


def resolve_symbols() -> dict[str, int]:
    names = [
        "SCUMM.running",
        "SCUMM.currentRoom",
        "SCUMM.newRoom",
        "SCUMM.cutsceneNest",
        "SCUMM.actors.1",
        "SCUMM.globalVars",
        "inputDevice.press",
        "inputDevice.trigger",
        "inputDevice.old",
        "_checkInputDevice",
    ]
    syms: dict[str, int] = {}
    for n in names:
        v = _parse_sym(n)
        if v is None:
            sys.exit(f"sym not found: {n} -- run build first")
        syms[n] = v
    return syms


LUA_TEMPLATE = r"""-- Auto-generated room harness. Do not edit by hand.
-- ROUTE: sequence of {room, x, y, settle_frames}. Last entry is the target.
-- Intermediate entries advance through intervening rooms so tests can
-- reproduce transition-path bugs (e.g. 96->33).
-- NATURAL_MODE = 1: skip pokes, press START through intro windows, screenshot
-- at SHOT_AT_FRAME. Reproduces the true natural-flow path where scripts
-- drive transitions (slower but faithful).
local ROUTE         = {{route}}
local SETTLE        = {{settle}}
local NATURAL_MODE  = {{natural_mode}}
local SHOT_AT_FRAME = {{shot_at_frame}}

local SM = emu.memType.snesMemory
local function r8(a)  return emu.read(a, SM) end
local function r16(a) return r8(a) | (r8(a+1) << 8) end
local function w8(a, v)  emu.write(a, v, SM) end
local function w16(a, v)
  w8(a,     v & 0xFF)
  w8(a+1, (v >> 8) & 0xFF)
end

local CURRENT_ROOM = {{sym_current_room}}
local NEW_ROOM     = {{sym_new_room}}
local RUNNING      = {{sym_running}}
local ACTOR1       = {{sym_actor1}}
local CUTSCENE_NEST = {{sym_cutscene_nest}}
local GLOBAL_VARS  = {{sym_global_vars}}
local INP_PRESS    = {{sym_inp_press}}
local INP_TRIG     = {{sym_inp_trig}}
local INP_OLD      = {{sym_inp_old}}
local HOOK_ADDR    = {{hook_addr}}
local FORCE_CUTSCENE = {{force_cutscene}}
local ZERO_EGO       = {{zero_ego}}
local BOOT_MIN_FRAMES = {{boot_min_frames}}
local FORCE_CAM      = {{force_cam}}

local BTN_START = 0x1000

local fc = 0
local state = "boot"
local poke_frame = -1
local shot_frame = -1
local settle_until = -1
local last_inp = 0
local leg = 1                -- current ROUTE index (1-based)

-- Hook the RTS of _checkInputDevice to inject controller state via WRAM.
-- Mirrors the mechanism in tools/smi_workflow_server.py run_with_input.
emu.addMemoryCallback(function()
  -- In natural mode, inject START throughout to drive script transitions.
  -- In poke mode, only inject during boot to reach the SCUMM VM ready state.
  if state ~= "boot" and NATURAL_MODE == 0 then return end
  local want = 0
  local windows = {{input_windows}}
  for _, w in ipairs(windows) do
    if fc >= w[1] and fc <= w[2] then want = BTN_START; break end
  end
  w16(INP_PRESS, want)
  local trig = want & (~last_inp & 0xFFFF)
  w16(INP_TRIG, trig)
  w16(INP_OLD, want)
  last_inp = want
end, emu.callbackType.exec, HOOK_ADDR)

local function dumpScreenshot()
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
    if #line > 0 then print(table.concat(line, " ")) end
    print("SCREENSHOT_ARGB_END")
  end
end

local function rvramByte(addr)
  return emu.read(addr, emu.memType.videoRam)
end

local function dumpWramTilemap()
  -- SCROLL_TILEMAP_WRAM @ $7F0000, column-major. For a 1008×144 room:
  -- 126 cols × 18 rows × 2B = 4536 bytes. Stride per col = 18×2 = 36 bytes.
  -- Dump col 47 (where the corrupt camera sits) row 0..17.
  local base = 0x7F0000 + 47 * 36
  local line = "[harness] wram-col47 rows:"
  for row = 0, 17 do
    local lo = emu.read(base + row * 2, emu.memType.snesMemory)
    local hi = emu.read(base + row * 2 + 1, emu.memType.snesMemory)
    line = line .. string.format(" %04x", (hi << 8) | lo)
  end
  print(line)
end

local function dumpRing()
  -- SCROLL_SLOT2TILE_WRAM @ $7F4000. Each slot = 2 bytes (tile ID, $FFFF = free).
  -- Check: how many slots are occupied? How many holes? What's the tile ID at
  -- the highest occupied slot?
  local free = 0
  local holes_before_first_free = 0
  local seen_free = false
  for slot = 0, 895 do
    local lo = emu.read(0x7F4000 + slot * 2, emu.memType.snesMemory)
    local hi = emu.read(0x7F4000 + slot * 2 + 1, emu.memType.snesMemory)
    local tid = (hi << 8) | lo
    if tid == 0xFFFF then
      free = free + 1
      seen_free = true
    else
      if seen_free then
        holes_before_first_free = holes_before_first_free + 1
      end
    end
  end
  print(string.format("[harness] ring: %d/896 free, %d occupied-after-first-free",
    free, holes_before_first_free))
end

local function dumpCgram()
  -- 16 palettes × 16 colors × 2 bytes = 512 bytes CGRAM
  -- BG1 room art typically uses palettes 1..5 (pal 0 = UI reserved)
  for pal = 0, 7 do
    local line = string.format("[harness] pal%d:", pal)
    for c = 0, 7 do
      local lo = emu.read(pal * 32 + c * 2, emu.memType.cgRam)
      local hi = emu.read(pal * 32 + c * 2 + 1, emu.memType.cgRam)
      line = line .. string.format(" %04x", (hi << 8) | lo)
    end
    print(line)
  end
end

local function dumpBg3Sample()
  -- BG3 tilemap at VRAM word $4C00 = byte $9800. Dump first 16 entries.
  -- Dialog text (when visible) populates this as 16-bit tilemap words.
  local line = "[harness] bg3tm[row0]:"
  for col = 0, 15 do
    local lo = emu.read(0x9800 + col * 2, emu.memType.videoRam)
    local hi = emu.read(0x9800 + col * 2 + 1, emu.memType.videoRam)
    line = line .. string.format(" %04x", (hi << 8) | lo)
  end
  print(line)
  -- Also dump the WRAM-side source (SCUMM.dialogTilemap @ $7EAB9E) so we
  -- can tell whether the problem is "source data wrong" or "source=zero
  -- but DMA did not fire". WRAM reads are reliable.
  local wl = "[harness] bg3src[row0]:"
  for col = 0, 15 do
    wl = wl .. string.format(" %04x", r16(0x7EAB9E + col * 2))
  end
  print(wl)
  print(string.format("[harness] bg3dma: pending=%d nmiPending=%d",
    r8(0x7EAB95), r8(0x7EAB9A)))
end

local function dumpTilemapSample()
  -- BG1 tilemap lives at VRAM word $1800 (= byte $3000). Each word is a
  -- tilemap entry for a specific (col, row). Dump first 16 columns of
  -- the top row to see what tile IDs BG1 is actually referencing.
  local line = "[harness] bg1tm[row0]:"
  local tid_first = nil
  for col = 0, 15 do
    local lo = rvramByte(0x3000 + col * 2)
    local hi = rvramByte(0x3000 + col * 2 + 1)
    local word = (hi << 8) | lo
    line = line .. string.format(" %04x", word)
    if tid_first == nil and (word & 0x07FF) ~= 0 then
      tid_first = word & 0x07FF
    end
  end
  print(line)
  -- Dump the first non-blank tile's pixel data (32 bytes at VRAM tile_id * 32).
  -- Helps spot when the ring buffer slot holds stale data.
  if tid_first then
    local base = tid_first * 32
    local line2 = string.format("[harness] tile[%03x]:", tid_first)
    for i = 0, 15 do
      line2 = line2 .. string.format(" %02x", rvramByte(base + i))
    end
    print(line2)
  end
end

local function dumpState(tag)
  print(string.format("[harness] %s curRoom=%d new=%d cut=%d VAR_EGO=%d cam=%d xSc=%d ySc=%d cacheNextSlot=%d cacheMiss=%d",
    tag,
    r8(CURRENT_ROOM), r8(NEW_ROOM), r8(CUTSCENE_NEST),
    r8(GLOBAL_VARS + 1 * 2),
    r16(0x7EFA1F),   -- GLOBAL.room.cameraX
    r16(0x7EFD54),   -- xScrollBG1
    r16(0x7EFD56),   -- yScrollBG1
    r16(0x7EFA60),   -- GLOBAL.room.cacheNextSlot
    r16(0x7EFA62)))  -- GLOBAL.room.cacheMissCount
  for ai = 1, 4 do
    local base = ACTOR1 + (ai - 1) * 16
    print(string.format("  a%d: rm=%d cos=%d x=%d y=%d fac=%d vis=%d",
      ai,
      r8(base + 0), r8(base + 1),
      r16(base + 2), r16(base + 4), r16(base + 6), r8(base + 11)))
  end
  dumpCgram()
  dumpRing()
  dumpWramTilemap()
  dumpBg3Sample()
end

emu.addEventCallback(function()
  fc = fc + 1

  if NATURAL_MODE == 1 then
    if fc >= SHOT_AT_FRAME then
      dumpState(string.format("f=%d [NATURAL]", fc))
      dumpScreenshot()
      emu.stop(0)
    end
    return
  end

  if state == "boot" then
    -- Wait until the SCUMM VM is running. Accept any currentRoom -- we will
    -- poke ours regardless. Minimum fc gives the intro scripts enough time
    -- to initialize globals but not to advance into Part One etc.
    if r16(RUNNING) ~= 0 and fc > BOOT_MIN_FRAMES then
      state = "poke"
      print(string.format("[harness] f=%d SCUMM up, curRoom=%d -- ready to poke", fc, r8(CURRENT_ROOM)))
    end
  elseif state == "poke" then
    -- Seed actor 1 (Guybrush) position + visibility, then request the
    -- current ROUTE leg's room change.
    local entry = ROUTE[leg]
    local rm, x, y = entry[1], entry[2], entry[3]
    if ZERO_EGO == 1 and leg == #ROUTE then
      -- Simulate natural-flow state: leave actor 1 fully zeroed, place
      -- another actor (actor 2) at the target coords instead.
      w8(ACTOR1 + 0, 0)
      w16(ACTOR1 + 2, 0)
      w16(ACTOR1 + 4, 0)
      w8(ACTOR1 + 11, 0)
      local A2 = ACTOR1 + 16
      w8(A2 + 0, rm)
      w8(A2 + 1, 1)                -- costume 1 (Guybrush)
      w16(A2 + 2, 1)               -- x=1 to mirror natural flow
      w16(A2 + 4, y)
      w16(A2 + 6, 270)             -- facing west
      w8(A2 + 11, 1)
    else
      w8(ACTOR1 + 0, rm)
      w16(ACTOR1 + 2, x)
      w16(ACTOR1 + 4, y)
      w8(ACTOR1 + 11, 1)
    end
    -- Optional cutscene context: set cutsceneNest before transition so
    -- processRoomChange sees the same nest depth as a script-driven load.
    if FORCE_CUTSCENE > 0 and leg == #ROUTE then
      w8(CUTSCENE_NEST, FORCE_CUTSCENE)
    end
    w8(NEW_ROOM, rm)
    poke_frame = fc
    state = "wait"
    print(string.format("[harness] f=%d leg %d/%d poked newRoom=%d actor1=(%d,%d) cut=%d",
      fc, leg, #ROUTE, rm, x, y, r8(CUTSCENE_NEST)))
  elseif state == "wait" then
    local entry = ROUTE[leg]
    local rm = entry[1]
    if r8(CURRENT_ROOM) == rm then
      local legSettle = entry[4]
      settle_until = fc + legSettle
      state = "legSettle"
      print(string.format("[harness] f=%d leg %d curRoom=%d reached; settling %d frames",
        fc, leg, rm, legSettle))
    elseif fc - poke_frame > 600 then
      print(string.format("[harness] f=%d TIMEOUT waiting for room %d (cur=%d new=%d)",
        fc, rm, r8(CURRENT_ROOM), r8(NEW_ROOM)))
      emu.stop(0)
    end
  elseif state == "legSettle" then
    if fc >= settle_until then
      if leg < #ROUTE then
        leg = leg + 1
        state = "poke"
      else
        shot_frame = fc + SETTLE
        state = "settle"
        print(string.format("[harness] f=%d final leg complete; settling %d frames for shot",
          fc, SETTLE))
      end
    end
  elseif state == "settle" then
    if fc == shot_frame - 60 and FORCE_CAM > 0 then
      -- Shove cameraX + xScrollBG1 to a specific value to isolate what
      -- the current camera position reveals about VRAM contents.
      w16(0x7EFA1F, FORCE_CAM)
      w16(0x7EFD54, FORCE_CAM)
      print(string.format("[harness] f=%d forced cam=%d", fc, FORCE_CAM))
    end
    if fc >= shot_frame then
      dumpState(string.format("f=%d [STABLE]", fc))
      dumpScreenshot()
      emu.stop(0)
    end
  end
end, emu.eventType.endFrame)
"""


Leg = tuple[int, int, int, int]  # (room, x, y, settle_after_leg_frames)


def build_harness(
    route: list[Leg],
    settle: int,
    force_cutscene: int = 0,
    natural_mode: bool = False,
    shot_at_frame: int = 0,
    zero_ego: bool = False,
    boot_min_frames: int = 2500,
    force_cam: int = 0,
) -> str:
    """Render the Lua template. Uses str.replace rather than .format because
    the template has Lua table literals like `local line = {}` that would
    collide with format placeholders."""
    if not route:
        raise ValueError("route must contain at least one leg")
    syms = resolve_symbols()
    # Hook the RTS at _checkInputDevice + 0x1E. Mesen wants the full 24-bit
    # execution address, which for HiROM is $C0:xxxx — the sym file stores
    # it bank-relative, so we OR in the $C0 bank.
    hook_exec = 0xC00000 | ((syms["_checkInputDevice"] + 0x1E) & 0xFFFF)
    # In natural mode we need many START windows spanning the whole intro
    # (LucasArts logo, credits, title, Part One title card). In poke mode
    # we only need to reach the "SCUMM running + some room loaded" state
    # around frame 2500.
    if natural_mode:
        bursts = [(a, a + 40) for a in range(60, 13001, 800)]
    else:
        bursts = [
            (60, 120), (800, 840), (1600, 1640),
            (2400, 2440), (3200, 3240), (4000, 4040),
        ]
    windows_lua = "{" + ",".join(f"{{{a},{b}}}" for a, b in bursts) + "}"
    route_lua = "{" + ",".join(
        f"{{{rm},{x},{y},{leg_settle}}}"
        for rm, x, y, leg_settle in route
    ) + "}"
    subs = {
        "{{route}}":             route_lua,
        "{{settle}}":            str(settle),
        "{{force_cutscene}}":    str(force_cutscene),
        "{{natural_mode}}":      "1" if natural_mode else "0",
        "{{shot_at_frame}}":     str(shot_at_frame),
        "{{zero_ego}}":          "1" if zero_ego else "0",
        "{{boot_min_frames}}":   str(boot_min_frames),
        "{{force_cam}}":         str(force_cam),
        "{{sym_current_room}}":  f"0x{syms['SCUMM.currentRoom']:X}",
        "{{sym_new_room}}":      f"0x{syms['SCUMM.newRoom']:X}",
        "{{sym_running}}":       f"0x{syms['SCUMM.running']:X}",
        "{{sym_actor1}}":        f"0x{syms['SCUMM.actors.1']:X}",
        "{{sym_cutscene_nest}}": f"0x{syms['SCUMM.cutsceneNest']:X}",
        "{{sym_global_vars}}":   f"0x{syms['SCUMM.globalVars']:X}",
        "{{sym_inp_press}}":     f"0x{syms['inputDevice.press']:X}",
        "{{sym_inp_trig}}":      f"0x{syms['inputDevice.trigger']:X}",
        "{{sym_inp_old}}":       f"0x{syms['inputDevice.old']:X}",
        "{{hook_addr}}":         f"0x{hook_exec:06X}",
        "{{input_windows}}":     windows_lua,
    }
    out = LUA_TEMPLATE
    for k, v in subs.items():
        out = out.replace(k, v)
    return out


def run(
    route: list[Leg],
    settle: int,
    out_png: str,
    timeout: int,
    force_cutscene: int = 0,
    natural_mode: bool = False,
    shot_at_frame: int = 0,
    zero_ego: bool = False,
    boot_min_frames: int = 2500,
    force_cam: int = 0,
) -> int:
    lua = build_harness(
        route, settle, force_cutscene,
        natural_mode=natural_mode, shot_at_frame=shot_at_frame,
        zero_ego=zero_ego, boot_min_frames=boot_min_frames,
        force_cam=force_cam,
    )
    script = DIST / "_room_harness.lua"
    script.write_text(lua, encoding="utf-8")
    out_txt = DIST / "room_harness_out.txt"

    # Run Mesen testrunner; capture stdout, swallow stderr
    cmd = (
        f'cd /d "{DIST}" && "{MESEN}" --testrunner '
        f'SuperMonkeyIsland.sfc _room_harness.lua > room_harness_out.txt 2>NUL'
    )
    try:
        subprocess.run(
            f'cmd.exe /c "{cmd}"',
            shell=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {timeout}s -- partial output below.")

    # Surface harness lines from the output. Includes actor-dump lines that
    # start with two-space indent + "aN:" — they belong to the preceding [harness]
    # block. Ignore CPU warning floods and everything else.
    text = out_txt.read_text(errors="replace") if out_txt.exists() else ""
    keep = []
    for ln in text.splitlines():
        if "[harness]" in ln:
            keep.append(ln)
        elif ln.startswith("  a") and ": rm=" in ln:
            keep.append(ln)
    print("\n".join(keep))
    (out_txt.parent / "_room_harness_full.txt").write_text(text)


    # Extract ARGB frame buffer dump -> PNG. Protocol matches MCP take_screenshot.
    dest = DIST / out_png
    if _save_png(text, dest):
        print(f"screenshot: {dest}")
        return 0
    print("no screenshot produced (ARGB dump missing)", file=sys.stderr)
    return 1


def _save_png(output: str, dest: Path) -> bool:
    """Parse SCREENSHOT_ARGB_START/END stream from Lua output and write a PNG.

    Strips NTSC overscan (removes 7 rows top / 8 rows bottom for 256×239 input
    to yield 256×224, matching the MCP take_screenshot behaviour).
    """
    start_tag = "SCREENSHOT_ARGB_START"
    end_tag = "SCREENSHOT_ARGB_END"
    if start_tag not in output or end_tag not in output:
        return False

    header_idx = output.index(start_tag)
    header_line = output[header_idx:].split("\n", 1)[0]
    parts = header_line.split()
    w = int(parts[1]) if len(parts) > 1 else 256
    h = int(parts[2]) if len(parts) > 2 else 224

    body_start = output.index("\n", header_idx) + 1
    body_end = output.index(end_tag)
    hex_stream = output[body_start:body_end].split()

    pixels = [int(tok, 16) for tok in hex_stream]

    if h == 239:
        pixels = pixels[7 * w : len(pixels) - 8 * w]
        h = 224
    elif h == 478:
        pixels = pixels[14 * w : len(pixels) - 16 * w]
        h = 448

    dest.write_bytes(_argb_to_png(w, h, pixels))
    return True


def _argb_to_png(width: int, height: int, pixels: list[int]) -> bytes:
    """Build a minimal PNG (8-bit RGB, no alpha) from ARGB8888 pixels."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: None
        row = pixels[y * width : (y + 1) * width]
        for px in row:
            raw.append((px >> 16) & 0xFF)
            raw.append((px >> 8) & 0xFF)
            raw.append(px & 0xFF)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def _parse_via(spec: str) -> list[Leg]:
    """Parse --via CSV form. Each entry is room[:x:y:settle] where missing
    coordinates default to room-center-ish (160,100) and leg settle defaults
    to 300 frames -- long enough for ENCD scripts to run in simple rooms."""
    legs: list[Leg] = []
    if not spec:
        return legs
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split(":")
        rm = int(parts[0])
        x = int(parts[1]) if len(parts) > 1 and parts[1] else 160
        y = int(parts[2]) if len(parts) > 2 and parts[2] else 100
        leg_settle = int(parts[3]) if len(parts) > 3 and parts[3] else 300
        legs.append((rm, x, y, leg_settle))
    return legs


def run_batch(
    room_ids: list[int],
    timeout: int,
    out_dir: str,
    boot_min: int = 500,
) -> int:
    """Load each room from a fresh boot and snapshot it.

    Saves screenshots as {out_dir}/room_NNN_batch.png. Each room spawns a
    fresh Mesen subprocess (~30s each), so this is slow — use for CI
    regression or once-per-session health checks, not per-edit feedback.
    """
    out_path = DIST / out_dir
    out_path.mkdir(parents=True, exist_ok=True)
    ok = 0
    fail = 0
    for rid in room_ids:
        out_png = f"{out_dir}/room_{rid:03d}_batch.png"
        rc = run(
            [(rid, 160, 100, 0)], 300, out_png, timeout,
            force_cutscene=0, natural_mode=False, shot_at_frame=0,
            zero_ego=False, boot_min_frames=boot_min, force_cam=0,
        )
        if rc == 0:
            ok += 1
        else:
            fail += 1
    print(f"batch: {ok} ok, {fail} fail, out={out_path}")
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Load a room (or sequence of rooms) and capture state + screenshot."
    )
    ap.add_argument("room", type=str,
        help='target room number, or a range like "1-20", or "all" for 1-99'
    )
    ap.add_argument("-x", type=int, default=160, help="Guybrush x (default 160)")
    ap.add_argument("-y", type=int, default=100, help="Guybrush y (default 100)")
    ap.add_argument(
        "--via", default="",
        help="CSV of intermediate rooms, earliest first. "
             "Each entry: room[:x:y:settle_frames]. "
             "Example: --via 96 goes room 10 -> 96 -> TARGET. "
             "Example: --via 96:160:100:400,38 chains two intermediates."
    )
    ap.add_argument(
        "--settle", type=int, default=600,
        help="frames to settle after final leg before screenshot (default 600)"
    )
    ap.add_argument(
        "--cutscene", type=int, default=0,
        help="force cutsceneNest = N before the final transition. "
             "Reproduces script-driven transitions from within a cutscene."
    )
    ap.add_argument(
        "--zero-ego", action="store_true",
        help="Zero actor 1 and place actor 2 (costume 1) at the target "
             "coordinates instead. Mirrors the natural-flow WRAM state "
             "where the intro uses actor 2 as Guybrush while VAR_EGO=1."
    )
    ap.add_argument(
        "--natural", action="store_true",
        help="Skip the WRAM-poke fast path. Press START through the whole "
             "intro so scripts drive all transitions (much slower, faithful)."
    )
    ap.add_argument(
        "--shot-at", type=int, default=13400,
        help="natural mode: frame to capture state + screenshot (default 13400)."
    )
    ap.add_argument(
        "--boot-min", type=int, default=500,
        help="minimum frame count before harness starts poking. Higher = "
             "more intro progression before pokes. Lower = faster boot."
    )
    ap.add_argument(
        "--force-cam", type=int, default=0,
        help="Override cameraX + xScrollBG1 to this value 60 frames before "
             "screenshot. Useful for isolating viewport-position-dependent "
             "rendering bugs. 0 = leave camera alone."
    )
    ap.add_argument("--out", default=None, help="output PNG name (default room_NNN_harness.png)")
    ap.add_argument(
        "--batch-out", default="batch",
        help="batch mode: output directory under distribution/ (default 'batch')"
    )
    ap.add_argument("--timeout", type=int, default=300, help="Mesen timeout (s)")
    args = ap.parse_args()

    # Batch / range mode: a CSV or range replaces single target.
    room_arg = args.room.strip()
    if room_arg == "all":
        room_ids = list(range(1, 100))
    elif "-" in room_arg:
        lo, hi = room_arg.split("-", 1)
        room_ids = list(range(int(lo), int(hi) + 1))
    elif "," in room_arg:
        room_ids = [int(t) for t in room_arg.split(",") if t]
    else:
        room_ids = [int(room_arg)]

    if len(room_ids) > 1:
        return run_batch(room_ids, args.timeout, args.batch_out, args.boot_min)

    target = room_ids[0]
    route: list[Leg] = _parse_via(args.via)
    # Final leg is the target room; leg_settle here is ignored (--settle covers it).
    route.append((target, args.x, args.y, 0))

    out_png = args.out or f"room_{target:03d}_harness.png"
    return run(
        route, args.settle, out_png, args.timeout, args.cutscene,
        natural_mode=args.natural, shot_at_frame=args.shot_at,
        zero_ego=args.zero_ego, boot_min_frames=args.boot_min,
        force_cam=args.force_cam,
    )


if __name__ == "__main__":
    sys.exit(main())
