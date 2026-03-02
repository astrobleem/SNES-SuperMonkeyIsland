"""Decode MI1 boot script (script 1, 42 bytes) using opcodes_v5.py."""
import sys, struct
from io import BytesIO

sys.path.insert(0, 'E:/gh/SNES-SuperMonkeyIsland/tools')
import scumm.opcodes_v5 as op

# ---------------------------------------------------------------------------
# Raw bytecode
# ---------------------------------------------------------------------------
raw = bytes([
    0x0C, 0x01, 0x23,
    0x1A, 0x00, 0x40, 0x08, 0x00,
    0xAD, 0x00, 0x40, 0x00,
    0x46, 0x00, 0x40,
    0x44, 0x00, 0x40, 0x0B, 0x00, 0xF2, 0xFF,
    0x80,
    0x80,
    0x80,
    0xD6, 0x00, 0x00, 0x01, 0x00,
    0xA8, 0x00, 0x00, 0xF3, 0xFF,
    0x4A, 0x23, 0xFF,
    0x18, 0xED, 0xFF,
    0xA0,
])

# ---------------------------------------------------------------------------
# Variable reference decoder
# SCUMM v5 variable encoding (2-byte little-endian word):
#   bit15=0, bit14=0 -> local var   (bits 3-0 = index 0..15)
#   bit15=0, bit14=1 -> bit var     (bits 13-0 = bit number)
#   bit15=1          -> global var  (bits 14-0 = global number)
# ---------------------------------------------------------------------------
KNOWN_GLOBALS = {
    0:   'VAR_KEYPRESS',
    1:   'VAR_EGO',
    8:   'VAR_SOUNDRESULT',
    15:  'VAR_OVERRIDE_HIT',
    17:  'VAR_HAVE_MSG',
    19:  'VAR_CURRENT_LIGHTS',
    98:  'VAR_NUM_GLOBAL_OBJS',
    124: 'VAR_CAMERA_MIN_X',
    125: 'VAR_CAMERA_MAX_X',
}

def fmt_var(lo, hi):
    v = lo | (hi << 8)
    if v & 0x8000:
        idx = v & 0x7FFF
        name = KNOWN_GLOBALS.get(idx, f'VAR[{idx}]')
        return name
    elif v & 0x4000:
        return f'BITVAR[{v & 0x3FFF}]'
    else:
        return f'local[{v & 0x0F}]'

# ---------------------------------------------------------------------------
# Step 1: mechanical decode using decode_opcode
# ---------------------------------------------------------------------------
print('=' * 85)
print('PASS 1 — Mechanical decode (offset / opcode / name / param bytes / raw params)')
print('=' * 85)

stream = BytesIO(raw)
rows = []
while True:
    pos = stream.tell()
    b = stream.read(1)
    if not b:
        break
    opcode_byte = b[0]
    name, param_bytes = op.decode_opcode(opcode_byte, stream)
    param_raw = raw[pos + 1 : pos + 1 + param_bytes]
    param_hex = ' '.join(f'{x:02X}' for x in param_raw)
    total_len = 1 + param_bytes
    rows.append((pos, opcode_byte, name, param_bytes, param_raw, param_hex, total_len))
    print(f'  [{pos:3d}]  {opcode_byte:#04x}  {name:<26}  {param_bytes} param bytes  [ {param_hex} ]')

print(f'\nStream fully consumed: {stream.tell()} / {len(raw)} bytes')

# ---------------------------------------------------------------------------
# Step 2: semantic decode
# ---------------------------------------------------------------------------
print()
print('=' * 85)
print('PASS 2 — Semantic decode with variable names and jump targets')
print('=' * 85)
print()

# We work from the rows collected above, parsing param_raw manually per opcode.

