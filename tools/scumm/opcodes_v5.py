"""SCUMM v5 opcode table and parameter decoders.

Complete 256-entry opcode table for SCUMM v5 (MI1 CD Talkie).
Reusable by the opcode audit tool, Phase 1 script packer, and interpreter design.

Reference: ScummVM engines/scumm/script_v5.cpp
"""

from io import BytesIO
from typing import Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Parameter reading helpers
# ---------------------------------------------------------------------------

def _p8_size(opcode: int, bit: int) -> int:
    """Byte count for a p8 param: 2 if flag bit set (var ref), 1 if clear."""
    return 2 if (opcode & bit) else 1


def _read_p8(stream: BytesIO, opcode: int, bit: int) -> int:
    """Read a p8 param from stream, return bytes consumed."""
    n = _p8_size(opcode, bit)
    stream.read(n)
    return n


def _read_p16(stream: BytesIO) -> int:
    """Read a p16 param (always 2 bytes), return bytes consumed."""
    stream.read(2)
    return 2


def _read_var(stream: BytesIO) -> int:
    """Read a variable reference (always 2 bytes), return bytes consumed."""
    stream.read(2)
    return 2


# ---------------------------------------------------------------------------
# Decoder functions — each returns total param bytes consumed
# ---------------------------------------------------------------------------

def _dec_none(op: int, s: BytesIO) -> int:
    """No parameters."""
    return 0


def _dec_jump(op: int, s: BytesIO) -> int:
    """jumpRelative: 2-byte signed offset."""
    s.read(2)
    return 2


def _dec_delay(op: int, s: BytesIO) -> int:
    """delay: 3-byte (24-bit) LE delay value."""
    s.read(3)
    return 3


def _dec_delay_variable(op: int, s: BytesIO) -> int:
    """delayVariable: 2-byte variable reference."""
    s.read(2)
    return 2


# --- Simple fixed/flagged param patterns ---

def _make_simple_decoder(p8_bits: List[int], fixed: int) -> Callable:
    """Create decoder for opcodes with p8 flag params + fixed-size params."""
    def decoder(op: int, s: BytesIO) -> int:
        total = 0
        for bit in p8_bits:
            total += _read_p8(s, op, bit)
        if fixed > 0:
            s.read(fixed)
            total += fixed
        return total
    return decoder


# putActor: p8[7] + p16 + p16
_dec_putActor = _make_simple_decoder([0x80], 4)

# startMusic/startSound/stopSound/loadRoom/stopScript/freezeScripts: p8[7]
_dec_p8_7 = _make_simple_decoder([0x80], 0)

# getActorRoom, getActorElevation, getRandomNr, getActorCostume,
# getActorWalkBox, getActorMoving, getActorFacing, getActorWidth,
# getScriptRunning, isSoundRunning, getInventoryCount, getActorScale,
# getStringWidth: result(2) + p8[7]
_dec_result_p8_7 = _make_simple_decoder([0x80], 2)

# getObjectState, getObjectOwner, getClosestObjActor: result(2) + p16
_dec_result_p16 = _make_simple_decoder([], 4)

# getActorX, getActorY: result(2) + p16[7] (word param, flag doesn't change size)
_dec_result_p16_7 = _make_simple_decoder([], 4)

# isGreaterEqual, isNotEqual, isLessEqual, isGreater, isLess, isEqual:
# p16(var) + p16[7] + jump(2) = 6 bytes always
_dec_cond_jump = _make_simple_decoder([], 6)

# equalZero, notEqualZero: p16(var) + jump(2) = 4 bytes
_dec_zero_jump = _make_simple_decoder([], 4)

# setState: p16 + p8[7]
_dec_setState = _make_simple_decoder([0x80], 2)

# faceActor: p8[7] + p16[6] = p8(0x80) + 2
_dec_faceActor = _make_simple_decoder([0x80], 2)

# animateActor: p8[7] + p8[6]
_dec_animateActor = _make_simple_decoder([0x80, 0x40], 0)

# panCameraTo / setCameraAt: p16[7] = 2 bytes always
_dec_p16_7 = _make_simple_decoder([], 2)

# move, and, or, add, subtract, multiply, divide: result(2) + p16[7] = 4
_dec_arith = _make_simple_decoder([], 4)

# walkActorTo: p8[7] + p16[6] + p16[5] = p8(0x80) + 4
_dec_walkActorTo = _make_simple_decoder([0x80], 4)

# walkActorToActor: p8[7] + p8[6] + byte(1) = variable + 1
_dec_walkActorToActor = _make_simple_decoder([0x80, 0x40], 1)

# putActorAtObject: p8[7] + p16[6] = p8(0x80) + 2
_dec_putActorAtObject = _make_simple_decoder([0x80], 2)

# getVerbEntrypoint: result(2) + p16[7] + p16[6] = 6
_dec_getVerbEntrypoint = _make_simple_decoder([], 6)

# walkActorToObject: p8[7] + p16[6] = p8(0x80) + 2
_dec_walkActorToObject = _make_simple_decoder([0x80], 2)

# getDist: result(2) + p16[7] + p16[6] = 6
_dec_getDist = _make_simple_decoder([], 6)

# findObject: result(2) + p8[7] + p8[6]
_dec_findObject = _make_simple_decoder([0x80, 0x40], 2)

# findInventory: result(2) + p8[7] + p8[6]
_dec_findInventory = _make_simple_decoder([0x80, 0x40], 2)

# actorFromPos: result(2) + p16[7] + p16[6] = 6
_dec_actorFromPos = _make_simple_decoder([], 6)

# putActorInRoom: p8[7] + p8[6]
_dec_putActorInRoom = _make_simple_decoder([0x80, 0x40], 0)

# loadRoomWithEgo: p16[7] + p8[6] + word(2) + word(2) = p16 + p8(0x40) + 4
def _dec_loadRoomWithEgo(op: int, s: BytesIO) -> int:
    total = _read_p16(s)          # object (p16, always 2)
    total += _read_p8(s, op, 0x40)  # room (p8, flag bit 6)
    s.read(4)                     # x(2) + y(2) fixed words
    total += 4
    return total

# pickupObject: p16[7] + p8[6]
_dec_pickupObject = _make_simple_decoder([0x40], 2)

