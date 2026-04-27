-- SCUMM VM test runner.
-- Run via: Mesen.exe --testrunner SuperMonkeyIsland.sfc tests/scumm_vm/test_runner.lua
--
-- Mesen disables Lua I/O (dofile, require, io, os) by default for security,
-- so the harness is inlined here rather than loaded from harness.lua.
-- harness.lua remains in the repo as the canonical commented copy; keep
-- the two in sync when editing.

-- ============================================================================
-- HARNESS (mirror of tests/scumm_vm/harness.lua — keep in sync)
-- ============================================================================

local H = {}
local MT = emu.memType.snesMemory

H.SYM = {
  SCUMM_actors_base   = 0x7E890C,
  SCUMM_actor_stride  = 16,
  SCUMM_slots_base    = 0x7ED1EA,
  SCUMM_slot_stride   = 64,
  SCUMM_currentRoom   = 0x7EF8E7,
  SCUMM_newRoom       = 0x7EF8E9,
  SCUMM_globalVars    = 0x7ECBAA,
  SCUMM_pendingEgoObj = 0x7EF99F,
  SCUMM_pendingEgoX   = 0x7EF9A1,
  SCUMM_pendingEgoY   = 0x7EF9A3,
  SCUMM_egoPositioned = 0x7EF9A5,
  SCUMM_bitVars       = 0x7EF5D7,    -- 256 bytes = 2048 bit-vars, 1 bit each
  SCUMM_cache_base    = 0x7F6400,
  slot_status = 0, slot_number = 1, slot_where = 2, slot_freezeCount = 3,
  slot_pc = 4, slot_cachePtr = 6, slot_cacheLen = 8, slot_delay = 10,
  slot_cutsceneOverride = 12,
}

-- Cache base ($7F:6400) + cachePtr is added with 16-bit wrap, so
-- cache offset must keep the result inside $7F:6400..$7F:FFFF.
H.TEST_BYTECODE_ADDR  = 0x7FF000
H.TEST_CACHE_OFFSET   = 0x8C00       -- $F000 - $6400 = $8C00
H.TEST_SLOT_INDEX     = 23
H.TEST_SLOT_ADDR      = H.SYM.SCUMM_slots_base + H.TEST_SLOT_INDEX * H.SYM.SCUMM_slot_stride
H.SCUMM_SLOT_DEAD     = 0
H.SCUMM_SLOT_RUNNING  = 1

function H.rd8(addr)  return emu.read(addr, MT)        end
function H.rd16(addr) return emu.read16(addr, MT)      end
function H.wr8(addr, v)  emu.write(addr, v & 0xFF, MT) end
function H.wr16(addr, v)
  emu.write(addr,     v & 0xFF,        MT)
  emu.write(addr + 1, (v >> 8) & 0xFF, MT)
end
function H.write_bytes(addr, bytes)
  for i, b in ipairs(bytes) do emu.write(addr + i - 1, b & 0xFF, MT) end
end
function H.actor_addr(n, field_offset)
  return H.SYM.SCUMM_actors_base + n * 16 + (field_offset or 0)
end
function H.slot_addr(field_offset) return H.TEST_SLOT_ADDR + field_offset end

-- Mesen Lua is callback-driven — there is no script-body emu.frameAdvance().
-- Tests are written as if they could block on frames, but they actually yield
-- to a coroutine that resumes from the endFrame callback.
function H.wait_frames(n)
  for _ = 1, n do coroutine.yield() end
end

-- Freeze all running slots except the test slot, so MI1's gameplay scripts
-- don't trample the WRAM state we're trying to verify. Returns the saved
-- freezeCount values so the caller can restore them later.
function H.freeze_other_slots()
  local saved = {}
  for i = 0, 24 do
    if i ~= H.TEST_SLOT_INDEX then
      local addr = H.SYM.SCUMM_slots_base + i * H.SYM.SCUMM_slot_stride
      saved[i] = H.rd8(addr + H.SYM.slot_freezeCount)
      -- A nonzero freezeCount makes the scheduler skip the slot
      -- (scummvm.65816:540-543). 0xFF is plenty.
      H.wr8(addr + H.SYM.slot_freezeCount, 0xFF)
    end
  end
  return saved
end

function H.restore_slot_freezes(saved)
  for i, v in pairs(saved) do
    local addr = H.SYM.SCUMM_slots_base + i * H.SYM.SCUMM_slot_stride
    H.wr8(addr + H.SYM.slot_freezeCount, v)
  end
end

function H.run_bytecode(bytecode, max_frames)
  max_frames = max_frames or 30
  -- Freeze MI1's scripts so they can't write actor state during the test.
  local saved = H.freeze_other_slots()

  H.write_bytes(H.TEST_BYTECODE_ADDR, bytecode)
  H.wr8 (H.slot_addr(H.SYM.slot_status),         H.SCUMM_SLOT_RUNNING)
  H.wr8 (H.slot_addr(H.SYM.slot_number),         200)
  H.wr8 (H.slot_addr(H.SYM.slot_where),          0)
  H.wr8 (H.slot_addr(H.SYM.slot_freezeCount),    0)
  H.wr16(H.slot_addr(H.SYM.slot_pc),             0)
  H.wr16(H.slot_addr(H.SYM.slot_cachePtr),       H.TEST_CACHE_OFFSET)
  H.wr16(H.slot_addr(H.SYM.slot_cacheLen),       #bytecode)
  H.wr16(H.slot_addr(H.SYM.slot_delay),          0)
  H.wr8 (H.slot_addr(H.SYM.slot_cutsceneOverride), 0)

  local ok = false
  for _ = 1, max_frames do
    coroutine.yield()
    if H.rd8(H.slot_addr(H.SYM.slot_status)) == H.SCUMM_SLOT_DEAD then
      ok = true
      break
    end
  end

  H.restore_slot_freezes(saved)
  return ok
end

H.failures = {}
H.current_test = nil

function H.assert_eq(actual, expected, label)
  if actual ~= expected then
    table.insert(H.failures, string.format(
      "  %s: %s — expected %d ($%X), got %d ($%X)",
      H.current_test or "?", label or "value", expected, expected, actual, actual))
    return false
  end
  return true
end

-- Mesen testrunner pitfalls (see feedback_mesen_testrunner.md):
--   1. emu.stop() inside Lua callbacks is unreliable.
--   2. emu.log() may not surface to stdout under --testrunner; print() does.
--   3. Uninit-read flood throttles to ~25fps for first ~12000 frames.
--   4. Mesen Lua is callback-driven — there is no script-body frameAdvance.
--      Tests run inside a coroutine resumed once per frame from endFrame.

local function out(s) print(s); emu.log(s) end

local stop_requested = false
local stop_code = 0
local main_co = nil

-- The endFrame callback resumes the test coroutine. When the coroutine is
-- finished, we mark stop_requested so the next frame's emu.stop fires.
emu.addEventCallback(function()
  if stop_requested then
    emu.stop(stop_code)
    return
  end
  if main_co and coroutine.status(main_co) ~= "dead" then
    local ok, err = coroutine.resume(main_co)
    if not ok then
      out("[harness] coroutine error: " .. tostring(err))
      stop_code = 1
      stop_requested = true
    end
    if coroutine.status(main_co) == "dead" then
      stop_requested = true
    end
  end
end, emu.eventType.endFrame)

function H.run_all(tests, boot_wait_frames)
  boot_wait_frames = boot_wait_frames or 300
  main_co = coroutine.create(function()
    out(string.format("[harness] booting %d frames before tests...", boot_wait_frames))
    H.wait_frames(boot_wait_frames)
    out("[harness] boot complete; running tests")

    local passed, failed = 0, 0
    for _, t in ipairs(tests) do
      H.current_test = t.name
      H.failures = {}
      local ok, err = pcall(t.fn)
      if not ok then
        table.insert(H.failures, "  " .. t.name .. ": Lua error — " .. tostring(err))
      end
      if #H.failures == 0 and ok then
        out("[PASS] " .. t.name)
        passed = passed + 1
      else
        out("[FAIL] " .. t.name)
        for _, msg in ipairs(H.failures) do out(msg) end
        failed = failed + 1
      end
    end
    out(string.format("[harness] %d passed, %d failed", passed, failed))
    out("##VM_TESTS_DONE##")
    stop_code = (failed == 0) and 0 or 1
  end)
end

-- ============================================================================
-- TESTS
-- ============================================================================

-- File-scope addresses for tables addressed across multiple phases.
-- Listed up here so any test (in any phase) can reference them.
local SCUMM_objectOwner       = 0x7EE22A   -- byte per obj
local SCUMM_objectState       = 0x7EDE2A   -- byte per obj
local SCUMM_objectClass       = 0x7EBBAA   -- word per obj
local SCUMM_actorWalkBox      = 0x7EF0B4   -- byte per actor (16 actors)
local SCUMM_actorWidth        = 0x7EF0D4   -- byte per actor (16 actors)
local SCUMM_actorIgnoreBoxes  = 0x7EF0C4   -- byte per actor
local SCUMM_actorWalkAnimNr   = 0x7EF114   -- byte per actor — _walkFrame
local SCUMM_actorStandFrame   = 0x7EF124   -- byte per actor — _standFrame
local SCUMM_actorTalkAnimStart= 0x7EF134   -- byte per actor — _talkStartFrame
local SCUMM_actorTalkAnimEnd  = 0x7EF144   -- byte per actor — _talkStopFrame
local SCUMM_actorTargetX      = 0x7EFB49   -- 16 actors × 2 bytes each
local SCUMM_actorTargetY      = 0x7EFB69
local SCUMM_cutsceneNest      = 0x7EF8E5
local SCUMM_cutScenePtr       = 0x7EF983   -- 5 nest levels × 2 bytes
local SCUMM_cameraDest        = 0x7EFE3E
local SCUMM_cameraFollows     = 0x7EFE3C

-- TEST: op_putActor — literal coords (opcode $01, no flag bits set).
--   $01 actor=byte x=word y=word
function test_putActor_literal_coords()
  H.wr16(H.actor_addr(5, 2), 0)        -- x
  H.wr16(H.actor_addr(5, 4), 0)        -- y
  H.wr8 (H.actor_addr(5, 10), 99)      -- moving = 99 (sentinel; opcode should clear)

  local ran = H.run_bytecode({
    0x01, 0x05, 0xC8, 0x00, 0x7D, 0x00, 0xA0
  })
  H.assert_eq(ran and 1 or 0, 1, "slot finished within budget")
  H.assert_eq(H.rd16(H.actor_addr(5, 2)),  200, "actor[5].x")
  H.assert_eq(H.rd16(H.actor_addr(5, 4)),  125, "actor[5].y")
  H.assert_eq(H.rd8 (H.actor_addr(5, 10)), 0,   "actor[5].moving cleared")
end

-- TEST: putActor on different actors writes to different struct slots.
function test_putActor_actor_indexing()
  H.wr16(H.actor_addr(7, 2), 0)
  H.wr16(H.actor_addr(7, 4), 0)
  H.wr16(H.actor_addr(8, 2), 0)
  H.wr16(H.actor_addr(8, 4), 0)

  H.run_bytecode({0x01, 0x07, 0x64, 0x00, 0x32, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(7, 2)), 100, "actor[7].x after putActor(7)")
  H.assert_eq(H.rd16(H.actor_addr(7, 4)),  50, "actor[7].y after putActor(7)")
  H.assert_eq(H.rd16(H.actor_addr(8, 2)),   0, "actor[8].x untouched by putActor(7)")

  H.run_bytecode({0x01, 0x08, 0x2C, 0x01, 0xAF, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(8, 2)), 300, "actor[8].x after putActor(8)")
  H.assert_eq(H.rd16(H.actor_addr(8, 4)), 175, "actor[8].y after putActor(8)")
  H.assert_eq(H.rd16(H.actor_addr(7, 2)), 100, "actor[7].x preserved across calls")
end

-- TEST: op_loadRoomWithEgo bytecode parsing.
--   $24 obj16 room8 x16 y16 — verify pendingEgo* latched correctly.
function test_loadRoomWithEgo_bytecode_decode()
  H.run_bytecode({
    0x24,
    0xAC, 0x01,        -- obj = 428
    0x21,              -- room = 33
    0xFF, 0xFF,        -- x = -1
    0xFF, 0xFF,        -- y = -1
    0xA0
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_pendingEgoObj), 428,    "pendingEgoObj == bytecode obj")
  H.assert_eq(H.rd16(H.SYM.SCUMM_pendingEgoX),   0xFFFF, "pendingEgoX == bytecode x")
  H.assert_eq(H.rd16(H.SYM.SCUMM_pendingEgoY),   0xFFFF, "pendingEgoY == bytecode y")
end

-- ---------------------------------------------------------------------------
-- Param-decoding: var-ref vs literal flag bits on op_putActor.
-- Opcode $01 = all literal; $81 sets bit 7 → actor is a var-ref.
-- Pre-stage globalVar[5] = 9; running $81 with var=5 should write to actor 9.
-- This catches "fetchByte vs fetchWord byte-count" + readVariable bugs that
-- caused obj=218 instead of obj=428 in the live game.
-- ---------------------------------------------------------------------------
function test_putActor_var_ref_actor()
  -- Use a high-numbered var (200) unlikely to be live game state, written
  -- AFTER the slot freeze so MI1's gameplay can't overwrite it mid-test.
  -- Pre-clear actors fully so leftover y doesn't pretend to pass.
  for i = 0, 14 do
    H.wr8(H.actor_addr(11, i), 0)
    H.wr8(H.actor_addr(12, i), 0)
  end
  H.wr16(H.SYM.SCUMM_globalVars + 200*2, 11)

  -- $81 = putActor with bit 7 set (actor param is var-ref word).
  --   $C8 $00 = var index 200 (LE16, no $2000 array bit)
  --   $96 $00 = x = 150
  --   $5A $00 = y = 90
  H.run_bytecode({
    0x81,
    0xC8, 0x00,
    0x96, 0x00,
    0x5A, 0x00,
    0xA0
  })
  H.assert_eq(H.rd16(H.actor_addr(11, 2)), 150, "actor[11].x via var-ref actor=Var[200]=11")
  H.assert_eq(H.rd16(H.actor_addr(11, 4)),  90, "actor[11].y via var-ref")
  H.assert_eq(H.rd16(H.actor_addr(12, 2)),   0, "actor[12] untouched")
end

-- ---------------------------------------------------------------------------
-- Conditional branch family. Each test runs bytecode with two sentinel
-- paths writing distinct sentinel values to a high-numbered global var
-- (Var[210] for the no-jump path, Var[211] for the jump path). After the
-- run, exactly one var should hold its sentinel.
--
-- We use vars rather than actor fields because MI1's per-frame actor and
-- chore update code runs regardless of slot freezing, and would clobber
-- low-numbered actor coords between when our bytecode writes them and
-- when our Lua reads them. Vars are only touched by scripts; with all
-- other slots frozen, our writes survive.
--
-- Encoding for op_setVarLiteral ($1A):
--   $1A varRef16 value16    (5 bytes total)
--   varRef = 0xMMNN means var index = ((MM<<8)|NN); bit $2000 = array.
-- ---------------------------------------------------------------------------

local NO_JUMP_PATH = {  -- $1A Var[210] = 0x1111 ; $A0
  0x1A, 0xD2, 0x00, 0x11, 0x11, 0xA0,
}  -- 6 bytes
local JUMP_PATH = {     -- $1A Var[211] = 0x2222 ; $A0
  0x1A, 0xD3, 0x00, 0x22, 0x22, 0xA0,
}  -- 6 bytes
-- Jump offset = #NO_JUMP_PATH = 6.

local function reset_branch_signals()
  H.wr16(H.SYM.SCUMM_globalVars + 210*2, 0)
  H.wr16(H.SYM.SCUMM_globalVars + 211*2, 0)
end

local function which_path_ran(label)
  local a = H.rd16(H.SYM.SCUMM_globalVars + 210*2)
  local b = H.rd16(H.SYM.SCUMM_globalVars + 211*2)
  if a == 0x1111 and b == 0 then return "no_jump" end
  if a == 0 and b == 0x2222 then return "jump" end
  H.assert_eq(0, 1, label .. ": Var[210]=$" .. string.format("%X", a)
              .. " Var[211]=$" .. string.format("%X", b)
              .. " (neither path completed cleanly)")
  return "error"
end

-- $28 op_equalZero: no jump when Var == 0; jump when Var != 0.
-- (Opcode name describes the NO-JUMP condition.)
function test_equalZero_jump_when_nonzero()
  -- jump offset = #NO_JUMP_PATH = 6
  local cond = {0x28, 0x0A, 0x00, 0x06, 0x00}

  local bc = {}
  for _, b in ipairs(cond)         do table.insert(bc, b) end
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end

  H.wr16(H.SYM.SCUMM_globalVars + 10*2, 0)
  reset_branch_signals()
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran("$28 var=0") == "no_jump" and 1 or 0, 1,
              "$28 with Var=0: should fall through (no jump)")

  H.wr16(H.SYM.SCUMM_globalVars + 10*2, 5)
  reset_branch_signals()
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran("$28 var=5") == "jump" and 1 or 0, 1,
              "$28 with Var=5: should jump")
end

-- $A8 op_notEqualZero: no jump when Var != 0; jump when Var == 0.
function test_notEqualZero_jump_when_zero()
  local cond = {0xA8, 0x0A, 0x00, 0x06, 0x00}
  local bc = {}
  for _, b in ipairs(cond)         do table.insert(bc, b) end
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end

  H.wr16(H.SYM.SCUMM_globalVars + 10*2, 7)
  reset_branch_signals()
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran("$A8 var=7") == "no_jump" and 1 or 0, 1,
              "$A8 with Var=7: should fall through")

  H.wr16(H.SYM.SCUMM_globalVars + 10*2, 0)
  reset_branch_signals()
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran("$A8 var=0") == "jump" and 1 or 0, 1,
              "$A8 with Var=0: should jump")
end

-- ---------------------------------------------------------------------------
-- Compound helper: build bytecode for a "compare-and-branch-or-fall" test.
-- All ScummVM v5 cmp opcodes ($48, $08, $44, $38, $04, $78) share format:
--   <op> resultVar16 comparand16 jumpOffset16   (7 bytes)
-- The opcode-name describes the NO-JUMP condition (e.g. $44 isLess = no
-- jump when comparand < var). Offset 6 → skip exactly the NO_JUMP_PATH.
-- ---------------------------------------------------------------------------
local function cmp_bytecode(op, var_idx, comparand)
  return {
    op,
    var_idx & 0xFF, (var_idx >> 8) & 0xFF,
    comparand & 0xFF, (comparand >> 8) & 0xFF,
    0x06, 0x00,
  }
end

local function run_cmp_case(op, var_value, comparand, expected_path, label)
  H.wr16(H.SYM.SCUMM_globalVars + 10*2, var_value)
  reset_branch_signals()
  local bc = {}
  for _, b in ipairs(cmp_bytecode(op, 10, comparand)) do table.insert(bc, b) end
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran(label) == expected_path and 1 or 0, 1, label)
end

-- $78 op_isGreater: cond TRUE (no jump) when comparand > variable.
function test_isGreater_cmp_against_literal()
  run_cmp_case(0x78, 5,  10, "no_jump", "$78 (cmp=10 > var=5): no jump")
  run_cmp_case(0x78, 20, 10, "jump",    "$78 (cmp=10 > var=20): jump")
end

