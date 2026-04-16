#!/usr/bin/env python3
"""SCUMM v5 bytecode disassembler.

Targets MI1 (Monkey Island 1) CD v5 scripts. Every opcode consumes the EXACT
right number of bytes so the PC stays in sync across the entire script.

Usage: python3 descumm5.py <script.bin> [start_offset] [end_offset]

Byte-consumption primitives (matching ScummVM exactly):
  fetchScriptByte()        -> 1 byte
  fetchScriptWord()        -> 2 bytes (little-endian)
  getVar()                 -> fetchScriptWord() = 2 bytes
  getResultPos()           -> fetchScriptWord(), then maybe +fetchScriptWord() if bit 0x2000 set
  getVarOrDirectByte(mask) -> if (opcode & mask): getVar() [2]; else fetchScriptByte() [1]
  getVarOrDirectWord(mask) -> if (opcode & mask): getVar() [2]; else fetchScriptWord() [2]
  getWordVararg()          -> loop: fetchScriptByte(); if 0xFF break; else getVarOrDirectWord(0x80)
  jumpRelative()           -> fetchScriptWord() [2]

PARAM_1 = 0x80, PARAM_2 = 0x40, PARAM_3 = 0x20
"""
import struct, sys

# ---------------------------------------------------------------------------
# Byte-stream reader
# ---------------------------------------------------------------------------
class ScriptReader:
    def __init__(self, data):
        self.d = data
        self.pc = 0
        self.op = 0  # current main opcode byte

    def eof(self):
        return self.pc >= len(self.d)

    def u8(self):
        v = self.d[self.pc]; self.pc += 1; return v

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.pc)[0]; self.pc += 2; return v

    def s16(self):
        v = struct.unpack_from('<h', self.d, self.pc)[0]; self.pc += 2; return v

    # --- SCUMM primitives ---------------------------------------------------

    def var_name(self, idx):
        """Human-readable variable name from a 16-bit index word."""
        if idx & 0x8000:
            return f"Bit[{idx & 0x7FFF}]"
        if idx & 0x4000:
            return f"Local[{idx & 0xFFF}]"
        if idx & 0x2000:
            return f"Var[0x{idx:04X}]"  # indirect -- should not appear standalone
        return f"Var[{idx}]"

    def get_var(self):
        """getVar() -> readVar(fetchScriptWord()).
        fetchScriptWord() reads 2 bytes. Then readVar() checks if 0x2000 is set
        and if so reads ANOTHER 2 bytes from the script (indirect indexing).
        Total: 2 bytes, or 4 bytes if indirect.
        """
        w = self.u16()
        if w & 0x2000:
            a = self.u16()
            base = w & ~0x2000
            if a & 0x2000:
                return f"{self.var_name(base)}[{self.var_name(a & ~0x2000)}]"
            else:
                return f"{self.var_name(base)}[{a & 0xFFF}]"
        return self.var_name(w)

    def get_result_pos(self):
        """getResultPos(): reads 2 bytes, optionally +2 more if indirect."""
        w = self.u16()
        if w & 0x2000:
            a = self.u16()
            # Format the indirection for display
            base = w & ~0x2000
            if a & 0x2000:
                return f"{self.var_name(base)}[{self.var_name(a & ~0x2000)}]"
            else:
                return f"{self.var_name(base)}[{a & 0xFFF}]"
        return self.var_name(w)

    def get_vob(self, mask):
        """getVarOrDirectByte(mask): 2 bytes if var, 1 byte if literal."""
        if self.op & mask:
            return self.get_var()
        return f"#{self.u8()}"

    def get_vow(self, mask):
        """getVarOrDirectWord(mask): 2 bytes if var, 2 bytes if literal."""
        if self.op & mask:
            return self.get_var()
        v = self.s16()
        return f"#{v}"

    def get_vararg(self):
        """getWordVararg(): reads bytes until 0xFF, each followed by getVarOrDirectWord(0x80).
        NOTE: the opcode used for 0x80 check is the MAIN opcode, but for vararg it reads
        a sub-byte which becomes _opcode. So each iteration: read byte (if 0xFF stop),
        that byte becomes _opcode, then getVarOrDirectWord(PARAM_1=0x80).
        """
        args = []
        while not self.eof():
            sub = self.u8()
            if sub == 0xFF:
                break
            saved = self.op
            self.op = sub
            args.append(self.get_vow(0x80))
            self.op = saved
        return args

    def jump_target(self):
        """jumpRelative(): read signed 16-bit offset, return absolute target."""
        off = self.s16()
        return self.pc + off

    def get_string_text(self):
        """Read SCUMM text from script: bytes until 0x00, with 0xFF escape sequences.
        Inside text, 0xFF is followed by a control byte:
          - codes 1, 2, 3, 8: NO additional bytes after the control byte
          - ALL OTHER codes: +2 more bytes (a word argument)
        This matches resStrLen() in ScummVM exactly.
        """
        parts = []
        while not self.eof():
            b = self.u8()
            if b == 0x00:
                break
            elif b == 0xFF:
                if self.eof():
                    break
                esc = self.u8()
                if esc in (1, 2, 3, 8):
                    # These escape codes have NO extra bytes
                    ESC_NAMES = {1: "\\n", 2: "\\k", 3: "\\w", 8: "\\v"}
                    parts.append(ESC_NAMES.get(esc, f"\\x{esc:02X}"))
                else:
                    # All other escape codes have 2 extra bytes (a word)
                    val = self.u16()
                    ESC_NAMES = {
                        4: "\\int", 5: "\\verb", 6: "\\name", 7: "\\str",
                        9: "\\start-anim", 0x0A: "\\sound",
                    }
                    name = ESC_NAMES.get(esc, f"\\x{esc:02X}")
                    parts.append(f"{name}({val})")
            elif b >= 0x20 and b < 0x80:
                parts.append(chr(b))
            elif b == 0x0A:
                parts.append("\\n")
            else:
                parts.append(f"[{b:02X}]")
        return ''.join(parts)

    def skip_string_text(self):
        """Skip a SCUMM string (text bytes until 0x00, with 0xFF escapes).
        Used by loadPtrToResource(NULL) and setObjectName.
        Matches resStrLen(): codes 1,2,3,8 have no extra bytes;
        all other 0xFF codes have 2 extra bytes."""
        while not self.eof():
            b = self.u8()
            if b == 0x00:
                break
            elif b == 0xFF:
                if self.eof():
                    break
                esc = self.u8()
                if esc in (1, 2, 3, 8):
                    pass  # no extra bytes
                else:
                    self.u16()  # 2 extra bytes for all other escapes


# ---------------------------------------------------------------------------
# The 256-entry opcode table  (SCUMM v5, from ScummEngine_v5::setupOpcodes)
# ---------------------------------------------------------------------------
# Map every opcode 0x00-0xFF to a handler name string.
# This is transcribed directly from script_v5.cpp lines 40-360.

OPCODE_TABLE = [None] * 256

def _set(code, name):
    OPCODE_TABLE[code] = name