# setOwnerOf: p16[7] + p8[6]
_dec_setOwnerOf = _make_simple_decoder([0x40], 2)

# stopObjectScript: p16[7] = 2
_dec_stopObjScript = _make_simple_decoder([], 2)

# debug: p16[7] = 2
_dec_debug = _make_simple_decoder([], 2)

# increment/decrement: result(2)
_dec_inc_dec = _make_simple_decoder([], 2)

# actorFollowCamera: p8[7]
_dec_actorFollowCamera = _make_simple_decoder([0x80], 0)

# lights: p8[7] + byte + byte = p8(0x80) + 2
_dec_lights = _make_simple_decoder([0x80], 2)

# isActorInBox: p8[7] + p8[6] + jump(2)
_dec_isActorInBox = _make_simple_decoder([0x80, 0x40], 2)

# ifNotState / ifState: p16[7] + p8[6] + jump(2)
_dec_ifState = _make_simple_decoder([0x40], 4)

# getAnimCounter: result(2) + p8[7]
_dec_getAnimCounter = _make_simple_decoder([0x80], 2)


# --- Variable-length argument lists ---

def _read_varargs(s: BytesIO) -> int:
    """Read word varargs until $FF byte. Each arg = 1-byte aux + p16 value.
    Actually in SCUMM v5, getWordVararg reads p16 values until the NEXT
    opcode byte has value 0xFF. The args are p16[flag] where the flag
    comes from each arg's leading byte."""
    total = 0
    while True:
        b = s.read(1)
        if not b or b[0] == 0xFF:
            total += 1
            break
        # This byte is the "aux opcode" for this arg — its bit 7 is the flag
        total += 1
        # Read word param (p16 — always 2 bytes regardless of flag)
        s.read(2)
        total += 2
    return total


def _dec_startScript(op: int, s: BytesIO) -> int:
    """startScript: p8[7] + varargs."""
    total = _read_p8(s, op, 0x80)
    total += _read_varargs(s)
    return total


def _dec_startObject(op: int, s: BytesIO) -> int:
    """startObject: p16[7] + p8[6] + varargs."""
    total = _read_p16(s)             # object (always 2)
    total += _read_p8(s, op, 0x40)   # script (p8, flag bit 6)
    total += _read_varargs(s)
    return total


def _dec_chainScript(op: int, s: BytesIO) -> int:
    """chainScript: p8[7] + varargs."""
    total = _read_p8(s, op, 0x80)
    total += _read_varargs(s)
    return total


def _dec_cutscene(op: int, s: BytesIO) -> int:
    """cutscene: varargs only."""
    return _read_varargs(s)


def _dec_soundKludge(op: int, s: BytesIO) -> int:
    """soundKludge: varargs only."""
    return _read_varargs(s)


def _dec_setClass(op: int, s: BytesIO) -> int:
    """setClass (actorSetClass): p16[7] + varargs."""
    total = _read_p16(s)
    total += _read_varargs(s)
    return total


def _dec_ifClassOfIs(op: int, s: BytesIO) -> int:
    """ifClassOfIs: p16[7] + varargs + jump(2)."""
    total = _read_p16(s)
    total += _read_varargs(s)
    s.read(2)
    total += 2
    return total


# --- String-terminated params ---

def _read_string(s: BytesIO) -> int:
    """Read null-terminated string, return bytes consumed including the NUL."""
    total = 0
    while True:
        b = s.read(1)
        total += 1
        if not b or b[0] == 0x00:
            break
    return total


def _dec_setObjectName(op: int, s: BytesIO) -> int:
    """setObjectName: p16[7] + null-terminated string."""
    total = _read_p16(s)
    total += _read_string(s)
    return total


# --- Sub-opcode sequences ---

def _actor_subop_decoder(sub: int, s: BytesIO) -> int:
    """Decode params for one actorOps sub-opcode."""
    key = sub & 0x1F
    if key == 0x00:  # dummy / costume in some versions
        return _read_p8(s, sub, 0x80)
    elif key == 0x01:  # costume
        return _read_p8(s, sub, 0x80)
    elif key == 0x02:  # walkSpeed: xspeed, yspeed
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x03:  # sound
        return _read_p8(s, sub, 0x80)
    elif key == 0x04:  # walkFrame
        return _read_p8(s, sub, 0x80)
    elif key == 0x05:  # talkStartFrame, talkStopFrame
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x06:  # standFrame
        return _read_p8(s, sub, 0x80)
    elif key == 0x07:  # (v3-v4 only or palette): 3 p8 params
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40) + _read_p8(s, sub, 0x20)
    elif key == 0x08:  # init
        return 0
    elif key == 0x09:  # elevation (p16)
        return _read_p16(s)
    elif key == 0x0A:  # animDefaults
        return 0
    elif key == 0x0B:  # palette: index, value
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x0C:  # talkColor
        return _read_p8(s, sub, 0x80)
    elif key == 0x0D:  # actorName: null-terminated string
        return _read_string(s)
    elif key == 0x0E:  # initFrame
        return _read_p8(s, sub, 0x80)
    elif key == 0x10:  # width
        return _read_p8(s, sub, 0x80)
    elif key == 0x11:  # scale: x, y
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x12:  # neverZClip
        return 0
    elif key == 0x13:  # setZClip
        return _read_p8(s, sub, 0x80)
    elif key == 0x14:  # ignoreBoxes
        return 0
    elif key == 0x15:  # followBoxes
        return 0
    elif key == 0x16:  # animSpeed
        return _read_p8(s, sub, 0x80)
    elif key == 0x17:  # shadow mode
        return _read_p8(s, sub, 0x80)
    else:
        return 0  # unknown sub-op, assume no params


def _dec_actorOps(op: int, s: BytesIO) -> int:
    """actorOps: p8[7] (actor) + sub-opcode sequence terminated by $FF."""
    total = _read_p8(s, op, 0x80)
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0xFF:
            break
        total += _actor_subop_decoder(b[0], s)
    return total


