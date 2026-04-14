#!/usr/bin/env python3
"""Generate object-to-room mapping table for loadRoomWithEgo room=0 lookup."""
import json
import os
import sys

def main():
    rooms_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'scumm_extracted', 'rooms')
    out_path = os.path.join(os.path.dirname(__file__), '..', 'build', 'obj_room_table.inc')

    obj_to_room = {}
    for d in sorted(os.listdir(rooms_dir)):
        meta_path = os.path.join(rooms_dir, d, 'metadata.json')
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            room_id = meta.get('room_id', 0)
            for obj in meta.get('objects', []):
                obj_to_room[obj['obj_id']] = room_id

    if not obj_to_room:
        print("ERROR: no objects found", file=sys.stderr)
        sys.exit(1)

    max_obj = max(obj_to_room.keys())
    table_size = max_obj + 1
    table = bytearray(table_size)
    for obj_id, room_id in obj_to_room.items():
        table[obj_id] = room_id

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write('; Auto-generated object-to-room mapping table\n')
        f.write(f'; {len(obj_to_room)} objects mapped, table size {table_size} bytes\n')
        f.write(f'.define OBJ_ROOM_TABLE_SIZE {table_size}\n')
        f.write('ObjRoomTable:\n')
        for i in range(0, table_size, 16):
            chunk = table[i:i+16]
            vals = ', '.join(f'${b:02X}' for b in chunk)
            f.write(f'  .db {vals}\n')

    print(f"obj_room_table.inc: {table_size} bytes, {len(obj_to_room)} mappings")

if __name__ == '__main__':
    main()