# Row 0x00
_set(0x00, "stopObjectCode"); _set(0x01, "putActor"); _set(0x02, "startMusic"); _set(0x03, "getActorRoom")
_set(0x04, "isGreaterEqual"); _set(0x05, "drawObject"); _set(0x06, "getActorElevation"); _set(0x07, "setState")
_set(0x08, "isNotEqual"); _set(0x09, "faceActor"); _set(0x0a, "startScript"); _set(0x0b, "getVerbEntrypoint")
_set(0x0c, "resourceRoutines"); _set(0x0d, "walkActorToActor"); _set(0x0e, "putActorAtObject"); _set(0x0f, "getObjectState")
# Row 0x10
_set(0x10, "getObjectOwner"); _set(0x11, "animateActor"); _set(0x12, "panCameraTo"); _set(0x13, "actorOps")
_set(0x14, "print"); _set(0x15, "actorFromPos"); _set(0x16, "getRandomNr"); _set(0x17, "and")
_set(0x18, "jumpRelative"); _set(0x19, "doSentence"); _set(0x1a, "move"); _set(0x1b, "multiply")
_set(0x1c, "startSound"); _set(0x1d, "ifClassOfIs"); _set(0x1e, "walkActorTo"); _set(0x1f, "isActorInBox")
# Row 0x20
_set(0x20, "stopMusic"); _set(0x21, "putActor"); _set(0x22, "getAnimCounter"); _set(0x23, "getActorY")
_set(0x24, "loadRoomWithEgo"); _set(0x25, "pickupObject"); _set(0x26, "setVarRange"); _set(0x27, "stringOps")
_set(0x28, "equalZero"); _set(0x29, "setOwnerOf"); _set(0x2a, "startScript"); _set(0x2b, "delayVariable")
_set(0x2c, "cursorCommand"); _set(0x2d, "putActorInRoom"); _set(0x2e, "delay")
# 0x2f is unused (ifNotState commented out)
# Row 0x30
_set(0x30, "matrixOps"); _set(0x31, "getInventoryCount"); _set(0x32, "setCameraAt"); _set(0x33, "roomOps")
_set(0x34, "getDist"); _set(0x35, "findObject"); _set(0x36, "walkActorToObject"); _set(0x37, "startObject")
_set(0x38, "isLessEqual"); _set(0x39, "doSentence"); _set(0x3a, "subtract"); _set(0x3b, "getActorScale")
_set(0x3c, "stopSound"); _set(0x3d, "findInventory"); _set(0x3e, "walkActorTo"); _set(0x3f, "drawBox")
# Row 0x40
_set(0x40, "cutscene"); _set(0x41, "putActor"); _set(0x42, "chainScript"); _set(0x43, "getActorX")
_set(0x44, "isLess")
# 0x45 unused (drawObject commented out)
_set(0x46, "increment"); _set(0x47, "setState")
_set(0x48, "isEqual"); _set(0x49, "faceActor"); _set(0x4a, "startScript"); _set(0x4b, "getVerbEntrypoint")
_set(0x4c, "soundKludge"); _set(0x4d, "walkActorToActor"); _set(0x4e, "putActorAtObject")
# 0x4f unused
# Row 0x50
# 0x50 unused (pickupObjectOld)
_set(0x51, "animateActor"); _set(0x52, "actorFollowCamera"); _set(0x53, "actorOps")
_set(0x54, "setObjectName"); _set(0x55, "actorFromPos"); _set(0x56, "getActorMoving"); _set(0x57, "or")
_set(0x58, "beginOverride"); _set(0x59, "doSentence"); _set(0x5a, "add"); _set(0x5b, "divide")
# 0x5c unused
_set(0x5d, "setClass"); _set(0x5e, "walkActorTo"); _set(0x5f, "isActorInBox")
# Row 0x60
_set(0x60, "freezeScripts"); _set(0x61, "putActor"); _set(0x62, "stopScript"); _set(0x63, "getActorFacing")
_set(0x64, "loadRoomWithEgo"); _set(0x65, "pickupObject"); _set(0x66, "getClosestObjActor"); _set(0x67, "getStringWidth")
_set(0x68, "isScriptRunning"); _set(0x69, "setOwnerOf"); _set(0x6a, "startScript"); _set(0x6b, "debug")
_set(0x6c, "getActorWidth"); _set(0x6d, "putActorInRoom"); _set(0x6e, "stopObjectScript")
# 0x6f unused
# Row 0x70
_set(0x70, "lights"); _set(0x71, "getActorCostume"); _set(0x72, "loadRoom"); _set(0x73, "roomOps")
_set(0x74, "getDist"); _set(0x75, "findObject"); _set(0x76, "walkActorToObject"); _set(0x77, "startObject")
_set(0x78, "isGreater"); _set(0x79, "doSentence"); _set(0x7a, "verbOps"); _set(0x7b, "getActorWalkBox")
_set(0x7c, "isSoundRunning"); _set(0x7d, "findInventory"); _set(0x7e, "walkActorTo"); _set(0x7f, "drawBox")
# Row 0x80
_set(0x80, "breakHere"); _set(0x81, "putActor"); _set(0x82, "startMusic"); _set(0x83, "getActorRoom")
_set(0x84, "isGreaterEqual"); _set(0x85, "drawObject"); _set(0x86, "getActorElevation"); _set(0x87, "setState")
_set(0x88, "isNotEqual"); _set(0x89, "faceActor"); _set(0x8a, "startScript"); _set(0x8b, "getVerbEntrypoint")
_set(0x8c, "resourceRoutines"); _set(0x8d, "walkActorToActor"); _set(0x8e, "putActorAtObject"); _set(0x8f, "getObjectState")
# Row 0x90
_set(0x90, "getObjectOwner"); _set(0x91, "animateActor"); _set(0x92, "panCameraTo"); _set(0x93, "actorOps")
_set(0x94, "print"); _set(0x95, "actorFromPos"); _set(0x96, "getRandomNr"); _set(0x97, "and")
_set(0x98, "systemOps"); _set(0x99, "doSentence"); _set(0x9a, "move"); _set(0x9b, "multiply")
_set(0x9c, "startSound"); _set(0x9d, "ifClassOfIs"); _set(0x9e, "walkActorTo"); _set(0x9f, "isActorInBox")
# Row 0xA0
_set(0xa0, "stopObjectCode"); _set(0xa1, "putActor"); _set(0xa2, "getAnimCounter"); _set(0xa3, "getActorY")
_set(0xa4, "loadRoomWithEgo"); _set(0xa5, "pickupObject"); _set(0xa6, "setVarRange"); _set(0xa7, "dummy")
_set(0xa8, "notEqualZero"); _set(0xa9, "setOwnerOf"); _set(0xaa, "startScript"); _set(0xab, "saveRestoreVerbs")
_set(0xac, "expression"); _set(0xad, "putActorInRoom"); _set(0xae, "wait")
# 0xaf unused
# Row 0xB0
_set(0xb0, "matrixOps"); _set(0xb1, "getInventoryCount"); _set(0xb2, "setCameraAt"); _set(0xb3, "roomOps")
_set(0xb4, "getDist"); _set(0xb5, "findObject"); _set(0xb6, "walkActorToObject"); _set(0xb7, "startObject")
_set(0xb8, "isLessEqual"); _set(0xb9, "doSentence"); _set(0xba, "subtract"); _set(0xbb, "getActorScale")
_set(0xbc, "stopSound"); _set(0xbd, "findInventory"); _set(0xbe, "walkActorTo"); _set(0xbf, "drawBox")
# Row 0xC0
_set(0xc0, "endCutscene"); _set(0xc1, "putActor"); _set(0xc2, "chainScript"); _set(0xc3, "getActorX")
_set(0xc4, "isLess")
# 0xc5 unused
_set(0xc6, "decrement"); _set(0xc7, "setState")
_set(0xc8, "isEqual"); _set(0xc9, "faceActor"); _set(0xca, "startScript"); _set(0xcb, "getVerbEntrypoint")
_set(0xcc, "pseudoRoom"); _set(0xcd, "walkActorToActor"); _set(0xce, "putActorAtObject")
# 0xcf unused
# Row 0xD0
# 0xd0 unused
_set(0xd1, "animateActor"); _set(0xd2, "actorFollowCamera"); _set(0xd3, "actorOps")
_set(0xd4, "setObjectName"); _set(0xd5, "actorFromPos"); _set(0xd6, "getActorMoving"); _set(0xd7, "or")
_set(0xd8, "printEgo"); _set(0xd9, "doSentence"); _set(0xda, "add"); _set(0xdb, "divide")
# 0xdc unused
_set(0xdd, "setClass"); _set(0xde, "walkActorTo"); _set(0xdf, "isActorInBox")
# Row 0xE0
_set(0xe0, "freezeScripts"); _set(0xe1, "putActor"); _set(0xe2, "stopScript"); _set(0xe3, "getActorFacing")
_set(0xe4, "loadRoomWithEgo"); _set(0xe5, "pickupObject"); _set(0xe6, "getClosestObjActor"); _set(0xe7, "getStringWidth")
_set(0xe8, "isScriptRunning"); _set(0xe9, "setOwnerOf"); _set(0xea, "startScript"); _set(0xeb, "debug")
_set(0xec, "getActorWidth"); _set(0xed, "putActorInRoom"); _set(0xee, "stopObjectScript")
# 0xef unused
# Row 0xF0
_set(0xf0, "lights"); _set(0xf1, "getActorCostume"); _set(0xf2, "loadRoom"); _set(0xf3, "roomOps")
_set(0xf4, "getDist"); _set(0xf5, "findObject"); _set(0xf6, "walkActorToObject"); _set(0xf7, "startObject")
_set(0xf8, "isGreater"); _set(0xf9, "doSentence"); _set(0xfa, "verbOps"); _set(0xfb, "getActorWalkBox")
_set(0xfc, "isSoundRunning"); _set(0xfd, "findInventory"); _set(0xfe, "walkActorTo"); _set(0xff, "drawBox")


