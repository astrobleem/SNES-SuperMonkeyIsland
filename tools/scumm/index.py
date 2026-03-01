"""SCUMM v5 index file parser (monkey.000)."""

import struct
import logging
from typing import Dict, List
from .chunks import read_encrypted_file, iter_chunks

log = logging.getLogger(__name__)


class ResourceDirectory:
    """A directory block (DROO, DSCR, DSOU, DCOS, DCHR).
    Split-array format: count(LE16), room_nums[count](bytes), offsets[count](LE32).
    """

    def __init__(self, tag: str, count: int, room_nums: List[int], offsets: List[int]):
        self.tag = tag
        self.count = count
        self.room_nums = room_nums
        self.offsets = offsets

    def __repr__(self):
        return f"ResourceDirectory({self.tag!r}, count={self.count})"


class IndexData:
    """Parsed contents of the SCUMM index file."""

    def __init__(self):
        self.room_names: Dict[int, str] = {}
        self.maxs: Dict[str, int] = {}
        self.directories: Dict[str, ResourceDirectory] = {}
        self.object_owner_state: List[tuple] = []  # (owner, state) per object

    @property
    def num_rooms(self) -> int:
        if 'DROO' in self.directories:
            return self.directories['DROO'].count
        return 0

    @property
    def num_scripts(self) -> int:
        if 'DSCR' in self.directories:
            return self.directories['DSCR'].count
        return 0

    @property
    def num_sounds(self) -> int:
        if 'DSOU' in self.directories:
            return self.directories['DSOU'].count
        return 0

    @property
    def num_costumes(self) -> int:
        if 'DCOS' in self.directories:
            return self.directories['DCOS'].count
        return 0

    @property
    def num_charsets(self) -> int:
        if 'DCHR' in self.directories:
            return self.directories['DCHR'].count
        return 0


def _parse_rnam(data: bytes) -> Dict[int, str]:
    """Parse RNAM chunk — room number → name mapping.
    Room names are XOR 0xFF encoded after the file-level decryption.
    """
    names = {}
    pos = 0
    while pos < len(data):
        if pos + 1 > len(data):
            break
        room_no = data[pos]
        pos += 1
        if room_no == 0:
            break
        if pos + 9 > len(data):
            break
        name_raw = data[pos:pos + 9]
        name_bytes = bytes(b ^ 0xFF for b in name_raw)
        name = name_bytes.split(b'\x00')[0].decode('ascii', errors='replace')
        names[room_no] = name
        pos += 9
    return names


def _parse_maxs(data: bytes) -> Dict[str, int]:
    """Parse MAXS chunk — maximum resource counts.
    SCUMM v5 has 15 LE16 fields but some versions have fewer.
    """
    result = {}
    keys = [
        'num_variables', 'unknown1', 'num_bit_variables',
        'num_local_objects', 'num_arrays', 'unknown2',
        'num_verbs', 'num_fl_objects', 'num_inventory',
        'num_rooms', 'num_scripts', 'num_sounds',
        'num_charsets', 'num_costumes', 'num_global_objects',
    ]
    num_fields = min(len(data) // 2, len(keys))
    if num_fields == 0:
        return result
    fields = struct.unpack_from(f'<{num_fields}H', data, 0)
    for i, val in enumerate(fields):
        result[keys[i]] = val
    return result


def _parse_directory(tag: str, data: bytes) -> ResourceDirectory:
    """Parse a directory block (DROO/DSCR/DSOU/DCOS/DCHR)."""
    count = struct.unpack_from('<H', data, 0)[0]
    room_nums = list(data[2:2 + count])
    offsets = []
    offset_start = 2 + count
    for i in range(count):
        off = struct.unpack_from('<I', data, offset_start + i * 4)[0]
        offsets.append(off)
    return ResourceDirectory(tag, count, room_nums, offsets)


def _parse_dobj(data: bytes) -> List[tuple]:
    """Parse DOBJ chunk — object owner/state table.
    Each entry is 1 byte packed: high nibble = state, low nibble = owner.
    Then class data (4 bytes per object)."""
    count = struct.unpack_from('<H', data, 0)[0]
    entries = []
    for i in range(count):
        if 2 + i >= len(data):
            break
        byte = data[2 + i]
        owner = byte & 0x0F
        state = (byte >> 4) & 0x0F
        entries.append((owner, state))
    return entries


def parse_index(filepath: str) -> IndexData:
    """Parse the SCUMM index file (monkey.000)."""
    data = read_encrypted_file(filepath)
    log.info("Index file: %d bytes decrypted", len(data))

    index = IndexData()
    chunks = iter_chunks(data)

    for chunk in chunks:
        tag = chunk.tag
        payload = chunk.data

        if tag == 'RNAM':
            index.room_names = _parse_rnam(payload)
            log.info("RNAM: %d room names", len(index.room_names))

        elif tag == 'MAXS':
            index.maxs = _parse_maxs(payload)
            log.info("MAXS: %s", index.maxs)

        elif tag in ('DROO', 'DSCR', 'DSOU', 'DCOS', 'DCHR'):
            d = _parse_directory(tag, payload)
            index.directories[tag] = d
            # Count non-zero room entries (actual resources)
            active = sum(1 for r in d.room_nums if r != 0)
            log.info("%s: %d slots, %d active resources", tag, d.count, active)

        elif tag == 'DOBJ':
            index.object_owner_state = _parse_dobj(payload)
            log.info("DOBJ: %d objects", len(index.object_owner_state))

        else:
            log.debug("Unknown index chunk: %s (%d bytes)", tag, chunk.size)

    return index