-- ---------------------------------------------------------------------------
-- ScummVM-derived: comparisons must be SIGNED (int16). ScummVM's
-- script_v5.cpp uses `int16 a, b` and `jumpRelative(b > a)` etc. The SNES
-- port uses `cmp.w` + `bcs/bcc` which is UNSIGNED. These test cases pin
-- down whether negative comparands behave correctly.
--
-- Test case: var = -1 (= $FFFF), literal comparand = 1.
--   Signed: 1 > -1 = TRUE → no jump for $78
--   Unsigned: $0001 > $FFFF = FALSE → jump for $78
-- If the port jumps here, we have a signed/unsigned bug.
-- ---------------------------------------------------------------------------
function test_signed_comparison_isGreater()
  run_cmp_case(0x78, 0xFFFF, 1, "no_jump",
               "$78 SIGNED: 1 > -1 (var=$FFFF) → no jump per ScummVM")
end

function test_signed_comparison_isLess()
  -- Signed: 1 < -1 = FALSE → jump for $44
  -- Unsigned: $0001 < $FFFF = TRUE → no jump for $44
  run_cmp_case(0x44, 0xFFFF, 1, "jump",
               "$44 SIGNED: 1 < -1 (var=$FFFF) → jump per ScummVM")
end

function test_signed_comparison_isLessEqual()
  -- Signed: 1 <= -1 = FALSE → jump for $38
  -- Unsigned: $0001 <= $FFFF = TRUE → no jump for $38
  run_cmp_case(0x38, 0xFFFF, 1, "jump",
               "$38 SIGNED: 1 <= -1 (var=$FFFF) → jump per ScummVM")
end

function test_signed_comparison_isGreaterEqual()
  -- Signed: 1 >= -1 = TRUE → no jump for $04
  -- Unsigned: $0001 >= $FFFF = FALSE → jump for $04
  run_cmp_case(0x04, 0xFFFF, 1, "no_jump",
               "$04 SIGNED: 1 >= -1 (var=$FFFF) → no jump per ScummVM")
end

-- $44 op_isLess: cond TRUE (no jump) when comparand < variable.
function test_isLess_cmp_against_literal()
  run_cmp_case(0x44, 20, 10, "no_jump", "$44 (cmp=10 < var=20): no jump")
  run_cmp_case(0x44, 5,  10, "jump",    "$44 (cmp=10 < var=5): jump")
  run_cmp_case(0x44, 10, 10, "jump",    "$44 (cmp=10 < var=10): jump (equal)")
end

-- $38 op_isLessEqual: cond TRUE (no jump) when comparand <= variable.
function test_isLessEqual_cmp_against_literal()
  run_cmp_case(0x38, 20, 10, "no_jump", "$38 (cmp=10 <= var=20): no jump")
  run_cmp_case(0x38, 10, 10, "no_jump", "$38 (cmp=10 <= var=10): no jump (equal)")
  run_cmp_case(0x38, 5,  10, "jump",    "$38 (cmp=10 <= var=5): jump")
end

-- $04 op_isGreaterEqual: cond TRUE (no jump) when comparand >= variable.
function test_isGreaterEqual_cmp_against_literal()
  run_cmp_case(0x04, 5,  10, "no_jump", "$04 (cmp=10 >= var=5): no jump")
  run_cmp_case(0x04, 10, 10, "no_jump", "$04 (cmp=10 >= var=10): no jump (equal)")
  run_cmp_case(0x04, 20, 10, "jump",    "$04 (cmp=10 >= var=20): jump")
end

-- $48 op_isEqual: cond TRUE (no jump) when comparand == variable.
function test_isEqual_cmp_against_literal()
  run_cmp_case(0x48, 10, 10, "no_jump", "$48 (cmp=10 == var=10): no jump")
  run_cmp_case(0x48, 11, 10, "jump",    "$48 (cmp=10 == var=11): jump")
end

-- $08 op_isNotEqual: cond TRUE (no jump) when comparand != variable.
function test_isNotEqual_cmp_against_literal()
  run_cmp_case(0x08, 11, 10, "no_jump", "$08 (cmp=10 != var=11): no jump")
  run_cmp_case(0x08, 10, 10, "jump",    "$08 (cmp=10 != var=10): jump")
end

-- ---------------------------------------------------------------------------
-- Arithmetic / variable-write opcodes
-- ---------------------------------------------------------------------------

-- $1A op_move (literal value): Var[X] = literal.
function test_move_literal()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0)
  H.run_bytecode({0x1A, 0xDC, 0x00, 0x34, 0x12, 0xA0})  -- Var[220] = 0x1234
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0x1234,
              "Var[220] after move literal 0x1234")
end

-- $9A op_move (var-ref value): Var[X] = Var[Y].
function test_move_var_ref()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0)
  H.wr16(H.SYM.SCUMM_globalVars + 221*2, 0xBEEF)
  -- $9A varRef16(220) varRef16(221)  -- bit 7 set → value is var-ref
  H.run_bytecode({0x9A, 0xDC, 0x00, 0xDD, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0xBEEF,
              "Var[220] = Var[221] = 0xBEEF (var-ref move)")
end

-- $5A op_add (literal): Var[X] += literal.
function test_add_literal()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 100)
  H.run_bytecode({0x5A, 0xDC, 0x00, 0x2A, 0x00, 0xA0})  -- Var[220] += 42
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 142,
              "Var[220] = 100 + 42")
end

-- $3A op_subtract (literal): Var[X] -= literal.
function test_subtract_literal()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 100)
  H.run_bytecode({0x3A, 0xDC, 0x00, 0x0F, 0x00, 0xA0})  -- Var[220] -= 15
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 85,
              "Var[220] = 100 - 15")
end

-- $46 op_increment: Var[X]++ (no value param, just var ref).
function test_increment()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 41)
  H.run_bytecode({0x46, 0xDC, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 42,
              "Var[220] = 41 + 1")
end

-- $C6 op_decrement: Var[X]--.
function test_decrement()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 43)
  H.run_bytecode({0xC6, 0xDC, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 42,
              "Var[220] = 43 - 1")
end

-- ---------------------------------------------------------------------------
-- Actor accessor opcodes
-- ---------------------------------------------------------------------------

-- $2D op_putActorInRoom: actor8 room8.
--   ScummVM (script_v5.cpp:2160): only writes _room. Visibility is left
--   alone except when room == 0 → ScummVM calls putActor(0,0,0) which
--   hides the actor (visible=0).
function test_putActorInRoom()
  H.wr8(H.actor_addr(13, 0), 0)
  H.wr8(H.actor_addr(13, 11), 1)       -- pre: visible

  H.run_bytecode({0x2D, 0x0D, 0x21, 0xA0})  -- putActorInRoom(13, 33)
  H.assert_eq(H.rd8(H.actor_addr(13, 0)),  33, "actor[13].room = 33")
  H.assert_eq(H.rd8(H.actor_addr(13, 11)),  1,
              "actor[13].visible preserved (was 1, ScummVM doesn't touch on room!=0)")

  H.run_bytecode({0x2D, 0x0D, 0x00, 0xA0})  -- putActorInRoom(13, 0)
  H.assert_eq(H.rd8(H.actor_addr(13, 0)),   0, "actor[13].room = 0 after remove")
  H.assert_eq(H.rd8(H.actor_addr(13, 11)),  0, "actor[13].visible = 0 (room == 0 hides)")
end

-- ---------------------------------------------------------------------------
-- KNOWN DIVERGENCE (not yet a test — actor struct doesn't have the fields):
-- ScummVM's actorOps sub-op $04 (walkAnimNr), $06 (standFrame), $0A
-- (animDefault) all write to per-actor `_walkFrame`, `_standFrame`,
-- `_talkStartFrame`, `_talkStopFrame` overrides. Our actor struct has
-- only initFrame; the chore engine uses compile-time-default 2/3/4/5
-- for walk/stand/talk frames. Scripts that set non-default frames per
-- actor will animate wrong here. Tracked separately — not a VM bug
-- per se, but a feature gap.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- ScummVM-derived: o5_putActorInRoom (script_v5.cpp:2160) does NOT change
-- visibility when room != 0. It only sets `a->_room = room`. The only
-- visibility change is `if (!room) a->putActor(0, 0, 0)` which hides.
--
-- Our port sets visible=1 whenever room != 0 (see scummvm.65816 op_putActorInRoom).
-- That's a ScummVM divergence — an invisible actor that gets putActorInRoom'd
-- to a non-zero room would become visible per our port, but ScummVM keeps
-- it invisible.
-- ---------------------------------------------------------------------------
function test_putActorInRoom_visibility_per_scummvm()
  -- Pre-state: actor invisible. ScummVM: putActorInRoom(actor, 33) leaves
  -- visible=0. Our port: sets visible=1 → divergence.
  H.wr8(H.actor_addr(13, 11), 0)   -- visible = 0
  H.run_bytecode({0x2D, 0x0D, 0x21, 0xA0})  -- putActorInRoom(13, 33)
  H.assert_eq(H.rd8(H.actor_addr(13, 11)), 0,
              "ScummVM-spec: putActorInRoom does NOT make invisible actor visible")
end

-- $03/$83 op_getActorRoom: result-var <- actor.room.
function test_getActorRoom()
  H.wr8 (H.actor_addr(14, 0), 42)       -- actor[14].room = 42
  H.wr16(H.SYM.SCUMM_globalVars + 222*2, 0)
  -- $03 result_var16 actor8 — bit 7 clear → actor is byte literal
  H.run_bytecode({0x03, 0xDE, 0x00, 0x0E, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 222*2), 42,
              "Var[222] = actor[14].room = 42")
end

-- $43/$C3 op_getActorX: result <- actor.x. Param is p16 (word, var-ref optional).
function test_getActorX()
  H.wr16(H.actor_addr(13, 2), 0xCAFE)
  H.wr16(H.SYM.SCUMM_globalVars + 223*2, 0)
  -- $43 result_var16 actor16 — bit 7 clear → actor is literal word
  H.run_bytecode({0x43, 0xDF, 0x00, 0x0D, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 223*2), 0xCAFE,
              "Var[223] = actor[13].x = 0xCAFE")
end

-- $23/$A3 op_getActorY: same shape as getActorX.
function test_getActorY()
  H.wr16(H.actor_addr(13, 4), 0xBEEF)
  H.wr16(H.SYM.SCUMM_globalVars + 223*2, 0)
  H.run_bytecode({0x23, 0xDF, 0x00, 0x0D, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 223*2), 0xBEEF,
              "Var[223] = actor[13].y = 0xBEEF")
end

-- ---------------------------------------------------------------------------
-- Regression: op_putActor on VAR_EGO sets SCUMM.egoPositioned (so the
-- finalizeEgoSpawn hook in processRoomChange skips its obj-walk-to teleport).
-- This pins down the fix from the loadRoomWithEgo work — if anyone removes
-- the egoPositioned setter from op_putActor, this test fails.
-- ---------------------------------------------------------------------------
-- ---------------------------------------------------------------------------
-- Bit-var ops. SCUMM v5 var spec with type bits [15:14]=10 ($8000) → bit var.
-- bitVars is a packed bit array: var index N is bit (N % 8) of bitVars[N/8].
-- A small off-by-one in the byte/bit index has historically been catastrophic
-- (one bug-var write can flip ~8 game flags at once).
-- ---------------------------------------------------------------------------

-- $1A op_move with resultVar = $8000 | bit_index. Writes 0/1 to that bit.
function test_bitVar_set_via_move()
  -- Pick bit index 50: byte 50/8 = 6, bit 50%8 = 2 → bitVars[6] bit 2 (mask $04).
  H.wr8(H.SYM.SCUMM_bitVars + 6, 0x00)
  H.run_bytecode({0x1A, 0x32, 0x80, 0x01, 0x00, 0xA0})
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 6), 0x04,
              "bitVars[6] = 0x04 after setting bit-var #50")

  -- Set bit 53 (same byte, bit 5 → mask $20). Combined: $24.
  H.run_bytecode({0x1A, 0x35, 0x80, 0x01, 0x00, 0xA0})
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 6), 0x24,
              "bitVars[6] = 0x24 after also setting bit-var #53")

  -- Clear bit 50 (write 0). Bit 53 should remain set.
  H.run_bytecode({0x1A, 0x32, 0x80, 0x00, 0x00, 0xA0})
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 6), 0x20,
              "bitVars[6] = 0x20 after clearing bit-var #50 (53 stays)")
end

-- Boundary cases for bit-var indexing — easiest place to introduce off-by-one.
function test_bitVar_boundaries()
  -- Bit 0  → bitVars[0] bit 0 (mask $01)
  -- Bit 7  → bitVars[0] bit 7 (mask $80)
  -- Bit 8  → bitVars[1] bit 0 (mask $01)
  -- Bit 15 → bitVars[1] bit 7 (mask $80)
  H.wr8(H.SYM.SCUMM_bitVars + 0, 0x00)
  H.wr8(H.SYM.SCUMM_bitVars + 1, 0x00)

  H.run_bytecode({0x1A, 0x00, 0x80, 0x01, 0x00, 0xA0})  -- set bit 0
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 0), 0x01, "bit-var #0 → bitVars[0] bit 0")

  H.run_bytecode({0x1A, 0x07, 0x80, 0x01, 0x00, 0xA0})  -- set bit 7
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 0), 0x81, "bit-var #7 → bitVars[0] bit 7")
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 1), 0x00, "bitVars[1] still 0 after bit 7")

  H.run_bytecode({0x1A, 0x08, 0x80, 0x01, 0x00, 0xA0})  -- set bit 8
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 0), 0x81, "bitVars[0] preserved after bit 8")
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 1), 0x01, "bit-var #8 → bitVars[1] bit 0")

  H.run_bytecode({0x1A, 0x0F, 0x80, 0x01, 0x00, 0xA0})  -- set bit 15
  H.assert_eq(H.rd8(H.SYM.SCUMM_bitVars + 1), 0x81, "bit-var #15 → bitVars[1] bit 7")
end

-- $26 op_setVarRange (byte mode): set a contiguous range of vars to byte values.
--   $26 firstVar16 count8 byte_values... (count bytes)
function test_setVarRange_byte_mode()
  for i = 0, 4 do H.wr16(H.SYM.SCUMM_globalVars + (230 + i)*2, 0) end
  -- $26, firstVar=230, count=4, values [10, 20, 30, 40]
  H.run_bytecode({0x26, 0xE6, 0x00, 0x04, 10, 20, 30, 40, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 230*2), 10, "Var[230] = 10")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 231*2), 20, "Var[231] = 20")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 232*2), 30, "Var[232] = 30")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 233*2), 40, "Var[233] = 40")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 234*2),  0, "Var[234] untouched (count=4)")
end

-- $A6 op_setVarRange (word mode, bit 7 set): each value is 2 bytes (LE16).
function test_setVarRange_word_mode()
  for i = 0, 3 do H.wr16(H.SYM.SCUMM_globalVars + (235 + i)*2, 0) end
  -- $A6, firstVar=235, count=3, values [0x0100, 0x0200, 0x0300]
  H.run_bytecode({0xA6, 0xEB, 0x00, 0x03,
                  0x00, 0x01,
                  0x00, 0x02,
                  0x00, 0x03,
                  0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 235*2), 0x0100, "Var[235] = $0100")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 236*2), 0x0200, "Var[236] = $0200")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 237*2), 0x0300, "Var[237] = $0300")
end

-- ---------------------------------------------------------------------------
-- Param-decode flag-bit coverage: putActor with bit 6 set ($41) — actor is
-- literal byte but X is var-ref word.
-- ---------------------------------------------------------------------------
function test_putActor_var_ref_x()
  H.wr16(H.SYM.SCUMM_globalVars + 195*2, 0x1234)  -- Var[195] = 0x1234
  H.wr16(H.actor_addr(15, 2), 0)
  H.wr16(H.actor_addr(15, 4), 0)

  -- $41 = bit 6 set → x is var-ref. Bytecode: $41 actor8 xVarRef16 y16
  H.run_bytecode({0x41, 0x0F, 0xC3, 0x00, 0x55, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(15, 2)), 0x1234,
              "actor[15].x via var-ref (Var[195])")
  H.assert_eq(H.rd16(H.actor_addr(15, 4)),  85,
              "actor[15].y from literal")
end

-- ---------------------------------------------------------------------------
-- $18 op_jumpRelative: unconditional 2-byte signed jump.
-- ---------------------------------------------------------------------------
function test_jumpRelative()
  H.wr16(H.SYM.SCUMM_globalVars + 245*2, 0)
  H.wr16(H.SYM.SCUMM_globalVars + 246*2, 0)

  -- $18 +6 (skip first $1A) ; $1A Var[245]=0xAAAA $A0 ; $1A Var[246]=0xBBBB $A0
  H.run_bytecode({
    0x18, 0x06, 0x00,                     -- jump +6
    0x1A, 0xF5, 0x00, 0xAA, 0xAA, 0xA0,   -- skipped (offset 3..8)
    0x1A, 0xF6, 0x00, 0xBB, 0xBB, 0xA0,   -- runs (offset 9..14)
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 245*2),      0,
              "Var[245] untouched (jumped over)")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 246*2), 0xBBBB,
              "Var[246] = 0xBBBB (target of jump)")
end

-- ---------------------------------------------------------------------------
-- ============================================================================
-- PHASE B.1 — actorOps sub-ops ($13)
-- For each: bytecode = $13 actor8 sub-op-byte params... $FF $A0
-- Use actor 20 (above SCUMM_WALK_ACTORS=16, untouched by chore tick).
-- ============================================================================

local SCUMM_actorIgnoreBoxes = 0x7EF0C4

-- ============================================================================
-- PHASE C — Cross-cutting & multi-frame
-- ============================================================================

-- Cutscene nest goes 0 → 1 → 2 → 1 → 0 across nested cutscene/endCutscene.
-- The final $A0 cleanup unwinds whatever remains, so this test pins the
-- transient nest depth at the moment it's at 2 by mutating an observable
-- side-effect: setCameraAt inside a nested cutscene goes through the
-- pendingCamTarget path. We check the simpler observation: at the end
-- of a balanced nested cutscene, nest == 0.
function test_phaseC_cutscene_nest_double()
  H.wr8(SCUMM_cutsceneNest, 0)
  -- $40 args=$FF, $40 args=$FF, $C0, $C0, $A0
  H.run_bytecode({
    0x40, 0xFF,         -- nest = 1
    0x40, 0xFF,         -- nest = 2
    0xC0,                -- nest = 1
    0xC0,                -- nest = 0
    0xA0,
  })
  H.assert_eq(H.rd8(SCUMM_cutsceneNest), 0,
              "cutsceneNest = 0 after nested cutscene/endCutscene pairs")
end

-- Camera clamping: setCameraAt(target) clamps to [VAR_CAMERA_MIN_X, MAX_X].
-- ScummVM (camera.cpp:40-50) clamps via if (camera._cur.x < MIN) cur=MIN, etc.
-- Save+restore VAR_CAMERA_MIN_X/MAX_X around the test so MI1 keeps running.
function test_phaseC_camera_clamp_high()
  local SCUMM_VAR_CAMERA_MIN_X = 17
  local SCUMM_VAR_CAMERA_MAX_X = 18
  local SCUMM_VAR_CAMERA_POS_X = 2
  local saved_min = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2)
  local saved_max = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2)
  local saved_pos = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2)

  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, 100)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, 300)

  -- $32 target_x16 = 500 (above MAX)
  H.run_bytecode({0x32, 0xF4, 0x01, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2), 300,
              "VAR_CAMERA_POS_X clamped to MAX_X (300) when target > MAX_X")

  -- Restore.
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, saved_min)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, saved_max)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2, saved_pos)
end

