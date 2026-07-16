-- clouds_behind_logo.lua — visual regression: the intro title clouds must drift
-- BEHIND the "Secret of Monkey Island" logo, per-pixel.
--
-- The cloud actors (7,8) render as OBJ priority-2 sprites. The logo letters are
-- on BG1 at priority 1 (the per-pixel BG2 mask), so a letter pixel occludes a
-- priority-2 cloud pixel. Proof: at a fixed frame, scan the region where a cloud
-- overlaps "The Secret of" and count composited pixels that are (a) letter-
-- magenta AND (b) covered by a cloud sprite's box (OAM). A priority-2 sprite
-- hidden at that pixel means the letter is in front → clouds are behind.
-- Also require cloud-blue pixels in the same region (the cloud is actually
-- rendered there, so the letter test is not trivially satisfied).
--
-- FAILS on a "clouds in front" build (letters covered by cloud → few letter-
-- under-cloud pixels); PASSES only when the logo occludes the clouds.
--
-- Run via: mcp__smi-workflow__run_test clouds_behind_logo.lua   (copy to
-- distribution/ first) — or the Python wrapper tests/run_clouds_test.py.

local TARGET = 1700          -- deterministic (title is a static, input-free cutscene)
local X0, X1 = 50, 116       -- "The Secret of" span overlapped by the left cloud
local Y0, Y1 = 34, 46
local MIN_LETTER_UNDER_CLOUD = 20
local MIN_CLOUD = 20
local MIN_CLOUD_SPRITES = 4

local function isLetter(r, g, b) return r >= 170 and b >= 170 and g <= 100 end
local function isCloud(r, g, b)
  return b >= 90 and r <= 110 and g <= 110 and not isLetter(r, g, b)
end

-- priority-2 cloud sprite boxes (cloud costume tile range 96..160), from OAM
local function cloudBoxes()
  local OAM = emu.memType.snesSpriteRam
  local boxes = {}
  for i = 0, 127 do
    local attr = emu.read(i*4+3, OAM, false)
    if ((attr >> 4) & 3) == 2 then
      local tile = emu.read(i*4+2, OAM, false)
      if tile >= 96 and tile <= 160 then
        local x  = emu.read(i*4+0, OAM, false)
        local y  = emu.read(i*4+1, OAM, false)
        local hi = emu.read(0x200 + (i >> 2), OAM, false)
        local xh = (hi >> ((i & 3) * 2)) & 1
        boxes[#boxes+1] = { x | (xh << 8), y }
      end
    end
  end
  return boxes
end

local function covered(boxes, px, py)
  for _, b in ipairs(boxes) do
    if px >= b[1] and px < b[1]+8 and py >= b[2] and py < b[2]+8 then return true end
  end
  return false
end

local done = false
emu.addEventCallback(function()
  if done then return end
  local st = emu.getState()
  if (st["ppu.frameCount"] or 0) < TARGET then return end
  done = true

  local buf = emu.getScreenBuffer()
  local sz  = emu.getScreenSize()
  local W, H = sz.width or 256, sz.height or 224
  local yoff = 0
  if H == 239 then yoff = 7 elseif H == 478 then yoff = 14 end

  local boxes = cloudBoxes()
  local letterUnderCloud, cloudPix = 0, 0
  for py = Y0, Y1 do
    for px = X0, X1 do
      local argb = buf[(py+yoff)*W + px + 1] or 0
      local r = (argb >> 16) & 0xFF
      local g = (argb >> 8) & 0xFF
      local b = argb & 0xFF
      if covered(boxes, px, py) then
        if isLetter(r, g, b) then letterUnderCloud = letterUnderCloud + 1 end
        if isCloud(r, g, b)  then cloudPix = cloudPix + 1 end
      end
    end
  end

  print(string.format(
    "CLOUDS_BEHIND cloudSprites=%d letterUnderCloud=%d (min %d) cloudPix=%d (min %d)",
    #boxes, letterUnderCloud, MIN_LETTER_UNDER_CLOUD, cloudPix, MIN_CLOUD))
  local ok = (#boxes >= MIN_CLOUD_SPRITES)
    and (letterUnderCloud >= MIN_LETTER_UNDER_CLOUD)
    and (cloudPix >= MIN_CLOUD)
  print(ok and "RESULT: PASS" or "RESULT: FAIL")
  print("##CLOUDS_BEHIND_DONE##")
  emu.stop()
end, emu.eventType.endFrame)
