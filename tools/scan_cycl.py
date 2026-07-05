#!/usr/bin/env python3
"""Scan MI1 talkie resource for CYCL chunks. File is XOR-encoded with 0x69."""
import struct

with open('data/monkeypacks/talkie/monkey.001', 'rb') as f:
    raw = f.read()
data = bytes(b ^ 0x69 for b in raw)

cycls = []
pos = 0
while True:
    idx = data.find(b'CYCL', pos)
    if idx < 0:
        break
    size = struct.unpack('>I', data[idx+4:idx+8])[0]
    cycls.append((idx, size))
    pos = idx + 1

sizes = {}
for _, size in cycls:
    sizes[size] = sizes.get(size, 0) + 1

print(f'Total CYCL chunks: {len(cycls)}')
print('CYCL size distribution:')
for sz, n in sorted(sizes.items()):
    print(f'  {sz} bytes: {n} chunks')

big = [(i, s) for i, s in cycls if s > 10]
print(f'\nNon-empty CYCL chunks: {len(big)}')
for idx, size in big[:15]:
    body = data[idx+8:idx+size]
    hx = ' '.join(f'{b:02x}' for b in body)
    print(f'  @ 0x{idx:x} size={size} body={hx}')
