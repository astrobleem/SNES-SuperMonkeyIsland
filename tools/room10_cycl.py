#!/usr/bin/env python3
"""Find room 10 in MI1 talkie resource and extract its CYCL chunk."""
import struct

with open('data/monkeypacks/talkie/monkey.001', 'rb') as f:
    raw = f.read()
data = bytes(b ^ 0x69 for b in raw)

# Walk LFLF chunks. Each LFLF has header + body containing RO (room), then ROL/SO subchunks.
# LFLF header: 4-byte "LFLF" + 4-byte BE size
# Inside, first chunk is usually ROOM (RO) for v5+ or similar.
# For MI1 v5: LFLF > ROOM > {RMHD, CYCL, TRNS, EPAL, BOXD, BOXM, CLUT, SCAL, RMIM, OBIM, OBCD, EXCD, ENCD, NLSC, LSCR}
# Room ID from RMHD.

def walk(data, offset=0, depth=0):
    pos = offset
    while pos < len(data) - 8:
        chunk_type = data[pos:pos+4]
        if not all(32 <= b < 127 for b in chunk_type):
            return
        size = struct.unpack('>I', data[pos+4:pos+8])[0]
        if size < 8 or pos + size > len(data):
            return
        yield depth, chunk_type.decode('ascii', errors='replace'), pos, size
        # Recurse into container chunks
        if chunk_type in (b'LFLF', b'LECF', b'LOFF', b'ROOM', b'RO  '):
            yield from walk(data, pos + 8, depth + 1)
        pos += size

rooms_with_cycl = {}
current_room = None
for depth, ctype, pos, size in walk(data):
    if ctype == 'LFLF':
        current_room = None
    elif ctype == 'RMHD':
        # Room header: width (word), height (word), num_objects (word)
        w, h, n = struct.unpack('<HHH', data[pos+8:pos+14])
        current_room = (w, h, n, pos)
    elif ctype == 'CYCL' and current_room:
        w, h, n, rpos = current_room
        # Find room ID via ROxx - actually in v5 it's embedded in LOFF table. Use dimensions+position as proxy.
        if size > 10:
            body = data[pos+8:pos+size]
            rooms_with_cycl[pos] = (w, h, n, body)

print(f'Rooms with non-empty CYCL: {len(rooms_with_cycl)}')
for pos, (w, h, n, body) in rooms_with_cycl.items():
    hx = ' '.join(f'{b:02x}' for b in body)
    print(f'  Room @ CYCL offset 0x{pos:x}: {w}x{h}, {n} objects, body: {hx}')

# Specifically: room 10 is 640x200 per our metadata
print('\n--- Rooms matching 640x200 (room 10 candidate) ---')
for depth, ctype, pos, size in walk(data):
    if ctype == 'RMHD':
        w, h, n = struct.unpack('<HHH', data[pos+8:pos+14])
        if w == 640 and h == 200:
            print(f'RMHD @ 0x{pos:x}: {w}x{h}, {n} objects')
