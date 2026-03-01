"""XOR decryption for SCUMM v5 data files."""

XOR_KEY = 0x69


def decrypt_byte(b: int) -> int:
    return b ^ XOR_KEY


def decrypt_bytes(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY for b in data)


class DecryptedReader:
    """File-like wrapper that XOR-decrypts on read."""

    def __init__(self, fileobj):
        self._f = fileobj

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        return decrypt_bytes(data)

    def seek(self, offset: int, whence: int = 0):
        return self._f.seek(offset, whence)

    def tell(self) -> int:
        return self._f.tell()

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
