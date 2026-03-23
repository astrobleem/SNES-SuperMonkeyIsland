-- ============================================================================
-- Test Template for SNES Super Monkey Island
-- ============================================================================
-- Copy this file to E:\gh\SNES-SuperMonkeyIsland\distribution\ and rename it.
--
-- Run:
--   cmd.exe /c "cd /d E:\gh\SNES-SuperMonkeyIsland\distribution && ^
--     E:\gh\SNES-SuperMonkeyIsland\mesen\Mesen.exe --testrunner ^
--     SuperMonkeyIsland.sfc test_myscript.lua > out.txt 2>&1"
--
-- After EVERY build, update ROM addresses from build/SuperMonkeyIsland.sym:
--   grep -E 'core\.error\.trigger$|_checkInputDevice$' build/*.sym
--   _checkInputDevice RTS = entry + $1E, then add $C0 bank prefix.
--
-- PREFER using the MCP server's run_with_input tool instead of manual scripts.
-- It auto-resolves all addresses from the sym file.
-- ============================================================================

-- ===== ROM ADDRESSES (shift every build -- grep sym file!) ==================
local ADDR_ERROR_TRIGGER   = 0xC04214  -- core.error.trigger
local ADDR_CHECK_INPUT_RTS = 0xC039D9  -- _checkInputDevice + $1E

-- ===== WRAM ADDRESSES (shift when RAMSECTION layout changes) ================
-- Verify with: grep -E 'inputDevice\.|OopStack$|GLOBAL\.currentFrame' build/*.sym
local ADDR_INPUT_PRESS     = 0x7EED63  -- inputDevice.press
local ADDR_INPUT_TRIGGER   = 0x7EED65  -- inputDevice.trigger
local ADDR_INPUT_MASK      = 0x7EED67  -- inputDevice.mask
local ADDR_INPUT_OLD       = 0x7EED69  -- inputDevice.old
local ADDR_OOP_STACK       = 0x7ED976  -- OopStack base (48 slots x 16 bytes)
local ADDR_CURRENT_FRAME   = 0x7EEDB9  -- GLOBAL.currentFrame

-- SCUMM engine state
local ADDR_SCUMM_ROOM      = 0x7EE92A  -- SCUMM.currentRoom
local ADDR_SCUMM_RUNNING   = 0x7EE904  -- SCUMM.running
local ADDR_CURSOR_X        = 0x7EED4A  -- SCUMM.cursorX
local ADDR_CURSOR_Y        = 0x7EED4C  -- SCUMM.cursorY

-- PPU shadows
local ADDR_SCREEN_BRIGHT   = 0x7EEC6B  -- ScreenBrightness
local ADDR_SCREEN_MODE     = 0x7EEC3B  -- ScreenMode
local ADDR_MAIN_SCREEN     = 0x7EEC3C  -- MainScreen

-- Room state
local ADDR_ROOM_HDR        = 0x7EECE0  -- GLOBAL.room.hdr (32 bytes)
local ADDR_ROOM_IDX        = 0x7EED00  -- GLOBAL.room.idx
local ADDR_ROOM_CURRENT_ID = 0x7EED08  -- GLOBAL.room.currentId

-- ===== BUTTON CONSTANTS (SNES JOY1L format) =================================
local JOY_B     = 0x8000; local JOY_Y      = 0x4000
local JOY_SEL   = 0x2000; local JOY_START  = 0x1000
local JOY_UP    = 0x0800; local JOY_DOWN   = 0x0400
local JOY_LEFT  = 0x0200; local JOY_RIGHT  = 0x0100
local JOY_A     = 0x0080; local JOY_X      = 0x0040
local JOY_L     = 0x0020; local JOY_R      = 0x0010

-- ===== OOP CONSTANTS ========================================================
local OOP_ENTRY_SIZE  = 0x10
local OOP_SLOT_COUNT  = 48
-- Key OBJIDs (enum order from oop.h):
-- $00=abstract.Iterator  $01=abstract.Sort     $02=abstract.Sprite
-- $03=abstract.Background  $04=abstract.Hdma   $05=abstract.Script
-- $06=abstract.Palette   $07=Script            $08=Msu1
-- $09=Msu1.audio         $0D=Background.framebuffer
-- $0E=abstract.Event     $0F=Event.chapter     $10=Event.direction_generic
-- $11=Event.checkpoint   $12=VideoMask         $13=Background.generic
-- $14=Brightness         $15=Player
-- $16=Background.textlayer.8x8  $17=Background.textlayer.16x16
-- $18=Sprite.super       $19=ScummVM

-- ===== COMMON UTILITIES =====================================================
local MAX_FRAMES = 2000
local errorHit = false

local function readWord(addr)
    return emu.read(addr, emu.memType.snesMemory)
         + emu.read(addr + 1, emu.memType.snesMemory) * 256
end

local function readWordSigned(addr)
    local v = readWord(addr)
    if v >= 32768 then v = v - 65536 end
    return v
end

local function writeWord(addr, val)
    emu.write(addr, val & 0xFF, emu.memType.snesMemory)
    emu.write(addr + 1, (val >> 8) & 0xFF, emu.memType.snesMemory)
end

--- Search OopStack for an object by OBJID. Returns its DP (ZP base), or nil.
local function findObjectDP(objId)
    for i = 0, OOP_SLOT_COUNT - 1 do
        local base = ADDR_OOP_STACK + i * OOP_ENTRY_SIZE
        local flags = emu.read(base, emu.memType.snesMemory)
        if flags ~= 0 then
            local id = emu.read(base + 1, emu.memType.snesMemory)
            if id == objId then return readWord(base + 8) end
        end
    end
    return nil
end

--- Dump all active OopStack entries (for diagnostics).
local function dumpOopStack()
    for i = 0, OOP_SLOT_COUNT - 1 do
        local base = ADDR_OOP_STACK + i * OOP_ENTRY_SIZE
        local flags = emu.read(base, emu.memType.snesMemory)
        if flags ~= 0 then
            local id = emu.read(base + 1, emu.memType.snesMemory)
            local dp = readWord(base + 8)
            print(string.format("  slot %02d: flags=%02X id=%02X dp=%04X", i, flags, id, dp))
        end
    end
end

-- ===== INPUT INJECTION ======================================================
local injectButton = 0

emu.addMemoryCallback(function()
    if injectButton ~= 0 then
        writeWord(ADDR_INPUT_PRESS, injectButton)
        writeWord(ADDR_INPUT_TRIGGER, injectButton)
        writeWord(ADDR_INPUT_OLD, 0)
    end
end, emu.callbackType.exec, ADDR_CHECK_INPUT_RTS)

-- ===== ERROR DETECTION ======================================================
emu.addMemoryCallback(function()
    if errorHit then return end; errorHit = true
    local state = emu.getState()
    local errCode = readWord(state["cpu.sp"] + 3)
    local frame = state["ppu.frameCount"]
    print(string.format("FAIL: error code=%d frame=%d", errCode, frame))
    dumpOopStack()
    emu.stop()
end, emu.callbackType.exec, ADDR_ERROR_TRIGGER)

-- ===== INPUT SCHEDULE =======================================================
-- Each entry: {startFrame, endFrame, button}
-- Use 1-frame windows {f, f, btn} for sequential inputs.
-- Use 3-frame windows {f, f+2, btn} for isolated presses.
local schedule = {
    -- Example: press A button at frame 850 (after boot + SCUMM init at ~800)
    -- {850, 852, JOY_A},
}

-- ===== MAIN FRAME HANDLER ==================================================
emu.addEventCallback(function()
    local frame = emu.getState()["ppu.frameCount"]

    -- Apply input schedule
    injectButton = 0
    for _, s in ipairs(schedule) do
        if frame >= s[1] and frame <= s[2] then
            injectButton = s[3]
            break
        end
    end

    -- TODO: Add test-specific logic here
    -- Game boots directly to SCUMM interpreter (~frame 300 without input).
    -- Default safe test start frame: 800.
    --
    -- Example: verify SCUMM is running
    -- if frame == 800 then
    --     local room = emu.read(ADDR_SCUMM_ROOM, emu.memType.snesMemory)
    --     local bright = emu.read(ADDR_SCREEN_BRIGHT, emu.memType.snesMemory)
    --     if room > 0 and bright > 0 then
    --         print("PASS: SCUMM running, room=" .. room)
    --     else
    --         print("FAIL: room=" .. room .. " bright=" .. bright)
    --     end
    --     emu.stop()
    --     return
    -- end

    if frame >= MAX_FRAMES then
        print(string.format("TIMEOUT at frame %d", frame))
        emu.stop()
    end
end, emu.eventType.endFrame)