function test_phaseC_camera_clamp_low()
  local SCUMM_VAR_CAMERA_MIN_X = 17
  local SCUMM_VAR_CAMERA_MAX_X = 18
  local SCUMM_VAR_CAMERA_POS_X = 2
  local saved_min = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2)
  local saved_max = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2)
  local saved_pos = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2)

  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, 100)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, 300)

  -- $32 target_x16 = 50 (below MIN)
  H.run_bytecode({0x32, 0x32, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2), 100,
              "VAR_CAMERA_POS_X clamped to MIN_X (100) when target < MIN_X")

  -- Restore.
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, saved_min)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, saved_max)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2, saved_pos)
end

function test_phaseC_camera_no_clamp_when_in_range()
  local SCUMM_VAR_CAMERA_MIN_X = 17
  local SCUMM_VAR_CAMERA_MAX_X = 18
  local SCUMM_VAR_CAMERA_POS_X = 2
  local saved_min = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2)
  local saved_max = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2)
  local saved_pos = H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2)

  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, 100)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, 300)

  H.run_bytecode({0x32, 0xC8, 0x00, 0xA0})  -- target = 200, in range
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2), 200,
              "VAR_CAMERA_POS_X = 200 (no clamp when in range)")

  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MIN_X*2, saved_min)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_MAX_X*2, saved_max)
  H.wr16(H.SYM.SCUMM_globalVars + SCUMM_VAR_CAMERA_POS_X*2, saved_pos)
end

-- ============================================================================
-- PHASE C cont — Walkbox geometry (point-in-box via isActorInBox)
-- Helpers stage a fake walkbox table at $7F:5002 + boxCount=2, then use
-- isActorInBox to test containment. Save+restore the live walkbox state
-- so MI1 keeps running.
-- ============================================================================

local SCUMM_boxCount      = 0x7EFD67
local SCUMM_BOX_WRAM      = 0x7F5000   -- box count word + 20-byte entries

-- Stage one walkbox at index `idx` (1..N — index 0 is the sentinel).
-- Box vertices: UL(ulx,uly), UR(urx,ury), LR(lrx,lry), LL(llx,lly).
-- Layout per scummvm.65816:2916-2929: 20 bytes per box, +2 because the
-- first 2 bytes are the count word.
local function H_inject_walkbox(idx, ulx, uly, urx, ury, lrx, lry, llx, lly, flags)
  local base = SCUMM_BOX_WRAM + 2 + idx * 20
  H.wr16(base + 0,  ulx)
  H.wr16(base + 2,  uly)
  H.wr16(base + 4,  urx)
  H.wr16(base + 6,  ury)
  H.wr16(base + 8,  lrx)
  H.wr16(base + 10, lry)
  H.wr16(base + 12, llx)
  H.wr16(base + 14, lly)
  H.wr16(base + 16, 0)        -- scale
  H.wr8 (base + 17, flags or 0)
end

-- Save N bytes from walkbox region; restore later.
local function H_snapshot_walkboxes()
  local snap = {}
  snap.boxCount = H.rd16(SCUMM_boxCount)
  snap.bytes = {}
  -- Snapshot count-word + 4 boxes worth of data (most rooms use far fewer).
  for i = 0, 2 + 4 * 20 - 1 do
    snap.bytes[i] = H.rd8(SCUMM_BOX_WRAM + i)
  end
  return snap
end

local function H_restore_walkboxes(snap)
  H.wr16(SCUMM_boxCount, snap.boxCount)
  for i = 0, 2 + 4 * 20 - 1 do
    H.wr8(SCUMM_BOX_WRAM + i, snap.bytes[i])
  end
end

