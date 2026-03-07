"""Analyze ROM bank usage from .sym file and ROM binary."""
import sys
from collections import defaultdict

def parse_sym(path):
    syms = defaultdict(list)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(';') or not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                addr_str, name = parts
                bank_str, off_str = addr_str.split(':')
                bank = int(bank_str, 16)
                offset = int(off_str, 16)
                # Skip WRAM symbols (bank $7E/$7F)
                if bank in (0x7E, 0x7F):
                    continue
                # Skip zero-page / low RAM
                if bank == 0 and offset < 0x2000 and not name.startswith(('core.', 'Boot', 'Nmi', 'Irq', 'Stop', 'Empty')):
                    continue
                syms[bank].append((offset, name))
    return syms

def analyze_rom(rom_path):
    with open(rom_path, 'rb') as f:
        rom = f.read()

    rom_size = len(rom)
    bank_size = 0x10000  # 64KB per bank in HiROM
    num_banks = rom_size // bank_size

    print(f"ROM size: {rom_size} bytes ({rom_size // 1024} KB), {num_banks} banks")
    print()

    for bank in range(num_banks):
        start = bank * bank_size
        end = start + bank_size
        bank_data = rom[start:end]

        # Count non-zero bytes (rough usage estimate)
        used = sum(1 for b in bank_data if b != 0)
        # Count non-FF bytes (some linkers fill with FF)
        used_ff = sum(1 for b in bank_data if b != 0xFF)

        # Find last non-zero byte
        last_used = 0
        for i in range(bank_size - 1, -1, -1):
            if bank_data[i] != 0:
                last_used = i
                break

        # Find first non-zero byte
        first_used = bank_size
        for i in range(bank_size):
            if bank_data[i] != 0:
                first_used = i
                break

        pct = used * 100.0 / bank_size
        if used > 0:
            print(f"  Bank {bank:2d} ($C{bank:X}): {used:6d}/{bank_size} bytes non-zero ({pct:5.1f}%)  range ${first_used:04X}-${last_used:04X}")

sym_path = sys.argv[1]
rom_path = sys.argv[2]

print("=== ROM Bank Usage ===")
analyze_rom(rom_path)

print()
print("=== Symbol Distribution ===")
syms = parse_sym(sym_path)
for bank in sorted(syms.keys()):
    entries = sorted(syms[bank])
    if entries:
        lo = entries[0][0]
        hi = entries[-1][0]
        print(f"  Bank {bank:2d}: {len(entries):4d} symbols, range ${lo:04X}-${hi:04X}")