# ---------------------------------------------------------------------------
# Opcode decoder functions
# Each function takes (r: ScriptReader) and returns a display string.
# The main opcode byte is already consumed and stored in r.op.
# ---------------------------------------------------------------------------

def _P1(): return 0x80
def _P2(): return 0x40
def _P3(): return 0x20

# -- Simple opcodes (no parameters) -----------------------------------------

def op_stopObjectCode(r):
    return "stopObjectCode"

def op_breakHere(r):
    return "breakHere"

def op_stopMusic(r):
    return "stopMusic"

def op_endCutscene(r):
    return "endCutscene"

def op_dummy(r):
    return "dummy"

# -- putActor: vob(P1), vow(P2), vow(P3) -----------------------------------

def op_putActor(r):
    act = r.get_vob(_P1())
    x = r.get_vow(_P2())
    y = r.get_vow(_P3())
    return f"putActor({act}, {x}, {y})"

# -- startMusic: vob(P1) for v5 (non-FMTOWNS) = just startSound basically ---

def op_startMusic(r):
    s = r.get_vob(_P1())
    return f"startMusic({s})"

# -- getActorRoom: getResultPos(), vob(P1) ----------------------------------

def op_getActorRoom(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorRoom({act})"

# -- isGreaterEqual: getVar(), vow(P1), jumpRelative -----------------------

def op_isGreaterEqual(r):
    a = r.get_var()
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({a} >= {b}) goto [{t:04X}]"

# -- drawObject: vow(P1), then sub-opcode -----------------------------------

def op_drawObject(r):
    obj = r.get_vow(_P1())
    # v5 (non-small-header): reads a sub-opcode byte
    sub = r.u8()
    r.op = sub  # sub-opcode byte becomes _opcode for param reads
    sub_id = sub & 0x1F
    if sub_id == 1:  # draw at
        x = r.get_vow(_P1())
        y = r.get_vow(_P2())
        return f"drawObject({obj}, at={x},{y})"
    elif sub_id == 2:  # set state
        st = r.get_vow(_P1())
        return f"drawObject({obj}, state={st})"
    elif sub_id == 0x1F:  # neither
        return f"drawObject({obj})"
    else:
        return f"drawObject({obj}, sub={sub_id})"

# -- getActorElevation: getResultPos(), vob(P1) ----------------------------

def op_getActorElevation(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorElevation({act})"

# -- setState: vow(P1), vob(P2) -------------------------------------------

def op_setState(r):
    obj = r.get_vow(_P1())
    st = r.get_vob(_P2())
    return f"setState({obj}, {st})"

# -- isNotEqual: getVar(), vow(P1), jumpRelative ---------------------------

def op_isNotEqual(r):
    a = r.get_var()
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({a} != {b}) goto [{t:04X}]"

# -- faceActor: vob(P1), vow(P2) ------------------------------------------

def op_faceActor(r):
    act = r.get_vob(_P1())
    obj = r.get_vow(_P2())
    return f"faceActor({act}, {obj})"

# -- startScript: vob(P1), getWordVararg -----------------------------------

def op_startScript(r):
    main_op = r.op  # save for flags
    script = r.get_vob(_P1())
    args = r.get_vararg()
    flags = []
    if main_op & 0x20: flags.append("F")
    if main_op & 0x40: flags.append("R")
    f = ",".join(flags)
    return f"startScript({script}, [{', '.join(args)}]{', ' + f if f else ''})"

# -- getVerbEntrypoint: getResultPos(), vow(P1), vow(P2) ------------------

def op_getVerbEntrypoint(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    b = r.get_vow(_P2())
    return f"{dst} = getVerbEntrypoint({a}, {b})"

# -- resourceRoutines: complex sub-opcode system ----------------------------

def op_resourceRoutines(r):
    sub = r.u8()
    sub_id = sub & 0x3F  # v5 uses & 0x3F

    # Sub 17 (0x11) = clearHeap: no parameters
    if sub_id == 17:
        return "resourceRoutines.clearHeap()"

    # All other subs read resid first (except 17)
    r.op = sub  # sub byte becomes _opcode for param flag bits
    resid = r.get_vob(_P1())

    NAMES = {
        1: "loadScript", 2: "loadSound", 3: "loadCostume", 4: "loadRoom",
        5: "nukeScript", 6: "nukeSound", 7: "nukeCostume", 8: "nukeRoom",
        9: "lockScript", 10: "lockSound", 11: "lockCostume", 12: "lockRoom",
        13: "unlockScript", 14: "unlockSound", 15: "unlockCostume", 16: "unlockRoom",
        18: "loadCharset", 19: "nukeCharset",
    }

    if sub_id == 20:
        # loadFlObject: resid (byte), then getVarOrDirectWord(P2)
        obj = r.get_vow(_P2())
        return f"resourceRoutines.loadFlObject({resid}, {obj})"
    elif sub_id == 35:
        # FM-TOWNS setVolumeCD: resid + vob(P2)
        vol = r.get_vob(_P2())
        return f"resourceRoutines.setVolumeCD({resid}, {vol})"
    elif sub_id == 36:
        # FM-TOWNS setSoundVolume: resid + vob(P2) + fetchScriptByte
        foo = r.get_vob(_P2())
        bar = r.u8()
        return f"resourceRoutines.setSoundVolume({resid}, {foo}, #{bar})"
    elif sub_id == 37:
        # FM-TOWNS setSoundNote: resid + vob(P2)
        note = r.get_vob(_P2())
        return f"resourceRoutines.setSoundNote({resid}, {note})"
    else:
        name = NAMES.get(sub_id, f"sub{sub_id}")
        return f"resourceRoutines.{name}({resid})"

# -- walkActorToActor: vob(P1), vob(P2), fetchScriptByte ------------------

def op_walkActorToActor(r):
    a = r.get_vob(_P1())
    b = r.get_vob(_P2())
    dist = r.u8()
    return f"walkActorToActor({a}, {b}, dist=#{dist})"

# -- putActorAtObject: vob(P1), vow(P2) -----------------------------------

def op_putActorAtObject(r):
    act = r.get_vob(_P1())
    obj = r.get_vow(_P2())
    return f"putActorAtObject({act}, {obj})"

# -- getObjectState: getResultPos(), vow(P1) --------------------------------

def op_getObjectState(r):
    dst = r.get_result_pos()
    obj = r.get_vow(_P1())
    return f"{dst} = getObjectState({obj})"

# -- getObjectOwner: getResultPos(), vow(P1) --------------------------------

def op_getObjectOwner(r):
    dst = r.get_result_pos()
    obj = r.get_vow(_P1())
    return f"{dst} = getObjectOwner({obj})"

# -- animateActor: vob(P1), vob(P2) ----------------------------------------

def op_animateActor(r):
    act = r.get_vob(_P1())
    anim = r.get_vob(_P2())
    return f"animateActor({act}, {anim})"

# -- panCameraTo: vow(P1) --------------------------------------------------

def op_panCameraTo(r):
    x = r.get_vow(_P1())
    return f"panCameraTo({x})"

# -- actorOps: vob(P1), then sub-opcode loop until 0xFF --------------------

def op_actorOps(r):
    act = r.get_vob(_P1())
    parts = [f"actorOps({act})"]

    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub  # sub byte becomes _opcode for param reads
        sub_id = sub & 0x1F
        if sub_id == 0:    # dummy
            r.get_vob(_P1())
            parts.append(f"  .dummy({r.get_vob.__name__})")  # already consumed
        elif sub_id == 1:  # SO_COSTUME
            parts.append(f"  .costume({r.get_vob(_P1())})")
        elif sub_id == 2:  # SO_STEP_DIST
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .stepDist({a}, {b})")
        elif sub_id == 3:  # SO_SOUND
            parts.append(f"  .sound({r.get_vob(_P1())})")
        elif sub_id == 4:  # SO_WALK_ANIMATION
            parts.append(f"  .walkAnim({r.get_vob(_P1())})")
        elif sub_id == 5:  # SO_TALK_ANIMATION
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .talkAnim({a}, {b})")
        elif sub_id == 6:  # SO_STAND_ANIMATION
            parts.append(f"  .standAnim({r.get_vob(_P1())})")
        elif sub_id == 7:  # SO_ANIMATION (3 params)
            a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
            parts.append(f"  .animation({a}, {b}, {c})")
        elif sub_id == 8:  # SO_DEFAULT (no params)
            parts.append("  .default")
        elif sub_id == 9:  # SO_ELEVATION
            parts.append(f"  .elevation({r.get_vow(_P1())})")
        elif sub_id == 10: # SO_ANIMATION_DEFAULT (no params)
            parts.append("  .animDefault")
        elif sub_id == 11: # SO_PALETTE
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .palette({a}, {b})")
        elif sub_id == 12: # SO_TALK_COLOR
            parts.append(f"  .talkColor({r.get_vob(_P1())})")
        elif sub_id == 13: # SO_ACTOR_NAME (inline string)
            s = r.get_string_text()
            parts.append(f'  .name("{s}")')
        elif sub_id == 14: # SO_INIT_ANIMATION
            parts.append(f"  .initAnim({r.get_vob(_P1())})")
        elif sub_id == 15: # (v5) unused — skip to be safe
            parts.append("  .sub15(??)")
        elif sub_id == 16: # SO_ACTOR_WIDTH
            parts.append(f"  .width({r.get_vob(_P1())})")
        elif sub_id == 17: # SO_ACTOR_SCALE
            # v5: two params
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .scale({a}, {b})")
        elif sub_id == 18: # SO_NEVER_ZCLIP (no params)
            parts.append("  .neverZClip")
        elif sub_id == 19: # SO_ALWAYS_ZCLIP
            parts.append(f"  .alwaysZClip({r.get_vob(_P1())})")
        elif sub_id in (20, 21): # SO_IGNORE_BOXES / SO_FOLLOW_BOXES (no params)
            parts.append(f"  .{'ignoreBoxes' if sub_id == 20 else 'followBoxes'}")
        elif sub_id == 22: # SO_ANIMATION_SPEED
            parts.append(f"  .animSpeed({r.get_vob(_P1())})")
        elif sub_id == 23: # SO_SHADOW
            parts.append(f"  .shadow({r.get_vob(_P1())})")
        else:
            parts.append(f"  .unknown_sub{sub_id}")

    # Fix: the first "dummy" entry above double-consumed; let's redo sub 0
    # Actually we need to rewrite sub 0. It was already consumed above -- the
    # parts.append was wrong. Let me just leave it as is since the bytes are correct.
    return "\n".join(parts)

# Fix sub 0 in actorOps - rewrite it properly
# Actually looking at it again, the sub 0 handling calls get_vob twice (once in
# the append message and once for real). Let me fix it by restructuring.
# The above code for sub 0 has a bug - it calls get_vob(_P1()) which consumes
# bytes, then tries to use the function name. This needs fixing in the write.
# I'll handle it correctly below in a cleaner version.

def op_actorOps_v2(r):
    """actorOps — properly handles all sub-opcodes."""
    act = r.get_vob(_P1())
    parts = [f"actorOps({act})"]

    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        sub_id = sub & 0x1F
        if sub_id == 0:
            v = r.get_vob(_P1())
            parts.append(f"  .dummy({v})")
        elif sub_id == 1:
            v = r.get_vob(_P1())
            parts.append(f"  .costume({v})")
        elif sub_id == 2:
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .stepDist({a}, {b})")
        elif sub_id == 3:
            v = r.get_vob(_P1())
            parts.append(f"  .sound({v})")
        elif sub_id == 4:
            v = r.get_vob(_P1())
            parts.append(f"  .walkAnim({v})")
        elif sub_id == 5:
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .talkAnim({a}, {b})")
        elif sub_id == 6:
            v = r.get_vob(_P1())
            parts.append(f"  .standAnim({v})")
        elif sub_id == 7:
            a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
            parts.append(f"  .animation({a}, {b}, {c})")
        elif sub_id == 8:
            parts.append("  .default")
        elif sub_id == 9:
            v = r.get_vow(_P1())
            parts.append(f"  .elevation({v})")
        elif sub_id == 10:
            parts.append("  .animDefault")
        elif sub_id == 11:
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .palette({a}, {b})")
        elif sub_id == 12:
            v = r.get_vob(_P1())
            parts.append(f"  .talkColor({v})")
        elif sub_id == 13:
            s = r.get_string_text()
            parts.append(f'  .name("{s}")')
        elif sub_id == 14:
            v = r.get_vob(_P1())
            parts.append(f"  .initAnim({v})")
        elif sub_id == 16:
            v = r.get_vob(_P1())
            parts.append(f"  .width({v})")
        elif sub_id == 17:
            a = r.get_vob(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .scale({a}, {b})")
        elif sub_id == 18:
            parts.append("  .neverZClip")
        elif sub_id == 19:
            v = r.get_vob(_P1())
            parts.append(f"  .alwaysZClip({v})")
        elif sub_id in (20, 21):
            parts.append(f"  .{'ignoreBoxes' if sub_id == 20 else 'followBoxes'}")
        elif sub_id == 22:
            v = r.get_vob(_P1())
            parts.append(f"  .animSpeed({v})")
        elif sub_id == 23:
            v = r.get_vob(_P1())
            parts.append(f"  .shadow({v})")
        else:
            parts.append(f"  .unknown_sub{sub_id}")

    return "\n".join(parts)

# Replace the buggy version
op_actorOps = op_actorOps_v2

# -- print / printEgo: decodeParseString ------------------------------------

def _decode_parse_string(r):
    """Shared print sub-opcode decoder (decodeParseString).
    Reads sub-opcodes until 0xFF terminator.
    Sub-opcode byte becomes _opcode for param bit checks.
    Sub & 0xF determines the action.
    """
    parts = []
    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        sub_f = sub & 0x0F
        if sub_f == 0:    # SO_AT
            x = r.get_vow(_P1()); y = r.get_vow(_P2())
            parts.append(f"at({x},{y})")
        elif sub_f == 1:  # SO_COLOR
            c = r.get_vob(_P1())
            parts.append(f"color({c})")
        elif sub_f == 2:  # SO_CLIPPED
            w = r.get_vow(_P1())
            parts.append(f"clip({w})")
        elif sub_f == 3:  # SO_ERASE
            w = r.get_vow(_P1()); h = r.get_vow(_P2())
            parts.append(f"erase({w},{h})")
        elif sub_f == 4:  # SO_CENTER
            parts.append("center")
        elif sub_f == 6:  # SO_LEFT (v5: no params)
            parts.append("left")
        elif sub_f == 7:  # SO_OVERHEAD
            parts.append("overhead")
        elif sub_f == 8:  # SO_SAY_VOICE
            off = r.get_vow(_P1()); delay = r.get_vow(_P2())
            parts.append(f"voice({off},{delay})")
        elif sub_f == 15: # SO_TEXTSTRING
            s = r.get_string_text()
            parts.append(f'"{s}"')
            return " ".join(parts)  # text terminates the print, no 0xFF after
        else:
            parts.append(f"sub{sub_f}")
    return " ".join(parts)

def op_print(r):
    actor = r.get_vob(_P1())
    body = _decode_parse_string(r)
    return f"print({actor}) {body}"

def op_printEgo(r):
    body = _decode_parse_string(r)
    return f"printEgo() {body}"

# -- actorFromPos: getResultPos(), vow(P1), vow(P2) -----------------------

def op_actorFromPos(r):
    dst = r.get_result_pos()
    x = r.get_vow(_P1()); y = r.get_vow(_P2())
    return f"{dst} = actorFromPos({x}, {y})"

# -- getRandomNr: getResultPos(), vob(P1) ----------------------------------

def op_getRandomNr(r):
    dst = r.get_result_pos()
    mx = r.get_vob(_P1())
    return f"{dst} = getRandomNr({mx})"

# -- and: getResultPos(), vow(P1) ------------------------------------------

def op_and(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} &= {a}"

# -- jumpRelative: fetchScriptWord (signed offset) -------------------------

def op_jumpRelative(r):
    t = r.jump_target()
    return f"goto [{t:04X}]"

# -- doSentence: vob(P1), (if verb!=0xFE: vow(P2), vow(P3)) ---------------

def op_doSentence(r):
    verb = r.get_vob(_P1())
    # If the verb literal value is 0xFE, no further params.
    # But we can't always know statically (could be a variable).
    # ScummVM: reads verb, checks if == 0xFE. If so, returns early.
    # For disassembly: read verb. If it's a literal #254, skip params.
    # Otherwise always read objectA and objectB.
    if verb == "#254":
        return f"doSentence(stop)"
    objA = r.get_vow(_P2())
    objB = r.get_vow(_P3())
    return f"doSentence({verb}, {objA}, {objB})"

# -- move: getResultPos(), vow(P1) -----------------------------------------

def op_move(r):
    dst = r.get_result_pos()
    val = r.get_vow(_P1())
    return f"{dst} = {val}"

# -- multiply: getResultPos(), vow(P1) -------------------------------------

def op_multiply(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} *= {a}"

# -- startSound: vob(P1) ---------------------------------------------------

def op_startSound(r):
    s = r.get_vob(_P1())
    return f"startSound({s})"

# -- ifClassOfIs: vow(P1), then loop fetchByte/vow(P1) until 0xFF, then jump

def op_ifClassOfIs(r):
    obj = r.get_vow(_P1())
    classes = []
    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        cls = r.get_vow(_P1())
        classes.append(cls)
    t = r.jump_target()
    return f"ifClassOfIs({obj}, [{', '.join(classes)}]) goto [{t:04X}]"

# -- walkActorTo: vob(P1), vow(P2), vow(P3) --------------------------------

def op_walkActorTo(r):
    act = r.get_vob(_P1())
    x = r.get_vow(_P2())
    y = r.get_vow(_P3())
    return f"walkActorTo({act}, {x}, {y})"

# -- isActorInBox: vob(P1), vob(P2), jumpRelative ---------------------------

def op_isActorInBox(r):
    act = r.get_vob(_P1())
    box = r.get_vob(_P2())
    t = r.jump_target()
    return f"unless isActorInBox({act}, {box}) goto [{t:04X}]"

# -- getAnimCounter: getResultPos(), vob(P1) --------------------------------

def op_getAnimCounter(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getAnimCounter({act})"

# -- getActorY: getResultPos(), vow(P1) (v5) --------------------------------

def op_getActorY(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} = getActorY({a})"

# -- loadRoomWithEgo: vow(P1), vob(P2), s16, s16 ---------------------------

def op_loadRoomWithEgo(r):
    obj = r.get_vow(_P1())
    room = r.get_vob(_P2())
    x = r.s16()
    y = r.s16()
    return f"loadRoomWithEgo({obj}, room={room}, walk={x},{y})"

# -- pickupObject: vow(P1), vob(P2) ----------------------------------------

def op_pickupObject(r):
    obj = r.get_vow(_P1())
    room = r.get_vob(_P2())
    return f"pickupObject({obj}, room={room})"

# -- setVarRange: getResultPos(), fetchByte(count), then count * (byte or word)

def op_setVarRange(r):
    dst = r.get_result_pos()
    count = r.u8()
    vals = []
    for _ in range(count):
        if r.op & 0x80:
            vals.append(f"#{r.s16()}")
        else:
            vals.append(f"#{r.u8()}")
    return f"setVarRange({dst}, [{', '.join(vals[:8])}{'...' if len(vals) > 8 else ''}])"

# -- stringOps: fetchByte (sub), then sub-specific params -------------------

def op_stringOps(r):
    sub = r.u8()
    r.op = sub
    sub_id = sub & 0x1F
    if sub_id == 1:    # loadstring (inline text)
        idx = r.get_vob(_P1())
        s = r.get_string_text()
        return f'stringOps.load({idx}, "{s}")'
    elif sub_id == 2:  # copystring
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"stringOps.copy({a}, {b})"
    elif sub_id == 3:  # set string char
        a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
        return f"stringOps.setChar({a}, {b}, {c})"
    elif sub_id == 4:  # get string char
        dst = r.get_result_pos()
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"{dst} = stringOps.getChar({a}, {b})"
    elif sub_id == 5:  # create empty string
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"stringOps.create({a}, size={b})"
    else:
        return f"stringOps.sub{sub_id}"

# -- equalZero: getVar(), jumpRelative --------------------------------------

def op_equalZero(r):
    a = r.get_var()
    t = r.jump_target()
    return f"unless ({a} == 0) goto [{t:04X}]"

# -- setOwnerOf: vow(P1), vob(P2) ------------------------------------------

def op_setOwnerOf(r):
    obj = r.get_vow(_P1())
    owner = r.get_vob(_P2())
    return f"setOwnerOf({obj}, {owner})"

# -- delayVariable: getVar() -----------------------------------------------

def op_delayVariable(r):
    v = r.get_var()
    return f"delayVariable({v})"

# -- cursorCommand: fetchByte (sub), then sub-specific params ---------------

def op_cursorCommand(r):
    sub = r.u8()
    r.op = sub
    sub_id = sub & 0x1F
    NO_PARAMS = {1: "cursorOn", 2: "cursorOff", 3: "userputOn", 4: "userputOff",
                 5: "cursorSoftOn", 6: "cursorSoftOff", 7: "userputSoftOn", 8: "userputSoftOff"}
    if sub_id in NO_PARAMS:
        return f"cursorCommand.{NO_PARAMS[sub_id]}()"
    elif sub_id == 10: # SO_CURSOR_IMAGE
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"cursorCommand.image({a}, {b})"
    elif sub_id == 11: # SO_CURSOR_HOTSPOT
        a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
        return f"cursorCommand.hotspot({a}, {b}, {c})"
    elif sub_id == 12: # SO_CURSOR_SET
        a = r.get_vob(_P1())
        return f"cursorCommand.set({a})"
    elif sub_id == 13: # SO_CHARSET_SET
        a = r.get_vob(_P1())
        return f"cursorCommand.charset({a})"
    elif sub_id == 14: # SO_CHARSET_COLORS (v5: getWordVararg)
        args = r.get_vararg()
        return f"cursorCommand.charsetColors([{', '.join(args)}])"
    else:
        return f"cursorCommand.sub{sub_id}"

# -- putActorInRoom: vob(P1), vob(P2) --------------------------------------

def op_putActorInRoom(r):
    act = r.get_vob(_P1())
    room = r.get_vob(_P2())
    return f"putActorInRoom({act}, {room})"

# -- delay: 3 bytes (24-bit little-endian) ----------------------------------

def op_delay(r):
    lo = r.u8(); mid = r.u8(); hi = r.u8()
    val = lo | (mid << 8) | (hi << 16)
    return f"delay({val})"

# -- matrixOps: fetchByte (sub), then sub-specific params -------------------

def op_matrixOps(r):
    sub = r.u8()
    r.op = sub
    sub_id = sub & 0x1F
    if sub_id in (1, 2, 3):
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        NAMES = {1: "setBoxFlags", 2: "setBoxScale", 3: "setBoxScaleSlot"}
        return f"matrixOps.{NAMES[sub_id]}({a}, {b})"
    elif sub_id == 4:
        return "matrixOps.createBoxMatrix()"
    else:
        return f"matrixOps.sub{sub_id}"

# -- getInventoryCount: getResultPos(), vob(P1) ----------------------------

def op_getInventoryCount(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getInventoryCount({act})"

# -- setCameraAt: vow(P1) --------------------------------------------------

def op_setCameraAt(r):
    x = r.get_vow(_P1())
    return f"setCameraAt({x})"

# -- roomOps: fetchByte (sub), then sub-specific params ---------------------
# Note: v5 (non v3): params come AFTER the sub-opcode byte

def op_roomOps(r):
    sub = r.u8()
    r.op = sub
    sub_id = sub & 0x1F

    if sub_id == 1:   # SO_ROOM_SCROLL
        a = r.get_vow(_P1()); b = r.get_vow(_P2())
        return f"roomOps.scroll({a}, {b})"
    elif sub_id == 3: # SO_ROOM_SCREEN
        a = r.get_vow(_P1()); b = r.get_vow(_P2())
        return f"roomOps.screen({a}, {b})"
    elif sub_id == 4: # SO_ROOM_PALETTE (v5: a,b,c words, then fetchByte -> sub, vob(P1))
        a = r.get_vow(_P1()); b = r.get_vow(_P2()); c = r.get_vow(_P3())
        sub2 = r.u8()
        r.op = sub2
        d = r.get_vob(_P1())
        return f"roomOps.palette({a}, {b}, {c}, idx={d})"
    elif sub_id == 5: # SO_ROOM_SHAKE_ON
        return "roomOps.shakeOn()"
    elif sub_id == 6: # SO_ROOM_SHAKE_OFF
        return "roomOps.shakeOff()"
    elif sub_id == 7: # SO_ROOM_SCALE: vob,vob, fetchByte->sub, vob,vob, fetchByte->sub, vob
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        sub2 = r.u8(); r.op = sub2
        c = r.get_vob(_P1()); d = r.get_vob(_P2())
        sub3 = r.u8(); r.op = sub3
        e = r.get_vob(_P2())
        return f"roomOps.scale({a}, {b}, {c}, {d}, slot={e})"
    elif sub_id == 8: # SO_ROOM_INTENSITY
        a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
        return f"roomOps.intensity({a}, {b}, {c})"
    elif sub_id == 9: # SO_ROOM_SAVEGAME
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"roomOps.savegame({a}, {b})"
    elif sub_id == 10: # SO_ROOM_FADE
        a = r.get_vow(_P1())
        return f"roomOps.fade({a})"
    elif sub_id == 11: # SO_RGB_ROOM_INTENSITY: vow,vow,vow, fetchByte->sub, vob,vob
        a = r.get_vow(_P1()); b = r.get_vow(_P2()); c = r.get_vow(_P3())
        sub2 = r.u8(); r.op = sub2
        d = r.get_vob(_P1()); e = r.get_vob(_P2())
        return f"roomOps.rgbIntensity({a}, {b}, {c}, {d}, {e})"
    elif sub_id == 12: # SO_ROOM_SHADOW: same as 11
        a = r.get_vow(_P1()); b = r.get_vow(_P2()); c = r.get_vow(_P3())
        sub2 = r.u8(); r.op = sub2
        d = r.get_vob(_P1()); e = r.get_vob(_P2())
        return f"roomOps.shadow({a}, {b}, {c}, {d}, {e})"
    elif sub_id == 13: # SO_SAVE_STRING: vob(P1), then null-terminated filename string
        a = r.get_vob(_P1())
        fn = []
        while not r.eof():
            ch = r.u8()
            if ch == 0: break
            fn.append(chr(ch) if 32 <= ch < 127 else '?')
        return f'roomOps.saveString({a}, "{"".join(fn)}")'
    elif sub_id == 14: # SO_LOAD_STRING: vob(P1), then null-terminated filename string
        a = r.get_vob(_P1())
        fn = []
        while not r.eof():
            ch = r.u8()
            if ch == 0: break
            fn.append(chr(ch) if 32 <= ch < 127 else '?')
        return f'roomOps.loadString({a}, "{"".join(fn)}")'
    elif sub_id == 15: # SO_ROOM_TRANSFORM: vob, fetchByte->sub, vob,vob, fetchByte->sub, vob
        a = r.get_vob(_P1())
        sub2 = r.u8(); r.op = sub2
        b = r.get_vob(_P1()); c = r.get_vob(_P2())
        sub3 = r.u8(); r.op = sub3
        d = r.get_vob(_P1())
        return f"roomOps.transform({a}, {b}, {c}, {d})"
    elif sub_id == 16: # SO_CYCLE_SPEED
        a = r.get_vob(_P1()); b = r.get_vob(_P2())
        return f"roomOps.cycleSpeed({a}, {b})"
    else:
        return f"roomOps.sub{sub_id}"

# -- getDist: getResultPos(), vow(P1), vow(P2) ----------------------------

def op_getDist(r):
    dst = r.get_result_pos()
    o1 = r.get_vow(_P1()); o2 = r.get_vow(_P2())
    return f"{dst} = getDist({o1}, {o2})"

# -- findObject: getResultPos(), vob(P1), vob(P2) --------------------------

def op_findObject(r):
    dst = r.get_result_pos()
    x = r.get_vob(_P1()); y = r.get_vob(_P2())
    return f"{dst} = findObject({x}, {y})"

# -- walkActorToObject: vob(P1), vow(P2) -----------------------------------

def op_walkActorToObject(r):
    act = r.get_vob(_P1())
    obj = r.get_vow(_P2())
    return f"walkActorToObject({act}, {obj})"

# -- startObject: vow(P1), vob(P2), getWordVararg --------------------------

def op_startObject(r):
    obj = r.get_vow(_P1())
    script = r.get_vob(_P2())
    args = r.get_vararg()
    return f"startObject({obj}, {script}, [{', '.join(args)}])"

# -- isLessEqual: fetchScriptWord (var), vow(P1), jumpRelative -------------

def op_isLessEqual(r):
    # isLessEqual: fetchScriptWord() -> readVar() -> getVarOrDirectWord -> jumpRelative
    # readVar may consume extra bytes if var & 0x2000 (indirect)
    vname = r.get_var()  # handles indirection
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({vname} <= {b}) goto [{t:04X}]"

# -- subtract: getResultPos(), vow(P1) -------------------------------------

def op_subtract(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} -= {a}"

# -- getActorScale: getResultPos(), vob(P1) --------------------------------

def op_getActorScale(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorScale({act})"

# -- stopSound: vob(P1) ----------------------------------------------------

def op_stopSound(r):
    s = r.get_vob(_P1())
    return f"stopSound({s})"

# -- findInventory: getResultPos(), vob(P1), vob(P2) -----------------------

def op_findInventory(r):
    dst = r.get_result_pos()
    x = r.get_vob(_P1()); y = r.get_vob(_P2())
    return f"{dst} = findInventory({x}, {y})"

# -- drawBox: vow(P1), vow(P2), fetchByte -> _opcode, vow(P1), vow(P2), vob(P3)

def op_drawBox(r):
    x = r.get_vow(_P1()); y = r.get_vow(_P2())
    sub = r.u8()
    r.op = sub
    x2 = r.get_vow(_P1()); y2 = r.get_vow(_P2())
    color = r.get_vob(_P3())
    return f"drawBox({x}, {y}, {x2}, {y2}, color={color})"

# -- cutscene: getWordVararg -----------------------------------------------

def op_cutscene(r):
    args = r.get_vararg()
    return f"cutscene([{', '.join(args)}])"

# -- chainScript: vob(P1), getWordVararg -----------------------------------

def op_chainScript(r):
    script = r.get_vob(_P1())
    args = r.get_vararg()
    return f"chainScript({script}, [{', '.join(args)}])"

# -- getActorX: getResultPos(), vow(P1) (v5) --------------------------------

def op_getActorX(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} = getActorX({a})"

# -- isLess: getVar(), vow(P1), jumpRelative --------------------------------

def op_isLess(r):
    a = r.get_var()
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({a} < {b}) goto [{t:04X}]"

# -- increment / decrement: getResultPos() ----------------------------------

def op_increment(r):
    dst = r.get_result_pos()
    return f"{dst}++"

def op_decrement(r):
    dst = r.get_result_pos()
    return f"{dst}--"

# -- isEqual: fetchScriptWord(var), vow(P1), jumpRelative ------------------

def op_isEqual(r):
    # isEqual: fetchScriptWord() -> readVar() -> getVarOrDirectWord -> jumpRelative
    # readVar may consume extra bytes if var & 0x2000 (indirect)
    vname = r.get_var()  # handles indirection
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({vname} == {b}) goto [{t:04X}]"

# -- soundKludge: getWordVararg --------------------------------------------

def op_soundKludge(r):
    args = r.get_vararg()
    return f"soundKludge([{', '.join(args)}])"

# -- actorFollowCamera: vob(P1) --------------------------------------------

def op_actorFollowCamera(r):
    act = r.get_vob(_P1())
    return f"actorFollowCamera({act})"

# -- setObjectName: vow(P1), then inline string (loadPtrToResource NULL) ---

def op_setObjectName(r):
    obj = r.get_vow(_P1())
    s = r.get_string_text()
    return f'setObjectName({obj}, "{s}")'

# -- getActorMoving: getResultPos(), vob(P1) --------------------------------

def op_getActorMoving(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorMoving({act})"

# -- or: getResultPos(), vow(P1) -------------------------------------------

def op_or(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} |= {a}"

# -- beginOverride: fetchScriptByte ----------------------------------------

def op_beginOverride(r):
    flag = r.u8()
    if flag != 0:
        return "beginOverride"
    else:
        return "endOverride"

# -- add: getResultPos(), vow(P1) ------------------------------------------

def op_add(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} += {a}"

# -- divide: getResultPos(), vow(P1) ---------------------------------------

def op_divide(r):
    dst = r.get_result_pos()
    a = r.get_vow(_P1())
    return f"{dst} /= {a}"

# -- setClass: vow(P1), then loop fetchByte/vow(P1) until 0xFF ------------

def op_setClass(r):
    obj = r.get_vow(_P1())
    classes = []
    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        cls = r.get_vow(_P1())
        classes.append(cls)
    return f"setClass({obj}, [{', '.join(classes)}])"

# -- freezeScripts: vob(P1) ------------------------------------------------

def op_freezeScripts(r):
    s = r.get_vob(_P1())
    return f"freezeScripts({s})"

# -- stopScript: vob(P1) ---------------------------------------------------

def op_stopScript(r):
    s = r.get_vob(_P1())
    return f"stopScript({s})"

# -- getActorFacing: getResultPos(), vob(P1) --------------------------------

def op_getActorFacing(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorFacing({act})"

# -- getClosestObjActor: getResultPos(), vow(P1) ---------------------------

def op_getClosestObjActor(r):
    dst = r.get_result_pos()
    act = r.get_vow(_P1())
    return f"{dst} = getClosestObjActor({act})"

# -- getStringWidth: getResultPos(), vob(P1) --------------------------------

def op_getStringWidth(r):
    dst = r.get_result_pos()
    s = r.get_vob(_P1())
    return f"{dst} = getStringWidth({s})"

# -- isScriptRunning: getResultPos(), vob(P1) ------------------------------

def op_isScriptRunning(r):
    dst = r.get_result_pos()
    s = r.get_vob(_P1())
    return f"{dst} = isScriptRunning({s})"

# -- debug: vow(P1) --------------------------------------------------------

def op_debug(r):
    a = r.get_vow(_P1())
    return f"debug({a})"

# -- getActorWidth: getResultPos(), vob(P1) --------------------------------

def op_getActorWidth(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorWidth({act})"

# -- stopObjectScript: vow(P1) ---------------------------------------------

def op_stopObjectScript(r):
    obj = r.get_vow(_P1())
    return f"stopObjectScript({obj})"

# -- lights: vob(P1), fetchByte, fetchByte ---------------------------------

def op_lights(r):
    a = r.get_vob(_P1())
    b = r.u8()
    c = r.u8()
    return f"lights({a}, #{b}, #{c})"

# -- getActorCostume: getResultPos(), vob(P1) --------------------------------

def op_getActorCostume(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorCostume({act})"

# -- loadRoom: vob(P1) -----------------------------------------------------

def op_loadRoom(r):
    room = r.get_vob(_P1())
    return f"loadRoom({room})"

# -- isGreater: getVar(), vow(P1), jumpRelative ----------------------------

def op_isGreater(r):
    a = r.get_var()
    b = r.get_vow(_P1())
    t = r.jump_target()
    return f"unless ({a} > {b}) goto [{t:04X}]"

# -- verbOps: vob(P1), then sub-opcode loop until 0xFF ---------------------

def op_verbOps(r):
    verb = r.get_vob(_P1())
    parts = [f"verbOps({verb})"]

    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        sub_id = sub & 0x1F
        if sub_id == 1:    # SO_VERB_IMAGE
            a = r.get_vow(_P1())
            parts.append(f"  .image({a})")
        elif sub_id == 2:  # SO_VERB_NAME (inline string)
            s = r.get_string_text()
            parts.append(f'  .name("{s}")')
        elif sub_id == 3:  # SO_VERB_COLOR
            v = r.get_vob(_P1())
            parts.append(f"  .color({v})")
        elif sub_id == 4:  # SO_VERB_HICOLOR
            v = r.get_vob(_P1())
            parts.append(f"  .hicolor({v})")
        elif sub_id == 5:  # SO_VERB_AT
            x = r.get_vow(_P1()); y = r.get_vow(_P2())
            parts.append(f"  .at({x}, {y})")
        elif sub_id == 6:  # SO_VERB_ON
            parts.append("  .on")
        elif sub_id == 7:  # SO_VERB_OFF
            parts.append("  .off")
        elif sub_id == 8:  # SO_VERB_DELETE
            parts.append("  .delete")
        elif sub_id == 9:  # SO_VERB_NEW
            parts.append("  .new")
        elif sub_id == 16: # SO_VERB_DIMCOLOR
            v = r.get_vob(_P1())
            parts.append(f"  .dimcolor({v})")
        elif sub_id == 17: # SO_VERB_DIM
            parts.append("  .dim")
        elif sub_id == 18: # SO_VERB_KEY
            v = r.get_vob(_P1())
            parts.append(f"  .key({v})")
        elif sub_id == 19: # SO_VERB_CENTER
            parts.append("  .center")
        elif sub_id == 20: # SO_VERB_NAME_STR
            v = r.get_vow(_P1())
            parts.append(f"  .nameStr({v})")
        elif sub_id == 22: # assign object
            a = r.get_vow(_P1()); b = r.get_vob(_P2())
            parts.append(f"  .assignObj({a}, {b})")
        elif sub_id == 23: # set back color
            v = r.get_vob(_P1())
            parts.append(f"  .bkcolor({v})")
        else:
            parts.append(f"  .sub{sub_id}")

    return "\n".join(parts)

# -- getActorWalkBox: getResultPos(), vob(P1) --------------------------------

def op_getActorWalkBox(r):
    dst = r.get_result_pos()
    act = r.get_vob(_P1())
    return f"{dst} = getActorWalkBox({act})"

# -- isSoundRunning: getResultPos(), vob(P1) --------------------------------

def op_isSoundRunning(r):
    dst = r.get_result_pos()
    s = r.get_vob(_P1())
    return f"{dst} = isSoundRunning({s})"

# -- saveRestoreVerbs: fetchByte (sub), vob(P1), vob(P2), vob(P3) ----------

def op_saveRestoreVerbs(r):
    sub = r.u8()
    r.op = sub
    a = r.get_vob(_P1()); b = r.get_vob(_P2()); c = r.get_vob(_P3())
    NAMES = {1: "saveVerbs", 2: "restoreVerbs", 3: "deleteVerbs"}
    name = NAMES.get(sub, f"sub{sub}")
    return f"saveRestoreVerbs.{name}({a}, {b}, {c})"

# -- expression: getResultPos(), then loop of sub-ops until 0xFF -----------

def op_expression(r):
    dst = r.get_result_pos()
    parts = []

    while not r.eof():
        sub = r.u8()
        if sub == 0xFF:
            break
        r.op = sub
        sub_id = sub & 0x1F
        if sub_id == 1:    # push var or direct word
            v = r.get_vow(_P1())
            parts.append(v)
        elif sub_id == 2:  # add
            parts.append("+")
        elif sub_id == 3:  # sub
            parts.append("-")
        elif sub_id == 4:  # mul
            parts.append("*")
        elif sub_id == 5:  # div
            parts.append("/")
        elif sub_id == 6:  # nested opcode
            nested_op = r.u8()
            r.op = nested_op
            name = OPCODE_TABLE[nested_op]
            handler = HANDLER_MAP.get(name)
            if handler:
                nested_text = handler(r)
                parts.append(f"[{nested_text}]")
            else:
                parts.append(f"[op_{nested_op:02X}]")
        else:
            parts.append(f"??sub{sub_id}")

    return f"{dst} = expr({' '.join(parts)})"

# -- wait: fetchByte (sub), then sub-specific params -----------------------

def op_wait(r):
    sub = r.u8()
    r.op = sub
    sub_id = sub & 0x1F
    if sub_id == 1:   # SO_WAIT_FOR_ACTOR
        act = r.get_vob(_P1())
        return f"waitForActor({act})"
    elif sub_id == 2: # SO_WAIT_FOR_MESSAGE
        return "waitForMessage"
    elif sub_id == 3: # SO_WAIT_FOR_CAMERA
        return "waitForCamera"
    elif sub_id == 4: # SO_WAIT_FOR_SENTENCE
        return "waitForSentence"
    else:
        return f"wait.sub{sub_id}"

# -- notEqualZero: getVar(), jumpRelative ----------------------------------

def op_notEqualZero(r):
    a = r.get_var()
    t = r.jump_target()
    return f"unless ({a} != 0) goto [{t:04X}]"

# -- pseudoRoom: fetchByte, then fetchByte until 0 -------------------------

def op_pseudoRoom(r):
    res = r.u8()
    rooms = []
    while not r.eof():
        b = r.u8()
        if b == 0:
            break
        rooms.append(f"#{b}")
    return f"pseudoRoom(#{res}, [{', '.join(rooms)}])"

# -- systemOps: fetchByte (sub) --------------------------------------------

def op_systemOps(r):
    sub = r.u8()
    NAMES = {1: "restart", 2: "pause", 3: "quit"}
    return f"systemOps.{NAMES.get(sub, f'sub{sub}')}"

# ---------------------------------------------------------------------------
# Handler map: opcode name -> function
# ---------------------------------------------------------------------------

HANDLER_MAP = {
    "stopObjectCode": op_stopObjectCode,
    "breakHere": op_breakHere,
    "stopMusic": op_stopMusic,
    "endCutscene": op_endCutscene,
    "dummy": op_dummy,
    "putActor": op_putActor,
    "startMusic": op_startMusic,
    "getActorRoom": op_getActorRoom,
    "isGreaterEqual": op_isGreaterEqual,
    "drawObject": op_drawObject,
    "getActorElevation": op_getActorElevation,
    "setState": op_setState,
    "isNotEqual": op_isNotEqual,
    "faceActor": op_faceActor,
    "startScript": op_startScript,
    "getVerbEntrypoint": op_getVerbEntrypoint,
    "resourceRoutines": op_resourceRoutines,
    "walkActorToActor": op_walkActorToActor,
    "putActorAtObject": op_putActorAtObject,
    "getObjectState": op_getObjectState,
    "getObjectOwner": op_getObjectOwner,
    "animateActor": op_animateActor,
    "panCameraTo": op_panCameraTo,
    "actorOps": op_actorOps,
    "print": op_print,
    "printEgo": op_printEgo,
    "actorFromPos": op_actorFromPos,
    "getRandomNr": op_getRandomNr,
    "and": op_and,
    "jumpRelative": op_jumpRelative,
    "doSentence": op_doSentence,
    "move": op_move,
    "multiply": op_multiply,
    "startSound": op_startSound,
    "ifClassOfIs": op_ifClassOfIs,
    "walkActorTo": op_walkActorTo,
    "isActorInBox": op_isActorInBox,
    "getAnimCounter": op_getAnimCounter,
    "getActorY": op_getActorY,
    "loadRoomWithEgo": op_loadRoomWithEgo,
    "pickupObject": op_pickupObject,
    "setVarRange": op_setVarRange,
    "stringOps": op_stringOps,
    "equalZero": op_equalZero,
    "setOwnerOf": op_setOwnerOf,
    "delayVariable": op_delayVariable,
    "cursorCommand": op_cursorCommand,
    "putActorInRoom": op_putActorInRoom,
    "delay": op_delay,
    "matrixOps": op_matrixOps,
    "getInventoryCount": op_getInventoryCount,
    "setCameraAt": op_setCameraAt,
    "roomOps": op_roomOps,
    "getDist": op_getDist,
    "findObject": op_findObject,
    "walkActorToObject": op_walkActorToObject,
    "startObject": op_startObject,
    "isLessEqual": op_isLessEqual,
    "subtract": op_subtract,
    "getActorScale": op_getActorScale,
    "stopSound": op_stopSound,
    "findInventory": op_findInventory,
    "drawBox": op_drawBox,
    "cutscene": op_cutscene,
    "chainScript": op_chainScript,
    "getActorX": op_getActorX,
    "isLess": op_isLess,
    "increment": op_increment,
    "decrement": op_decrement,
    "isEqual": op_isEqual,
    "soundKludge": op_soundKludge,
    "actorFollowCamera": op_actorFollowCamera,
    "setObjectName": op_setObjectName,
    "getActorMoving": op_getActorMoving,
    "or": op_or,
    "beginOverride": op_beginOverride,
    "add": op_add,
    "divide": op_divide,
    "setClass": op_setClass,
    "freezeScripts": op_freezeScripts,
    "stopScript": op_stopScript,
    "getActorFacing": op_getActorFacing,
    "getClosestObjActor": op_getClosestObjActor,
    "getStringWidth": op_getStringWidth,
    "isScriptRunning": op_isScriptRunning,
    "debug": op_debug,
    "getActorWidth": op_getActorWidth,
    "stopObjectScript": op_stopObjectScript,
    "lights": op_lights,
    "getActorCostume": op_getActorCostume,
    "loadRoom": op_loadRoom,
    "isGreater": op_isGreater,
    "verbOps": op_verbOps,
    "getActorWalkBox": op_getActorWalkBox,
    "isSoundRunning": op_isSoundRunning,
    "saveRestoreVerbs": op_saveRestoreVerbs,
    "expression": op_expression,
    "wait": op_wait,
    "notEqualZero": op_notEqualZero,
    "pseudoRoom": op_pseudoRoom,
    "systemOps": op_systemOps,
    "roomOps": op_roomOps,
    "walkActorTo": op_walkActorTo,
    "isActorInBox": op_isActorInBox,
    "drawBox": op_drawBox,
    "putActorInRoom": op_putActorInRoom,
}


# ---------------------------------------------------------------------------
# Main disassembly loop
# ---------------------------------------------------------------------------

def disassemble(data, start=0, end=None):
    if end is None:
        end = len(data)
    r = ScriptReader(data)
    r.pc = start
    lines = []

    while r.pc < end and not r.eof():
        addr = r.pc
        try:
            op_byte = r.u8()
            r.op = op_byte
            name = OPCODE_TABLE[op_byte]

            if name is None:
                lines.append((addr, op_byte, f"UNKNOWN opcode 0x{op_byte:02X}"))
                continue

            handler = HANDLER_MAP.get(name)
            if handler is None:
                lines.append((addr, op_byte, f"{name} [NO HANDLER]"))
                continue

            text = handler(r)

            # Multi-line opcodes (actorOps, verbOps)
            if "\n" in text:
                first, *rest = text.split("\n")
                lines.append((addr, op_byte, first))
                for extra in rest:
                    lines.append((None, None, extra))
            else:
                lines.append((addr, op_byte, text))

        except (IndexError, struct.error) as e:
            lines.append((addr, None, f"[DECODE ERROR at 0x{addr:04X}: {e}]"))
            break

    return lines


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 descumm5.py <script.bin> [start] [end]")
        print()
        print("LSCR files have a 1-byte header (script number); this tool auto-skips it.")
        print("Use explicit start offset to override: descumm5.py script.bin 0")
        sys.exit(1)

    fn = sys.argv[1]

    with open(fn, 'rb') as f:
        data = f.read()

    # Auto-detect LSCR files (1-byte script number header)
    basename = fn.replace("\\", "/").split("/")[-1].lower()
    auto_skip = 0
    if basename.startswith("lscr_") and len(sys.argv) <= 2:
        auto_skip = 1
        script_num = data[0]
        print(f"  ; LSCR script #{script_num} (auto-skipping 1-byte header)")

    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else auto_skip
    end = int(sys.argv[3], 0) if len(sys.argv) > 3 else None

    lines = disassemble(data, start, end)
    for addr, op_byte, text in lines:
        if addr is not None and op_byte is not None:
            print(f"  [{addr:04X}] ({op_byte:02X}) {text}")
        elif addr is not None:
            print(f"  [{addr:04X}] (--) {text}")
        else:
            print(f"                {text}")


if __name__ == '__main__':
    main()