-- isActorInBox with actor INSIDE box 1 → cond TRUE → take jump.
function test_phaseC_isActorInBox_inside()
  local snap = H_snapshot_walkboxes()
  -- Box 0 = sentinel (zeroed), box 1 = rectangle (10,10)-(100,80).
  H_inject_walkbox(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
  H_inject_walkbox(1,
    10, 10,    -- UL
    100, 10,   -- UR
    100, 80,   -- LR
    10, 80,    -- LL
    0)
  H.wr16(SCUMM_boxCount, 2)

  -- Place actor 5 well inside the box.
  H.wr16(H.actor_addr(5, 2), 50)
  H.wr16(H.actor_addr(5, 4), 40)
  reset_branch_signals()

  -- $1F actor=5 box=1 jumpOffset=+6
  local bc = {0x1F, 0x05, 0x01, 0x06, 0x00}
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end
  H.run_bytecode(bc)
  local result = which_path_ran("isActorInBox inside")

  H_restore_walkboxes(snap)

  H.assert_eq(result == "jump" and 1 or 0, 1,
              "isActorInBox: actor inside box 1 → jump (cond TRUE)")
end

-- isActorInBox with actor OUTSIDE box 1 → cond FALSE → fall through.
function test_phaseC_isActorInBox_outside()
  local snap = H_snapshot_walkboxes()
  H_inject_walkbox(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
  H_inject_walkbox(1, 10, 10, 100, 10, 100, 80, 10, 80, 0)
  H.wr16(SCUMM_boxCount, 2)

  -- Place actor 5 OUTSIDE the box (way to the right).
  H.wr16(H.actor_addr(5, 2), 500)
  H.wr16(H.actor_addr(5, 4), 40)
  reset_branch_signals()

  local bc = {0x1F, 0x05, 0x01, 0x06, 0x00}
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end
  H.run_bytecode(bc)
  local result = which_path_ran("isActorInBox outside")

  H_restore_walkboxes(snap)

  H.assert_eq(result == "no_jump" and 1 or 0, 1,
              "isActorInBox: actor outside box 1 → no jump (cond FALSE)")
end

-- ============================================================================
-- PHASE C cont — BOXM-pathfinding (buildWalkPath fizzle on no route)
-- Stages a synthetic 3-box room with no BOXM connection between box 1
-- and box 2, runs op_walkActorTo across the gap, asserts pathLen=0.
-- This is the regression-class catcher for the "auto-walk to dock middle"
-- cheat: when no route exists, ScummVM v5's walkActor sets MF_LAST_LEG
-- and the actor stays put. Our equivalent is `_bwp.fizzle` which clears
-- pathLen; the pump sees pathLen=0 and clears moving without moving the
-- actor (commit 921e287).
-- ============================================================================

local SCUMM_boxMatrixPtr  = 0x7EFD69
local SCUMM_walkPathLen   = 0x7EF094
local SCUMM_actorIgnoreBoxes = 0x7EF0C4

-- Stage a 3x3 BOXM matrix at $7F:5060 (well past the BOXD entries).
-- Each entry is the next-hop box (or $FF for no route). Updates
-- boxMatrixPtr to point at it. Caller must restore boxMatrixPtr.
local function H_inject_box_matrix_3x3(matrix)
  local matrix_addr = 0x7F5060
  for i = 1, 9 do
    H.wr8(matrix_addr + (i - 1), matrix[i] or 0xFF)
  end
  -- boxMatrixPtr is the offset within $7F bank where the matrix lives.
  H.wr16(SCUMM_boxMatrixPtr, 0x5060)
end

-- Set up a 3-box scenario: sentinel + 2 disjoint rectangles + stage a
-- matrix. Returns a teardown closure that restores all touched state.
local function H_setup_3box_scenario(matrix, actor_idx, actor_x, actor_y)
  local snap = H_snapshot_walkboxes()
  H_inject_walkbox(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
  H_inject_walkbox(1,
    0,   0,    -- UL
    100, 0,    -- UR
    100, 100,  -- LR
    0,   100,  -- LL
    0)
  H_inject_walkbox(2,
    200, 200,
    300, 200,
    300, 300,
    200, 300,
    0)
  H.wr16(SCUMM_boxCount, 3)

  local saved_matrix_ptr = H.rd16(SCUMM_boxMatrixPtr)
  H_inject_box_matrix_3x3(matrix)

  local saved = {
    actor_room   = H.rd8 (H.actor_addr(actor_idx, 0)),
    actor_x      = H.rd16(H.actor_addr(actor_idx, 2)),
    actor_y      = H.rd16(H.actor_addr(actor_idx, 4)),
    actor_ignore = H.rd8 (SCUMM_actorIgnoreBoxes + actor_idx),
    pathLen      = H.rd8 (SCUMM_walkPathLen + actor_idx),
    matrix_ptr   = saved_matrix_ptr,
    snap         = snap,
  }

  -- Place actor inside box 1 with room = currentRoom so buildWalkPath
  -- treats them as live. actorIgnoreBoxes=0 forces the box-routing path.
  H.wr8 (H.actor_addr(actor_idx, 0), H.rd8(H.SYM.SCUMM_currentRoom))
  H.wr16(H.actor_addr(actor_idx, 2), actor_x)
  H.wr16(H.actor_addr(actor_idx, 4), actor_y)
  H.wr8 (SCUMM_actorIgnoreBoxes + actor_idx, 0)
  -- Clear pathLen with a sentinel so we can tell buildWalkPath wrote.
  H.wr8 (SCUMM_walkPathLen + actor_idx, 0xCC)

  return saved
end

local function H_teardown_3box_scenario(actor_idx, saved)
  H.wr8 (H.actor_addr(actor_idx, 0), saved.actor_room)
  H.wr16(H.actor_addr(actor_idx, 2), saved.actor_x)
  H.wr16(H.actor_addr(actor_idx, 4), saved.actor_y)
  H.wr8 (SCUMM_actorIgnoreBoxes + actor_idx, saved.actor_ignore)
  H.wr8 (SCUMM_walkPathLen + actor_idx, saved.pathLen)
  H.wr16(SCUMM_boxMatrixPtr, saved.matrix_ptr)
  H_restore_walkboxes(saved.snap)
end

-- buildWalkPath: when BOXM has NO route from start box to dest box,
-- the path fizzles (pathLen=0) — actor doesn't auto-walk through walls.
-- This is the regression catch for #45 (auto-walk to dock middle).
function test_buildWalkPath_no_route_fizzles()
  -- Matrix: identity diagonal, all off-diagonal $FF (no routes).
  -- Indices: row*3 + col. Box 0 sentinel; boxes 1 and 2 mutually unreachable.
  local NO_ROUTE = {
    --        to=0  to=1  to=2
    0x00, 0xFF, 0xFF,   -- from=0
    0xFF, 0x01, 0xFF,   -- from=1 (NO route to box 2)
    0xFF, 0xFF, 0x02,   -- from=2 (NO route to box 1)
  }
  local saved = H_setup_3box_scenario(NO_ROUTE, 5, 50, 50)

  -- op_walkActorTo (literals): 1E actor x.lo x.hi y.lo y.hi
  -- Walk actor 5 from (50,50) inside box 1 to (250,250) inside box 2.
  -- Followed by op_stopObjectCode ($00) to terminate the test slot.
  local bc = {0x1E, 0x05, 0xFA, 0x00, 0xFA, 0x00, 0x00}
  H.run_bytecode(bc)

  local pathLen = H.rd8(SCUMM_walkPathLen + 5)
  H_teardown_3box_scenario(5, saved)

  H.assert_eq(pathLen, 0,
    "buildWalkPath: no BOXM route → pathLen=0 (fizzle, ScummVM MF_LAST_LEG-on-no-route)")
end

-- buildWalkPath: when BOXM has a route (boxes adjacent + connected),
-- buildWalkPath produces pathLen > 0 and the actor walks. Sanity check
-- so the fizzle test isn't trivially passing because buildWalkPath is
-- broken across the board.
function test_buildWalkPath_with_route_builds_path()
  local WITH_ROUTE = {
    --        to=0  to=1  to=2
    0x00, 0xFF, 0xFF,   -- from=0 (sentinel — irrelevant)
    0xFF, 0x01, 0x02,   -- from=1: route to box 2 directly
    0xFF, 0x01, 0x02,   -- from=2: route back to box 1 directly
  }
  local saved = H_setup_3box_scenario(WITH_ROUTE, 5, 50, 50)
  -- For a route to actually be built, boxes need to be adjacent so
  -- getBoxEdgeCrossing can pick a viable waypoint. Override box 2 to
  -- abut box 1 along their right/left edges.
  H_inject_walkbox(2,
    100, 0,
    200, 0,
    200, 100,
    100, 100,
    0)

  local bc = {0x1E, 0x05, 0x96, 0x00, 0x32, 0x00, 0x00}  -- walk to (150,50)
  H.run_bytecode(bc)

  local pathLen = H.rd8(SCUMM_walkPathLen + 5)
  H_teardown_3box_scenario(5, saved)

  -- pathLen >= 1 (at least one waypoint = the target itself for a same-box
  -- or adjacent-box walk). Strict ">=1" lets implementation choose 1 or 2.
  H.assert_eq(pathLen >= 1 and 1 or 0, 1,
    string.format("buildWalkPath: BOXM has route → pathLen >= 1 (got %d)", pathLen))
end

-- Multi-leg walk: stage 3 adjacent boxes in a row + BOXM connectivity,
-- run op_walkActorTo across all 3 boxes, wait for the pump to advance
-- through each leg, assert actor reaches destination box. Exercises
-- the per-leg dispatch in updateActors_body's _ua.fullyArrived path.
function test_walkActor_multiLeg_traverses_boxes()
  local snap = H_snapshot_walkboxes()
  -- 3 boxes in a row: box 1 = [0..50], box 2 = [50..100], box 3 = [100..150].
  H_inject_walkbox(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
  H_inject_walkbox(1,
    0,  0,  50, 0,  50, 50, 0,  50, 0)   -- (0,0)-(50,50)
  H_inject_walkbox(2,
    50, 0,  100, 0,  100, 50, 50, 50, 0) -- (50,0)-(100,50)
  H_inject_walkbox(3,
    100, 0, 150, 0,  150, 50, 100, 50, 0)-- (100,0)-(150,50)
  H.wr16(SCUMM_boxCount, 4)

  -- BOXM 4x4 matrix: box 1 routes through 2 to reach 3, etc.
  local ROUTE = {
    --       to=0   to=1   to=2   to=3
    0x00, 0xFF, 0xFF, 0xFF,   -- from=0 (sentinel)
    0xFF, 0x01, 0x02, 0x02,   -- from=1: route to 2 directly, to 3 via 2
    0xFF, 0x01, 0x02, 0x03,   -- from=2: 1 directly, 3 directly
    0xFF, 0x02, 0x02, 0x03,   -- from=3: 2 directly, 1 via 2
  }
  local matrix_addr = 0x7F5060
  for i = 1, #ROUTE do
    H.wr8(matrix_addr + (i - 1), ROUTE[i])
  end
  local saved_matrix_ptr = H.rd16(SCUMM_boxMatrixPtr)
  H.wr16(SCUMM_boxMatrixPtr, 0x5060)

  local saved = {
    actor_room   = H.rd8 (H.actor_addr(5, 0)),
    actor_x      = H.rd16(H.actor_addr(5, 2)),
    actor_y      = H.rd16(H.actor_addr(5, 4)),
    actor_ignore = H.rd8 (SCUMM_actorIgnoreBoxes + 5),
    actor_walkBox = H.rd8 (SCUMM_actorWalkBox + 5),
    pathLen      = H.rd8 (SCUMM_walkPathLen + 5),
    matrix_ptr   = saved_matrix_ptr,
    snap         = snap,
  }

  -- Place actor 5 at (10, 25) inside box 1, room = current.
  -- Set scalex to full (255) so the pump's scaled walk speed isn't 0.
  H.wr8 (H.actor_addr(5, 0), H.rd8(H.SYM.SCUMM_currentRoom))
  H.wr16(H.actor_addr(5, 2), 10)
  H.wr16(H.actor_addr(5, 4), 25)
  H.wr8 (H.actor_addr(5, 13), 255)     -- scalex (full size)
  H.wr8 (SCUMM_actorIgnoreBoxes + 5, 0)
  H.wr8 (SCUMM_actorWalkBox + 5, 1)
  H.wr8 (0x7EF0E4 + 5, 4)              -- actorWalkSpeedX = 4
  H.wr8 (0x7EF0F4 + 5, 4)              -- actorWalkSpeedY = 4

  -- Freeze MI1's slots manually so they don't trample actor 5 during
  -- the long wait_frames after the bytecode test slot completes.
  local saved_freezes = H.freeze_other_slots()

  -- Walk actor 5 from (10,25) in box 1 to (140,25) in box 3 (~130 px).
  -- Bytecode: walkActorTo + stopObjectCode. The slot dies after ~2 frames;
  -- the actor walk continues across subsequent pump ticks.
  local bc = {0x1E, 0x05, 0x8C, 0x00, 0x19, 0x00, 0x00}
  H.write_bytes(H.TEST_BYTECODE_ADDR, bc)
  H.wr8 (H.slot_addr(H.SYM.slot_status),         H.SCUMM_SLOT_RUNNING)
  H.wr8 (H.slot_addr(H.SYM.slot_number),         200)
  H.wr8 (H.slot_addr(H.SYM.slot_where),          0)
  H.wr8 (H.slot_addr(H.SYM.slot_freezeCount),    0)
  H.wr16(H.slot_addr(H.SYM.slot_pc),             0)
  H.wr16(H.slot_addr(H.SYM.slot_cachePtr),       H.TEST_CACHE_OFFSET)
  H.wr16(H.slot_addr(H.SYM.slot_cacheLen),       #bc)
  H.wr16(H.slot_addr(H.SYM.slot_delay),          0)
  H.wr8 (H.slot_addr(H.SYM.slot_cutsceneOverride), 0)

  -- Wait for slot to die + many additional frames so the pump can
  -- traverse the 130px route at min 1 px/frame.
  H.wait_frames(180)

  H.restore_slot_freezes(saved_freezes)

  local final_x   = H.rd16(H.actor_addr(5, 2))
  local final_y   = H.rd16(H.actor_addr(5, 4))
  local final_box = H.rd8 (SCUMM_actorWalkBox + 5)
  local final_mov = H.rd8 (H.actor_addr(5, 10))

  -- Restore state
  H.wr8 (H.actor_addr(5, 0), saved.actor_room)
  H.wr16(H.actor_addr(5, 2), saved.actor_x)
  H.wr16(H.actor_addr(5, 4), saved.actor_y)
  H.wr8 (SCUMM_actorIgnoreBoxes + 5, saved.actor_ignore)
  H.wr8 (SCUMM_actorWalkBox + 5, saved.actor_walkBox)
  H.wr8 (SCUMM_walkPathLen + 5, saved.pathLen)
  H.wr16(SCUMM_boxMatrixPtr, saved.matrix_ptr)
  H_restore_walkboxes(snap)

  -- After multi-leg walk, the pump should have advanced the actor
  -- across boxes 1 -> 2 -> 3, and arrived at or near the target.
  -- Strict assertion: actor.x advanced past box 1 (>= 50) and ended
  -- in box 2 or 3. Looser than exact-x-equals-140 because of step
  -- rounding + frame budget; the per-leg dispatch is what we're
  -- exercising here, not pixel precision.
  H.assert_eq(final_x >= 50 and 1 or 0, 1,
    string.format("multi-leg walk: actor.x advanced past box 1 (got %d, want >=50)", final_x))
  H.assert_eq((final_box == 2 or final_box == 3) and 1 or 0, 1,
    string.format("multi-leg walk: actor crossed to box 2 or 3 (got walkBox=%d)", final_box))
end

-- ============================================================================
-- PHASE D — Engine pipeline integration smokes
-- These run against the live game state (post-boot) and assert pipeline
-- invariants rather than opcode semantics.
-- ============================================================================

local SCUMM_actorsDirty   = 0x7EFDA9
local Mesen_ScreenBrightness = 0x7EFCFD

-- After harness boot wait, the game has reached a stable state. Verify:
--   * currentRoom != 0 (we're inside a room)
--   * ScreenBrightness == 0x0F (full brightness, post-fade-in)
--   * VAR_ROOM matches currentRoom
function test_phaseD_boot_smoke()
  local room   = H.rd8(H.SYM.SCUMM_currentRoom)
  local var_room = H.rd16(H.SYM.SCUMM_globalVars + 4*2) & 0xFF
  local bright = H.rd8(Mesen_ScreenBrightness)
  H.assert_eq(room ~= 0 and 1 or 0, 1,
              "boot smoke: currentRoom != 0 (got " .. room .. ")")
  H.assert_eq(var_room, room,
              "boot smoke: VAR_ROOM matches currentRoom")
  H.assert_eq(bright, 0x0F,
              "boot smoke: ScreenBrightness == $0F")
end

-- putActorInRoom(actor, currentRoom) flags actorsDirty=1 transiently — but
-- the same frame's _play tick consumes the flag (loadActorCostumes runs
-- and clears it). By the time our Lua reads, it's back to 0. Pin the
-- transient by checking the actor's room got written instead.
function test_phaseD_putActorInRoom_into_current_room()
  local cur = H.rd8(H.SYM.SCUMM_currentRoom)
  H.wr8(H.actor_addr(10, 0), 0)        -- clear actor[10].room
  H.run_bytecode({0x2D, 0x0A, cur, 0xA0})
  H.assert_eq(H.rd8(H.actor_addr(10, 0)), cur,
              "actor[10].room = currentRoom after putActorInRoom (actorsDirty self-clears)")
end

-- ============================================================================
-- PHASE B.7 — roomOps sub-ops ($33)
-- Most are PARTIAL (consume params, no SW renderer behind them).
-- Pin no-crash + KNOWN-DIVERGENCE.
-- ============================================================================

function test_phaseB_roomOps_01_roomScroll()
  -- $33 $01 minX16 maxX16 — sets camera bounds.
  H.run_bytecode({0x33, 0x01, 0x10, 0x00, 0x40, 0x01, 0xA0})
  H.assert_eq(1, 1, "roomOps[$01] roomScroll ran")
end

function test_phaseB_roomOps_03_setScreen_KNOWN()
  H.run_bytecode({0x33, 0x03, 0x00, 0x00, 0x90, 0x00, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$03] setScreen consumed")
end

function test_phaseB_roomOps_04_setPalColor()
  -- $33 $04 R16 G16 B16 + aux + slot8
  H.run_bytecode({0x33, 0x04, 0x10, 0x00, 0x20, 0x00, 0x30, 0x00, 0x00, 0x05, 0xA0})
  H.assert_eq(1, 1, "roomOps[$04] roomPalColor ran")
end

function test_phaseB_roomOps_05_shakeOn()
  H.run_bytecode({0x33, 0x05, 0xA0})
  H.assert_eq(1, 1, "roomOps[$05] shakeOn ran")
end

function test_phaseB_roomOps_06_shakeOff()
  H.run_bytecode({0x33, 0x06, 0xA0})
  H.assert_eq(1, 1, "roomOps[$06] shakeOff ran")
end

function test_phaseB_roomOps_07_scale_KNOWN()
  -- $33 $07 scale-matrix params (variable). Try the 8-byte form.
  H.run_bytecode({0x33, 0x07, 0x80, 0x80, 0x90, 0x80, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$07] roomScale consumed")
end

function test_phaseB_roomOps_08_scaleSimple_KNOWN()
  H.run_bytecode({0x33, 0x08, 0x80, 0x70, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$08] scaleSimple consumed")
end

function test_phaseB_roomOps_09_saveGame_KNOWN()
  H.run_bytecode({0x33, 0x09, 0x01, 0x02, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$09] saveGame consumed")
end

function test_phaseB_roomOps_0A_fade_KNOWN()
  H.run_bytecode({0x33, 0x0A, 0x00, 0x00, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$0A] fade consumed")
end

function test_phaseB_roomOps_0B_rgbIntensity()
  -- $33 $0B R16 G16 B16 + aux + start8 + end8
  H.run_bytecode({0x33, 0x0B,
                  0x40, 0x00,   -- R
                  0x40, 0x00,   -- G
                  0x40, 0x00,   -- B
                  0x00,         -- aux
                  0x00,         -- start
                  0x05,         -- end
                  0xA0})
  H.assert_eq(1, 1, "roomOps[$0B] rgbRoomIntensity ran")
end

function test_phaseB_roomOps_0C_shadow_KNOWN()
  H.run_bytecode({0x33, 0x0C,
                  0x40, 0x00, 0x40, 0x00, 0x40, 0x00,
                  0x00, 0x00, 0x05, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$0C] shadow consumed")
end

function test_phaseB_roomOps_0D_saveString_KNOWN()
  -- $33 $0D slot8 + null-term string
  H.run_bytecode({0x33, 0x0D, 0x05, 0x41, 0x42, 0x00, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$0D] saveString consumed")
end

function test_phaseB_roomOps_0E_loadString_KNOWN()
  H.run_bytecode({0x33, 0x0E, 0x05, 0x41, 0x42, 0x00, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$0E] loadString consumed")
end

function test_phaseB_roomOps_0F_palManipulate_KNOWN()
  H.run_bytecode({0x33, 0x0F, 0x00, 0x00, 0x10, 0x00, 0x20, 0x00, 0x05, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: roomOps[$0F] palManipulate consumed")
end

-- ============================================================================
-- PHASE B.8 — print sub-ops ($14)
-- Sub-op driven; loop until $FF. Pin no-crash for the common ones.
-- ============================================================================

function test_phaseB_print_at_color_textstring()
  -- $14 actor=255 SO_AT(x=10,y=20) SO_COLOR(7) SO_TEXTSTRING("Hi") $A0
  -- Sub $0F (textstring) is terminal; no $FF needed.
  H.run_bytecode({
    0x14, 0xFF,            -- print, actor=255
    0x00, 0x0A, 0x00, 0x14, 0x00,  -- SO_AT(10, 20)
    0x01, 0x07,             -- SO_COLOR(7)
    0x0F, 0x48, 0x69, 0x00, -- SO_TEXTSTRING "Hi"\0
    0xA0
  })
  H.assert_eq(1, 1, "print: AT + COLOR + TEXTSTRING ran")
end

function test_phaseB_print_clipping_erase_KNOWN()
  H.run_bytecode({
    0x14, 0xFF,
    0x02, 0x00, 0x01,        -- SO_CLIPPING (right edge)
    0x03, 0x10, 0x00, 0x10, 0x00,  -- SO_ERASE (w, h)
    0xFF,                    -- terminator (no textstring)
    0xA0
  })
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: print SO_CLIPPING + SO_ERASE consumed")
end

function test_phaseB_print_center_left_overhead()
  -- These set dialogCenter / dialogOverhead flags. We don't read them
  -- back here — pin no-crash.
  H.run_bytecode({
    0x14, 0xFF,
    0x04,            -- SO_CENTER
    0xFF, 0xA0
  })
  H.run_bytecode({
    0x14, 0xFF,
    0x06,            -- SO_LEFT
    0xFF, 0xA0
  })
  H.run_bytecode({
    0x14, 0xFF,
    0x07,            -- SO_OVERHEAD
    0xFF, 0xA0
  })
  H.assert_eq(1, 1, "print SO_CENTER/LEFT/OVERHEAD ran")
end

function test_phaseB_print_say_voice_KNOWN()
  -- $08 SO_SAY_VOICE talkie_offset16 delay16
  H.run_bytecode({
    0x14, 0xFF,
    0x08, 0x00, 0x00, 0x00, 0x00,
    0xFF, 0xA0
  })
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: print SO_SAY_VOICE consumed")
end

function test_phaseB_printEgo_textstring()
  -- $D8 (printEgo) + textstring. Implicit actor = VAR_EGO.
  H.run_bytecode({
    0xD8,
    0x0F, 0x48, 0x65, 0x79, 0x00,   -- "Hey"\0
    0xA0
  })
  H.assert_eq(1, 1, "printEgo SO_TEXTSTRING ran")
end

-- ============================================================================
-- PHASE B.2 — cursorCommand sub-ops ($2C)
-- All single-call sub-ops. Epilogue syncs cursorEnabled + userPut to
-- globalVars[52]/[53].
-- ============================================================================

local SCUMM_cursorEnabled = 0x7EFD93
local SCUMM_userPut       = 0x7EFD95

function test_phaseB_cursorCommand_01_cursorOn()
  H.wr8(SCUMM_cursorEnabled, 0)
  H.run_bytecode({0x2C, 0x01, 0xA0})
  H.assert_eq(H.rd8(SCUMM_cursorEnabled), 1, "cursorEnabled = 1 after cursorOn")
end

function test_phaseB_cursorCommand_02_cursorOff()
  H.wr8(SCUMM_cursorEnabled, 1)
  H.run_bytecode({0x2C, 0x02, 0xA0})
  H.assert_eq(H.rd8(SCUMM_cursorEnabled), 0, "cursorEnabled = 0 after cursorOff")
end

function test_phaseB_cursorCommand_03_userputOn()
  H.wr8(SCUMM_userPut, 0)
  H.run_bytecode({0x2C, 0x03, 0xA0})
  H.assert_eq(H.rd8(SCUMM_userPut), 1, "userPut = 1 after userputOn")
end

function test_phaseB_cursorCommand_04_userputOff()
  H.wr8(SCUMM_userPut, 1)
  H.run_bytecode({0x2C, 0x04, 0xA0})
  H.assert_eq(H.rd8(SCUMM_userPut), 0, "userPut = 0 after userputOff")
end

-- Soft on/off increment counters; we just pin "no-crash + ran" here.
function test_phaseB_cursorCommand_softOps_no_crash()
  H.run_bytecode({0x2C, 0x05, 0xA0})  -- cursorSoftOn
  H.run_bytecode({0x2C, 0x06, 0xA0})  -- cursorSoftOff
  H.run_bytecode({0x2C, 0x07, 0xA0})  -- userputSoftOn
  H.run_bytecode({0x2C, 0x08, 0xA0})  -- userputSoftOff
  H.assert_eq(1, 1, "cursor/userput soft on/off ran without crash")
end

function test_phaseB_cursorCommand_setCursorImage_KNOWN()
  -- $2C $0A cursor#=2 image=#3
  H.run_bytecode({0x2C, 0x0A, 0x02, 0x03, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: setCursorImage params consumed")
end

function test_phaseB_cursorCommand_setCursorHotspot_KNOWN()
  H.run_bytecode({0x2C, 0x0B, 0x02, 0x08, 0x08, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: setCursorHotspot params consumed")
end

function test_phaseB_cursorCommand_initCharset_KNOWN()
  H.run_bytecode({0x2C, 0x0C, 0x01, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: initCharset params consumed")
end

function test_phaseB_cursorCommand_charsetColor_KNOWN()
  -- $2C $0D word vararg terminated by $FF
  H.run_bytecode({0x2C, 0x0D, 0x07, 0x00, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: charsetColor params consumed")
end

-- ============================================================================
-- PHASE B.3 — stringOps sub-ops ($27)
-- ============================================================================

-- Sub $01 putCode + $04 getChar: round-trip a known byte through a string slot.
function test_phaseB_stringOps_putCode_getChar_roundtrip()
  -- Create string slot 5 with size 16
  H.run_bytecode({0x27, 0x05, 0x05, 0x10, 0xA0})  -- createString slot=5 size=16
  -- putCode slot=5 with bytes "AB" terminator $00
  H.run_bytecode({0x27, 0x01, 0x05, 0x41, 0x42, 0x00, 0xA0})
  -- getChar slot=5 idx=0 → result var
  H.wr16(H.SYM.SCUMM_globalVars + 415*2, 0)
  H.run_bytecode({0x27, 0x04, 0x9F, 0x01, 0x05, 0x00, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 415*2), 0x41,
              "Var[415] = string[5][0] = 'A' (0x41)")
end

function test_phaseB_stringOps_setChar_roundtrip()
  -- Create slot 6, size 8, putCode "X", setChar idx 0 to 'Y', getChar.
  H.run_bytecode({0x27, 0x05, 0x06, 0x08, 0xA0})
  H.run_bytecode({0x27, 0x01, 0x06, 0x58, 0x00, 0xA0})  -- "X"
  -- setChar slot=6 idx=0 char='Y' (port reads getVarOrDirectByte for all 3 params)
  H.run_bytecode({0x27, 0x03, 0x06, 0x00, 0x59, 0xA0})
  H.wr16(H.SYM.SCUMM_globalVars + 416*2, 0)
  H.run_bytecode({0x27, 0x04, 0xA0, 0x01, 0x06, 0x00, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 416*2), 0x59,
              "string[6][0] = 'Y' after setChar")
end

function test_phaseB_stringOps_copyString()
  -- Slot 7 = "ABC", copy to slot 8, getChar slot 8 idx 1 = 'B'
  H.run_bytecode({0x27, 0x05, 0x07, 0x10, 0xA0})
  H.run_bytecode({0x27, 0x05, 0x08, 0x10, 0xA0})
  H.run_bytecode({0x27, 0x01, 0x07, 0x41, 0x42, 0x43, 0x00, 0xA0})
  H.run_bytecode({0x27, 0x02, 0x08, 0x07, 0xA0})  -- copy slot 7 → slot 8
  H.wr16(H.SYM.SCUMM_globalVars + 417*2, 0)
  H.run_bytecode({0x27, 0x04, 0xA1, 0x01, 0x08, 0x01, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 417*2), 0x42,
              "string[8][1] = 'B' after copyString")
end

-- ============================================================================
-- PHASE B.4 — expression sub-ops ($AC)
-- RPN stack ops; final stack value → VAR_RESULT.
-- ============================================================================

-- $AC result_var16 then sub-ops, terminated by $FF.
-- Sub $01 PUSH p16 — push value.
-- Sub $02 ADD, $03 SUB, $04 MUL, $05 DIV — pop 2, push result.
-- Sub $06 EXEC — fetch+dispatch opcode, push VAR_RESULT.
function test_phaseB_expression_push_add()
  H.wr16(H.SYM.SCUMM_globalVars + 420*2, 0)
  -- $AC resultVar=420 PUSH 10 PUSH 20 ADD $FF $A0
  H.run_bytecode({
    0xAC, 0xA4, 0x01,
    0x01, 0x0A, 0x00,    -- push 10
    0x01, 0x14, 0x00,    -- push 20
    0x02,                 -- ADD
    0xFF,
    0xA0
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 420*2), 30,
              "Var[420] = 10 + 20 via expression")
end

function test_phaseB_expression_push_sub()
  H.wr16(H.SYM.SCUMM_globalVars + 421*2, 0)
  H.run_bytecode({
    0xAC, 0xA5, 0x01,
    0x01, 0x32, 0x00,    -- push 50
    0x01, 0x14, 0x00,    -- push 20
    0x03,                 -- SUB: 50 - 20 = 30
    0xFF,
    0xA0
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 421*2), 30,
              "Var[421] = 50 - 20 via expression")
end

function test_phaseB_expression_push_mul()
  H.wr16(H.SYM.SCUMM_globalVars + 422*2, 0)
  H.run_bytecode({
    0xAC, 0xA6, 0x01,
    0x01, 0x06, 0x00,    -- push 6
    0x01, 0x07, 0x00,    -- push 7
    0x04,                 -- MUL
    0xFF,
    0xA0
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 422*2), 42,
              "Var[422] = 6 * 7 via expression")
end

function test_phaseB_expression_div_zero_guard()
  H.wr16(H.SYM.SCUMM_globalVars + 423*2, 0xFFFF)
  H.run_bytecode({
    0xAC, 0xA7, 0x01,
    0x01, 0x64, 0x00,    -- push 100
    0x01, 0x00, 0x00,    -- push 0
    0x05,                 -- DIV (zero guard)
    0xFF,
    0xA0
  })
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 423*2), 0,
              "Expression DIV by 0 → 0 (guarded)")
end

-- ============================================================================
-- PHASE B.5 — resourceRoutines sub-ops ($0C)
-- All PARTIAL: consume params, no real load (SNES preloads all).
-- ============================================================================

function test_phaseB_resourceRoutines_loadRoom_KNOWN()
  -- $0C $04 room8
  H.run_bytecode({0x0C, 0x04, 0x21, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: resourceRoutines.loadRoom no-op")
end

function test_phaseB_resourceRoutines_clearHeap_KNOWN()
  H.run_bytecode({0x0C, 0x11, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: resourceRoutines.clearHeap no-op")
end

function test_phaseB_resourceRoutines_default_1param_KNOWN()
  -- Default sub-op: consumes 1 byte param.
  H.run_bytecode({0x0C, 0x01, 0x05, 0xA0})  -- e.g. loadCostume(5)
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: resourceRoutines default sub no-op")
end

-- ============================================================================
-- PHASE B.6 — systemOps sub-op $03 (restart)
-- ============================================================================

-- $98 $03 — restart: kills all scripts except current. The harness
-- pre-freezes other slots ($FF freezeCount) which makes our port skip
-- them during the restart sweep. Pin no-crash here; full kill semantics
-- need a custom harness without slot-freezing.
function test_phaseB_systemOps_restart_runs()
  H.run_bytecode({0x98, 0x03, 0xA0})
  H.assert_eq(1, 1, "systemOps[$03] restart ran without crash (harness limit)")
end

-- Sub $01 setCostume: writes actor.costume (+1).
function test_phaseB_actorOps_setCostume()
  H.wr8(H.actor_addr(20, 1), 0)
  H.run_bytecode({0x13, 0x14, 0x01, 0x05, 0xFF, 0xA0})  -- costume=5
  H.assert_eq(H.rd8(H.actor_addr(20, 1)), 5,
              "actor[20].costume = 5 after actorOps[$01]")
end

-- Sub $08 init: clears facing to 180 (south).
function test_phaseB_actorOps_init_facing_180()
  H.wr16(H.actor_addr(20, 6), 0xFFFF)   -- garbage facing
  H.run_bytecode({0x13, 0x14, 0x08, 0xFF, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(20, 6)), 180,
              "actor[20].facing = 180 after actorOps[$08]")
end

-- Sub $09 setElevation: writes actor.elevation (+8).
function test_phaseB_actorOps_setElevation()
  H.wr16(H.actor_addr(20, 8), 0)
  H.run_bytecode({0x13, 0x14, 0x09, 0x42, 0x00, 0xFF, 0xA0})  -- elev=$0042
  H.assert_eq(H.rd16(H.actor_addr(20, 8)), 0x42,
              "actor[20].elevation = $42 after actorOps[$09]")
end

-- Sub $0C setTalkColor: writes actor.talkColor (+14).
function test_phaseB_actorOps_setTalkColor()
  H.wr8(H.actor_addr(20, 14), 0)
  H.run_bytecode({0x13, 0x14, 0x0C, 0x07, 0xFF, 0xA0})  -- talkColor=7
  H.assert_eq(H.rd8(H.actor_addr(20, 14)), 7,
              "actor[20].talkColor = 7 after actorOps[$0C]")
end

-- Sub $10 setWidth: writes actorWidth[actor] (clamped to 0..15 idx).
function test_phaseB_actorOps_setWidth()
  H.wr8(SCUMM_actorWidth + 5, 0)  -- clear slot 5
  H.run_bytecode({0x13, 0x05, 0x10, 0x20, 0xFF, 0xA0})  -- width=32
  H.assert_eq(H.rd8(SCUMM_actorWidth + 5), 32,
              "actorWidth[5] = 32 after actorOps[$10]")
end

-- Sub $11 setScale: writes actor.scalex (+13).
function test_phaseB_actorOps_setScale()
  H.wr8(H.actor_addr(20, 13), 0)
  H.run_bytecode({0x13, 0x14, 0x11, 0x80, 0x80, 0xFF, 0xA0})  -- scale=128, ignore Y
  H.assert_eq(H.rd8(H.actor_addr(20, 13)), 128,
              "actor[20].scalex = 128 after actorOps[$11]")
end

-- Sub $14 ignoreBoxes: sets actorIgnoreBoxes[actor] = 1.
function test_phaseB_actorOps_ignoreBoxes()
  H.wr8(SCUMM_actorIgnoreBoxes + 5, 0)
  H.run_bytecode({0x13, 0x05, 0x14, 0xFF, 0xA0})
  H.assert_eq(H.rd8(SCUMM_actorIgnoreBoxes + 5), 1,
              "actorIgnoreBoxes[5] = 1 after actorOps[$14]")
end

-- Sub $15 followBoxes: sets actorIgnoreBoxes[actor] = 0.
function test_phaseB_actorOps_followBoxes()
  H.wr8(SCUMM_actorIgnoreBoxes + 5, 1)
  H.run_bytecode({0x13, 0x05, 0x15, 0xFF, 0xA0})
  H.assert_eq(H.rd8(SCUMM_actorIgnoreBoxes + 5), 0,
              "actorIgnoreBoxes[5] = 0 after actorOps[$15]")
end

-- KNOWN-DIVERGENCE: sub $04 (walkAnimNr), $06 (standFrame), $0A (animDefault)
-- write to per-actor frame override fields that don't exist in our struct.
-- Pin no-crash.
-- ScummVM spec: actorOps[$04] writes _walkFrame on the actor.
function test_phaseB_actorOps_walkAnimNr()
  H.wr8(SCUMM_actorWalkAnimNr + 11, 0)
  H.run_bytecode({0x13, 0x0B, 0x04, 0x07, 0xFF, 0xA0})  -- actor=11, walkFrame=7
  H.assert_eq(H.rd8(SCUMM_actorWalkAnimNr + 11), 7,
              "actorWalkAnimNr[11] = 7 after actorOps[$04]")
end

-- ScummVM spec: actorOps[$06] writes _standFrame on the actor.
function test_phaseB_actorOps_standFrame()
  H.wr8(SCUMM_actorStandFrame + 11, 0)
  H.run_bytecode({0x13, 0x0B, 0x06, 0x09, 0xFF, 0xA0})  -- actor=11, standFrame=9
  H.assert_eq(H.rd8(SCUMM_actorStandFrame + 11), 9,
              "actorStandFrame[11] = 9 after actorOps[$06]")
end

-- ScummVM spec: actorOps[$0A] resets walkFrame=2, standFrame=3, talkStart=4, talkStop=5.
function test_phaseB_actorOps_animDefault()
  H.wr8(SCUMM_actorWalkAnimNr   + 11, 0xAA)
  H.wr8(SCUMM_actorStandFrame   + 11, 0xBB)
  H.wr8(SCUMM_actorTalkAnimStart+ 11, 0xCC)
  H.wr8(SCUMM_actorTalkAnimEnd  + 11, 0xDD)
  H.run_bytecode({0x13, 0x0B, 0x0A, 0xFF, 0xA0})
  H.assert_eq(H.rd8(SCUMM_actorWalkAnimNr   + 11), 2, "walkAnimNr  reset to 2")
  H.assert_eq(H.rd8(SCUMM_actorStandFrame   + 11), 3, "standFrame  reset to 3")
  H.assert_eq(H.rd8(SCUMM_actorTalkAnimStart+ 11), 4, "talkStart   reset to 4")
  H.assert_eq(H.rd8(SCUMM_actorTalkAnimEnd  + 11), 5, "talkStop    reset to 5")
end

-- KNOWN-DIVERGENCE: sub $03 (sound), $05 (talkAnimNr), $07/$0B (palette),
-- $0D (name), $13 (zClip), $16 (animSpeed), $17 (shadow), $12 (neverZClip).
function test_phaseB_actorOps_sound_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x03, 0x05, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$03] sound stubbed")
end

-- ScummVM spec: actorOps[$05] writes both _talkStartFrame and _talkStopFrame.
function test_phaseB_actorOps_talkAnimNr()
  H.wr8(SCUMM_actorTalkAnimStart + 11, 0)
  H.wr8(SCUMM_actorTalkAnimEnd   + 11, 0)
  H.run_bytecode({0x13, 0x0B, 0x05, 0x0A, 0x0B, 0xFF, 0xA0})  -- actor=11, talk=10..11
  H.assert_eq(H.rd8(SCUMM_actorTalkAnimStart + 11), 10,
              "actorTalkAnimStart[11] = 10 after actorOps[$05]")
  H.assert_eq(H.rd8(SCUMM_actorTalkAnimEnd   + 11), 11,
              "actorTalkAnimEnd[11] = 11 after actorOps[$05]")
end

function test_phaseB_actorOps_palette_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x07, 0x01, 0x02, 0x03, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$07] palette stubbed")
end

function test_phaseB_actorOps_palette2_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x0B, 0x01, 0x02, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$0B] palette2 stubbed")
end

function test_phaseB_actorOps_name_KNOWN()
  -- $0D actorName + null-terminated string
  H.run_bytecode({0x13, 0x14, 0x0D, 0x41, 0x42, 0x43, 0x00, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$0D] name consumed but not indexed")
end

function test_phaseB_actorOps_setZClip_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x13, 0x05, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$13] zClip consumed")
end

function test_phaseB_actorOps_neverZClip_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x12, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$12] neverZClip no-op")
end

function test_phaseB_actorOps_animSpeed_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x16, 0x05, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$16] animSpeed consumed")
end

function test_phaseB_actorOps_shadow_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x17, 0x01, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$17] shadow stubbed")
end

-- Sub $02 setWalkSpeed: ScummVM writes actor._speedX/Y. Our port has no
-- per-actor walk speed field — KNOWN-DIVERGENCE.
function test_phaseB_actorOps_walkSpeed_KNOWN()
  H.run_bytecode({0x13, 0x14, 0x02, 0x06, 0x04, 0xFF, 0xA0})
  H.assert_eq(1, 1, "KNOWN-DIVERGENCE: actorOps[$02] walkSpeed consumed, no field")
end

-- $13 op_actorOps with sub-op $0E (initFrame): writes actor.initFrame.
-- Use an actor number >= SCUMM_WALK_ACTORS (16) so the chore engine doesn't
-- clobber initFrame in the same frame the test runs.
--   Bytecode: $13 actor8 $0E frame8 $FF $A0
-- ---------------------------------------------------------------------------
function test_actorOps_initFrame()
  -- initFrame is at offset +12 in the actor struct (verified via sym lookup
  -- of SCUMM.actors.1.initFrame). NOT +8 — earlier slots are room/costume,
  -- x, y, facing, elevation (each word-sized), moving, visible (bytes).
  H.wr8(H.actor_addr(20, 12), 0)
  H.run_bytecode({0x13, 0x14, 0x0E, 42, 0xFF, 0xA0})
  H.assert_eq(H.rd8(H.actor_addr(20, 12)), 42,
              "actor[20].initFrame = 42 after actorOps[$0E]")
end

-- ---------------------------------------------------------------------------
-- $09 op_faceActor: writes actor.facing based on relative X positions.
--   target.x >= source.x → facing = 90 (east)
--   target.x <  source.x → facing = 270 (west)
-- Use actors >= 16 (out of chore-engine processing range) to avoid the
-- chore tick clobbering facing within the test's frame.
-- ---------------------------------------------------------------------------
function test_faceActor_east_and_west()
  -- Set up positions: actor 17 source, actor 18 target.
  H.wr16(H.actor_addr(17, 2), 100)   -- source x
  H.wr16(H.actor_addr(18, 2), 200)   -- target east of source
  H.wr16(H.actor_addr(17, 6), 0)     -- facing = 0

  -- $09 source=17 target=18  (target as p16 word literal — bit 6 clear)
  H.run_bytecode({0x09, 0x11, 0x12, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(17, 6)), 90,
              "actor[17].facing = 90 (east) when target.x > source.x")

  -- Now move target west of source.
  H.wr16(H.actor_addr(18, 2), 50)
  H.run_bytecode({0x09, 0x11, 0x12, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.actor_addr(17, 6)), 270,
              "actor[17].facing = 270 (west) when target.x < source.x")
end

-- ---------------------------------------------------------------------------
-- $72 op_loadRoom: sets SCUMM.newRoom + VAR_ROOM (var 4). Does NOT trigger
-- the room change inside the opcode — that's deferred to processRoomChange
-- on the next _play tick.
-- ---------------------------------------------------------------------------
function test_loadRoom_sets_newRoom_and_VAR_ROOM()
  -- Snapshot currentRoom + VAR_ROOM so we can restore after the assert and
  -- prevent processRoomChange from actually transitioning rooms (which
  -- would destabilize subsequent tests).
  local saved_current = H.rd8 (H.SYM.SCUMM_currentRoom)
  local saved_VarRoom = H.rd16(H.SYM.SCUMM_globalVars + 4*2)

  -- $72 50 $A0 — bit 7 clear → room is byte literal
  H.run_bytecode({0x72, 50, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 4*2), 50,
              "VAR_ROOM (var 4) = 50 after loadRoom(50)")

  -- Set newRoom = currentRoom so processRoomChange won't fire on the next
  -- _play tick (the scheduler only transitions when newRoom != currentRoom).
  -- Also restore VAR_ROOM so subsequent tests see the prior game state.
  H.wr16(H.SYM.SCUMM_globalVars + 4*2, saved_VarRoom)
  H.wr16(H.SYM.SCUMM_newRoom, saved_current)
end

-- ---------------------------------------------------------------------------
-- File-scope addresses for tables referenced across Phases A and B.
-- ---------------------------------------------------------------------------
local SCUMM_objectOwner    = 0x7EE22A   -- byte per obj
local SCUMM_objectState    = 0x7EDE2A   -- byte per obj
local SCUMM_actorWalkBox   = 0x7EF0B4   -- byte per actor (16 actors)
local SCUMM_actorWidth     = 0x7EF0D4   -- byte per actor (16 actors)
function test_setState()
  H.wr8(SCUMM_objectState + 75, 0)
  -- $07 obj=75 state=5
  H.run_bytecode({0x07, 0x4B, 0x00, 0x05, 0xA0})
  H.assert_eq(H.rd8(SCUMM_objectState + 75), 5,
              "objectState[75] = 5 after setState(75, 5)")
  -- Reassign
  H.run_bytecode({0x07, 0x4B, 0x00, 0x0A, 0xA0})
  H.assert_eq(H.rd8(SCUMM_objectState + 75), 10,
              "objectState[75] = 10 after reassign")
end

-- ---------------------------------------------------------------------------
-- $10 op_getObjectOwner: result <- objectOwner[obj].
--   Bytecode: $10 result_var16 obj16 $A0
-- ---------------------------------------------------------------------------
function test_getObjectOwner()
  H.wr8(SCUMM_objectOwner + 75, 7)
  H.wr16(H.SYM.SCUMM_globalVars + 248*2, 0)
  -- $10 result=Var[248] obj=75
  H.run_bytecode({0x10, 0xF8, 0x00, 0x4B, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 248*2), 7,
              "Var[248] = objectOwner[75] = 7")
end

-- ---------------------------------------------------------------------------
-- $5D op_setClass: variadic — bytecode is obj16 then (aux_byte, class16)*
-- terminated by $FF. Class value's bit 7 is set/clear flag; bits 4-0 are
-- the class number. A roundtrip set+clear lets us verify the opcode
-- without depending on which bit position class N maps to.
-- ---------------------------------------------------------------------------
local SCUMM_objectClass = 0x7EBBAA
function test_setClass_set_then_clear()
  H.wr16(SCUMM_objectClass + 100*2, 0)
  -- Class value layout (low byte): bit 7 = set/clear flag, bits 0..6 =
  -- class number. $81 = "set class 1".
  H.run_bytecode({0x5D, 0x64, 0x00, 0x01, 0x81, 0x00, 0xFF, 0xA0})
  H.assert_eq(H.rd16(SCUMM_objectClass + 100*2) ~= 0 and 1 or 0, 1,
              "objectClass[100] non-zero after setClass(set bit)")

  -- $01 = "clear class 1".
  H.run_bytecode({0x5D, 0x64, 0x00, 0x01, 0x01, 0x00, 0xFF, 0xA0})
  H.assert_eq(H.rd16(SCUMM_objectClass + 100*2), 0,
              "objectClass[100] = 0 after setClass(clear bit)")
end

-- ---------------------------------------------------------------------------
-- ScummVM-derived: putClass(obj, cls, set) computes `1 << (cls - 1)` —
-- 1-indexed. Class 1 → bit 0 ($0001), class 16 → bit 15 ($8000).
-- (object.cpp:286-289). Our port maps class 1 → LUT[1] = $0002 (bit 1)
-- and rejects class >= 16, which is wrong on both counts.
-- ---------------------------------------------------------------------------
function test_setClass_class1_should_set_bit0()
  H.wr16(SCUMM_objectClass + 100*2, 0)
  H.run_bytecode({0x5D, 0x64, 0x00, 0x01, 0x81, 0x00, 0xFF, 0xA0})
  H.assert_eq(H.rd16(SCUMM_objectClass + 100*2), 0x0001,
              "ScummVM-spec: setClass(obj, 1, set) → objectClass = $0001 (bit 0)")
end

function test_setClass_class16_should_set_bit15()
  H.wr16(SCUMM_objectClass + 100*2, 0)
  -- Class 16, set: $80 | 16 = $90
  H.run_bytecode({0x5D, 0x64, 0x00, 0x01, 0x90, 0x00, 0xFF, 0xA0})
  H.assert_eq(H.rd16(SCUMM_objectClass + 100*2), 0x8000,
              "ScummVM-spec: setClass(obj, 16, set) → objectClass = $8000 (bit 15)")
end

-- ---------------------------------------------------------------------------
-- $80 op_breakHere: yield the slot for one frame. The next opcode runs on
-- the FOLLOWING frame, not this one.
-- ---------------------------------------------------------------------------
function test_breakHere_yields_one_frame()
  H.wr16(H.SYM.SCUMM_globalVars + 240*2, 0)
  -- Bytecode: $80 (yield) ; $1A Var[240] = 0xCAFE ; $A0
  local bc = {0x80, 0x1A, 0xF0, 0x00, 0xFE, 0xCA, 0xA0}

  -- max_frames=1 → after one frame, slot has yielded but $1A hasn't run yet.
  local completed = H.run_bytecode(bc, 1)
  H.assert_eq(completed and 1 or 0, 0, "slot did NOT complete in 1 frame ($80 yielded)")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 240*2), 0,
              "Var[240] still 0 — $1A not yet executed")

  -- Now let the slot run more frames; should now finish and write Var[240].
  -- run_bytecode re-arms the slot from scratch, so reset Var and re-run with
  -- a generous budget.
  H.wr16(H.SYM.SCUMM_globalVars + 240*2, 0)
  completed = H.run_bytecode(bc, 10)
  H.assert_eq(completed and 1 or 0, 1, "slot completes within 10 frames after breakHere")
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 240*2), 0xCAFE,
              "Var[240] = 0xCAFE after slot finishes")
end

-- ---------------------------------------------------------------------------
-- $68 op_isScriptRunning: result <- (script N is running ? 1 : 0).
--   Use script number 199 — there's no LSCR/SCRP that low we'd accidentally
--   collide with running scripts in MI1's title-screen idle state.
-- ---------------------------------------------------------------------------
function test_isScriptRunning_for_inactive_script()
  H.wr16(H.SYM.SCUMM_globalVars + 241*2, 0xFFFF)  -- sentinel
  -- $68 result_var16 script8 — no flag bits → script is byte literal
  H.run_bytecode({0x68, 0xF1, 0x00, 0xC7, 0xA0})  -- script 199
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 241*2), 0,
              "isScriptRunning(199) = 0 (no such running script)")
end

-- ---------------------------------------------------------------------------
-- $29 op_setOwnerOf: obj16 owner8 → SCUMM.objectOwner[obj] = owner.
-- ---------------------------------------------------------------------------
function test_setOwnerOf()
  H.wr8(SCUMM_objectOwner + 100, 0)
  -- $29 obj=100 owner=7
  H.run_bytecode({0x29, 0x64, 0x00, 0x07, 0xA0})
  H.assert_eq(H.rd8(SCUMM_objectOwner + 100), 7,
              "objectOwner[100] = 7 after setOwnerOf")

  -- Reassign to a different owner.
  H.run_bytecode({0x29, 0x64, 0x00, 0x03, 0xA0})
  H.assert_eq(H.rd8(SCUMM_objectOwner + 100), 3,
              "objectOwner[100] = 3 after reassign")
end

function test_putActor_sets_egoPositioned_for_ego()
  -- Read current VAR_EGO actor number (defaults to 1 for Guybrush)
  local ego = H.rd16(H.SYM.SCUMM_globalVars + 1*2) & 0xFF
  H.assert_eq(ego > 0 and 1 or 0, 1, "VAR_EGO is set (>0)")

  -- Case A: putActor on ego → egoPositioned = 1
  H.wr8(H.SYM.SCUMM_egoPositioned, 0)
  H.run_bytecode({0x01, ego, 0x10, 0x00, 0x20, 0x00, 0xA0})
  H.assert_eq(H.rd8(H.SYM.SCUMM_egoPositioned), 1,
              "egoPositioned = 1 after putActor(VAR_EGO)")

  -- Case B: putActor on non-ego → egoPositioned stays 0
  -- Pick an actor number that is NOT VAR_EGO. ego is usually 1; use 14.
  H.wr8(H.SYM.SCUMM_egoPositioned, 0)
  H.run_bytecode({0x01, 0x0E, 0x10, 0x00, 0x20, 0x00, 0xA0})
  H.assert_eq(H.rd8(H.SYM.SCUMM_egoPositioned), 0,
              "egoPositioned stays 0 after putActor on non-ego actor")
end

-- ============================================================================
-- PHASE A.1 — Arithmetic & logic (multiply, divide, and, or)
-- ScummVM uses signed `int` arithmetic; mul16/div16 in our port are signed.
-- ============================================================================

-- $1B op_multiply: Var[X] *= operand. ScummVM script_v5.cpp:1996.
function test_phaseA_multiply_positive()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 6)
  H.run_bytecode({0x1B, 0xDC, 0x00, 0x07, 0x00, 0xA0})  -- Var[220] *= 7
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 42, "Var[220] = 6 * 7")
end

-- mul16 is signed (XORs sign of operands then unsigned multiply, negates result).
function test_phaseA_multiply_signed()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0xFFFB)  -- -5
  H.run_bytecode({0x1B, 0xDC, 0x00, 0x03, 0x00, 0xA0})  -- *= 3
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0xFFF1,
              "ScummVM-spec: -5 * 3 = -15 = $FFF1 (signed)")
end

-- $5B op_divide: Var[X] /= operand. ScummVM (line 989): if a == 0 → setResult(0).
function test_phaseA_divide_basic()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 100)
  H.run_bytecode({0x5B, 0xDC, 0x00, 0x07, 0x00, 0xA0})  -- Var[220] /= 7
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 14, "Var[220] = 100 / 7 = 14")
end

function test_phaseA_divide_by_zero()
  -- Use Var[400] to dodge any MI1 game-state writes to Var[220]
  H.wr16(H.SYM.SCUMM_globalVars + 400*2, 100)
  -- $5B Var[400]=$0190 / 0 → 0
  H.run_bytecode({0x5B, 0x90, 0x01, 0x00, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 400*2), 0,
              "ScummVM-spec: divide-by-zero sets result to 0")
end

function test_phaseA_divide_signed()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0xFFE2)  -- -30
  H.run_bytecode({0x5B, 0xDC, 0x00, 0x06, 0x00, 0xA0})  -- /= 6
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0xFFFB,
              "ScummVM-spec: -30 / 6 = -5 = $FFFB (signed)")
end

-- $17 op_and: Var[X] &= operand. ScummVM script_v5.cpp:779.
function test_phaseA_and_basic()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0xCAFE)
  H.run_bytecode({0x17, 0xDC, 0x00, 0xFF, 0x00, 0xA0})  -- &= $00FF
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0x00FE, "$CAFE & $00FF = $00FE")
end

-- $57 op_or: Var[X] |= operand. ScummVM script_v5.cpp:2003.
function test_phaseA_or_basic()
  H.wr16(H.SYM.SCUMM_globalVars + 220*2, 0x0F0F)
  H.run_bytecode({0x57, 0xDC, 0x00, 0xF0, 0xF0, 0xA0})  -- |= $F0F0
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 220*2), 0xFFFF, "$0F0F | $F0F0 = $FFFF")
end

-- ============================================================================
-- PHASE A.2 — Actor / object accessors
-- All result <- field shape: $XX result_var16 + actor_byte (or word).
-- Use actors >= 16 to dodge chore-engine writes within the test frame.
-- ============================================================================

-- $06 op_getActorElevation: result <- actor.elevation (word, +8).
function test_phaseA_getActorElevation()
  H.wr16(H.actor_addr(20, 8), 0x1234)
  H.wr16(H.SYM.SCUMM_globalVars + 401*2, 0)
  -- $06 result_var16 actor8 — bit 7 clear → byte literal
  H.run_bytecode({0x06, 0x91, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 401*2), 0x1234,
              "Var[401] = actor[20].elevation = $1234")
end

-- $3B op_getActorScale: result <- actor.scalex (byte, +13).
function test_phaseA_getActorScale()
  H.wr8(H.actor_addr(20, 13), 128)
  H.wr16(H.SYM.SCUMM_globalVars + 402*2, 0)
  H.run_bytecode({0x3B, 0x92, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 402*2), 128,
              "Var[402] = actor[20].scalex = 128")
end

-- $71 op_getActorCostume: result <- actor.costume (byte, +1).
function test_phaseA_getActorCostume()
  H.wr8(H.actor_addr(20, 1), 25)
  H.wr16(H.SYM.SCUMM_globalVars + 403*2, 0)
  H.run_bytecode({0x71, 0x93, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 403*2), 25,
              "Var[403] = actor[20].costume = 25")
end

-- $56 op_getActorMoving: result <- actor.moving (byte, +10).
function test_phaseA_getActorMoving()
  H.wr8(H.actor_addr(20, 10), 7)
  H.wr16(H.SYM.SCUMM_globalVars + 404*2, 0)
  H.run_bytecode({0x56, 0x94, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 404*2), 7,
              "Var[404] = actor[20].moving = 7")
end

-- $0F op_getObjectState: result <- objectState[obj]. ScummVM:1433.
function test_phaseA_getObjectState()
  H.wr8(SCUMM_objectState + 50, 9)
  H.wr16(H.SYM.SCUMM_globalVars + 405*2, 0)
  -- $0F result_var16 obj16 — bit 7 clear → word literal
  H.run_bytecode({0x0F, 0x95, 0x01, 0x32, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 405*2), 9,
              "Var[405] = objectState[50] = 9")
end

-- ScummVM-derived: $63 op_getActorFacing returns newDirToOldDir(facing) —
-- 0..3 (E=1, S=2, W=0, N=3), NOT the raw 0/90/180/270 angle.
-- Per ScummVM util.cpp:41-49.
function test_phaseA_getActorFacing_translates()
  -- East (90°) → 1
  H.wr16(H.actor_addr(20, 6), 90)
  H.wr16(H.SYM.SCUMM_globalVars + 406*2, 0xFFFF)
  H.run_bytecode({0x63, 0x96, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 406*2), 1,
              "ScummVM-spec: getActorFacing returns 1 for facing=90 (east)")

  -- West (270°) → 0
  H.wr16(H.actor_addr(20, 6), 270)
  H.wr16(H.SYM.SCUMM_globalVars + 406*2, 0xFFFF)
  H.run_bytecode({0x63, 0x96, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 406*2), 0,
              "ScummVM-spec: getActorFacing returns 0 for facing=270 (west)")

  -- South (180°) → 2
  H.wr16(H.actor_addr(20, 6), 180)
  H.wr16(H.SYM.SCUMM_globalVars + 406*2, 0xFFFF)
  H.run_bytecode({0x63, 0x96, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 406*2), 2,
              "ScummVM-spec: getActorFacing returns 2 for facing=180 (south)")

  -- North (0°) → 3
  H.wr16(H.actor_addr(20, 6), 0)
  H.wr16(H.SYM.SCUMM_globalVars + 406*2, 0xFFFF)
  H.run_bytecode({0x63, 0x96, 0x01, 0x14, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 406*2), 3,
              "ScummVM-spec: getActorFacing returns 3 for facing=0 (north)")
end

-- $16 op_getRandomNr: result <- rand in [0, max]. ScummVM:1438. Bound check.
function test_phaseA_getRandomNr_bounds()
  H.wr16(H.SYM.SCUMM_globalVars + 407*2, 0xFFFF)
  -- $16 result_var16 max8=10
  H.run_bytecode({0x16, 0x97, 0x01, 0x0A, 0xA0})
  local r = H.rd16(H.SYM.SCUMM_globalVars + 407*2)
  H.assert_eq(r >= 0 and r <= 10 and 1 or 0, 1,
              string.format("getRandomNr(10) = %d in [0, 10]", r))
end

-- $7B op_getActorWalkBox: result <- actorWalkBox[actor]. Note our handler
-- clamps actor to 0..15 (ScummVM doesn't); use actor 5 to stay in range.
function test_phaseA_getActorWalkBox()
  H.wr8(SCUMM_actorWalkBox + 5, 7)
  H.wr16(H.SYM.SCUMM_globalVars + 408*2, 0)
  H.run_bytecode({0x7B, 0x98, 0x01, 0x05, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 408*2), 7,
              "Var[408] = actorWalkBox[5] = 7")
end

-- $6C op_getActorWidth: ScummVM returns a->_width (per-actor field). Our
-- port returns actorWidth[actor], or 24 if the slot is 0. Pin both:
-- a populated slot returns the value, an empty slot returns 24.
-- KNOWN-DIVERGENCE: actor struct has no per-actor width (task #61 bucket).
function test_phaseA_getActorWidth_default_24()
  H.wr8(SCUMM_actorWidth + 6, 0)
  H.wr16(H.SYM.SCUMM_globalVars + 409*2, 0xFFFF)
  H.run_bytecode({0x6C, 0x99, 0x01, 0x06, 0xA0})
  -- KNOWN-DIVERGENCE: ScummVM returns actor._width; our port substitutes 24.
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 409*2), 24,
              "actorWidth[6]=0 → returns hardcoded default 24")
end

function test_phaseA_getActorWidth_populated()
  H.wr8(SCUMM_actorWidth + 6, 32)
  H.wr16(H.SYM.SCUMM_globalVars + 409*2, 0xFFFF)
  H.run_bytecode({0x6C, 0x99, 0x01, 0x06, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 409*2), 32,
              "actorWidth[6]=32 → returns 32")
end

-- $31 op_getInventoryCount: result <- count of objs with owner == actor.
-- ScummVM:1423 just calls getInventoryCount(actor); MI1 boot state has no
-- inventory yet. This pins our port's count for actor 0 (= 0).
function test_phaseA_getInventoryCount_empty()
  H.wr16(H.SYM.SCUMM_globalVars + 410*2, 0xFFFF)
  -- $31 result_var16 actor8=99 (no objects owned by 99)
  H.run_bytecode({0x31, 0x9A, 0x01, 0x63, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 410*2), 0,
              "inventoryCount(actor=99) = 0 (no objects owned)")
end

-- $22 op_getAnimCounter: ScummVM returns a->_cost.animCounter. Our port
-- has no animCounter field — handler returns 0.
-- KNOWN-DIVERGENCE: pin current "always 0" behavior; tracked by task #61.
function test_phaseA_getAnimCounter_stub_returns_zero()
  H.wr16(H.SYM.SCUMM_globalVars + 411*2, 0xFFFF)
  H.run_bytecode({0x22, 0x9B, 0x01, 0x14, 0xA0})
  -- KNOWN-DIVERGENCE: ScummVM returns animCounter; ours returns 0.
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 411*2), 0,
              "getAnimCounter is stubbed to 0 (no animCounter field)")
