"""SCUMM v5 data file parser (monkey.001) — LECF/LOFF/LFLF/ROOM structure."""

import struct
import logging
from typing import Dict, List, Optional
from .chunks import read_encrypted_file, iter_chunks, read_chunk, Chunk

log = logging.getLogger(__name__)


class RoomResource:
    """A parsed LFLF block with its ROOM sub-chunks."""

    def __init__(self, room_id: int, lflf_offset: int, lflf_chunk: Chunk):
        self.room_id = room_id
        self.lflf_offset = lflf_offset
        self.lflf = lflf_chunk
        self._room_chunk: Optional[Chunk] = None
        self._room_sub_chunks: Optional[List[Chunk]] = None
        self._trailing_chunks: Optional[List[Chunk]] = None

    @property
    def room_chunk(self) -> Optional[Chunk]:
        if self._room_chunk is None:
            self._parse_lflf()
        return self._room_chunk

    @property
    def room_sub_chunks(self) -> List[Chunk]:
        if self._room_sub_chunks is None:
            self._parse_room()
        return self._room_sub_chunks

    @property
    def trailing_chunks(self) -> List[Chunk]:
        """Chunks after ROOM inside LFLF (SCRP, SOUN, COST, CHAR)."""
        if self._trailing_chunks is None:
            self._parse_lflf()
        return self._trailing_chunks

    def _parse_lflf(self):
        """Parse LFLF payload into ROOM + trailing chunks."""
        data = self.lflf.data
        all_chunks = iter_chunks(data)
        self._room_chunk = None
        self._trailing_chunks = []
        for chunk in all_chunks:
            if chunk.tag == 'ROOM' and self._room_chunk is None:
                self._room_chunk = chunk
            else:
                self._trailing_chunks.append(chunk)

    def _parse_room(self):
        """Parse ROOM chunk into sub-chunks."""
        if self.room_chunk is None:
            self._room_sub_chunks = []
            return
        self._room_sub_chunks = iter_chunks(self.room_chunk.data)

    def get_room_sub(self, tag: str) -> Optional[Chunk]:
        """Get a specific sub-chunk from ROOM."""
        for c in self.room_sub_chunks:
            if c.tag == tag:
                return c
        return None

    def get_all_room_sub(self, tag: str) -> List[Chunk]:
        """Get all sub-chunks with a given tag from ROOM."""
        return [c for c in self.room_sub_chunks if c.tag == tag]

    def get_trailing(self, tag: str) -> List[Chunk]:
        """Get trailing chunks of a given type (SCRP, SOUN, COST, CHAR)."""
        return [c for c in self.trailing_chunks if c.tag == tag]


class DataFile:
    """Parsed SCUMM data file (monkey.001)."""

    def __init__(self, data: bytes):
        self.data = data
        self.rooms: Dict[int, RoomResource] = {}
        self._parse()

    def _parse(self):
        """Parse LECF → LOFF + LFLF blocks."""
        lecf = read_chunk(self.data, 0)
        if lecf is None or lecf.tag != 'LECF':
            raise ValueError("Data file does not start with LECF")
        log.info("LECF: %d bytes", lecf.size)

        lecf_data = lecf.data
        # First child should be LOFF
        loff = read_chunk(lecf_data, 0)
        if loff is None or loff.tag != 'LOFF':
            raise ValueError("Expected LOFF as first child of LECF")

        # Parse LOFF: num_rooms (byte), then room_id(byte) + offset(LE32) per room
        num_rooms = loff.data[0]
        log.info("LOFF: %d rooms", num_rooms)

        room_offsets = {}
        for i in range(num_rooms):
            base = 1 + i * 5
            room_id = loff.data[base]
            offset = struct.unpack_from('<I', loff.data, base + 1)[0]
            room_offsets[room_id] = offset

        # Parse each LFLF at the given offsets.
        # LOFF offsets point to the start of the LFLF *payload* (i.e. 8 bytes
        # after the LFLF header). Subtract 8 to get the LFLF chunk header.
        for room_id, file_offset in sorted(room_offsets.items()):
            lflf_offset = file_offset - 8
            lflf = read_chunk(self.data, lflf_offset)
            if lflf is None:
                log.warning("Room %d: failed to read LFLF at 0x%X", room_id, lflf_offset)
                continue
            if lflf.tag != 'LFLF':
                log.warning("Room %d: expected LFLF at 0x%X, got %s",
                            room_id, lflf_offset, lflf.tag)
                continue
            self.rooms[room_id] = RoomResource(room_id, lflf_offset, lflf)
            log.debug("Room %d: LFLF at 0x%X, size %d", room_id, lflf_offset, lflf.size)

        log.info("Parsed %d rooms from data file", len(self.rooms))


def parse_data_file(filepath: str) -> DataFile:
    """Parse the SCUMM data file (monkey.001)."""
    data = read_encrypted_file(filepath)
    log.info("Data file: %d bytes decrypted", len(data))
    return DataFile(data)
