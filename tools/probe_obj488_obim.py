#!/usr/bin/env python3
"""Probe obj 488's OBIM data in room 38's LFLF. Our metadata extractor only
captures IMHD; for multi-state objects the actual per-state images live in
IM01/IM02/... sub-chunks which we haven't examined."""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scumm.chunks import iter_chunks, read_encrypted_file

# Load the entire data file and walk to room 38.
# monkey.001 is XOR-encrypted (decrypted by read_encrypted_file).
data = read_encrypted_file('data/monkeypacks/talkie/monkey.001')
print(f'monkey.001 decrypted: {len(data)} bytes')

# Walk top-level LECF -> LFLF*
lecf = None
for chunk in iter_chunks(data):
    if chunk.tag == 'LECF':
        lecf = chunk
        break

if lecf is None:
    print('LECF not found')
    sys.exit(1)

# Inside LECF: LOFF index + LFLF per room
print(f'LECF at offset 0x{lecf.offset:08x}, size {lecf.size}')
sub_chunks = iter_chunks(lecf.data)
loff = None
lflfs = []
for sub in sub_chunks:
    if sub.tag == 'LOFF':
        loff = sub
    elif sub.tag == 'LFLF':
        lflfs.append(sub)

print(f'LFLFs found: {len(lflfs)}')

# Find room 38's LFLF. LOFF maps room_id -> offset within LECF.data
# Format: 1 byte count, then count * (1-byte room_id + 4-byte LE offset)
if loff:
    n = loff.data[0]
    pos = 1
    room_lflf_ofs = {}
    for _ in range(n):
        rid = loff.data[pos]
        ofs = struct.unpack_from('<I', loff.data, pos + 1)[0]
        room_lflf_ofs[rid] = ofs
        pos += 5
    print(f'LOFF table has {n} rooms')
    if 38 in room_lflf_ofs:
        ofs_in_lecf_file = room_lflf_ofs[38]  # offset from start of LECF in the FILE
        print(f'Room 38 LFLF offset (file-abs): 0x{ofs_in_lecf_file:08x}')
        # LOFF stores FILE-absolute offsets (pointing at the LFLF chunk header).
        # Each LFLF has a 4-byte BE size + 4-byte tag header, then sub-chunks.
        # chunks are parsed via read_chunk which expects tag(4) + size(BE I).
        # Actually our iter_chunks parses header as '4-char tag + BE uint32 size'
        # Let me try parsing from that absolute offset.
        room_header_tag = data[ofs_in_lecf_file:ofs_in_lecf_file + 4].decode('ascii', errors='replace')
        room_header_size = struct.unpack_from('>I', data, ofs_in_lecf_file + 4)[0]
        print(f'At offset: tag={room_header_tag!r}, size=0x{room_header_size:x}')
        # Actually in SCUMM, LFLF uses the payload immediately after tag+size.
        # Walk sub-chunks
        room_start = ofs_in_lecf_file + 8
        room_end = ofs_in_lecf_file + room_header_size
        print(f'Sub-chunks of LFLF (rooms 38 body, 0x{room_start:x}-0x{room_end:x}):')
        for sub in iter_chunks(data, room_start, room_end):
            print(f'  {sub.tag} at 0x{sub.offset:08x} size=0x{sub.size:x}')
            # Look for ROOM chunk (which contains OBIM)
            if sub.tag == 'ROOM':
                print(f'  ROOM body sub-chunks:')
                for s2 in iter_chunks(data, sub.offset + 8, sub.offset + sub.size):
                    print(f'    {s2.tag} at 0x{s2.offset:08x} size=0x{s2.size:x}')
                    # OBIMs are here
                    if s2.tag == 'OBIM':
                        for s3 in iter_chunks(data, s2.offset + 8, s2.offset + s2.size):
                            # Peek IMHD to get obj_id
                            peek = ''
                            if s3.tag == 'IMHD' and len(s3.data) >= 2:
                                obj_id = struct.unpack_from('<H', s3.data, 0)[0]
                                peek = f' obj_id={obj_id}'
                                if obj_id == 488:
                                    print(f'    OBIM contains obj 488!')
                                    for s4 in iter_chunks(data, s2.offset + 8, s2.offset + s2.size):
                                        print(f'      {s4.tag} at 0x{s4.offset:08x} size=0x{s4.size:x}')
                                        if s4.tag == 'IMHD':
                                            hdr = s4.data[:32]
                                            oid = struct.unpack_from('<H', s4.data, 0)[0]
                                            n_images = struct.unpack_from('<H', s4.data, 2)[0]
                                            n_zplanes = struct.unpack_from('<H', s4.data, 4)[0]
                                            flags = s4.data[6]
                                            print(f'         IMHD: obj={oid} n_images={n_images} n_zplanes={n_zplanes} flags=0x{flags:02x}')
                                            print(f'         raw: {hdr.hex(" ")}')
                            print(f'      {s3.tag} at 0x{s3.offset:08x} size=0x{s3.size:x}{peek}')
                        break