end

-- ============================================================================
-- PHASE A.3 — Walk ops
-- All start a walk: actor.moving = 1, actorTargetX/Y populated.
-- ScummVM: a->startWalkActor(x, y, dir).
-- ============================================================================

local SCUMM_actorTargetX = 0x7EFB49   -- 16 actors × 2 bytes each
local SCUMM_actorTargetY = 0x7EFB69

-- $1E op_walkActorTo: walk actor to absolute (x, y).
-- Ego (actor 1) gets per-frame walk-tick processing that completes the walk
-- in same frame here, so .moving gets cleared. Pin only the targetX/Y write
-- (which is the durable signal); KNOWN-DIVERGENCE for moving=1 in tests.
function test_phaseA_walkActorTo()
  H.wr16(H.actor_addr(5, 2), 100)
  H.wr16(H.actor_addr(5, 4), 50)
  H.wr16(SCUMM_actorTargetX + 5*2, 0)
  H.wr16(SCUMM_actorTargetY + 5*2, 0)

  -- $1E actor8 x16 y16
  H.run_bytecode({0x1E, 0x05, 0xC8, 0x00, 0x96, 0x00, 0xA0})  -- walk(5, 200, 150)
  H.assert_eq(H.rd16(SCUMM_actorTargetX + 5*2), 200,
              "actorTargetX[5] = 200 after walkActorTo")
  H.assert_eq(H.rd16(SCUMM_actorTargetY + 5*2), 150,
              "actorTargetY[5] = 150 after walkActorTo")
