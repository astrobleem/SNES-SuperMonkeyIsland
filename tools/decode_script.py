#!/usr/bin/env python3
"""Decode SCUMM v5 bytecode from an extracted script binary.

Usage: python tools/decode_script.py <script_bin_path>
"""

import sys
import struct
from io import BytesIO
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.scumm.opcodes_v5 import OPCODE_MAP, BASE_OPCODES


# ---------------------------------------------------------------------------
# Verbose parameter decoders -- these read AND display param values
# ---------------------------------------------------------------------------

def read_u8(data, pos):
    """Read unsigned byte at pos."""
    if pos >= len(data):
        return None, pos
    return data[pos], pos + 1

def read_u16le(data, pos):
    """Read unsigned 16-bit LE at pos."""
    if pos + 2 > len(data):
        return None, pos
    val = data[pos] | (data[pos+1] << 8)
    return val, pos + 2

def read_s16le(data, pos):
    """Read signed 16-bit LE at pos."""
    if pos + 2 > len(data):
        return None, pos
    val = struct.unpack_from('<h', data, pos)[0]
    return val, pos + 2


def var_name(var_id):
    """Convert a SCUMM v5 variable ID to a human-readable name.

    SCUMM v5 variable encoding:
      0x0000-0x3FFF: Global variables (VAR_GLOBAL)
      0x4000-0x7FFF: Local variables (VAR_LOCAL), index = var_id & 0x3FFF
      0x8000-0xFFFF: Bit variables (VAR_BIT), index = var_id & 0x7FFF
    """
    if var_id & 0x8000:
        return f"BitVar[{var_id & 0x7FFF}]"
    elif var_id & 0x4000:
        return f"Local[{var_id & 0x3FFF}]"
    else:
        return f"Var[{var_id}]"


def read_p8_verbose(data, pos, opcode, bit):
    """Read getVarOrDirectByte: if flag bit set, var ref (2 bytes); else literal (1 byte)."""
    if opcode & bit:
        var_id, pos = read_u16le(data, pos)
        if var_id is None:
            return "<EOF>", pos
        return var_name(var_id), pos
    else:
        val, pos = read_u8(data, pos)
        if val is None:
            return "<EOF>", pos
        return str(val), pos


def read_p16_verbose(data, pos, opcode=0, bit=0):
    """Read getVarOrDirectWord: always 2 bytes. If flag bit set, it's a var ref;
    otherwise it's a literal word. When called without opcode/bit args, treats as literal."""
    val, pos = read_u16le(data, pos)
    if val is None:
        return "<EOF>", pos
    if bit and (opcode & bit):
        return var_name(val), pos
    return str(val), pos


def read_var_verbose(data, pos):
    """Read a variable reference (always 2 bytes, getResultPos/getVar)."""
    var_id, pos = read_u16le(data, pos)
    if var_id is None:
        return "<EOF>", pos
    return var_name(var_id), pos


def read_varargs_verbose(data, pos):
    """Read word varargs until 0xFF byte. Returns list of arg strings."""
    args = []
    while pos < len(data):
        aux = data[pos]
        pos += 1
        if aux == 0xFF:
            break
        # aux byte's bit 7 is flag for the following word param
        if aux & 0x80:
            arg, pos = read_var_verbose(data, pos)
        else:
            arg, pos = read_p16_verbose(data, pos)
        args.append(arg)
    return args, pos


def read_string_verbose(data, pos):
    """Read null-terminated string."""
    chars = []
    while pos < len(data):
        b = data[pos]
        pos += 1
        if b == 0x00:
            break
        chars.append(chr(b) if 32 <= b < 127 else f"\\x{b:02x}")
    return ''.join(chars), pos


def read_print_string_verbose(data, pos):
    """Read print string with embedded control codes."""
    chars = []
    while pos < len(data):
        b = data[pos]
        pos += 1
        if b == 0x00:
            break
        elif 0x01 <= b <= 0x08:
            var_id, pos = read_u16le(data, pos)
            chars.append(f"[ctrl{b}:{var_name(var_id)}]")
        elif b == 0xFF:
            # Embedded newline marker in some versions
            chars.append("[0xFF]")
        else:
            chars.append(chr(b) if 32 <= b < 127 else f"\\x{b:02x}")
    return ''.join(chars), pos


# ---------------------------------------------------------------------------
# Per-opcode verbose decoders
# ---------------------------------------------------------------------------

