"""Find largest code regions in bank 0 by looking at symbol gaps."""
import sys

syms = []
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if line.startswith(';') or not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        addr_str, name = parts
        bank_str, off_str = addr_str.split(':')
        bank = int(bank_str, 16)
        offset = int(off_str, 16)
        if bank == 0 and 0x0004 <= offset < 0x8000:
            syms.append((offset, name))

syms.sort()

# Find large gaps (= large functions/data)
gaps = []
for i in range(len(syms) - 1):
    gap = syms[i+1][0] - syms[i][0]
    if gap > 50:
        gaps.append((gap, syms[i][1], syms[i][0]))

gaps.sort(reverse=True)
print("=== Largest code/data blocks in bank 0 ===")
for gap, name, addr in gaps[:30]:
    print(f"  {gap:5d} bytes  ${addr:04X}  {name}")