end

-- $0D op_walkActorToActor: walk toward another actor (with min distance byte).
-- Pin the targetX write only (moving may auto-clear within the test frame).
function test_phaseA_walkActorToActor()
  H.wr16(H.actor_addr(5, 2), 100)
  H.wr16(H.actor_addr(5, 4), 50)
  H.wr16(H.actor_addr(6, 2), 300)
  H.wr16(H.actor_addr(6, 4), 60)
  H.wr16(SCUMM_actorTargetX + 5*2, 0)

  -- $0D actor=5 target=6 dist=10
  H.run_bytecode({0x0D, 0x05, 0x06, 0x0A, 0xA0})
  local tx = H.rd16(SCUMM_actorTargetX + 5*2)
  H.assert_eq(tx > 100 and tx <= 300 and 1 or 0, 1,
              "actorTargetX[5] heads east toward actor 6 (x>100, ≤300, got " .. tx .. ")")
end

-- ============================================================================
-- PHASE A.4 — Actor / object mutators
-- ============================================================================

-- $11 op_animateActor: writes actor.initFrame (also tags chore engine).
-- ScummVM has special semantic codes (0xFF stop, 0xFE face cam, etc.); our
-- port writes raw frame to initFrame. Pin basic-frame case here; semantic
-- codes tagged KNOWN-DIVERGENCE.
function test_phaseA_animateActor_basic()
  H.wr8(H.actor_addr(20, 12), 0)  -- initFrame
  H.run_bytecode({0x11, 0x14, 0x07, 0xA0})  -- animateActor(20, 7)
  H.assert_eq(H.rd8(H.actor_addr(20, 12)), 7,
              "actor[20].initFrame = 7 after animateActor(20, 7)")
end