def decode_verbose(opname, opcode, data, pos):
    """Decode parameters for the given opcode starting at pos in data.
    Returns (description_string, new_pos)."""

    start = pos

    if opname == 'stopObjectCode':
        return "", pos

    if opname == 'breakHere':
        return "", pos

    if opname == 'endCutscene':
        return "", pos

    if opname == 'stopMusic':
        return "", pos

    if opname == 'jumpRelative':
        offset, pos = read_s16le(data, pos)
        target = pos + offset  # offset is relative to position AFTER the param
        return f"-> 0x{target:04X} (offset {offset:+d})", pos

    if opname == 'delay':
        if pos + 3 > len(data):
            return "<EOF>", len(data)
        val = data[pos] | (data[pos+1] << 8) | (data[pos+2] << 16)
        pos += 3
        return f"ticks={val}", pos

    if opname == 'delayVariable':
        v, pos = read_var_verbose(data, pos)
        return f"var={v}", pos

    # startScript: p8[0x80] + varargs
    if opname == 'startScript':
        script_id, pos = read_p8_verbose(data, pos, opcode, 0x80)
        args, pos = read_varargs_verbose(data, pos)
        args_str = f" args=[{', '.join(args)}]" if args else ""
        return f"script={script_id}{args_str}", pos

    # startObject: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40) + varargs
    if opname == 'startObject':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        script, pos = read_p8_verbose(data, pos, opcode, 0x40)
        args, pos = read_varargs_verbose(data, pos)
        args_str = f" args=[{', '.join(args)}]" if args else ""
        return f"obj={obj} script={script}{args_str}", pos

    # chainScript: p8[0x80] + varargs
    if opname == 'chainScript':
        script_id, pos = read_p8_verbose(data, pos, opcode, 0x80)
        args, pos = read_varargs_verbose(data, pos)
        args_str = f" args=[{', '.join(args)}]" if args else ""
        return f"script={script_id}{args_str}", pos

    # move: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80)
    if opname == 'move':
        result, pos = read_var_verbose(data, pos)
        val, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"{result} = {val}", pos

    # add/subtract/multiply/divide/and/or: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80)
    if opname in ('add', 'subtract', 'multiply', 'divide', 'and', 'or'):
        result, pos = read_var_verbose(data, pos)
        val, pos = read_p16_verbose(data, pos, opcode, 0x80)
        ops = {'add': '+=', 'subtract': '-=', 'multiply': '*=',
               'divide': '/=', 'and': '&=', 'or': '|='}
        return f"{result} {ops[opname]} {val}", pos

    # increment/decrement: result(var)
    if opname in ('increment', 'decrement'):
        result, pos = read_var_verbose(data, pos)
        op_sym = '++' if opname == 'increment' else '--'
        return f"{result}{op_sym}", pos

    # isEqual/isNotEqual/isGreater/isGreaterEqual/isLess/isLessEqual:
    # getVar(2) + getVarOrDirectWord(PARAM_1=0x80) + jump(2)
    if opname in ('isEqual', 'isNotEqual', 'isGreater', 'isGreaterEqual',
                   'isLess', 'isLessEqual'):
        var, pos = read_var_verbose(data, pos)
        val, pos = read_p16_verbose(data, pos, opcode, 0x80)
        offset, pos = read_s16le(data, pos)
        target = pos + offset
        cmp_ops = {'isEqual': '==', 'isNotEqual': '!=', 'isGreater': '>',
                   'isGreaterEqual': '>=', 'isLess': '<', 'isLessEqual': '<='}
        return f"unless ({var} {cmp_ops[opname]} {val}) goto 0x{target:04X}", pos

    # equalZero/notEqualZero: var(2) + jump(2)
    if opname in ('equalZero', 'notEqualZero'):
        var, pos = read_var_verbose(data, pos)
        offset, pos = read_s16le(data, pos)
        target = pos + offset
        cmp = '== 0' if opname == 'equalZero' else '!= 0'
        return f"unless ({var} {cmp}) goto 0x{target:04X}", pos

    # loadRoom: p8[0x80]
    if opname == 'loadRoom':
        room, pos = read_p8_verbose(data, pos, opcode, 0x80)
        return f"room={room}", pos

    # startMusic/startSound/stopSound/stopScript/freezeScripts: p8[0x80]
    if opname in ('startMusic', 'startSound', 'stopSound', 'stopScript', 'freezeScripts'):
        val, pos = read_p8_verbose(data, pos, opcode, 0x80)
        return f"param={val}", pos

    # putActor: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40) + getVarOrDirectWord(PARAM_3=0x20)
    if opname == 'putActor':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        x, pos = read_p16_verbose(data, pos, opcode, 0x40)
        y, pos = read_p16_verbose(data, pos, opcode, 0x20)
        return f"actor={actor} x={x} y={y}", pos

    # walkActorTo: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40) + getVarOrDirectWord(PARAM_3=0x20)
    if opname == 'walkActorTo':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        x, pos = read_p16_verbose(data, pos, opcode, 0x40)
        y, pos = read_p16_verbose(data, pos, opcode, 0x20)
        return f"actor={actor} x={x} y={y}", pos

    # faceActor: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'faceActor':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        dir, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"actor={actor} dir={dir}", pos

    # animateActor: p8[0x80] + p8[0x40]
    if opname == 'animateActor':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        anim, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"actor={actor} anim={anim}", pos

    # setState: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40)
    # The word param flag uses bit 0x80, but word is always 2 bytes regardless.
    # The byte param flag uses bit 0x40.
    if opname == 'setState':
        # obj is a word: if bit 0x80 set, it's a var ref (2 bytes); if clear, literal (2 bytes)
        if opcode & 0x80:
            obj, pos = read_var_verbose(data, pos)
        else:
            obj, pos = read_p16_verbose(data, pos)
        state, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"obj={obj} state={state}", pos

    # panCameraTo / setCameraAt: getVarOrDirectWord(PARAM_1=0x80)
    if opname in ('panCameraTo', 'setCameraAt'):
        val, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"x={val}", pos

    # print: p8[0x80] (actor) + sub-opcode sequence
    if opname == 'print':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        subs = []
        while pos < len(data):
            sub = data[pos]
            pos += 1
            if sub == 0xFF:
                break
            key = sub & 0x0F
            if key == 0x00:
                x, pos = read_p16_verbose(data, pos)
                y, pos = read_p16_verbose(data, pos)
                subs.append(f"pos({x},{y})")
            elif key == 0x01:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"color({c})")
            elif key == 0x02:
                r, pos = read_p16_verbose(data, pos)
                subs.append(f"clipping({r})")
            elif key == 0x0F:
                text, pos = read_print_string_verbose(data, pos)
                subs.append(f'"{text}"')
            else:
                subs.append(f"sub_0x{sub:02X}")
        return f"actor={actor} {' '.join(subs)}", pos

    # printEgo: sub-opcode sequence (no actor param)
    if opname == 'printEgo':
        subs = []
        while pos < len(data):
            sub = data[pos]
            pos += 1
            if sub == 0xFF:
                break
            key = sub & 0x0F
            if key == 0x0F:
                text, pos = read_print_string_verbose(data, pos)
                subs.append(f'"{text}"')
            else:
                subs.append(f"sub_0x{sub:02X}")
        return ' '.join(subs), pos

    # getActorRoom / getActorElevation / getRandomNr / getActorCostume
    # getActorWalkBox / getActorMoving / getActorFacing / getActorWidth
    # getScriptRunning / isScriptRunning / isSoundRunning / getInventoryCount
    # getActorScale / getStringWidth: result(2) + p8[0x80]
    if opname in ('getActorRoom', 'getActorElevation', 'getRandomNr',
                   'getActorCostume', 'getActorWalkBox', 'getActorMoving',
                   'getActorFacing', 'getActorWidth', 'getScriptRunning',
                   'isScriptRunning', 'isSoundRunning', 'getInventoryCount',
                   'getActorScale', 'getStringWidth', 'getAnimCounter'):
        result, pos = read_var_verbose(data, pos)
        param, pos = read_p8_verbose(data, pos, opcode, 0x80)
        return f"{result} = {opname}({param})", pos

    # getObjectState / getObjectOwner / getClosestObjActor: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80)
    if opname in ('getObjectState', 'getObjectOwner', 'getClosestObjActor'):
        result, pos = read_var_verbose(data, pos)
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"{result} = {opname}({obj})", pos

    # getActorX / getActorY: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80)
    if opname in ('getActorX', 'getActorY'):
        result, pos = read_var_verbose(data, pos)
        actor, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"{result} = {opname}({actor})", pos

    # setOwnerOf: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40)
    if opname == 'setOwnerOf':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        owner, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"obj={obj} owner={owner}", pos

    # putActorAtObject: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'putActorAtObject':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        obj, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"actor={actor} obj={obj}", pos

    # putActorInRoom: p8[0x80] + p8[0x40]
    if opname == 'putActorInRoom':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        room, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"actor={actor} room={room}", pos

    # walkActorToActor: p8[0x80] + p8[0x40] + byte
    if opname == 'walkActorToActor':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        target, pos = read_p8_verbose(data, pos, opcode, 0x40)
        dist, pos = read_u8(data, pos)
        return f"actor={actor} toActor={target} dist={dist}", pos

    # walkActorToObject: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'walkActorToObject':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        obj, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"actor={actor} obj={obj}", pos

    # loadRoomWithEgo: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40) + word + word
    if opname == 'loadRoomWithEgo':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        room, pos = read_p8_verbose(data, pos, opcode, 0x40)
        x, pos = read_s16le(data, pos)
        y, pos = read_s16le(data, pos)
        return f"obj={obj} room={room} x={x} y={y}", pos

    # cutscene: varargs
    if opname == 'cutscene':
        args, pos = read_varargs_verbose(data, pos)
        return f"args=[{', '.join(args)}]", pos

    # override: byte flag, then if nonzero: jump opcode + offset
    if opname == 'override':
        flag, pos = read_u8(data, pos)
        if flag != 0:
            jmp_op, pos = read_u8(data, pos)
            offset, pos = read_s16le(data, pos)
            target = pos + offset
            return f"begin -> 0x{target:04X}", pos
        return "end", pos

    # setVarRange: result(2) + count(1) + N values
    if opname == 'setVarRange':
        result, pos = read_var_verbose(data, pos)
        count, pos = read_u8(data, pos)
        vals = []
        if opcode & 0x80:
            for _ in range(count):
                v, pos = read_u16le(data, pos)
                vals.append(str(v))
        else:
            for _ in range(count):
                v, pos = read_u8(data, pos)
                vals.append(str(v))
        return f"{result} count={count} vals=[{', '.join(vals)}]", pos

    # expression: result(2) + sub-opcode sequence until 0xFF
    if opname == 'expression':
        result, pos = read_var_verbose(data, pos)
        expr_parts = []
        while pos < len(data):
            sub = data[pos]
            pos += 1
            if sub == 0xFF:
                break
            key = sub & 0x1F
            if key == 0x01:
                val, pos = read_p16_verbose(data, pos)
                expr_parts.append(f"push({val})")
            elif key == 0x02:
                expr_parts.append("add")
            elif key == 0x03:
                expr_parts.append("sub")
            elif key == 0x04:
                expr_parts.append("mul")
            elif key == 0x05:
                expr_parts.append("div")
            else:
                expr_parts.append(f"op_{key}")
        return f"{result} = {' '.join(expr_parts)}", pos

    # isActorInBox: p8[0x80] + p8[0x40] + jump(2)
    if opname == 'isActorInBox':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        box, pos = read_p8_verbose(data, pos, opcode, 0x40)
        offset, pos = read_s16le(data, pos)
        target = pos + offset
        return f"actor={actor} box={box} -> 0x{target:04X}", pos

    # ifState/ifNotState: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40) + jump(2)
    if opname in ('ifState', 'ifNotState'):
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        state, pos = read_p8_verbose(data, pos, opcode, 0x40)
        offset, pos = read_s16le(data, pos)
        target = pos + offset
        return f"obj={obj} state={state} -> 0x{target:04X}", pos

    # getVerbEntrypoint: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'getVerbEntrypoint':
        result, pos = read_var_verbose(data, pos)
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        verb, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"{result} = getVerbEntrypoint(obj={obj}, verb={verb})", pos

    # setObjectName: getVarOrDirectWord(PARAM_1=0x80) + string
    if opname == 'setObjectName':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        name, pos = read_string_verbose(data, pos)
        return f'obj={obj} name="{name}"', pos

    # setClass: getVarOrDirectWord(PARAM_1=0x80) + varargs
    if opname == 'setClass':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        args, pos = read_varargs_verbose(data, pos)
        return f"obj={obj} classes=[{', '.join(args)}]", pos

    # ifClassOfIs: getVarOrDirectWord(PARAM_1=0x80) + varargs + jump(2)
    if opname == 'ifClassOfIs':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        args, pos = read_varargs_verbose(data, pos)
        offset, pos = read_s16le(data, pos)
        target = pos + offset
        return f"obj={obj} classes=[{', '.join(args)}] -> 0x{target:04X}", pos

    # pickupObject: getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectByte(PARAM_2=0x40)
    if opname == 'pickupObject':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        room, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"obj={obj} room={room}", pos

    # pickupObjectOld: getVarOrDirectWord(PARAM_1=0x80)
    if opname == 'pickupObjectOld':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"obj={obj}", pos

    # findObject: result(2) + p8[0x80] + p8[0x40]
    if opname == 'findObject':
        result, pos = read_var_verbose(data, pos)
        x, pos = read_p8_verbose(data, pos, opcode, 0x80)
        y, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"{result} = findObject({x}, {y})", pos

    # findInventory: result(2) + p8[0x80] + p8[0x40]
    if opname == 'findInventory':
        result, pos = read_var_verbose(data, pos)
        owner, pos = read_p8_verbose(data, pos, opcode, 0x80)
        idx, pos = read_p8_verbose(data, pos, opcode, 0x40)
        return f"{result} = findInventory({owner}, {idx})", pos

    # getDist: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'getDist':
        result, pos = read_var_verbose(data, pos)
        a, pos = read_p16_verbose(data, pos, opcode, 0x80)
        b, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"{result} = getDist({a}, {b})", pos

    # actorFromPos: getResultPos(2) + getVarOrDirectWord(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40)
    if opname == 'actorFromPos':
        result, pos = read_var_verbose(data, pos)
        x, pos = read_p16_verbose(data, pos, opcode, 0x80)
        y, pos = read_p16_verbose(data, pos, opcode, 0x40)
        return f"{result} = actorFromPos({x}, {y})", pos

    # lights: p8[0x80] + byte + byte
    if opname == 'lights':
        val, pos = read_p8_verbose(data, pos, opcode, 0x80)
        a, pos = read_u8(data, pos)
        b, pos = read_u8(data, pos)
        return f"val={val} a={a} b={b}", pos

    # doSentence: getVarOrDirectByte(PARAM_1=0x80) + getVarOrDirectWord(PARAM_2=0x40) + getVarOrDirectWord(PARAM_3=0x20)
    if opname == 'doSentence':
        if opcode & 0x80:
            # Var ref for verb
            verb, pos = read_var_verbose(data, pos)
            obj1, pos = read_p16_verbose(data, pos, opcode, 0x40)
            obj2, pos = read_p16_verbose(data, pos, opcode, 0x20)
            return f"verb={verb} obj1={obj1} obj2={obj2}", pos
        else:
            verb_val, pos = read_u8(data, pos)
            if verb_val in (0xFE, 0xFC):
                return f"verb=0x{verb_val:02X} (stop)", pos
            obj1, pos = read_p16_verbose(data, pos, opcode, 0x40)
            obj2, pos = read_p16_verbose(data, pos, opcode, 0x20)
            return f"verb={verb_val} obj1={obj1} obj2={obj2}", pos

    # actorOps: p8[0x80] (actor) + sub-ops until 0xFF
    if opname == 'actorOps':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        subs = []
        while pos < len(data):
            sub = data[pos]
            pos += 1
            if sub == 0xFF:
                break
            key = sub & 0x1F
            if key == 0x01:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"costume({c})")
            elif key == 0x02:
                xs, pos = read_p8_verbose(data, pos, sub, 0x80)
                ys, pos = read_p8_verbose(data, pos, sub, 0x40)
                subs.append(f"walkSpeed({xs},{ys})")
            elif key == 0x03:
                snd, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"sound({snd})")
            elif key == 0x04:
                f, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"walkFrame({f})")
            elif key == 0x05:
                a, pos = read_p8_verbose(data, pos, sub, 0x80)
                b, pos = read_p8_verbose(data, pos, sub, 0x40)
                subs.append(f"talkFrame({a},{b})")
            elif key == 0x06:
                f, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"standFrame({f})")
            elif key == 0x07:
                a, pos = read_p8_verbose(data, pos, sub, 0x80)
                b, pos = read_p8_verbose(data, pos, sub, 0x40)
                c, pos = read_p8_verbose(data, pos, sub, 0x20)
                subs.append(f"palette({a},{b},{c})")
            elif key == 0x08:
                subs.append("init")
            elif key == 0x09:
                e, pos = read_p16_verbose(data, pos)
                subs.append(f"elevation({e})")
            elif key == 0x0A:
                subs.append("animDefaults")
            elif key == 0x0B:
                idx, pos = read_p8_verbose(data, pos, sub, 0x80)
                val, pos = read_p8_verbose(data, pos, sub, 0x40)
                subs.append(f"palette({idx},{val})")
            elif key == 0x0C:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"talkColor({c})")
            elif key == 0x0D:
                name, pos = read_string_verbose(data, pos)
                subs.append(f'name("{name}")')
            elif key == 0x0E:
                f, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"initFrame({f})")
            elif key == 0x10:
                w, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"width({w})")
            elif key == 0x11:
                sx, pos = read_p8_verbose(data, pos, sub, 0x80)
                sy, pos = read_p8_verbose(data, pos, sub, 0x40)
                subs.append(f"scale({sx},{sy})")
            elif key == 0x12:
                subs.append("neverZClip")
            elif key == 0x13:
                cl, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"setZClip({cl})")
            elif key == 0x14:
                subs.append("ignoreBoxes")
            elif key == 0x15:
                subs.append("followBoxes")
            elif key == 0x16:
                sp, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"animSpeed({sp})")
            elif key == 0x17:
                sh, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"shadow({sh})")
            else:
                subs.append(f"sub_0x{sub:02X}")
        return f"actor={actor} {' '.join(subs)}", pos

    # verbOps: p8[0x80] (verb) + sub-ops until 0xFF
    if opname == 'verbOps':
        verb, pos = read_p8_verbose(data, pos, opcode, 0x80)
        subs = []
        while pos < len(data):
            sub = data[pos]
            pos += 1
            if sub == 0xFF:
                break
            key = sub & 0x1F
            if key == 0x01:
                img, pos = read_p16_verbose(data, pos)
                subs.append(f"image({img})")
            elif key == 0x02:
                name, pos = read_string_verbose(data, pos)
                subs.append(f'name("{name}")')
            elif key == 0x03:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"color({c})")
            elif key == 0x04:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"hiColor({c})")
            elif key == 0x05:
                x, pos = read_p16_verbose(data, pos)
                y, pos = read_p16_verbose(data, pos)
                subs.append(f"setXY({x},{y})")
            elif key == 0x06:
                subs.append("on")
            elif key == 0x07:
                subs.append("off")
            elif key == 0x08:
                subs.append("delete")
            elif key == 0x09:
                subs.append("new")
            elif key == 0x10:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"dimColor({c})")
            elif key == 0x12:
                k, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"key({k})")
            elif key == 0x13:
                subs.append("center")
            elif key == 0x14:
                s, pos = read_p16_verbose(data, pos)
                subs.append(f"setToString({s})")
            elif key == 0x16:
                o, pos = read_p16_verbose(data, pos)
                r, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"setToObject({o},{r})")
            elif key == 0x17:
                c, pos = read_p8_verbose(data, pos, sub, 0x80)
                subs.append(f"backColor({c})")
            else:
                subs.append(f"sub_0x{sub:02X}")
        return f"verb={verb} {' '.join(subs)}", pos

    # roomOps: sub-opcode + params
    if opname == 'roomOps':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key == 0x01:
            minx, pos = read_p16_verbose(data, pos)
            maxx, pos = read_p16_verbose(data, pos)
            return f"scroll min={minx} max={maxx}", pos
        elif key == 0x03:
            b, pos = read_p16_verbose(data, pos)
            h, pos = read_p16_verbose(data, pos)
            return f"setScreen b={b} h={h}", pos
        elif key == 0x04:
            r, pos = read_p16_verbose(data, pos)
            g, pos = read_p16_verbose(data, pos)
            bl, pos = read_p16_verbose(data, pos)
            idx, pos = read_p8_verbose(data, pos, sub, 0x80)
            return f"setPalColor r={r} g={g} b={bl} idx={idx}", pos
        elif key == 0x05:
            return "shakeOn", pos
        elif key == 0x06:
            return "shakeOff", pos
        elif key == 0x0A:
            eff, pos = read_p16_verbose(data, pos)
            return f"screenEffect({eff})", pos
        else:
            return f"sub_0x{sub:02X} (raw)", pos

    # resourceRoutines
    if opname == 'resourceRoutines':
        sub, pos = read_u8(data, pos)
        key = sub & 0x3F
        if key in range(0x01, 0x14) or key in (0x20, 0x21, 0x22, 0x23, 0x24, 0x25):
            res, pos = read_p8_verbose(data, pos, sub, 0x80)
            labels = {1:'loadScript', 2:'loadSound', 3:'loadCostume', 4:'loadRoom',
                      5:'nukeScript', 6:'nukeSound', 7:'nukeCostume', 8:'nukeRoom',
                      9:'lockScript', 10:'lockSound', 11:'lockCostume', 12:'lockRoom',
                      13:'unlockScript', 14:'unlockSound', 15:'unlockCostume', 16:'unlockRoom',
                      0x11:'clearHeap', 0x12:'loadCharset', 0x13:'nukeCharset',
                      0x14:'loadFlObject'}
            lbl = labels.get(key, f"op_{key}")
            return f"{lbl}({res})", pos
        elif key == 0x11:
            return "clearHeap", pos
        elif key == 0x14:
            room, pos = read_p8_verbose(data, pos, sub, 0x80)
            obj, pos = read_p16_verbose(data, pos)
            return f"loadFlObject room={room} obj={obj}", pos
        return f"sub_0x{sub:02X}", pos

    # cursorCommand
    if opname == 'cursorCommand':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        labels = {1:'cursorOn', 2:'cursorOff', 3:'userPutOn', 4:'userPutOff',
                  5:'softCursorOn', 6:'softCursorOff', 7:'softUserPutOn', 8:'softUserPutOff'}
        if key in labels:
            return labels[key], pos
        elif key == 0x0A:
            cur, pos = read_p8_verbose(data, pos, sub, 0x80)
            ch, pos = read_p8_verbose(data, pos, sub, 0x40)
            return f"setCursorImg({cur}, {ch})", pos
        elif key == 0x0D:
            cs, pos = read_p8_verbose(data, pos, sub, 0x80)
            return f"initCharset({cs})", pos
        return f"sub_0x{sub:02X}", pos

    # saveRestoreVerbs
    if opname == 'saveRestoreVerbs':
        sub, pos = read_u8(data, pos)
        a, pos = read_p8_verbose(data, pos, sub, 0x80)
        b, pos = read_p8_verbose(data, pos, sub, 0x40)
        c, pos = read_p8_verbose(data, pos, sub, 0x20)
        labels = {1:'save', 2:'restore', 3:'delete'}
        lbl = labels.get(sub & 0x1F, f"sub_{sub}")
        return f"{lbl}({a}, {b}, {c})", pos

    # wait
    if opname == 'wait':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key == 0x01:
            actor, pos = read_p8_verbose(data, pos, sub, 0x80)
            offset, pos = read_s16le(data, pos)
            target = pos + offset
            return f"waitForActor({actor}) -> 0x{target:04X}", pos
        elif key == 0x02:
            return "waitForMessage", pos
        elif key == 0x03:
            return "waitForCamera", pos
        elif key == 0x04:
            return "waitForSentence", pos
        return f"sub_0x{sub:02X}", pos

    # matrixOps
    if opname == 'matrixOps':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key in (1, 2, 3):
            box, pos = read_p8_verbose(data, pos, sub, 0x80)
            val, pos = read_p8_verbose(data, pos, sub, 0x40)
            labels = {1:'setBoxFlags', 2:'setBoxScale', 3:'setBoxSlot'}
            return f"{labels[key]}({box}, {val})", pos
        elif key == 4:
            return "createBoxMatrix", pos
        return f"sub_0x{sub:02X}", pos

    # drawObject: p16 + sub
    if opname == 'drawObject':
        obj, pos = read_p16_verbose(data, pos)
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key == 0x01:
            x, pos = read_p16_verbose(data, pos)
            y, pos = read_p16_verbose(data, pos)
            return f"obj={obj} setXY({x},{y})", pos
        elif key == 0x02:
            st, pos = read_p16_verbose(data, pos)
            return f"obj={obj} setState({st})", pos
        elif key == 0x1F:
            return f"obj={obj} draw", pos
        return f"obj={obj} sub_0x{sub:02X}", pos

    # drawBox: p16 + p16 + aux + p16 + p16 + p8
    if opname == 'drawBox':
        x1, pos = read_p16_verbose(data, pos)
        y1, pos = read_p16_verbose(data, pos)
        aux, pos = read_u8(data, pos)
        x2, pos = read_p16_verbose(data, pos)
        y2, pos = read_p16_verbose(data, pos)
        color, pos = read_p8_verbose(data, pos, aux, 0x80)
        return f"({x1},{y1})-({x2},{y2}) color={color}", pos

    # actorFollowCamera: p8[0x80]
    if opname == 'actorFollowCamera':
        actor, pos = read_p8_verbose(data, pos, opcode, 0x80)
        return f"actor={actor}", pos

    # stringOps
    if opname == 'stringOps':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key == 0x01:
            sid, pos = read_p8_verbose(data, pos, sub, 0x80)
            s, pos = read_string_verbose(data, pos)
            return f'putCodeInString({sid}, "{s}")', pos
        elif key == 0x02:
            d, pos = read_p8_verbose(data, pos, sub, 0x80)
            sr, pos = read_p8_verbose(data, pos, sub, 0x40)
            return f'copyString({d}, {sr})', pos
        return f"sub_0x{sub:02X}", pos

    # soundKludge: varargs
    if opname == 'soundKludge':
        args, pos = read_varargs_verbose(data, pos)
        return f"args=[{', '.join(args)}]", pos

    # oldRoomEffect
    if opname == 'oldRoomEffect':
        sub, pos = read_u8(data, pos)
        key = sub & 0x1F
        if key == 0x03:
            val, pos = read_p16_verbose(data, pos)
            return f"setScreen({val})", pos
        return f"sub_0x{sub:02X}", pos

    # pseudoRoom
    if opname == 'pseudoRoom':
        pairs = []
        while pos < len(data):
            b, pos = read_u8(data, pos)
            if b == 0:
                break
            r, pos = read_u8(data, pos)
            pairs.append(f"{b}->{r}")
        return ' '.join(pairs), pos

    # stopObjectScript: getVarOrDirectWord(PARAM_1=0x80)
    if opname == 'stopObjectScript':
        obj, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"obj={obj}", pos

    # debug: getVarOrDirectWord(PARAM_1=0x80)
    if opname == 'debug':
        val, pos = read_p16_verbose(data, pos, opcode, 0x80)
        return f"param={val}", pos

    # systemOps / dummy: no params
    if opname in ('systemOps', 'dummy'):
        return "", pos

    # --- fallback: use the byte-counting decoder to advance pos ---
    stream = BytesIO(data[pos:])
    consumed = BASE_OPCODES[opname](opcode, stream)
    pos += consumed
    return f"(raw {consumed} bytes)", pos


