"""SCUMM v5 room metadata extraction — walkboxes, scaling, objects → JSON."""

import struct
import json
import logging
from pathlib import Path
from .chunks import iter_chunks

log = logging.getLogger(__name__)


def _parse_rmhd(data: bytes) -> dict:
    """Parse RMHD: width(LE16), height(LE16), num_objects(LE16)."""
    w, h, num_obj = struct.unpack_from('<HHH', data, 0)
    return {'width': w, 'height': h, 'num_objects': num_obj}


def _parse_boxd(data: bytes) -> list:
    """Parse BOXD walkbox data.
    SCUMM v5: num_boxes(LE16), then 20 bytes per box.
    Each box: ulx, uly, urx, ury, lrx, lry, llx, lly (8 x LE16) + mask(byte) + flags(byte) + scale(LE16).
    """
    if len(data) < 2:
        return []
    num_boxes = struct.unpack_from('<H', data, 0)[0]
    boxes = []
    pos = 2
    for i in range(num_boxes):
        if pos + 20 > len(data):
            break
        vals = struct.unpack_from('<8h', data, pos)
        ulx, uly, urx, ury, lrx, lry, llx, lly = vals
        mask = data[pos + 16]
        flags = data[pos + 17]
        scale = struct.unpack_from('<H', data, pos + 18)[0]
        boxes.append({
            'index': i,
            'ul': [ulx, uly], 'ur': [urx, ury],
            'lr': [lrx, lry], 'll': [llx, lly],
            'mask': mask, 'flags': flags, 'scale': scale,
        })
        pos += 20
    return boxes


def _parse_boxm(data: bytes, num_boxes: int) -> bytes:
    """Parse BOXM compressed format into flat N*N routing matrix.

    SCUMM v5 format: rows of (first_dest, last_dest, next_hop) triples,
    each row terminated by 0xFF.  For destination boxes in [first..last],
    the next hop from this source box is next_hop.
    Output: flat N*N byte array where matrix[from * N + to] = next_hop.
    """
    matrix = bytearray([0xFF] * (num_boxes * num_boxes))
    pos = 0
    for from_box in range(num_boxes):
        while pos < len(data) and data[pos] != 0xFF:
            if pos + 2 >= len(data):
                break
            first = data[pos]
            last = data[pos + 1]
            hop = data[pos + 2]
            pos += 3
            for to_box in range(first, last + 1):
                if to_box < num_boxes:
                    matrix[from_box * num_boxes + to_box] = hop
        if pos < len(data) and data[pos] == 0xFF:
            pos += 1
    return bytes(matrix)


def export_walkbox_binary(room_resource, output_dir: Path) -> int:
    """Export walkbox data as binary .box file for SNES engine.

    Format:
        $00         num_boxes (LE16)
        $02         BOXD entries (N * 20 bytes, raw signed coords + mask/flags/scale)
        $02+N*20    flat routing matrix (N * N bytes)

    Returns number of walkboxes exported.
    """
    boxd = room_resource.get_room_sub('BOXD')
    if not boxd or len(boxd.data) < 2:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / 'walkbox.box').write_bytes(struct.pack('<H', 0))
        return 0

    num_boxes = struct.unpack_from('<H', boxd.data, 0)[0]
    if num_boxes == 0:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / 'walkbox.box').write_bytes(struct.pack('<H', 0))
        return 0

    # Raw BOXD entries (skip 2-byte count prefix)
    boxd_binary = boxd.data[2:2 + num_boxes * 20]

    # Parse BOXM routing matrix
    boxm = room_resource.get_room_sub('BOXM')
    if boxm and boxm.data:
        matrix = _parse_boxm(boxm.data, num_boxes)
    else:
        matrix = bytes([0xFF] * (num_boxes * num_boxes))

    # Parse SCAL data (4 slots x 8 bytes = 32 bytes, zero-padded if absent)
    scal_binary = bytes(32)  # default: all zeros (no scaling)
    scal_chunk = room_resource.get_room_sub('SCAL')
    if scal_chunk and len(scal_chunk.data) >= 8:
        scal_binary = scal_chunk.data[:32].ljust(32, b'\x00')

    # Build .box file: count + BOXD + matrix + SCAL(32)
    box_data = struct.pack('<H', num_boxes) + boxd_binary + matrix + scal_binary

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'walkbox.box').write_bytes(box_data)
    log.info("Room %d: exported %d walkboxes (%d bytes, +32B SCAL)",
             room_resource.room_id, num_boxes, len(box_data))

    return num_boxes