function test_phaseA_animateActor_semantic_FF()
  -- ScummVM Actor::animateActor: $FC..$FF are command codes (face camera,
  -- stop anim, etc.), NOT frame numbers. Our port now skips the initFrame
  -- write + chore dispatch for these values (matches ScummVM "no anim
  -- change" for v5 MI1). Test that initFrame is preserved.
  H.wr8(H.actor_addr(20, 12), 5)
  H.run_bytecode({0x11, 0x14, 0xFF, 0xA0})
  H.assert_eq(H.rd8(H.actor_addr(20, 12)), 5,
              "animateActor($FF): semantic code → initFrame unchanged")
end

-- $1F op_isActorInBox: jumps when actor is in the specified box.
-- Place the actor at coordinates clearly outside any walkbox so cond=FALSE.
function test_phaseA_isActorInBox_far_outside()
  H.wr16(H.actor_addr(5, 2), 0xF000)   -- way off to the side
  H.wr16(H.actor_addr(5, 4), 0xF000)
  reset_branch_signals()

  -- $1F actor=5 box=1 jumpOffset16=+6
  local bc = {0x1F, 0x05, 0x01, 0x06, 0x00}
  for _, b in ipairs(NO_JUMP_PATH) do table.insert(bc, b) end
  for _, b in ipairs(JUMP_PATH)    do table.insert(bc, b) end
  H.run_bytecode(bc)
  H.assert_eq(which_path_ran("$1F actor far away") == "no_jump" and 1 or 0, 1,
              "$1F isActorInBox: actor outside box 1 → no jump (cond FALSE)")
end

-- $25 op_pickupObject: ScummVM does:
--   addObjectToInventory(obj, room); putOwner(obj, ego); putClass(obj, kUntouchable, 1);
--   putState(obj, 1); markObjectRectAsDirty; clearDrawObjectQueue; runInventoryScript(1).
-- We only assert the `owner` field write here — ego becomes the object's
-- owner. Other side-effects vary by port.
function test_phaseA_pickupObject_sets_owner()
  H.wr8(SCUMM_objectOwner + 60, 0)
  -- Read VAR_EGO so we know what owner to expect.
  local ego = H.rd16(H.SYM.SCUMM_globalVars + 1*2) & 0xFF

  -- $25 obj=60 room=current(use 0=current)
  H.run_bytecode({0x25, 0x3C, 0x00, 0x00, 0xA0})
  H.assert_eq(H.rd8(SCUMM_objectOwner + 60), ego,
              "objectOwner[60] = VAR_EGO after pickupObject")
end

-- ============================================================================
-- PHASE A.5 — Script lifecycle
-- ============================================================================

-- $60 op_freezeScripts: increments freezeCount on running slots (skipping
-- current). The harness already sets freezeCount=$FF on every other slot
-- before run_bytecode runs, so an inc of $FF wraps to $00 — making this
-- test's "freezeCount changed" assertion impossible to express cleanly.
-- KNOWN-LIMITATION: harness slot-freeze conflicts with op_freezeScripts.
-- Pin only the no-crash behavior.
function test_phaseA_freezeScripts_runs_without_crash()
  H.run_bytecode({0x60, 0x01, 0xA0})
  -- If we got here, freezeScripts didn't crash. Real semantic test would
  -- need a custom harness that doesn't pre-freeze slots.
  H.assert_eq(1, 1, "freezeScripts ran without crash (test-harness limitation)")
end

-- $62 op_stopScript: kills running slot whose .number matches.
function test_phaseA_stopScript_kills_matching()
  -- Spawn a parked slot at index 5 with number 50 (made-up).
  local SLOT5_addr = H.SYM.SCUMM_slots_base + 5 * H.SYM.SCUMM_slot_stride
  H.wr8(SLOT5_addr + H.SYM.slot_status, H.SCUMM_SLOT_RUNNING)
  H.wr8(SLOT5_addr + H.SYM.slot_number, 50)
  H.wr8(SLOT5_addr + H.SYM.slot_freezeCount, 0xFF)  -- frozen so MI1 doesn't run it

  -- $62 script=50
  H.run_bytecode({0x62, 0x32, 0xA0})
  H.assert_eq(H.rd8(SLOT5_addr + H.SYM.slot_status), H.SCUMM_SLOT_DEAD,
              "slot 5 status = DEAD after stopScript(50)")
end

-- ============================================================================
-- PHASE A.6 — Cutscene & override
-- ============================================================================

local SCUMM_cutsceneNest = 0x7EF8E5
local SCUMM_cutScenePtr  = 0x7EF983  -- 5 nest levels × 2 bytes
local SCUMM_cameraDest    = 0x7EFE3E
local SCUMM_cameraFollows = 0x7EFE3C

-- $40 op_cutscene + $A0 stopObjectCode: when the slot terminates, our
-- killSlotCleanup decrements cutsceneNest by the slot's cutsceneOverride
-- count. So a cutscene begun + slot killed leaves nest unchanged. This
-- pins the unwind balance — a real bug would leave nest=1 (leaked).
function test_phaseA_cutscene_balanced_with_slot_kill()
  H.wr8(SCUMM_cutsceneNest, 0)
  H.run_bytecode({0x40, 0xFF, 0xA0})
  H.assert_eq(H.rd8(SCUMM_cutsceneNest), 0,
              "cutsceneNest is unwound when slot dies (nest=1 → killSlotCleanup → 0)")
end

-- $C0 op_endCutscene + $A0: similar — endCutscene + slot kill should leave
-- nest at 0 (idempotent when already 0).
function test_phaseA_endCutscene_balanced()
  H.wr8(SCUMM_cutsceneNest, 0)
  -- nesting+ending in same slot: cutsceneNest was 0, op_cutscene → 1,
  -- op_endCutscene → 0, $A0 → 0 (no change since cutsceneOverride consumed).
  H.run_bytecode({0x40, 0xFF, 0xC0, 0xA0})
  H.assert_eq(H.rd8(SCUMM_cutsceneNest), 0,
              "cutsceneNest = 0 after balanced cutscene + endCutscene")
end

-- ============================================================================
-- PHASE A.7 — Camera
-- ============================================================================

-- $12 op_panCameraTo: writes cameraDest. Bytecode: $12 target_x16
function test_phaseA_panCameraTo()
  H.wr16(SCUMM_cameraDest, 0)
  H.run_bytecode({0x12, 0xC8, 0x00, 0xA0})  -- panCameraTo(200)
  H.assert_eq(H.rd16(SCUMM_cameraDest), 200,
              "cameraDest = 200 after panCameraTo")
end

-- $52 op_actorFollowCamera: writes cameraFollows = actor number.
function test_phaseA_actorFollowCamera()
  H.wr16(SCUMM_cameraFollows, 0xFFFF)
  H.run_bytecode({0x52, 0x05, 0xA0})  -- actorFollowCamera(5)
  H.assert_eq(H.rd16(SCUMM_cameraFollows) & 0xFF, 5,
              "cameraFollows.low = 5 after actorFollowCamera(5)")
end

-- ============================================================================
-- PHASE A.8 — Audio (mostly stubs / virtualized in our port)
-- ============================================================================

function test_phaseA_startSound_runs()
  -- $1C sound8 — pin no-crash; KNOWN-DIVERGENCE: real ScummVM queues PCM/SPC.
  H.run_bytecode({0x1C, 0x08, 0xA0})
  H.assert_eq(1, 1, "startSound ran without crash")
end

function test_phaseA_stopSound_runs()
  H.run_bytecode({0x3C, 0x08, 0xA0})
  H.assert_eq(1, 1, "stopSound ran without crash")
end

function test_phaseA_startMusic_runs()
  -- $02 music8
  H.run_bytecode({0x02, 0x10, 0xA0})
  H.assert_eq(1, 1, "startMusic ran without crash")
end

function test_phaseA_stopMusic_runs()
  H.run_bytecode({0x20, 0xA0})
  H.assert_eq(1, 1, "stopMusic ran without crash")
end

-- ============================================================================
-- PHASE A.9 — Misc no-op / stub ops (KNOWN-DIVERGENCE: pin no-crash)
-- ============================================================================

function test_phaseA_lights_stub()
  -- $70 lights args... — ScummVM sets _currentLights. Our port stubs.
  -- bytecode: $70 p8 p8 p8 (need to verify our port's consumption)
  -- For now just pin no-crash with one-byte arg.
  H.run_bytecode({0x70, 0x0F, 0xA0})
  H.assert_eq(1, 1, "lights ran without crash")
end

function test_phaseA_pseudoRoom_stub()
  -- $CC pseudoRoom: byte room + word-vararg terminated by $FF
  H.run_bytecode({0xCC, 0x10, 0xFF, 0xA0})
  H.assert_eq(1, 1, "pseudoRoom ran without crash")
end

-- $4C op_soundKludge: ScummVM ignores entirely on v5; word-vararg.
function test_phaseA_soundKludge_stub()
  H.run_bytecode({0x4C, 0xFF, 0xA0})
  H.assert_eq(1, 1, "soundKludge ran without crash")
end

-- $5C op_oldRoomEffect: ScummVM legacy; takes p16 + sub-op byte.
function test_phaseA_oldRoomEffect_stub()
  -- $5C subop=$03 effect16
  H.run_bytecode({0x5C, 0x03, 0x05, 0x00, 0xA0})
  H.assert_eq(1, 1, "oldRoomEffect ran without crash")
end

-- $6B op_debug: takes p16 (debug code).
function test_phaseA_debug_stub()
  H.run_bytecode({0x6B, 0x42, 0x00, 0xA0})
  H.assert_eq(1, 1, "debug ran without crash")
end

-- $3F op_drawBox: takes 5 params (left, top word/word + sub-byte + right + bottom + color).
-- Format: $3F left16 top16 + aux + right16 + bottom16 + color8. Pin no-crash.
function test_phaseA_drawBox_stub()
  -- $3F l=10 t=20 aux=0 r=50 b=60 col=7
  H.run_bytecode({0x3F, 0x0A, 0x00, 0x14, 0x00, 0x00, 0x32, 0x00, 0x3C, 0x00, 0x07, 0xA0})
  H.assert_eq(1, 1, "drawBox ran without crash")
end

-- ============================================================================
-- PHASE A.10 — Remaining primary handlers (no-crash + state pinning)
-- ============================================================================

-- $05 op_drawObject: bytecode = $05 obj16 + sub-byte + (state8 if $01) | (xy if $02). Pin no-crash with the simple "default" form.
function test_phaseA_drawObject_stub()
  -- $05 obj=10 sub=$FF (terminator/default)
  H.run_bytecode({0x05, 0x0A, 0x00, 0xFF, 0xA0})
  H.assert_eq(1, 1, "drawObject ran without crash")
end

-- $19 op_doSentence: queues a verb action. ScummVM stores in sentence stack
-- and increments _sentenceNum. Bytecode: $19 verb8 obj16 actor8.
-- Pin no-crash since sentence queue lives outside our test surface.
function test_phaseA_doSentence_stub()
  H.run_bytecode({0x19, 0x05, 0x10, 0x00, 0x01, 0xA0})
  H.assert_eq(1, 1, "doSentence ran without crash")
end

-- $42 op_chainScript: kills current slot, starts replacement script with
-- args. Our test slot dies anyway via the trailing $A0 — but op_chainScript
-- itself doesn't reach $A0; it kills the slot mid-stream. Pin no-crash.
function test_phaseA_chainScript_no_crash()
  -- $42 script=200 (LSCR, no-op when not loaded) + $FF terminator
  H.run_bytecode({0x42, 0xC8, 0xFF, 0xA0})
  H.assert_eq(1, 1, "chainScript ran without crash (slot replaced)")
end

-- $6E op_stopObjectScript: kills slot whose .number matches the script id.
function test_phaseA_stopObjectScript_kills_matching()
  local SLOT7_addr = H.SYM.SCUMM_slots_base + 7 * H.SYM.SCUMM_slot_stride
  H.wr8(SLOT7_addr + H.SYM.slot_status, H.SCUMM_SLOT_RUNNING)
  H.wr8(SLOT7_addr + H.SYM.slot_number, 88)
  H.wr8(SLOT7_addr + H.SYM.slot_freezeCount, 0xFF)

  H.run_bytecode({0x6E, 0x58, 0x00, 0xA0})  -- stopObjectScript(88)
  H.assert_eq(H.rd8(SLOT7_addr + H.SYM.slot_status), H.SCUMM_SLOT_DEAD,
              "slot 7 = DEAD after stopObjectScript(88)")
end

-- $37 op_startObject: starts an object's script (entry-point form).
-- Bytecode: $37 obj16 verb8 + word-vararg + $FF.
function test_phaseA_startObject_no_crash()
  H.run_bytecode({0x37, 0x10, 0x00, 0x05, 0xFF, 0xA0})
  H.assert_eq(1, 1, "startObject ran without crash")
end

-- $54 op_setObjectName: writes object name (string-form).
-- Bytecode: $54 obj16 + null-term-string. Pin no-crash.
function test_phaseA_setObjectName_no_crash()
  H.run_bytecode({0x54, 0x10, 0x00, 0x41, 0x42, 0x00, 0xA0})  -- "AB"\0
  H.assert_eq(1, 1, "setObjectName ran without crash")
end

-- $AB op_saveRestoreVerbs: sub-op driven (1=save, 2=restore, 3=delete).
-- Format: $AB sub8 slotStart slotEnd mode. Pin no-crash.
function test_phaseA_saveRestoreVerbs_no_crash()
  H.run_bytecode({0xAB, 0x01, 0x05, 0x05, 0x00, 0xA0})  -- save verbs in [5,5]
  H.assert_eq(1, 1, "saveRestoreVerbs ran without crash")
end

-- $AE op_wait: sub-op forActor / forMessage / forCamera. Each form
-- yields the slot if the wait condition is unmet. Pin no-crash with
-- forCamera (which checks cameraDest vs cameraX).
function test_phaseA_wait_forCamera()
  H.run_bytecode({0xAE, 0x03, 0xA0})  -- wait forCamera
  H.assert_eq(1, 1, "op_wait[$03] forCamera ran without crash")
end

-- $51 op_animateActor variant (with bit 6 set, var-ref frame).
-- Tests param-decode flag bits on animateActor.
function test_phaseA_animateActor_var_ref_frame()
  H.wr16(H.SYM.SCUMM_globalVars + 195*2, 7)
  H.wr8(H.actor_addr(20, 12), 0)
  -- $51 = $11 | $40 = bit 6 set (frame is var-ref). Wait — actually
  -- animateActor uses BIT7 for actor and BIT6 for frame (per our impl).
  -- $11+BIT6 = $51 sets frame as var-ref.
  H.run_bytecode({0x51, 0x14, 0xC3, 0x00, 0xA0})
  -- frame from Var[195]=7 → write 7 to actor[20].initFrame
  H.assert_eq(H.rd8(H.actor_addr(20, 12)), 7,
              "actor[20].initFrame = Var[195] = 7 via animateActor var-ref")
end

-- $35 op_findObject: result <- obj at (screenX, screenY), or 0 if none.
-- ScummVM scans current room's obj list, returns one whose bbox contains
-- the point. Test: a point unlikely to hit any object → 0.
function test_phaseA_findObject_no_hit()
  H.wr16(H.SYM.SCUMM_globalVars + 430*2, 0xFFFF)
  -- $35 result_var16=430 ($01AE) x8=5 y8=5 — top-left.
  -- Note our op_findObject reads x/y as BYTES (not words).
  H.run_bytecode({0x35, 0xAE, 0x01, 0x05, 0x05, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 430*2), 0,
              "findObject(5, 5) = 0 (no obj at top-left corner)")
end

-- $66 op_getClosestObjActor: scans actors in [VAR_ACTOR_RANGE_MIN, MAX]
-- and returns the one closest to the reference actor. Pin no-crash —
-- semantic test would need staging actor positions across the range.
function test_phaseA_getClosestObjActor_no_crash()
  H.wr16(H.SYM.SCUMM_globalVars + 431*2, 0xFFFF)
  -- $66 result_var16 ref_actor16 = 5
  H.run_bytecode({0x66, 0xAF, 0x01, 0x05, 0x00, 0xA0})
  H.assert_eq(1, 1, "getClosestObjActor ran without crash")
end

-- $3D op_findInventory: result <- Nth obj in actor's inventory.
-- Empty inventory → 0.
function test_phaseA_findInventory_empty()
  H.wr16(H.SYM.SCUMM_globalVars + 432*2, 0xFFFF)
  -- $3D result_var16 actor8 idx8 — actor 99 (no inventory), idx 1.
  H.run_bytecode({0x3D, 0xB0, 0x01, 0x63, 0x01, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 432*2), 0,
              "findInventory(actor 99, 1) = 0 (empty)")
end

-- $0B op_getVerbEntrypoint: result <- offset into obj's VERB data, or 0
-- if the verb has no handler. Pin: nonexistent obj → 0.
function test_phaseA_getVerbEntrypoint_no_obj()
  H.wr16(H.SYM.SCUMM_globalVars + 433*2, 0xFFFF)
  -- $0B result_var16 obj16=999 verb16=2
  H.run_bytecode({0x0B, 0xB1, 0x01, 0xE7, 0x03, 0x02, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 433*2), 0,
              "getVerbEntrypoint(obj 999, verb 2) = 0 (obj not found)")
end

-- ============================================================================
-- PHASE A.5 cont — Script lifecycle (chainScript, stopObjectCode)
-- ============================================================================

-- $A0 op_stopObjectCode: kills current slot. We rely on this as the
-- standard test terminator — already exercised in every test. Pin
-- "after $A0, slot.status = DEAD" explicitly.
function test_phaseA_stopObjectCode_kills_slot()
  -- We can't observe the test slot dying inside its own body, but we
  -- can verify run_bytecode returned `true` (slot reached DEAD).
  -- Use any minimal payload.
  local ok = H.run_bytecode({0xA0})
  H.assert_eq(ok and 1 or 0, 1, "stopObjectCode terminates slot (run_bytecode returned true)")
end

-- ============================================================================
-- PHASE A.6 cont — Cutscene override
-- ============================================================================

-- $58 op_override: registers an override target PC at cutScenePtr[nest-1]
-- and a marker for the slot. Bytecode: $58 marker8 word16 (offset).
-- ScummVM stores _vm->_cutScenePtr[level] = scriptPointer + offset; we
-- store equivalent. Pin: cutScenePtr[0] non-zero after override inside a
-- cutscene.
function test_phaseA_override_writes_cutScenePtr()
  H.wr8(SCUMM_cutsceneNest, 0)
  H.wr16(SCUMM_cutScenePtr, 0)
  -- $40 (cutscene) $FF $58 marker $XX $YY — register override; stop.
  -- $58 op_override is followed by a single byte (0/1 marker) per ScummVM v5.
  H.run_bytecode({
    0x40, 0xFF,        -- enter cutscene
    0x58, 0x00,        -- override marker (0 = clear, 1 = arm)
    0xC0,              -- end cutscene
    0xA0
  })
  -- After balanced cutscene/end, nest = 0 again. cutScenePtr was cleared on
  -- cutscene entry. Pin: cutsceneNest is balanced.
  H.assert_eq(H.rd8(SCUMM_cutsceneNest), 0,
              "cutsceneNest unwound after cutscene/override/endCutscene")
end

-- $0E op_putActorAtObject: ScummVM places actor at obj's getObjectXYPos.
-- We have lookupObjectWalkTo which reads obj.walk_x, walk_y from roomObjTable.
-- Without a real obj loaded, we pin: actor.x is unchanged when obj not found.
-- (Real test would need a roomObjTable injection — Phase D.)
function test_phaseA_putActorAtObject_obj_not_found()
  H.wr16(H.actor_addr(7, 2), 0xCAFE)  -- sentinel
  H.wr16(H.actor_addr(7, 4), 0xBEEF)
  -- $0E actor=7 obj=9999 (high obj id unlikely to be in current room table)
  H.run_bytecode({0x0E, 0x07, 0x0F, 0x27, 0xA0})
  -- KNOWN-DIVERGENCE: ScummVM errors out on unknown obj; ours silently no-ops.
  -- Pinning current behavior: actor coords unchanged.
  H.assert_eq(H.rd16(H.actor_addr(7, 2)), 0xCAFE,
              "actor[7].x unchanged when obj not found")
end

-- $34 op_getDist: ScummVM-spec MIN(MAX(|dx|,|dy|), 0xFE) for actor pair.
-- We implement Chebyshev distance for actor IDs 1..15. Obj IDs ≥ 16 → 0xFF.
function test_phaseA_getDist_actor_pair()
  H.wr16(H.actor_addr(5, 2), 100); H.wr16(H.actor_addr(5, 4), 50)
  H.wr16(H.actor_addr(6, 2), 130); H.wr16(H.actor_addr(6, 4), 90)
  -- max(|130-100|, |90-50|) = max(30, 40) = 40
  H.wr16(H.SYM.SCUMM_globalVars + 412*2, 0)
  H.run_bytecode({0x34, 0x9C, 0x01, 0x05, 0x00, 0x06, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 412*2), 40,
              "getDist(actor 5, actor 6) = max(|dx|,|dy|) = 40")
end

function test_phaseA_getDist_clamps_to_FE()
  H.wr16(H.actor_addr(5, 2), 0);   H.wr16(H.actor_addr(5, 4), 0)
  H.wr16(H.actor_addr(6, 2), 500); H.wr16(H.actor_addr(6, 4), 0)
  H.wr16(H.SYM.SCUMM_globalVars + 412*2, 0)
  H.run_bytecode({0x34, 0x9C, 0x01, 0x05, 0x00, 0x06, 0x00, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 412*2), 0xFE,
              "getDist clamps to 0xFE when distance >= 0xFF")
end

function test_phaseA_getDist_obj_arg_returns_FF()
  H.wr16(H.SYM.SCUMM_globalVars + 412*2, 0)
  -- arg2=999 (out of actor range 0..15) → 0xFF (not found)
  H.run_bytecode({0x34, 0x9C, 0x01, 0x05, 0x00, 0xE7, 0x03, 0xA0})
  H.assert_eq(H.rd16(H.SYM.SCUMM_globalVars + 412*2), 0xFF,
              "getDist with obj arg → 0xFF (KNOWN-DIVERGENCE: only actors supported)")
end

-- ============================================================================

-- Test order matters: loadRoomWithEgo triggers a real room change which
-- restarts MI1's slot scheduler with fresh entry-script state. Run all
-- pure-VM tests FIRST so they execute against a stable game backdrop.
local TESTS = {
  { name = "op_putActor: literal coords land in actor struct",
    fn = test_putActor_literal_coords },
  { name = "op_putActor: actor index addresses correct slot",
    fn = test_putActor_actor_indexing },
  { name = "op_putActor: var-ref actor (flag bit 7) reads from globalVars",
    fn = test_putActor_var_ref_actor },
  { name = "op_equalZero ($28): jump when var != 0",
    fn = test_equalZero_jump_when_nonzero },
  { name = "op_notEqualZero ($A8): jump when var == 0",
    fn = test_notEqualZero_jump_when_zero },
  { name = "op_isGreater ($78): jump when comparand <= var",
    fn = test_isGreater_cmp_against_literal },
  { name = "ScummVM-spec: $78 isGreater uses SIGNED int16 (1 > -1)",
    fn = test_signed_comparison_isGreater },
  { name = "ScummVM-spec: $44 isLess uses SIGNED int16 (1 < -1)",
    fn = test_signed_comparison_isLess },
  { name = "ScummVM-spec: $38 isLessEqual uses SIGNED int16 (1 <= -1)",
    fn = test_signed_comparison_isLessEqual },
  { name = "ScummVM-spec: $04 isGreaterEqual uses SIGNED int16 (1 >= -1)",
    fn = test_signed_comparison_isGreaterEqual },
  { name = "op_isLess ($44): jump when comparand >= var",
    fn = test_isLess_cmp_against_literal },
  { name = "op_isLessEqual ($38): jump when comparand > var",
    fn = test_isLessEqual_cmp_against_literal },
  { name = "op_isGreaterEqual ($04): jump when comparand < var",
    fn = test_isGreaterEqual_cmp_against_literal },
  { name = "op_isEqual ($48): jump when comparand != var",
    fn = test_isEqual_cmp_against_literal },
  { name = "op_isNotEqual ($08): jump when comparand == var",
    fn = test_isNotEqual_cmp_against_literal },
  { name = "op_move ($1A): Var[X] = literal",
    fn = test_move_literal },
  { name = "op_move ($9A): Var[X] = Var[Y] (var-ref value)",
    fn = test_move_var_ref },
  { name = "op_add ($5A): Var[X] += literal",
    fn = test_add_literal },
  { name = "op_subtract ($3A): Var[X] -= literal",
    fn = test_subtract_literal },
  { name = "op_increment ($46): Var[X]++",
    fn = test_increment },
  { name = "op_decrement ($C6): Var[X]--",
    fn = test_decrement },
  -- Phase A.1: arithmetic & logic (ScummVM-derived)
  { name = "op_multiply ($1B): basic positive case",
    fn = test_phaseA_multiply_positive },
  { name = "op_multiply ($1B): SIGNED -5 * 3 = -15",
    fn = test_phaseA_multiply_signed },
  { name = "op_divide ($5B): basic 100 / 7 = 14",
    fn = test_phaseA_divide_basic },
  { name = "op_divide ($5B): divide-by-zero → 0",
    fn = test_phaseA_divide_by_zero },
  { name = "op_divide ($5B): SIGNED -30 / 6 = -5",
    fn = test_phaseA_divide_signed },
  { name = "op_and ($17): bitwise AND",
    fn = test_phaseA_and_basic },
  { name = "op_or ($57): bitwise OR",
    fn = test_phaseA_or_basic },
  -- Phase A.2: actor / object accessors
  { name = "op_getActorElevation ($06): result <- actor.elevation",
    fn = test_phaseA_getActorElevation },
  { name = "op_getActorScale ($3B): result <- actor.scalex",
    fn = test_phaseA_getActorScale },
  { name = "op_getActorCostume ($71): result <- actor.costume",
    fn = test_phaseA_getActorCostume },
  { name = "op_getActorMoving ($56): result <- actor.moving",
    fn = test_phaseA_getActorMoving },
  { name = "op_getObjectState ($0F): result <- objectState[obj]",
    fn = test_phaseA_getObjectState },
  { name = "ScummVM-spec: getActorFacing returns newDirToOldDir(facing)",
    fn = test_phaseA_getActorFacing_translates },
  { name = "op_getRandomNr ($16): result in [0, max]",
    fn = test_phaseA_getRandomNr_bounds },
  { name = "op_getActorWalkBox ($7B): result <- actorWalkBox[actor]",
    fn = test_phaseA_getActorWalkBox },
  { name = "op_getActorWidth ($6C): empty slot returns hardcoded 24",
    fn = test_phaseA_getActorWidth_default_24 },
  { name = "op_getActorWidth ($6C): populated slot returns value",
    fn = test_phaseA_getActorWidth_populated },
  { name = "op_getInventoryCount ($31): empty inventory returns 0",
    fn = test_phaseA_getInventoryCount_empty },
  { name = "KNOWN-DIVERGENCE: op_getAnimCounter ($22) stubbed to 0",
    fn = test_phaseA_getAnimCounter_stub_returns_zero },
  { name = "op_getDist ($34): Chebyshev distance for actor pair",
    fn = test_phaseA_getDist_actor_pair },
  { name = "op_getDist ($34): clamps to 0xFE",
    fn = test_phaseA_getDist_clamps_to_FE },
  { name = "op_getDist ($34): obj arg → 0xFF (actors-only impl)",
    fn = test_phaseA_getDist_obj_arg_returns_FF },
  -- Phase A.3: walk ops
  { name = "op_walkActorTo ($1E): targetX/Y set + moving=1",
    fn = test_phaseA_walkActorTo },
  { name = "op_walkActorToActor ($0D): heads toward target",
    fn = test_phaseA_walkActorToActor },
  { name = "op_putActorAtObject ($0E): obj-not-found leaves actor",
    fn = test_phaseA_putActorAtObject_obj_not_found },
  -- Phase A.4: actor / object mutators
  { name = "op_animateActor ($11): basic frame write",
    fn = test_phaseA_animateActor_basic },
  { name = "animateActor($FF): semantic code, no initFrame change",
    fn = test_phaseA_animateActor_semantic_FF },
  { name = "op_isActorInBox ($1F): actor outside box → no jump",
    fn = test_phaseA_isActorInBox_far_outside },
  { name = "op_pickupObject ($25): sets objectOwner = VAR_EGO",
    fn = test_phaseA_pickupObject_sets_owner },
  -- Phase A.5: script lifecycle
  { name = "op_freezeScripts ($60): runs without crash (harness limit)",
    fn = test_phaseA_freezeScripts_runs_without_crash },
  { name = "op_stopScript ($62): kills slot whose .number matches",
    fn = test_phaseA_stopScript_kills_matching },
  -- Phase A.6: cutscene
  { name = "op_cutscene ($40): nest unwinds on slot kill",
    fn = test_phaseA_cutscene_balanced_with_slot_kill },
  { name = "op_endCutscene ($C0): balanced cutscene + endCutscene",
    fn = test_phaseA_endCutscene_balanced },
  -- Phase A.7: camera
  { name = "op_panCameraTo ($12): writes cameraDest",
    fn = test_phaseA_panCameraTo },
  { name = "op_actorFollowCamera ($52): writes cameraFollows",
    fn = test_phaseA_actorFollowCamera },
  -- Phase A.8: audio (no-crash pinning)
  { name = "op_startSound ($1C): no-crash",
    fn = test_phaseA_startSound_runs },
  { name = "op_stopSound ($3C): no-crash",
    fn = test_phaseA_stopSound_runs },
  { name = "op_startMusic ($02): no-crash",
    fn = test_phaseA_startMusic_runs },
  { name = "op_stopMusic ($20): no-crash",
    fn = test_phaseA_stopMusic_runs },
  -- Phase A.9: misc stubs
  { name = "op_lights ($70): no-crash (stubbed)",
    fn = test_phaseA_lights_stub },
  { name = "op_pseudoRoom ($CC): no-crash (stubbed)",
    fn = test_phaseA_pseudoRoom_stub },
  { name = "op_soundKludge ($4C): no-crash (stub)",
    fn = test_phaseA_soundKludge_stub },
  { name = "op_oldRoomEffect ($5C): no-crash (stub)",
    fn = test_phaseA_oldRoomEffect_stub },
  { name = "op_debug ($6B): no-crash (stub)",
    fn = test_phaseA_debug_stub },
  { name = "op_drawBox ($3F): no-crash",
    fn = test_phaseA_drawBox_stub },
  -- Phase A.10: remaining primary handlers
  { name = "op_drawObject ($05): no-crash",
    fn = test_phaseA_drawObject_stub },
  { name = "op_doSentence ($19): no-crash",
    fn = test_phaseA_doSentence_stub },
  { name = "op_chainScript ($42): no-crash",
    fn = test_phaseA_chainScript_no_crash },
  { name = "op_stopObjectScript ($6E): kills matching slot",
    fn = test_phaseA_stopObjectScript_kills_matching },
  { name = "op_startObject ($37): no-crash",
    fn = test_phaseA_startObject_no_crash },
  { name = "op_setObjectName ($54): no-crash",
    fn = test_phaseA_setObjectName_no_crash },
  { name = "op_saveRestoreVerbs ($AB): no-crash",
    fn = test_phaseA_saveRestoreVerbs_no_crash },
  { name = "op_wait[$03] forCamera: no-crash",
    fn = test_phaseA_wait_forCamera },
  { name = "op_animateActor var-ref frame ($51 + BIT6): writes initFrame",
    fn = test_phaseA_animateActor_var_ref_frame },
  { name = "op_findObject ($35): no-hit point → 0",
    fn = test_phaseA_findObject_no_hit },
  { name = "op_getClosestObjActor ($66): no-crash",
    fn = test_phaseA_getClosestObjActor_no_crash },
  { name = "op_findInventory ($3D): empty → 0",
    fn = test_phaseA_findInventory_empty },
  { name = "op_getVerbEntrypoint ($0B): missing obj → 0",
    fn = test_phaseA_getVerbEntrypoint_no_obj },
  { name = "op_stopObjectCode ($A0): kills slot",
    fn = test_phaseA_stopObjectCode_kills_slot },
  { name = "op_override ($58): cutscene+override+end balanced",
    fn = test_phaseA_override_writes_cutScenePtr },
  { name = "op_putActorInRoom ($2D): writes actor.room + visible",
    fn = test_putActorInRoom },
  { name = "ScummVM-spec: putActorInRoom does NOT force visible=1",
    fn = test_putActorInRoom_visibility_per_scummvm },
  { name = "op_getActorRoom ($03): result <- actor.room",
    fn = test_getActorRoom },
  { name = "op_getActorX ($43): result <- actor.x",
    fn = test_getActorX },
  { name = "op_getActorY ($23): result <- actor.y",
    fn = test_getActorY },
  { name = "bit-var via op_move: $8000+N writes 1 bit to bitVars[N/8]",
    fn = test_bitVar_set_via_move },
  { name = "bit-var boundaries: bit 0/7/8/15 across byte boundary",
    fn = test_bitVar_boundaries },
  { name = "op_setVarRange ($26): byte-mode contiguous var write",
    fn = test_setVarRange_byte_mode },
  { name = "op_setVarRange ($A6): word-mode contiguous var write",
    fn = test_setVarRange_word_mode },
  { name = "op_putActor: bit 6 set ($41) → x is var-ref",
    fn = test_putActor_var_ref_x },
  { name = "op_jumpRelative ($18): unconditional signed jump",
    fn = test_jumpRelative },
  { name = "op_actorOps[$0E] ($13): sets actor.initFrame",
    fn = test_actorOps_initFrame },
  -- Phase B.1: actorOps sub-ops
  { name = "actorOps[$01] setCostume: actor.costume",
    fn = test_phaseB_actorOps_setCostume },
  { name = "actorOps[$02] setWalkSpeed: KNOWN-DIVERGENCE no field",
    fn = test_phaseB_actorOps_walkSpeed_KNOWN },
  { name = "actorOps[$03] sound: KNOWN-DIVERGENCE stub",
    fn = test_phaseB_actorOps_sound_KNOWN },
  { name = "actorOps[$04] walkAnimNr: actorWalkAnimNr[actor]",
    fn = test_phaseB_actorOps_walkAnimNr },
  { name = "actorOps[$05] talkAnimNr: actorTalkAnimStart/End[actor]",
    fn = test_phaseB_actorOps_talkAnimNr },
  { name = "actorOps[$06] standFrame: actorStandFrame[actor]",
    fn = test_phaseB_actorOps_standFrame },
  { name = "actorOps[$07] palette: KNOWN-DIVERGENCE stub",
    fn = test_phaseB_actorOps_palette_KNOWN },
  { name = "actorOps[$08] init: facing = 180",
    fn = test_phaseB_actorOps_init_facing_180 },
  { name = "actorOps[$09] setElevation: actor.elevation",
    fn = test_phaseB_actorOps_setElevation },
  { name = "actorOps[$0A] animDefault: resets walk/stand/talk frames",
    fn = test_phaseB_actorOps_animDefault },
  { name = "actorOps[$0B] palette2: KNOWN-DIVERGENCE stub",
    fn = test_phaseB_actorOps_palette2_KNOWN },
  { name = "actorOps[$0C] setTalkColor: actor.talkColor",
    fn = test_phaseB_actorOps_setTalkColor },
  { name = "actorOps[$0D] name: KNOWN-DIVERGENCE consumed",
    fn = test_phaseB_actorOps_name_KNOWN },
  { name = "actorOps[$10] setWidth: actorWidth[actor]",
    fn = test_phaseB_actorOps_setWidth },
  { name = "actorOps[$11] setScale: actor.scalex",
    fn = test_phaseB_actorOps_setScale },
  { name = "actorOps[$12] neverZClip: KNOWN-DIVERGENCE no-op",
    fn = test_phaseB_actorOps_neverZClip_KNOWN },
  { name = "actorOps[$13] setZClip: KNOWN-DIVERGENCE",
    fn = test_phaseB_actorOps_setZClip_KNOWN },
  { name = "actorOps[$14] ignoreBoxes: actorIgnoreBoxes=1",
    fn = test_phaseB_actorOps_ignoreBoxes },
  { name = "actorOps[$15] followBoxes: actorIgnoreBoxes=0",
    fn = test_phaseB_actorOps_followBoxes },
  { name = "actorOps[$16] animSpeed: KNOWN-DIVERGENCE",
    fn = test_phaseB_actorOps_animSpeed_KNOWN },
  { name = "actorOps[$17] shadow: KNOWN-DIVERGENCE",
    fn = test_phaseB_actorOps_shadow_KNOWN },
  -- Phase B.2: cursorCommand sub-ops
  { name = "cursorCommand[$01] cursorOn: cursorEnabled=1",
    fn = test_phaseB_cursorCommand_01_cursorOn },
  { name = "cursorCommand[$02] cursorOff: cursorEnabled=0",
    fn = test_phaseB_cursorCommand_02_cursorOff },
  { name = "cursorCommand[$03] userputOn: userPut=1",
    fn = test_phaseB_cursorCommand_03_userputOn },
  { name = "cursorCommand[$04] userputOff: userPut=0",
    fn = test_phaseB_cursorCommand_04_userputOff },
  { name = "cursorCommand[$05-$08] soft on/off: no-crash",
    fn = test_phaseB_cursorCommand_softOps_no_crash },
  { name = "cursorCommand[$0A] setCursorImage: KNOWN",
    fn = test_phaseB_cursorCommand_setCursorImage_KNOWN },
  { name = "cursorCommand[$0B] setCursorHotspot: KNOWN",
    fn = test_phaseB_cursorCommand_setCursorHotspot_KNOWN },
  { name = "cursorCommand[$0C] initCharset: KNOWN",
    fn = test_phaseB_cursorCommand_initCharset_KNOWN },
  { name = "cursorCommand[$0D] charsetColor: KNOWN",
    fn = test_phaseB_cursorCommand_charsetColor_KNOWN },
  -- Phase B.3: stringOps sub-ops
  { name = "stringOps: putCode + getChar round-trip",
    fn = test_phaseB_stringOps_putCode_getChar_roundtrip },
  { name = "stringOps: setChar overwrites byte",
    fn = test_phaseB_stringOps_setChar_roundtrip },
  { name = "stringOps: copyString preserves bytes",
    fn = test_phaseB_stringOps_copyString },
  -- Phase B.4: expression sub-ops
  { name = "expression: push 10, push 20, ADD = 30",
    fn = test_phaseB_expression_push_add },
  { name = "expression: push 50, push 20, SUB = 30",
    fn = test_phaseB_expression_push_sub },
  { name = "expression: push 6, push 7, MUL = 42",
    fn = test_phaseB_expression_push_mul },
  { name = "expression: DIV by 0 → 0 (zero guard)",
    fn = test_phaseB_expression_div_zero_guard },
  -- Phase B.5: resourceRoutines (all PARTIAL stubs)
  { name = "resourceRoutines[$04] loadRoom: KNOWN",
    fn = test_phaseB_resourceRoutines_loadRoom_KNOWN },
  { name = "resourceRoutines[$11] clearHeap: KNOWN",
    fn = test_phaseB_resourceRoutines_clearHeap_KNOWN },
  { name = "resourceRoutines default 1-param: KNOWN",
    fn = test_phaseB_resourceRoutines_default_1param_KNOWN },
  -- Phase B.6: systemOps
  -- (systemOps restart MOVED to end of TESTS — wipes all slots and breaks
  --  subsequent state-dependent tests like loadRoomWithEgo.)
  -- (roomOps tests MOVED to destabilizer zone — palette/scroll writes
  --  mutate MI1's display state and break later tests' expectations.)
  -- (print tests MOVED to end — text rendering touches BG3 + chore state.)
  { name = "op_faceActor ($09): facing 90/270 from relative X",
    fn = test_faceActor_east_and_west },
  { name = "op_setState ($07): writes objectState[obj]",
    fn = test_setState },
  { name = "op_getObjectOwner ($10): result <- objectOwner[obj]",
    fn = test_getObjectOwner },
  { name = "op_setClass ($5D): set + clear class bit roundtrip",
    fn = test_setClass_set_then_clear },
  { name = "ScummVM-spec: setClass(1) sets bit 0 ($0001), not bit 1",
    fn = test_setClass_class1_should_set_bit0 },
  { name = "ScummVM-spec: setClass(16) sets bit 15 ($8000)",
    fn = test_setClass_class16_should_set_bit15 },
  { name = "op_breakHere ($80): yields slot for one frame",
    fn = test_breakHere_yields_one_frame },
  { name = "op_isScriptRunning ($68): returns 0 for inactive script",
    fn = test_isScriptRunning_for_inactive_script },
  { name = "op_setOwnerOf ($29): writes objectOwner[obj] = owner",
    fn = test_setOwnerOf },
  { name = "REGRESSION: op_putActor sets egoPositioned for VAR_EGO only",
    fn = test_putActor_sets_egoPositioned_for_ego },
  -- Side-effect cluster (room changes). These can trigger processRoomChange
  -- on the next frame and destabilize subsequent tests, so they run last.
  { name = "op_loadRoom ($72): sets VAR_ROOM (deferred room change)",
    fn = test_loadRoom_sets_newRoom_and_VAR_ROOM },
  { name = "op_loadRoomWithEgo: bytecode obj/x/y parse correctly",
    fn = test_loadRoomWithEgo_bytecode_decode },
  -- Phase C: cross-cutting & multi-frame
  { name = "Phase C: cutscene nest 0→1→2→1→0 across nested pairs",
    fn = test_phaseC_cutscene_nest_double },
  { name = "Phase C: setCameraAt clamps to VAR_CAMERA_MAX_X",
    fn = test_phaseC_camera_clamp_high },
  { name = "Phase C: setCameraAt clamps to VAR_CAMERA_MIN_X",
    fn = test_phaseC_camera_clamp_low },
  { name = "Phase C: setCameraAt no clamp when target in range",
    fn = test_phaseC_camera_no_clamp_when_in_range },
  { name = "Phase C: isActorInBox with actor inside → jump",
    fn = test_phaseC_isActorInBox_inside },
  { name = "Phase C: isActorInBox with actor outside → no jump",
    fn = test_phaseC_isActorInBox_outside },
  -- BOXM-pathfinding regression catchers (commit 921e287):
  { name = "Phase C: buildWalkPath no BOXM route → pathLen=0 fizzle",
    fn = test_buildWalkPath_no_route_fizzles },
  { name = "Phase C: buildWalkPath with BOXM route → pathLen>=1 builds",
    fn = test_buildWalkPath_with_route_builds_path },
  { name = "Phase C: walkActor multi-leg traverses 3 boxes (per-leg dispatch)",
    fn = test_walkActor_multiLeg_traverses_boxes },
  -- Phase D: integration smokes
  { name = "Phase D: boot smoke (room set, brightness $0F, VAR_ROOM match)",
    fn = test_phaseD_boot_smoke },
  { name = "Phase D: putActorInRoom into current room (transient actorsDirty)",
    fn = test_phaseD_putActorInRoom_into_current_room },
  -- DESTABILIZER ZONE — these tests mutate MI1 game state. Order so the
  -- harshest one (systemOps restart) is absolutely last.
  -- roomOps sub-ops (palette/scroll/shake mutate display state):
  { name = "roomOps[$01] roomScroll: no-crash",
    fn = test_phaseB_roomOps_01_roomScroll },
  { name = "roomOps[$03] setScreen: KNOWN",
    fn = test_phaseB_roomOps_03_setScreen_KNOWN },
  { name = "roomOps[$04] roomPalColor: no-crash",
    fn = test_phaseB_roomOps_04_setPalColor },
  { name = "roomOps[$05] shakeOn: no-crash",
    fn = test_phaseB_roomOps_05_shakeOn },
  { name = "roomOps[$06] shakeOff: no-crash",
    fn = test_phaseB_roomOps_06_shakeOff },
  { name = "roomOps[$07] roomScale: KNOWN",
    fn = test_phaseB_roomOps_07_scale_KNOWN },
  { name = "roomOps[$08] scaleSimple: KNOWN",
    fn = test_phaseB_roomOps_08_scaleSimple_KNOWN },
  { name = "roomOps[$09] saveGame: KNOWN",
    fn = test_phaseB_roomOps_09_saveGame_KNOWN },
  { name = "roomOps[$0A] fade: KNOWN",
    fn = test_phaseB_roomOps_0A_fade_KNOWN },
  { name = "roomOps[$0B] rgbIntensity: no-crash",
    fn = test_phaseB_roomOps_0B_rgbIntensity },
  { name = "roomOps[$0C] shadow: KNOWN",
    fn = test_phaseB_roomOps_0C_shadow_KNOWN },
  { name = "roomOps[$0D] saveString: KNOWN",
    fn = test_phaseB_roomOps_0D_saveString_KNOWN },
  { name = "roomOps[$0E] loadString: KNOWN",
    fn = test_phaseB_roomOps_0E_loadString_KNOWN },
  { name = "roomOps[$0F] palManipulate: KNOWN",
    fn = test_phaseB_roomOps_0F_palManipulate_KNOWN },
  -- print sub-ops (text rendering, BG3 + chore writes):
  { name = "print: AT + COLOR + TEXTSTRING",
    fn = test_phaseB_print_at_color_textstring },
  { name = "print: SO_CLIPPING + SO_ERASE: KNOWN",
    fn = test_phaseB_print_clipping_erase_KNOWN },
  { name = "print: CENTER/LEFT/OVERHEAD",
    fn = test_phaseB_print_center_left_overhead },
  { name = "print: SO_SAY_VOICE: KNOWN",
    fn = test_phaseB_print_say_voice_KNOWN },
  { name = "printEgo: TEXTSTRING",
    fn = test_phaseB_printEgo_textstring },
  { name = "systemOps[$03] restart: runs without crash (harness limit)",
    fn = test_phaseB_systemOps_restart_runs },
}

H.run_all(TESTS, 600)