for pos, opcode_byte, name, param_bytes, param_raw, param_hex, total_len in rows:
    pr = param_raw  # shorthand

    if name == 'resourceRoutines':
        # sub-opcode in first byte, then param
        # sub 0x01 = loadScript; bit0 of main opcode -> script num is literal or var
        sub = pr[0]
        sub_names = {
            0x01: 'loadScript', 0x02: 'loadRoom', 0x03: 'nukeScript',
            0x04: 'nukeRoom', 0x05: 'loadCostume', 0x06: 'loadCharset',
        }
        sub_name = sub_names.get(sub, f'sub_{sub:#04x}')
        arg = pr[1]
        semantic = f'resourceRoutines.{sub_name}({arg})'
        note = f'sub={sub:#04x}={sub_name}, arg={arg}=0x{arg:02X}'

    elif name == 'move':
        # result(2B var) + value(2B — either var or p16 depending on opcode bits)
        # 0x1A: bit1=1 -> value IS a var ref? Let's check:
        # 0x1A & 0x02 = 0x02  -- bit1 set -> value is var reference
        # But OPCODE_MAP[0x1A] == 'move', decoder consumed 4 bytes: var(2) + var_or_p16(2)
        result = fmt_var(pr[0], pr[1])
        # bit1 of opcode (0x1A & 0x02 = 0): 0x1A in binary = 0001 1010
        # bit1 = (0x1A >> 1) & 1 = 1  -> value from var
        val_is_var = bool(opcode_byte & 0x02)
        if val_is_var:
            value = fmt_var(pr[2], pr[3])
        else:
            value = str(struct.unpack_from('<H', pr, 2)[0])
        semantic = f'{result} = {value}'
        note = f'result={result}, value={value} ({"var-ref" if val_is_var else "literal"})'

    elif name == 'putActorInRoom':
        # 0xAD: actor(p8, bit0=1->var), room(p8, bit6=?)
        # bit0 of 0xAD = 1 -> actor is var (2B): pr[0..1]
        # bit6 of 0xAD = 0 -> room is literal (1B): pr[2]
        actor = fmt_var(pr[0], pr[1])
        room = pr[2]
        semantic = f'putActorInRoom(actor={actor}, room={room})'
        note = f'bit0=1 -> actor from var {actor}, bit6=0 -> room literal={room}'

    elif name == 'increment':
        var = fmt_var(pr[0], pr[1])
        semantic = f'increment({var})'
        note = f'var={var}'

    elif name == 'isLess':
        # cond_jump: var(2B) + p16(2B) + jump_rel(2B signed)
        var = fmt_var(pr[0], pr[1])
        value = struct.unpack_from('<H', pr, 2)[0]
        rel = struct.unpack_from('<h', pr, 4)[0]
        target = (pos + total_len) + rel
        semantic = f'if {var} < {value}: jump [{target}]'
        note = f'var={var}, value={value}, rel={rel}, target={target}'

    elif name == 'breakHere':
        semantic = 'breakHere()'
        note = 'yield execution for 1 frame'

    elif name == 'getActorMoving':
        # result_var(2B) + actor(p8: bit1 or bit0 determines if var)
        # 0xD6 & 0x02 = 0x02 -> actor is var ref (2B)
        result = fmt_var(pr[0], pr[1])
        actor_is_var = bool(opcode_byte & 0x02)
        if actor_is_var:
            actor = fmt_var(pr[2], pr[3])
        else:
            actor = str(pr[2])
        semantic = f'{result} = getActorMoving({actor})'
        note = f'result={result}, actor={actor} ({"var" if actor_is_var else "literal"})'

    elif name == 'notEqualZero':
        # var(2B) + jump_rel(2B signed)
        var = fmt_var(pr[0], pr[1])
        rel = struct.unpack_from('<h', pr, 2)[0]
        target = (pos + total_len) + rel
        semantic = f'if {var} != 0: jump [{target}]'
        note = f'var={var}, rel={rel}, target={target}'

    elif name == 'startScript':
        # script_num(p8: bit0->var) + flags_byte + args(list ending 0xFF)
        # 0x4A & 0x01 = 0 -> script_num is literal p8 (1B)
        script_num = pr[0]
        # args list: starts at pr[1], terminated by 0xFF
        # Here pr[1] = 0xFF immediately -> no args
        semantic = f'startScript({script_num}, freeze=0, recursive=0, args=[])'
        note = f'script_num={script_num}=0x{script_num:02X}, 0xFF terminator at pr[1] -> no args'

    elif name == 'jumpRelative':
        rel = struct.unpack_from('<h', pr, 0)[0]
        target = (pos + total_len) + rel
        semantic = f'jump [{target}]'
        note = f'rel={rel}, target={target}'

    elif name == 'stopObjectCode':
        semantic = 'stopObjectCode()'
        note = 'terminate this script instance'

    else:
        semantic = f'{name}(...)'
        note = 'unhandled in semantic pass'

    print(f'  [{pos:3d}]  {opcode_byte:#04x}  {name:<22}  {semantic}')
    print(f'          {"":4}  {"":22}  # {note}')
    print()

# ---------------------------------------------------------------------------
# Step 3: control flow summary
# ---------------------------------------------------------------------------
print('=' * 85)
print('PASS 3 — Control flow summary')
print('=' * 85)
print("""
  [  0]  resourceRoutines.loadScript(35)   -- preload script 35 into resource cache
  [  3]  BITVAR[0] = VAR_EGO              -- copy actor 1 (Guybrush) number to BITVAR[0]
  [  8]  putActorInRoom(BITVAR[0], 0)      -- move Guybrush to room 0 (limbo / none)
  [ 12]  increment(BITVAR[0])              -- BITVAR[0]++ (now = VAR_EGO + 1 = 2)
  [ 15]  if BITVAR[0] < 11: jump [8]      -- loop: putActorInRoom actors 1..10 to room 0
                                           -- (ensures all actors start in limbo)
  ---- loop body (frames) ----
  [ 22]  breakHere()                       -- yield frame
  [ 23]  breakHere()                       -- yield frame
  [ 24]  breakHere()                       -- yield frame (3-frame pause)
  [ 25]  local[0] = getActorMoving(local[1])  -- check if ego is still walking
  [ 30]  if local[0] != 0: jump [22]      -- wait until ego stops moving
  ---- done waiting ----
  [ 35]  startScript(35)                   -- launch main game script (script 35)
  [ 38]  jump [22]                         -- unconditional loop back to frame yield
  [ 41]  stopObjectCode()                  -- (unreachable) script end marker
""")

print('Key insight: Script 1 is the MI1 bootstrap.')
print('  1. Loads script 35 into the resource cache.')
print('  2. Kicks all 10 actors (1-10) into room 0 (limbo) via a counted loop.')
print('  3. Waits 3 frames, then spins waiting for VAR_EGO to stop moving.')
print('  4. Launches script 35 (main game script) and loops forever yielding frames.')
