#!/usr/bin/env python3
"""
Generate 256-entry SCUMM v5 opcode dispatch table for 65816.

Reads the OPCODE_MAP from tools/scumm/opcodes_v5.py and generates
a .dw table where each entry points to the handler label for that
opcode byte. Unimplemented opcodes point to op_stub.

Output: src/object/scummvm/scummvm_dispatch_table.inc
"""

import sys
from pathlib import Path

# Add tools/ to path so we can import the SCUMM module
sys.path.insert(0, str(Path(__file__).parent))

from scumm.opcodes_v5 import OPCODE_MAP

# Opcodes with real handlers in scummvm.65816
# Maps base opcode name -> handler label
IMPLEMENTED = {
    # Tier 1 — Boot critical
    'stopObjectCode':   'op_stopObjectCode',
    'breakHere':        'op_breakHere',
    'jumpRelative':     'op_jumpRelative',
    'move':             'op_move',
    'startScript':      'op_startScript',
    'stopScript':       'op_stopScript',
    # Tier 2 — Conditionals
    'isEqual':          'op_isEqual',
    'notEqualZero':     'op_notEqualZero',
    'equalZero':        'op_equalZero',
    'isNotEqual':       'op_isNotEqual',
    'isGreater':        'op_isGreater',
    'isLess':           'op_isLess',
    'isLessEqual':      'op_isLessEqual',
    'isGreaterEqual':   'op_isGreaterEqual',
    # Tier 3 — Arithmetic
    'add':              'op_add',
    'subtract':         'op_subtract',
    'increment':        'op_increment',
    'decrement':        'op_decrement',
    # Misc boot-path
    'setVarRange':      'op_setVarRange',
    'delay':            'op_delay',
    'delayVariable':    'op_delayVariable',
    'dummy':            'op_dummy',
    'cutscene':         'op_cutscene',
    'endCutscene':      'op_endCutscene',
    'override':         'op_override',
    'freezeScripts':    'op_freezeScripts',
    'stopObjectScript': 'op_stopObjectScript',
    'isScriptRunning':  'op_isScriptRunning',
    'debug':            'op_debug',
    'and':              'op_and',
    'or':               'op_or',
    'expression':       'op_expression',
    'chainScript':      'op_chainScript',
    'cursorCommand':    'op_cursorCommand',
    'systemOps':        'op_systemOps',
    # Actor/resource stubs (consume params, no-op for now)
    'resourceRoutines': 'op_resourceRoutines',
    'putActorInRoom':   'op_putActorInRoom',
    'getActorMoving':   'op_getActorMoving',
    'getObjectState':   'op_getObjectState',
    'getObjectOwner':   'op_getObjectOwner',
    'setState':         'op_setState',
    'putActor':         'op_putActor',
    'getActorRoom':     'op_getActorRoom',
    'panCameraTo':      'op_panCameraTo',
    'setCameraAt':      'op_setCameraAt',
    'lights':           'op_lights',
    'loadRoom':         'op_loadRoom',
    'setOwnerOf':       'op_setOwnerOf',
    'getActorX':        'op_getActorX',
    'getActorY':        'op_getActorY',
    'actorOps':         'op_actorOps',
    # Sound stubs (no-op, consume params)
    'stopSound':        'op_stopSound',
    'startSound':       'op_startSound',
    'startMusic':       'op_startMusic',
    'stopMusic':        'op_stopMusic',
    'isSoundRunning':   'op_isSoundRunning',
    'soundKludge':      'op_soundKludge',
    # Object stubs
    'setClass':         'op_setClass',
    'drawObject':       'op_drawObject',
    # Room stubs
    'matrixOps':        'op_matrixOps',
    'roomOps':          'op_roomOps',
    'loadRoomWithEgo':  'op_loadRoomWithEgo',
    # Actor stubs
    'animateActor':     'op_animateActor',
    'faceActor':        'op_faceActor',
    'walkActorToActor': 'op_walkActorToActor',
    'walkActorTo':      'op_walkActorTo',
    'isActorInBox':     'op_isActorInBox',
    # Wait stub
    'wait':             'op_wait',
    # Print/verb/sentence/object/string stubs (consume params, no-op)
    'print':            'op_print',
    'printEgo':         'op_printEgo',
    'verbOps':          'op_verbOps',
    'doSentence':       'op_doSentence',
    'startObject':      'op_startObject',
    'stringOps':        'op_stringOps',
    'saveRestoreVerbs': 'op_saveRestoreVerbs',
    # Conditional stubs
    'ifState':          'op_ifState',
    'ifNotState':       'op_ifNotState',
    # Getter stubs (result + p8[7] → return 0)
    'getActorElevation':  'op_getActorElevation',
    'getAnimCounter':     'op_getAnimCounter',
    'getActorScale':      'op_getActorScale',
    'getActorFacing':     'op_getActorFacing',
    'getActorWidth':      'op_getActorWidth',
    'getActorCostume':    'op_getActorCostume',
    'getActorWalkBox':    'op_getActorWalkBox',
    'getInventoryCount':  'op_getInventoryCount',
    'getRandomNr':        'op_getRandomNr',
    'getStringWidth':     'op_getStringWidth',
    # Getter stubs (other patterns)
    'getClosestObjActor': 'op_getClosestObjActor',
    'getDist':            'op_getDist',
    'getVerbEntrypoint':  'op_getVerbEntrypoint',
    'actorFromPos':       'op_actorFromPos',
    'findObject':         'op_findObject',
    'findInventory':      'op_findInventory',
    # Actor action stubs
    'actorFollowCamera':  'op_actorFollowCamera',
    'putActorAtObject':   'op_putActorAtObject',
    'walkActorToObject':  'op_walkActorToObject',
    'pickupObject':       'op_pickupObject',
    'pickupObjectOld':    'op_pickupObjectOld',
    # Arithmetic stubs
    'multiply':           'op_multiply',
    'divide':             'op_divide',
    # Complex stubs
    'ifClassOfIs':        'op_ifClassOfIs',
    'setObjectName':      'op_setObjectName',
    'drawBox':            'op_drawBox',
    'oldRoomEffect':      'op_oldRoomEffect',
    'pseudoRoom':         'op_pseudoRoom',
}


def generate_table(outpath: Path):
    lines = []
    lines.append('; Auto-generated by tools/gen_dispatch_table.py')
    lines.append('; 256-entry SCUMM v5 opcode dispatch table')
    lines.append('; Each .dw entry is a handler address for jsr (table,x)')
    lines.append('')
    lines.append('_scummvm.dispatchTable:')

    for i in range(256):
        name = OPCODE_MAP[i]
        handler = IMPLEMENTED.get(name, 'op_stub')
        lines.append(f'  .dw {handler:<28s} ; ${i:02X} {name}')

    lines.append('')

    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"Generated {outpath} ({len(lines)} lines, 256 entries)")

    # Stats
    impl_set = set()
    stub_count = 0
    for i in range(256):
        name = OPCODE_MAP[i]
        if name in IMPLEMENTED:
            impl_set.add(name)
        else:
            stub_count += 1
    print(f"  Implemented base opcodes: {len(impl_set)}")
    print(f"  Stub entries: {stub_count}/256")


if __name__ == '__main__':
    project_root = Path(__file__).parent.parent
    outpath = project_root / 'src' / 'object' / 'scummvm' / 'scummvm_dispatch_table.inc'
    generate_table(outpath)