# print / printEgo sub-opcode decoding (decodeParseString)
def _print_subop_decoder(sub: int, s: BytesIO) -> int:
    """Decode params for one print sub-opcode."""
    key = sub & 0x0F
    if key == 0x00:  # pos: xpos(p16), ypos(p16)
        return _read_p16(s) + _read_p16(s)
    elif key == 0x01:  # color
        return _read_p8(s, sub, 0x80)
    elif key == 0x02:  # clipping / right
        return _read_p16(s)
    elif key == 0x03:  # center (AT in some refs) — width, height? or nothing
        # In v5, case 3 of decodeParseString: setCenter — no params
        # Actually this reads nothing in ScummVM v5
        return 0
    elif key == 0x04:  # center
        return 0
    elif key == 0x06:  # left
        return 0
    elif key == 0x07:  # overhead
        return 0
    elif key == 0x08:  # ignore (mumble)
        return 0
    elif key == 0x0F:  # string data (text) terminated by specific markers
        return _read_print_string(s)
    else:
        return 0


def _read_print_string(s: BytesIO) -> int:
    """Read print string data. Terminated by $00 (NUL consumed).
    Embedded control codes $01-$08 are followed by 2 bytes (variable refs).
    """
    total = 0
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        val = b[0]
        if val == 0x00:
            break
        elif 0x01 <= val <= 0x08:
            # Embedded variable/verb/newline/formatting codes: 2 extra bytes
            s.read(2)
            total += 2
    return total


def _dec_print(op: int, s: BytesIO) -> int:
    """print: p8[7] (actor) + sub-opcode sequence for decodeParseString."""
    total = _read_p8(s, op, 0x80)
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0xFF:
            break
        total += _print_subop_decoder(b[0], s)
    return total


def _dec_printEgo(op: int, s: BytesIO) -> int:
    """printEgo: no actor param (uses ego), sub-opcode sequence."""
    total = 0
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0xFF:
            break
        total += _print_subop_decoder(b[0], s)
    return total


# verbOps sub-opcode decoding
def _verb_subop_decoder(sub: int, s: BytesIO) -> int:
    """Decode params for one verbOps sub-opcode."""
    key = sub & 0x1F
    if key == 0x01:  # image: p16
        return _read_p16(s)
    elif key == 0x02:  # name: null-terminated string
        return _read_string(s)
    elif key == 0x03:  # color: p8
        return _read_p8(s, sub, 0x80)
    elif key == 0x04:  # hiColor: p8
        return _read_p8(s, sub, 0x80)
    elif key == 0x05:  # setXY: p16, p16
        return _read_p16(s) + _read_p16(s)
    elif key == 0x06:  # on
        return 0
    elif key == 0x07:  # off
        return 0
    elif key == 0x08:  # delete
        return 0
    elif key == 0x09:  # new
        return 0
    elif key == 0x10:  # dimColor: p8
        return _read_p8(s, sub, 0x80)
    elif key == 0x11:  # dim (key)
        return 0
    elif key == 0x12:  # key: p8
        return _read_p8(s, sub, 0x80)
    elif key == 0x13:  # center
        return 0
    elif key == 0x14:  # setToString: p16
        return _read_p16(s)
    elif key == 0x16:  # setToObject: p16, p8
        return _read_p16(s) + _read_p8(s, sub, 0x80)
    elif key == 0x17:  # backColor: p8
        return _read_p8(s, sub, 0x80)
    else:
        return 0


def _dec_verbOps(op: int, s: BytesIO) -> int:
    """verbOps: p8[7] (verb) + sub-opcode sequence terminated by $FF."""
    total = _read_p8(s, op, 0x80)
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0xFF:
            break
        total += _verb_subop_decoder(b[0], s)
    return total


# roomOps sub-opcode decoding
def _room_subop_decoder(sub: int, s: BytesIO) -> int:
    """Decode params for one roomOps sub-opcode."""
    key = sub & 0x1F
    if key == 0x01:  # scroll: minX(p16), maxX(p16)
        return _read_p16(s) + _read_p16(s)
    elif key == 0x02:  # roomColor (v3) — not used in v5
        return 0
    elif key == 0x03:  # setScreen: b(p16), h(p16)
        return _read_p16(s) + _read_p16(s)
    elif key == 0x04:  # setPalColor: r(p16), g(p16), b(p16), index(p8)
        # In ScummVM: reads fetchScriptByte for aux opcode, then params
        # Actually it reads 3 p16 + 1 p8 from the sub-opcode flags
        return _read_p16(s) + _read_p16(s) + _read_p16(s) + _read_p8(s, sub, 0x80)
    elif key == 0x05:  # shakeOn
        return 0
    elif key == 0x06:  # shakeOff
        return 0
    elif key == 0x07:  # roomScale: scale1(p8), y1(p8), scale2(p8), y2(p8), slot(p8)
        # Reads: fetchScriptByte (aux), then params with flags from aux
        # Actually in ScummVM v5, case 7 reads:
        # a = getVarOrDirectByte(0x80), b = getVarOrDirectByte(0x40)
        # then fetches aux opcode, c = getVarOrDirectByte(0x80), d = getVarOrDirectByte(0x40)
        # then fetches another aux, e = getVarOrDirectByte(0x40)
        # That's 5 p8 params with 2 extra aux bytes
        total = _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
        aux1 = s.read(1)
        total += 1
        if aux1:
            total += _read_p8(s, aux1[0], 0x80) + _read_p8(s, aux1[0], 0x40)
        aux2 = s.read(1)
        total += 1
        if aux2:
            total += _read_p8(s, aux2[0], 0x80)
        return total
    elif key == 0x08:  # roomScale (simple): scale(p8), startcolor(p8), endcolor(p8)
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40) + _read_p8(s, sub, 0x20)
    elif key == 0x09:  # savegame: loadflag(p8), loadslot(p8)
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x0A:  # screenEffect: effect(p16)
        return _read_p16(s)
    elif key == 0x0B:  # rgbRoomIntensity: r(p16), g(p16), b(p16), startcolor(p8), endcolor(p8)
        total = _read_p16(s) + _read_p16(s) + _read_p16(s)
        aux = s.read(1)
        total += 1
        if aux:
            total += _read_p8(s, aux[0], 0x80) + _read_p8(s, aux[0], 0x40)
        return total
    elif key == 0x0C:  # roomShadow: r(p16), g(p16), b(p16), startcolor(p8), endcolor(p8)
        total = _read_p16(s) + _read_p16(s) + _read_p16(s)
        aux = s.read(1)
        total += 1
        if aux:
            total += _read_p8(s, aux[0], 0x80) + _read_p8(s, aux[0], 0x40)
        return total
    elif key == 0x0D:  # saveString: resID(p8) + filename(string)
        return _read_p8(s, sub, 0x80) + _read_string(s)
    elif key == 0x0E:  # loadString: resID(p8) + filename(string)
        return _read_p8(s, sub, 0x80) + _read_string(s)
    elif key == 0x0F:  # palManipulate: resID(p8), start(p8), end(p8), time(p8)
        total = _read_p8(s, sub, 0x80)
        aux = s.read(1)
        total += 1
        if aux:
            total += _read_p8(s, aux[0], 0x80) + _read_p8(s, aux[0], 0x40)
        aux2 = s.read(1)
        total += 1
        if aux2:
            total += _read_p8(s, aux2[0], 0x80)
        return total
    elif key == 0x10:  # colorCycleDelay: index(p8), delay(p8)
        return _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    else:
        return 0


