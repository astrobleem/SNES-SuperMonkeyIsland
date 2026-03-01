"""SCUMM chunk reader — 4-byte ASCII tag + 4-byte BE size."""

import struct
import logging
from typing import Optional
from .crypto import decrypt_bytes

log = logging.getLogger(__name__)


class Chunk:
    """A SCUMM chunk: tag, total size (including 8-byte header), raw payload."""

    __slots__ = ('tag', 'size', 'offset', 'data')

    def __init__(self, tag: str, size: int, offset: int, data: bytes):
        self.tag = tag
        self.size = size
        self.offset = offset
        self.data = data  # payload only (size - 8 bytes)

    def __repr__(self):
        return f"Chunk({self.tag!r}, size={self.size}, offset=0x{self.offset:X})"

    @property
    def payload_size(self) -> int:
        return len(self.data)


def read_chunk_header(data: bytes, offset: int) -> Optional[tuple]:
    """Read a chunk header at offset. Returns (tag, size) or None if not enough data."""
    if offset + 8 > len(data):
        return None
    tag = data[offset:offset + 4].decode('ascii', errors='replace')
    size = struct.unpack_from('>I', data, offset + 4)[0]
    return tag, size


def read_chunk(data: bytes, offset: int) -> Optional[Chunk]:
    """Read a full chunk at offset. Returns Chunk or None."""
    hdr = read_chunk_header(data, offset)
    if hdr is None:
        return None
    tag, size = hdr
    if size < 8:
        log.warning("Chunk %s at 0x%X has size %d < 8", tag, offset, size)
        return None
    payload = data[offset + 8:offset + size]
    return Chunk(tag, size, offset, payload)


def iter_chunks(data: bytes, start: int = 0, end: int = None) -> list:
    """Iterate top-level chunks in a byte range. Returns list of Chunk."""
    if end is None:
        end = len(data)
    chunks = []
    pos = start
    while pos + 8 <= end:
        chunk = read_chunk(data, pos)
        if chunk is None:
            break
        if chunk.size < 8:
            break
        chunks.append(chunk)
        pos += chunk.size
    return chunks


def read_encrypted_file(filepath: str) -> bytes:
    """Read and XOR-decrypt an entire SCUMM file."""
    with open(filepath, 'rb') as f:
        raw = f.read()
    return decrypt_bytes(raw)


def find_child_chunk(data: bytes, start: int, end: int, tag: str) -> Optional[Chunk]:
    """Find the first child chunk with the given tag."""
    for chunk in iter_chunks(data, start, end):
        if chunk.tag == tag:
            return chunk
    return None


def find_all_child_chunks(data: bytes, start: int, end: int, tag: str) -> list:
    """Find all child chunks with the given tag."""
    return [c for c in iter_chunks(data, start, end) if c.tag == tag]