# ---------------------------------------------------------------------------
# Main disassembler
# ---------------------------------------------------------------------------

def disassemble(data):
    """Disassemble SCUMM v5 bytecode."""
    pos = 0
    lines = []
    while pos < len(data):
        offset = pos
        opcode = data[pos]
        pos += 1
        opname = OPCODE_MAP[opcode]

        # Get raw bytes for hex dump (we'll figure out length after decode)
        old_pos = pos
        desc, pos = decode_verbose(opname, opcode, data, pos)
        param_bytes = data[old_pos:pos]

        # Build hex string
        all_bytes = bytes([opcode]) + param_bytes
        hex_str = ' '.join(f'{b:02X}' for b in all_bytes)

        line = f"  0x{offset:04X}  [{hex_str:<40s}]  {opname}"
        if desc:
            line += f"  {desc}"
        lines.append(line)

    return lines


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <script.bin>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    data = path.read_bytes()
    print(f"=== SCUMM v5 Disassembly: {path.name} ({len(data)} bytes) ===\n")

    lines = disassemble(data)
    for line in lines:
        print(line)

    # Summary: find startScript calls
    print(f"\n=== startScript calls ===")
    pos = 0
    while pos < len(data):
        offset = pos
        opcode = data[pos]
        pos += 1
        opname = OPCODE_MAP[opcode]
        if opname == 'startScript':
            desc, new_pos = decode_verbose(opname, opcode, data, pos)
            print(f"  0x{offset:04X}  ${opcode:02X}  {desc}")
            pos = new_pos
        else:
            # Skip params using the counting decoder
            stream = BytesIO(data[pos:])
            consumed = BASE_OPCODES[opname](opcode, stream)
            pos += consumed


if __name__ == '__main__':
    main()