def _dec_roomOps(op: int, s: BytesIO) -> int:
    """roomOps: reads sub-opcode byte, then params based on sub-op."""
    total = 0
    b = s.read(1)
    if not b:
        return 0
    total += 1
    total += _room_subop_decoder(b[0], s)
    return total


# resourceRoutines sub-opcode decoding
def _dec_resourceRoutines(op: int, s: BytesIO) -> int:
    """resourceRoutines: reads sub-opcode, then params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x3F  # mask off flag bits

    if key in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
               0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10,
               0x12, 0x13):
        # Load/nuke/lock/unlock resource: resID (p8)
        total += _read_p8(s, sub, 0x80)
    elif key == 0x11:
        # clearHeap: no params
        pass
    elif key == 0x14:
        # loadFlObject: room(p8) + object(p16)
        total += _read_p8(s, sub, 0x80) + _read_p16(s)
    elif key in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25):
        # v5 extended resource ops: resID (p8)
        total += _read_p8(s, sub, 0x80)
    # else: unknown, no params
    return total


# cursorCommand sub-opcode decoding
def _dec_cursorCommand(op: int, s: BytesIO) -> int:
    """cursorCommand: reads sub-opcode, then params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x1F

    if key in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08):
        # Simple cursor state changes: no params
        pass
    elif key == 0x0A:
        # setCursorImg: cursor(p8), char(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x0B:
        # setCursorHotspot: index(p8), x(p8), y(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40) + _read_p8(s, sub, 0x20)
    elif key == 0x0C:
        # initCursor: cursor(p8)
        total += _read_p8(s, sub, 0x80)
    elif key == 0x0D:
        # initCharset: charset(p8)
        total += _read_p8(s, sub, 0x80)
    elif key == 0x0E:
        # cursorColor: varargs (word list)
        total += _read_varargs(s)
    # else: unknown
    return total


# expression sub-opcode decoding
def _dec_expression(op: int, s: BytesIO) -> int:
    """expression: result(2) + sub-opcode sequence terminated by $FF."""
    total = _read_var(s)  # getResultPos
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0xFF:
            break
        key = b[0] & 0x1F
        if key == 0x01:
            # Push value: p16[7] (word, always 2 bytes)
            total += _read_p16(s)
        elif key in (0x02, 0x03, 0x04, 0x05):
            # add, sub, mul, div: no additional params (pop from stack)
            pass
        elif key == 0x06:
            # Nested expression — no additional immediate params
            # ScummVM just does nothing here (it's handled by the evaluator)
            pass
        # else: unknown, no params
    return total


# stringOps sub-opcode decoding
def _dec_stringOps(op: int, s: BytesIO) -> int:
    """stringOps: reads sub-opcode, then params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x1F

    if key == 0x01:
        # putCodeInString: stringID(p8) + string data (null-terminated)
        total += _read_p8(s, sub, 0x80)
        total += _read_string(s)
    elif key == 0x02:
        # copyString: dest(p8), src(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x03:
        # setStringChar: string(p8), index(p8), char(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40) + _read_p8(s, sub, 0x20)
    elif key == 0x04:
        # getStringChar: result, string(p8), index(p8)
        total += _read_var(s) + _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x05:
        # createString: stringID(p8), size(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    # else: unknown
    return total


# saveRestoreVerbs sub-opcode decoding
def _dec_saveRestoreVerbs(op: int, s: BytesIO) -> int:
    """saveRestoreVerbs: reads sub-opcode, then 3x p8 params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    # All 3 sub-ops (1=save, 2=restore, 3=delete) take 3 p8 params
    total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40) + _read_p8(s, sub, 0x20)
    return total


# wait sub-opcode decoding
def _dec_wait(op: int, s: BytesIO) -> int:
    """wait: reads sub-opcode, then conditional params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x1F

    if key == 0x01:
        # waitForActor: actor(p8) + jump offset(2)
        total += _read_p8(s, sub, 0x80) + 2
        s.read(2)
    elif key == 0x02:
        # waitForMessage: no params
        pass
    elif key == 0x03:
        # waitForCamera: no params
        pass
    elif key == 0x04:
        # waitForSentence: no params
        pass
    # else: unknown
    return total


# matrixOps sub-opcode decoding
def _dec_matrixOps(op: int, s: BytesIO) -> int:
    """matrixOps: reads sub-opcode, then params."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x1F

    if key in (0x01, 0x02, 0x03):
        # setBoxFlags / setBoxScale / setBoxSlot: box(p8), val(p8)
        total += _read_p8(s, sub, 0x80) + _read_p8(s, sub, 0x40)
    elif key == 0x04:
        # createBoxMatrix: no params
        pass
    # else: unknown
    return total


# drawObject sub-opcode
def _dec_drawObject(op: int, s: BytesIO) -> int:
    """drawObject: p16[7] + sub-opcode with params."""
    total = _read_p16(s)  # object (always 2)
    # Read sub-opcode byte — its flag bits control sub-params
    b = s.read(1)
    if not b:
        return total
    total += 1
    sub = b[0]
    key = sub & 0x1F

    if key == 0x01:
        # setXY: xpos(p16), ypos(p16)
        total += _read_p16(s) + _read_p16(s)
    elif key == 0x02:
        # setState: state(p16)
        total += _read_p16(s)
    elif key == 0x1F:
        # $FF — draw with defaults, no params
        pass
    # else: unknown
    return total