def _parse_scal(data: bytes) -> list:
    """Parse SCAL: 4 scale slots, each 8 bytes (s1 LE16, y1 LE16, s2 LE16, y2 LE16)."""
    slots = []
    for i in range(4):
        off = i * 8
        if off + 8 > len(data):
            break
        s1, y1, s2, y2 = struct.unpack_from('<HHHH', data, off)
        if s1 or y1 or s2 or y2:
            slots.append({'slot': i, 's1': s1, 'y1': y1, 's2': s2, 'y2': y2})
    return slots


def _parse_cycl(data: bytes) -> list:
    """Parse CYCL: color cycling data (SCUMM v5, non-SMALL_HEADER path).

    Each entry is 9 bytes:
        1 byte  cycle index (1..16, 0 = terminator)
        2 bytes skip/unused (ignored by ScummVM)
        2 bytes BE delay_raw — ScummVM computes delay = 16384 / delay_raw.
                For ~60 FPS, delay is effectively frames-per-step.
        2 bytes BE flags (bit 1 = reverse direction)
        1 byte  PC palette start index (inclusive)
        1 byte  PC palette end index (inclusive)

    Terminator: single 0x00 byte (index). Extra trailing bytes are padding.
    Verified against ScummVM initCycl in engines/scumm/palette.cpp.
    """
    cycles = []
    pos = 0
    while pos < len(data):
        idx = data[pos]
        pos += 1
        if idx == 0:
            break
        if idx < 1 or idx > 16 or pos + 8 > len(data):
            break
        # 2 bytes skip/unused
        pos += 2
        delay_raw = struct.unpack_from('>H', data, pos)[0]
        pos += 2
        flags = struct.unpack_from('>H', data, pos)[0]
        pos += 2
        start = data[pos]
        end = data[pos + 1]
        pos += 2
        frames_per_step = (16384 // delay_raw) if delay_raw else 0
        cycles.append({
            'index': idx,
            'delay_raw': delay_raw,
            'frames_per_step': frames_per_step,
            'flags': flags,
            'start': start,
            'end': end,
        })
    return cycles


def _parse_obcd(data: bytes) -> dict:
    """Parse OBCD chunk — object code/data.
    Contains CDHD (header), VERB (verb table), OBNA (name).
    """
    subs = iter_chunks(data)
    result = {}

    for sub in subs:
        if sub.tag == 'CDHD':
            d = sub.data
            if len(d) >= 13:
                obj_id = struct.unpack_from('<H', d, 0)[0]
                x = d[2]
                y = d[3]
                w = d[4]
                h = d[5]
                flags = d[6]
                parent = d[7]
                walk_x = struct.unpack_from('<h', d, 8)[0]
                walk_y = struct.unpack_from('<h', d, 10)[0]
                result['obj_id'] = obj_id
                result['x'] = x * 8  # SCUMM stores x in 8-pixel units
                result['y'] = y * 8 if y & 0x80 == 0 else y  # some rooms use pixel coords
                result['width'] = w * 8
                result['height'] = h & 0xF8
                result['flags'] = flags
                result['parent'] = parent
                result['walk_x'] = walk_x
                result['walk_y'] = walk_y
                result['actor_dir'] = (h & 0x07)
                # ScummVM v5: object's initial state is encoded in the
                # `flags` byte (offset 6 of CDHD). The legacy code at byte 12
                # was reading actordir, not state — produced state=3 for
                # MI1 obj 428 (SCUMM bar door) which kept it stuck closed.
                #   if (flags == 0x80) → state = 1
                #   else               → state = flags & 0x0F
                # See ScummEngine::resetRoomObjects in scummvm/object.cpp.
                if flags == 0x80:
                    result['initial_state'] = 1
                else:
                    result['initial_state'] = flags & 0x0F

        elif sub.tag == 'VERB':
            # VERB chunk: verb table entries {verb_id:u8, offset:u16} terminated
            # by verb_id=0x00, followed by object script bytecode.
            # Offsets are relative to VERB chunk data start.
            d = sub.data
            pos = 0
            verb_entries = []
            while pos < len(d):
                verb_id = d[pos]
                pos += 1
                if verb_id == 0:
                    break
                offset = struct.unpack_from('<H', d, pos)[0]
                pos += 2
                verb_entries.append({'verb_id': verb_id, 'offset': offset})
            if verb_entries:
                result['verb_entries'] = verb_entries
            # Store the entire VERB data blob (verb table + bytecode)
            # for packing into .obj files
            result['verb_data'] = d

        elif sub.tag == 'OBNA':
            name = sub.data.split(b'\x00')[0].decode('ascii', errors='replace')
            name = name.rstrip('@')  # SCUMM uses @ as null padding
            result['name'] = name

    return result


def extract_metadata(room_resource, output_dir: Path, room_name: str = '') -> dict:
    """Extract room metadata to JSON.

    Returns the metadata dict.
    """
    meta = {'room_id': room_resource.room_id, 'room_name': room_name}

    # RMHD
    rmhd = room_resource.get_room_sub('RMHD')
    if rmhd:
        meta['header'] = _parse_rmhd(rmhd.data)

    # BOXD walkboxes
    boxd = room_resource.get_room_sub('BOXD')
    if boxd:
        meta['walkboxes'] = _parse_boxd(boxd.data)

    # SCAL
    scal = room_resource.get_room_sub('SCAL')
    if scal:
        meta['scaling'] = _parse_scal(scal.data)

    # CYCL color cycling — always emit key (empty list if no cycles)
    cycl = room_resource.get_room_sub('CYCL')
    meta['color_cycling'] = _parse_cycl(cycl.data) if cycl else []

    # TRNS transparent color
    trns = room_resource.get_room_sub('TRNS')
    if trns and len(trns.data) >= 2:
        meta['transparent_color'] = struct.unpack_from('<H', trns.data, 0)[0]

    # Objects — merge CDHD (walk coords, dir) with IMHD (image bounds)
    # IMHD has proper 16-bit x/y/width/height; CDHD height is often 0
    imhd_dims = {}
    obims = room_resource.get_all_room_sub('OBIM')
    for obim in obims:
        for sub in iter_chunks(obim.data):
            if sub.tag == 'IMHD' and len(sub.data) >= 16:
                oid = struct.unpack_from('<H', sub.data, 0)[0]
                ix = struct.unpack_from('<H', sub.data, 8)[0]
                iy = struct.unpack_from('<H', sub.data, 10)[0]
                iw = struct.unpack_from('<H', sub.data, 12)[0]
                ih = struct.unpack_from('<H', sub.data, 14)[0]
                imhd_dims[oid] = {'x': ix, 'y': iy, 'width': iw, 'height': ih}

    obcds = room_resource.get_all_room_sub('OBCD')
    objects = []
    for obcd in obcds:
        obj_data = _parse_obcd(obcd.data)
        if obj_data:
            oid = obj_data.get('obj_id')
            if oid in imhd_dims:
                obj_data['x'] = imhd_dims[oid]['x']
                obj_data['y'] = imhd_dims[oid]['y']
                obj_data['width'] = imhd_dims[oid]['width']
                obj_data['height'] = imhd_dims[oid]['height']
            objects.append(obj_data)
    if objects:
        # Save per-object verb data as binary files (can't go in JSON)
        verb_dir = output_dir / 'verbs'
        for obj_data in objects:
            verb_data = obj_data.pop('verb_data', None)
            if verb_data and len(verb_data) > 1:  # >1 = has actual verb entries
                oid = obj_data.get('obj_id', 0)
                verb_dir.mkdir(parents=True, exist_ok=True)
                (verb_dir / f'obj_{oid:04d}.verb').write_bytes(verb_data)
        meta['objects'] = objects

    # EPAL
    epal = room_resource.get_room_sub('EPAL')
    if epal:
        meta['has_epal'] = True
        meta['epal_size'] = len(epal.data)

    # Save JSON
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / 'metadata.json'
    with open(json_path, 'w') as f:
        json.dump(meta, f, indent=2)
    log.info("Room %d: saved metadata → %s", room_resource.room_id, json_path)

    return meta


def extract_scripts(room_resource, output_dir: Path):
    """Extract room scripts (ENCD, EXCD, LSCR) as raw binary files."""
    scripts_dir = output_dir / 'scripts'

    # Entry/exit scripts
    for tag in ('ENCD', 'EXCD'):
        chunk = room_resource.get_room_sub(tag)
        if chunk:
            scripts_dir.mkdir(parents=True, exist_ok=True)
            path = scripts_dir / f'{tag.lower()}.bin'
            path.write_bytes(chunk.data)
            log.debug("Room %d: saved %s script (%d bytes)",
                      room_resource.room_id, tag, len(chunk.data))

    # Local scripts
    lscrs = room_resource.get_all_room_sub('LSCR')
    for i, lscr in enumerate(lscrs):
        scripts_dir.mkdir(parents=True, exist_ok=True)
        # First byte of LSCR is the script number
        script_num = lscr.data[0] if lscr.data else i
        path = scripts_dir / f'lscr_{script_num:03d}.bin'
        path.write_bytes(lscr.data)
        log.debug("Room %d: saved LSCR %d (%d bytes)",
                  room_resource.room_id, script_num, len(lscr.data))

    # Trailing global scripts (SCRP)
    scrps = room_resource.get_trailing('SCRP')
    for i, scrp in enumerate(scrps):
        scripts_dir.mkdir(parents=True, exist_ok=True)
        path = scripts_dir / f'scrp_{i:03d}.bin'
        path.write_bytes(scrp.data)