# override / beginOverride
def _dec_override(op: int, s: BytesIO) -> int:
    """override: 1 byte flag, then if flag != 0: jump offset (2 bytes)."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    if b[0] != 0x00:
        # Begin override: followed by a jump opcode (0x18) + 2-byte offset
        # The jump opcode byte IS the next byte in the stream
        s.read(1)  # the jump opcode byte (0x18)
        total += 1
        s.read(2)  # the jump offset
        total += 2
    return total


# setVarRange
def _dec_setVarRange(op: int, s: BytesIO) -> int:
    """setVarRange: result(2) + count(1) + N values (1 or 2 bytes each)."""
    total = _read_var(s)  # result pos
    count_byte = s.read(1)
    total += 1
    if not count_byte:
        return total
    count = count_byte[0]
    if op & 0x80:
        # Word values
        s.read(count * 2)
        total += count * 2
    else:
        # Byte values
        s.read(count)
        total += count
    return total


# drawBox
def _dec_drawBox(op: int, s: BytesIO) -> int:
    """drawBox: x(p16) + y(p16) + auxOpcode(1) + x2(p16) + y2(p16) + color(p8)."""
    total = _read_p16(s) + _read_p16(s)  # x, y (always 2 each)
    aux = s.read(1)                       # aux opcode byte
    total += 1
    if aux:
        total += _read_p16(s) + _read_p16(s)  # x2, y2
        total += _read_p8(s, aux[0], 0x80)     # color
    return total


# doSentence
def _dec_doSentence(op: int, s: BytesIO) -> int:
    """doSentence: p8[7] (verb) — if verb == 0xFE/0xFC, stop; else + p16[6] + p16[5]."""
    total = _read_p8(s, op, 0x80)
    # Peek at what we just read to check for special verbs
    # Actually we can't easily peek. Let's just check: if the verb byte was
    # 0xFE or 0xFC (special stop sentinel), no more params.
    # But we already consumed it. We need to check the value.
    # Rewind and re-read to check the value
    pos = s.tell()
    if op & 0x80:
        # Variable ref — we can't know the runtime value, read remaining params
        total += _read_p16(s) + _read_p16(s)
    else:
        # Byte constant — check value
        s.seek(pos - 1)
        verb_val = s.read(1)[0]
        if verb_val in (0xFE, 0xFC):
            # Special: stop sentence, no more params
            pass
        else:
            total += _read_p16(s) + _read_p16(s)
    return total


# oldRoomEffect
def _dec_oldRoomEffect(op: int, s: BytesIO) -> int:
    """oldRoomEffect (screenEffect): reads sub-opcode + optional p16."""
    b = s.read(1)
    if not b:
        return 0
    total = 1
    sub = b[0]
    key = sub & 0x1F
    if key == 0x03:
        # setScreen: reads p16
        total += _read_p16(s)
    # Other cases: no params
    return total


# pseudoRoom
def _dec_pseudoRoom(op: int, s: BytesIO) -> int:
    """pseudoRoom: reads byte pairs until byte == 0."""
    total = 0
    while True:
        b = s.read(1)
        if not b:
            break
        total += 1
        if b[0] == 0x00:
            break
        # Each iteration: res byte + room byte
        s.read(1)
        total += 1
    return total


# pickupObjectOld — same as pickupObject but different param order in some versions
def _dec_pickupObjectOld(op: int, s: BytesIO) -> int:
    """pickupObjectOld: p16[7]."""
    return _read_p16(s)


# systemOps ($98) — dummy in v5
_dec_systemOps = _dec_none

# dummy ($A7)
_dec_dummy = _dec_none


# ---------------------------------------------------------------------------
# Opcode categories for reporting
# ---------------------------------------------------------------------------

OPCODE_CATEGORIES = {
    'Flow Control': [
        'stopObjectCode', 'breakHere', 'jumpRelative', 'cutscene',
        'endCutscene', 'override', 'freezeScripts', 'stopScript',
        'stopObjectScript', 'chainScript', 'delay', 'delayVariable',
        'wait', 'stopMusic', 'beginOverride',
    ],
    'Variables & Arithmetic': [
        'move', 'add', 'subtract', 'multiply', 'divide',
        'and', 'or', 'increment', 'decrement', 'setVarRange',
        'expression',
    ],
    'Conditionals': [
        'isEqual', 'isNotEqual', 'isGreater', 'isGreaterEqual',
        'isLess', 'isLessEqual', 'equalZero', 'notEqualZero',
        'ifClassOfIs', 'isActorInBox', 'ifState', 'ifNotState',
        'getObjectState', 'isScriptRunning', 'isSoundRunning',
    ],
    'Actor': [
        'putActor', 'putActorAtObject', 'putActorInRoom',
        'walkActorTo', 'walkActorToActor', 'walkActorToObject',
        'faceActor', 'animateActor', 'actorOps', 'actorFollowCamera',
        'actorFromPos', 'getActorRoom', 'getActorX', 'getActorY',
        'getActorElevation', 'getActorCostume', 'getActorFacing',
        'getActorMoving', 'getActorScale', 'getActorWalkBox',
        'getActorWidth', 'getAnimCounter', 'getClosestObjActor',
    ],
    'Object & Inventory': [
        'drawObject', 'setState', 'setObjectName', 'setClass',
        'setOwnerOf', 'getObjectOwner', 'pickupObject', 'pickupObjectOld',
        'findObject', 'findInventory', 'getInventoryCount',
        'getVerbEntrypoint',
    ],
    'Room & Camera': [
        'loadRoom', 'loadRoomWithEgo', 'roomOps', 'panCameraTo',
        'setCameraAt', 'getDist', 'lights', 'oldRoomEffect',
        'matrixOps', 'pseudoRoom',
    ],
    'Script Execution': [
        'startScript', 'startObject', 'doSentence',
        'getScriptRunning',
    ],
    'Sound': [
        'startMusic', 'startSound', 'stopSound', 'soundKludge',
    ],
    'Verb & UI': [
        'verbOps', 'cursorCommand', 'saveRestoreVerbs',
    ],
    'String & Print': [
        'print', 'printEgo', 'stringOps', 'getStringWidth',
    ],
    'Resource': [
        'resourceRoutines',
    ],
    'Misc': [
        'debug', 'drawBox', 'dummy', 'systemOps',
    ],
}


# ---------------------------------------------------------------------------
# Master opcode table: maps base name → decoder function
# ---------------------------------------------------------------------------

BASE_OPCODES: Dict[str, Callable] = {
    'stopObjectCode':       _dec_none,
    'putActor':             _dec_putActor,
    'startMusic':           _dec_p8_7,
    'getActorRoom':         _dec_result_p8_7,
    'isGreaterEqual':       _dec_cond_jump,
    'drawObject':           _dec_drawObject,
    'getActorElevation':    _dec_result_p8_7,
    'setState':             _dec_setState,
    'isNotEqual':           _dec_cond_jump,
    'faceActor':            _dec_faceActor,
    'startScript':          _dec_startScript,
    'getVerbEntrypoint':    _dec_getVerbEntrypoint,
    'resourceRoutines':     _dec_resourceRoutines,
    'walkActorToActor':     _dec_walkActorToActor,
    'putActorAtObject':     _dec_putActorAtObject,
    'getObjectState':       _dec_result_p16,
    'getObjectOwner':       _dec_result_p16,
    'animateActor':         _dec_animateActor,
    'panCameraTo':          _dec_p16_7,
    'actorOps':             _dec_actorOps,
    'print':                _dec_print,
    'actorFromPos':         _dec_actorFromPos,
    'getRandomNr':          _dec_result_p8_7,
    'and':                  _dec_arith,
    'jumpRelative':         _dec_jump,
    'doSentence':           _dec_doSentence,
    'move':                 _dec_arith,
    'multiply':             _dec_arith,
    'startSound':           _dec_p8_7,
    'ifClassOfIs':          _dec_ifClassOfIs,
    'walkActorTo':          _dec_walkActorTo,
    'isActorInBox':         _dec_isActorInBox,
    'stopMusic':            _dec_none,
    'getAnimCounter':       _dec_getAnimCounter,
    'getActorY':            _dec_result_p16_7,
    'loadRoomWithEgo':      _dec_loadRoomWithEgo,
    'pickupObject':         _dec_pickupObject,
    'setVarRange':          _dec_setVarRange,
    'stringOps':            _dec_stringOps,
    'equalZero':            _dec_zero_jump,
    'setOwnerOf':           _dec_setOwnerOf,
    'delayVariable':        _dec_delay_variable,
    'saveRestoreVerbs':     _dec_saveRestoreVerbs,
    'cursorCommand':        _dec_cursorCommand,
    'putActorInRoom':       _dec_putActorInRoom,
    'delay':                _dec_delay,
    'ifNotState':           _dec_ifState,
    'matrixOps':            _dec_matrixOps,
    'getInventoryCount':    _dec_result_p8_7,
    'setCameraAt':          _dec_p16_7,
    'roomOps':              _dec_roomOps,
    'getDist':              _dec_getDist,
    'findObject':           _dec_findObject,
    'walkActorToObject':    _dec_walkActorToObject,
    'startObject':          _dec_startObject,
    'isLessEqual':          _dec_cond_jump,
    'subtract':             _dec_arith,
    'getActorScale':        _dec_result_p8_7,
    'stopSound':            _dec_p8_7,
    'findInventory':        _dec_findInventory,
    'drawBox':              _dec_drawBox,
    'cutscene':             _dec_cutscene,
    'chainScript':          _dec_chainScript,
    'getActorX':            _dec_result_p16_7,
    'isLess':               _dec_cond_jump,
    'increment':            _dec_inc_dec,
    'isEqual':              _dec_cond_jump,
    'soundKludge':          _dec_soundKludge,
    'pickupObjectOld':      _dec_pickupObjectOld,
    'animateActor':         _dec_animateActor,
    'actorFollowCamera':    _dec_actorFollowCamera,
    'setObjectName':        _dec_setObjectName,
    'getActorMoving':       _dec_result_p8_7,
    'or':                   _dec_arith,
    'override':             _dec_override,
    'add':                  _dec_arith,
    'divide':               _dec_arith,
    'oldRoomEffect':        _dec_oldRoomEffect,
    'setClass':             _dec_setClass,
    'freezeScripts':        _dec_p8_7,
    'stopScript':           _dec_p8_7,
    'getActorFacing':       _dec_result_p8_7,
    'getClosestObjActor':   _dec_result_p16,
    'getStringWidth':       _dec_result_p8_7,
    'getScriptRunning':     _dec_result_p8_7,
    'debug':                _dec_debug,
    'getActorWidth':        _dec_result_p8_7,
    'stopObjectScript':     _dec_stopObjScript,
    'lights':               _dec_lights,
    'getActorCostume':      _dec_result_p8_7,
    'loadRoom':             _dec_p8_7,
    'isGreater':            _dec_cond_jump,
    'verbOps':              _dec_verbOps,
    'getActorWalkBox':      _dec_result_p8_7,
    'isSoundRunning':       _dec_result_p8_7,
    'breakHere':            _dec_none,
    'endCutscene':          _dec_none,
    'notEqualZero':         _dec_zero_jump,
    'expression':           _dec_expression,
    'wait':                 _dec_wait,
    'decrement':            _dec_inc_dec,
    'pseudoRoom':           _dec_pseudoRoom,
    'printEgo':             _dec_printEgo,
    'dummy':                _dec_dummy,
    'systemOps':            _dec_systemOps,
    'ifState':              _dec_ifState,
    'isScriptRunning':      _dec_result_p8_7,
}


# ---------------------------------------------------------------------------
# 256-entry byte → base opcode name mapping
# ---------------------------------------------------------------------------

def _build_opcode_map() -> List[Optional[str]]:
    """Build the 256-entry opcode lookup table."""
    table: List[Optional[str]] = [None] * 256

    def _set(positions, name):
        for p in positions:
            if table[p] is not None:
                raise ValueError(f"Conflict at ${p:02X}: existing={table[p]}, new={name}")
            table[p] = name

    # --- Mapping all 256 entries ---
    # Format: byte_value → opcode_name

    # $00
    table[0x00] = 'stopObjectCode'
    table[0x01] = 'putActor'
    table[0x02] = 'startMusic'
    table[0x03] = 'getActorRoom'
    table[0x04] = 'isGreaterEqual'
    table[0x05] = 'drawObject'
    table[0x06] = 'getActorElevation'
    table[0x07] = 'setState'
    table[0x08] = 'isNotEqual'
    table[0x09] = 'faceActor'
    table[0x0A] = 'startScript'
    table[0x0B] = 'getVerbEntrypoint'
    table[0x0C] = 'resourceRoutines'
    table[0x0D] = 'walkActorToActor'
    table[0x0E] = 'putActorAtObject'
    table[0x0F] = 'getObjectState'

    # $10
    table[0x10] = 'getObjectOwner'
    table[0x11] = 'animateActor'
    table[0x12] = 'panCameraTo'
    table[0x13] = 'actorOps'
    table[0x14] = 'print'
    table[0x15] = 'actorFromPos'
    table[0x16] = 'getRandomNr'
    table[0x17] = 'and'
    table[0x18] = 'jumpRelative'
    table[0x19] = 'doSentence'
    table[0x1A] = 'move'
    table[0x1B] = 'multiply'
    table[0x1C] = 'startSound'
    table[0x1D] = 'ifClassOfIs'
    table[0x1E] = 'walkActorTo'
    table[0x1F] = 'isActorInBox'

    # $20
    table[0x20] = 'stopMusic'
    table[0x21] = 'putActor'
    table[0x22] = 'getAnimCounter'
    table[0x23] = 'getActorY'
    table[0x24] = 'loadRoomWithEgo'
    table[0x25] = 'pickupObject'
    table[0x26] = 'setVarRange'
    table[0x27] = 'stringOps'
    table[0x28] = 'equalZero'
    table[0x29] = 'setOwnerOf'
    table[0x2A] = 'startScript'
    table[0x2B] = 'delayVariable'
    table[0x2C] = 'cursorCommand'
    table[0x2D] = 'putActorInRoom'
    table[0x2E] = 'delay'
    table[0x2F] = 'ifNotState'

    # $30
    table[0x30] = 'matrixOps'
    table[0x31] = 'getInventoryCount'
    table[0x32] = 'setCameraAt'
    table[0x33] = 'roomOps'
    table[0x34] = 'getDist'
    table[0x35] = 'findObject'
    table[0x36] = 'walkActorToObject'
    table[0x37] = 'startObject'
    table[0x38] = 'isLessEqual'
    table[0x39] = 'doSentence'
    table[0x3A] = 'subtract'
    table[0x3B] = 'getActorScale'
    table[0x3C] = 'stopSound'
    table[0x3D] = 'findInventory'
    table[0x3E] = 'walkActorTo'
    table[0x3F] = 'drawBox'

    # $40
    table[0x40] = 'cutscene'
    table[0x41] = 'putActor'
    table[0x42] = 'chainScript'
    table[0x43] = 'getActorX'
    table[0x44] = 'isLess'
    table[0x45] = 'drawObject'
    table[0x46] = 'increment'
    table[0x47] = 'setState'
    table[0x48] = 'isEqual'
    table[0x49] = 'faceActor'
    table[0x4A] = 'startScript'
    table[0x4B] = 'getVerbEntrypoint'
    table[0x4C] = 'soundKludge'
    table[0x4D] = 'walkActorToActor'
    table[0x4E] = 'putActorAtObject'
    table[0x4F] = 'ifState'

    # $50
    table[0x50] = 'pickupObjectOld'
    table[0x51] = 'animateActor'
    table[0x52] = 'actorFollowCamera'
    table[0x53] = 'actorOps'
    table[0x54] = 'setObjectName'
    table[0x55] = 'actorFromPos'
    table[0x56] = 'getActorMoving'
    table[0x57] = 'or'
    table[0x58] = 'override'
    table[0x59] = 'doSentence'
    table[0x5A] = 'add'
    table[0x5B] = 'divide'
    table[0x5C] = 'oldRoomEffect'
    table[0x5D] = 'setClass'
    table[0x5E] = 'walkActorTo'
    table[0x5F] = 'isActorInBox'

    # $60
    table[0x60] = 'freezeScripts'
    table[0x61] = 'putActor'
    table[0x62] = 'stopScript'
    table[0x63] = 'getActorFacing'
    table[0x64] = 'loadRoomWithEgo'
    table[0x65] = 'pickupObject'
    table[0x66] = 'getClosestObjActor'
    table[0x67] = 'getStringWidth'
    table[0x68] = 'isScriptRunning'
    table[0x69] = 'setOwnerOf'
    table[0x6A] = 'startScript'
    table[0x6B] = 'debug'
    table[0x6C] = 'getActorWidth'
    table[0x6D] = 'putActorInRoom'
    table[0x6E] = 'stopObjectScript'
    table[0x6F] = 'ifNotState'

    # $70
    table[0x70] = 'lights'
    table[0x71] = 'getActorCostume'
    table[0x72] = 'loadRoom'
    table[0x73] = 'roomOps'
    table[0x74] = 'getDist'
    table[0x75] = 'findObject'
    table[0x76] = 'walkActorToObject'
    table[0x77] = 'startObject'
    table[0x78] = 'isGreater'
    table[0x79] = 'doSentence'
    table[0x7A] = 'verbOps'
    table[0x7B] = 'getActorWalkBox'
    table[0x7C] = 'isSoundRunning'
    table[0x7D] = 'findInventory'
    table[0x7E] = 'walkActorTo'
    table[0x7F] = 'drawBox'

    # $80
    table[0x80] = 'breakHere'
    table[0x81] = 'putActor'
    table[0x82] = 'startMusic'
    table[0x83] = 'getActorRoom'
    table[0x84] = 'isGreaterEqual'
    table[0x85] = 'drawObject'
    table[0x86] = 'getActorElevation'
    table[0x87] = 'setState'
    table[0x88] = 'isNotEqual'
    table[0x89] = 'faceActor'
    table[0x8A] = 'startScript'
    table[0x8B] = 'getVerbEntrypoint'
    table[0x8C] = 'resourceRoutines'
    table[0x8D] = 'walkActorToActor'
    table[0x8E] = 'putActorAtObject'
    table[0x8F] = 'getObjectState'

    # $90
    table[0x90] = 'getObjectOwner'
    table[0x91] = 'animateActor'
    table[0x92] = 'panCameraTo'
    table[0x93] = 'actorOps'
    table[0x94] = 'print'
    table[0x95] = 'actorFromPos'
    table[0x96] = 'getRandomNr'
    table[0x97] = 'and'
    table[0x98] = 'systemOps'
    table[0x99] = 'doSentence'
    table[0x9A] = 'move'
    table[0x9B] = 'multiply'
    table[0x9C] = 'startSound'
    table[0x9D] = 'ifClassOfIs'
    table[0x9E] = 'walkActorTo'
    table[0x9F] = 'isActorInBox'

    # $A0
    table[0xA0] = 'stopObjectCode'
    table[0xA1] = 'putActor'
    table[0xA2] = 'getAnimCounter'
    table[0xA3] = 'getActorY'
    table[0xA4] = 'loadRoomWithEgo'
    table[0xA5] = 'pickupObject'
    table[0xA6] = 'setVarRange'
    table[0xA7] = 'dummy'
    table[0xA8] = 'notEqualZero'
    table[0xA9] = 'setOwnerOf'
    table[0xAA] = 'startScript'
    table[0xAB] = 'saveRestoreVerbs'
    table[0xAC] = 'expression'
    table[0xAD] = 'putActorInRoom'
    table[0xAE] = 'wait'
    table[0xAF] = 'ifNotState'

    # $B0
    table[0xB0] = 'matrixOps'
    table[0xB1] = 'getInventoryCount'
    table[0xB2] = 'setCameraAt'
    table[0xB3] = 'roomOps'
    table[0xB4] = 'getDist'
    table[0xB5] = 'findObject'
    table[0xB6] = 'walkActorToObject'
    table[0xB7] = 'startObject'
    table[0xB8] = 'isLessEqual'
    table[0xB9] = 'doSentence'
    table[0xBA] = 'subtract'
    table[0xBB] = 'getActorScale'
    table[0xBC] = 'stopSound'
    table[0xBD] = 'findInventory'
    table[0xBE] = 'walkActorTo'
    table[0xBF] = 'drawBox'

    # $C0
    table[0xC0] = 'endCutscene'
    table[0xC1] = 'putActor'
    table[0xC2] = 'chainScript'
    table[0xC3] = 'getActorX'
    table[0xC4] = 'isLess'
    table[0xC5] = 'drawObject'
    table[0xC6] = 'decrement'
    table[0xC7] = 'setState'
    table[0xC8] = 'isEqual'
    table[0xC9] = 'faceActor'
    table[0xCA] = 'startScript'
    table[0xCB] = 'getVerbEntrypoint'
    table[0xCC] = 'pseudoRoom'
    table[0xCD] = 'walkActorToActor'
    table[0xCE] = 'putActorAtObject'
    table[0xCF] = 'ifState'

    # $D0
    table[0xD0] = 'pickupObjectOld'
    table[0xD1] = 'animateActor'
    table[0xD2] = 'actorFollowCamera'
    table[0xD3] = 'actorOps'
    table[0xD4] = 'setObjectName'
    table[0xD5] = 'actorFromPos'
    table[0xD6] = 'getActorMoving'
    table[0xD7] = 'or'
    table[0xD8] = 'printEgo'
    table[0xD9] = 'doSentence'
    table[0xDA] = 'add'
    table[0xDB] = 'divide'
    table[0xDC] = 'oldRoomEffect'
    table[0xDD] = 'setClass'
    table[0xDE] = 'walkActorTo'
    table[0xDF] = 'isActorInBox'

    # $E0
    table[0xE0] = 'freezeScripts'
    table[0xE1] = 'putActor'
    table[0xE2] = 'stopScript'
    table[0xE3] = 'getActorFacing'
    table[0xE4] = 'loadRoomWithEgo'
    table[0xE5] = 'pickupObject'
    table[0xE6] = 'getClosestObjActor'
    table[0xE7] = 'getStringWidth'
    table[0xE8] = 'isScriptRunning'
    table[0xE9] = 'setOwnerOf'
    table[0xEA] = 'startScript'
    table[0xEB] = 'debug'
    table[0xEC] = 'getActorWidth'
    table[0xED] = 'putActorInRoom'
    table[0xEE] = 'stopObjectScript'
    table[0xEF] = 'ifNotState'

    # $F0
    table[0xF0] = 'lights'
    table[0xF1] = 'getActorCostume'
    table[0xF2] = 'loadRoom'
    table[0xF3] = 'roomOps'
    table[0xF4] = 'getDist'
    table[0xF5] = 'findObject'
    table[0xF6] = 'walkActorToObject'
    table[0xF7] = 'startObject'
    table[0xF8] = 'isGreater'
    table[0xF9] = 'doSentence'
    table[0xFA] = 'verbOps'
    table[0xFB] = 'getActorWalkBox'
    table[0xFC] = 'isSoundRunning'
    table[0xFD] = 'findInventory'
    table[0xFE] = 'walkActorTo'
    table[0xFF] = 'drawBox'

    return table


# Build once at import time
OPCODE_MAP: List[Optional[str]] = _build_opcode_map()

# Verify completeness
assert all(name is not None for name in OPCODE_MAP), \
    "Opcode table has gaps: " + str([i for i, n in enumerate(OPCODE_MAP) if n is None])

# Count unique base opcodes
UNIQUE_OPCODES = sorted(set(OPCODE_MAP))
NUM_BASE_OPCODES = len(UNIQUE_OPCODES)


def get_decoder(name: str) -> Callable:
    """Get the parameter decoder function for a base opcode name."""
    return BASE_OPCODES[name]


def decode_opcode(opcode_byte: int, stream: BytesIO) -> Tuple[str, int]:
    """Decode one opcode from stream. Returns (base_name, param_bytes_consumed).

    The stream should be positioned AFTER the opcode byte.
    """
    name = OPCODE_MAP[opcode_byte]
    decoder = BASE_OPCODES[name]
    consumed = decoder(opcode_byte, stream)
    return name, consumed
