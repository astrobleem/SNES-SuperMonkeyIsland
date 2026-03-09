"""BRK Scanner — detect unexpected BRK ($00) opcodes in 65816 ROM code sections.

Reads the ROM + sym file, classifies symbols as code vs data, then forward-
disassembles code regions looking for BRK opcodes that indicate WLA-DX
assembled an immediate operand with wrong width (phantom $00 bytes).

Usage:
    python tools/brk_scanner.py build/SuperMonkeyIsland.sym build/SuperMonkeyIsland.sfc [--verbose]
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# 65816 instruction length table
# ---------------------------------------------------------------------------

def build_instruction_table():
    """Build 256-entry instruction length table for 65816.

    Returns list of (base_len, flag_dep) tuples.
    base_len includes opcode byte.  flag_dep is 'M', 'X', or None.
    When flag_dep is set and the corresponding flag is 0 (16-bit mode),
    the actual instruction is base_len + 1.
    """
    # Default: 1 byte (opcode only) — will be overridden for all valid opcodes
    tbl = [(1, None)] * 256

    # Implied / Accumulator — 1 byte
    for op in [0x0A, 0x1A, 0x2A, 0x3A, 0x4A, 0x6A,
               0x18, 0x38, 0x58, 0x78, 0xB8, 0xD8, 0xF8,
               0x08, 0x28, 0x48, 0x68,
               0x0B, 0x2B, 0x4B, 0x6B, 0x8B, 0xAB, 0xCB, 0xEB, 0xFB,
               0x5A, 0x7A, 0xDA, 0xFA,
               0x1B, 0x3B, 0x5B, 0x7B, 0x9B, 0xBB,
               0xAA, 0xA8, 0xBA, 0x8A, 0x9A, 0x98,
               0xCA, 0xE8, 0x88, 0xC8,
               0xEA,
               0x40, 0x60,
               0xDB, 0x42]:
        tbl[op] = (1, None)

    # BRK and COP — 2 bytes (opcode + signature)
    tbl[0x00] = (2, None)
    tbl[0x02] = (2, None)

    # Immediate — 2 bytes base (3 when 16-bit mode)
    # M-dependent: ORA/AND/EOR/ADC/BIT/LDA/CMP/SBC
    for op in [0x09, 0x29, 0x49, 0x69, 0x89, 0xA9, 0xC9, 0xE9]:
        tbl[op] = (2, 'M')
    # X-dependent: LDY/LDX/CPY/CPX
    for op in [0xA0, 0xA2, 0xC0, 0xE0]:
        tbl[op] = (2, 'X')

    # REP/SEP — 2 bytes
    tbl[0xC2] = (2, None)
    tbl[0xE2] = (2, None)

    # Direct Page — 2 bytes
    for op in [0x05, 0x06, 0x07, 0x15, 0x16, 0x17,
               0x25, 0x26, 0x27, 0x35, 0x36, 0x37,
               0x45, 0x46, 0x47, 0x55, 0x56, 0x57,
               0x65, 0x66, 0x67, 0x75, 0x76, 0x77,
               0x85, 0x86, 0x87, 0x95, 0x96, 0x97,
               0xA5, 0xA6, 0xA7, 0xB5, 0xB6, 0xB7,
               0xC5, 0xC6, 0xC7, 0xD5, 0xD6, 0xD7,
               0xE5, 0xE6, 0xE7, 0xF5, 0xF6, 0xF7,
               0x04, 0x14, 0x24, 0x34, 0x44, 0x54, 0x64, 0x74]:
        tbl[op] = (2, None)

    # DP Indirect — 2 bytes
    for op in [0x12, 0x32, 0x52, 0x72, 0x92, 0xB2, 0xD2, 0xF2]:
        tbl[op] = (2, None)

    # DP Indexed Indirect (dp,X) — 2 bytes
    for op in [0x01, 0x21, 0x41, 0x61, 0x81, 0xA1, 0xC1, 0xE1]:
        tbl[op] = (2, None)

    # DP Indirect Indexed (dp),Y — 2 bytes
    for op in [0x11, 0x31, 0x51, 0x71, 0x91, 0xB1, 0xD1, 0xF1]:
        tbl[op] = (2, None)

    # DP Indirect Long [dp] — 2 bytes
    for op in [0x07, 0x27, 0x47, 0x67, 0x87, 0xA7, 0xC7, 0xE7]:
        tbl[op] = (2, None)

    # DP Indirect Long Indexed [dp],Y — 2 bytes
    for op in [0x17, 0x37, 0x57, 0x77, 0x97, 0xB7, 0xD7, 0xF7]:
        tbl[op] = (2, None)

    # Stack Relative — 2 bytes
    for op in [0x03, 0x23, 0x43, 0x63, 0x83, 0xA3, 0xC3, 0xE3]:
        tbl[op] = (2, None)

    # Stack Relative Indirect Indexed (sr,S),Y — 2 bytes
    for op in [0x13, 0x33, 0x53, 0x73, 0x93, 0xB3, 0xD3, 0xF3]:
        tbl[op] = (2, None)

    # Relative (branches) — 2 bytes
    for op in [0x10, 0x30, 0x50, 0x70, 0x80, 0x90, 0xB0, 0xD0, 0xF0]:
        tbl[op] = (2, None)

    # Relative Long (BRL) — 3 bytes
    tbl[0x82] = (3, None)

    # Absolute — 3 bytes
    for op in [0x0C, 0x0D, 0x0E, 0x1C, 0x1D, 0x1E,
               0x2C, 0x2D, 0x2E, 0x3C, 0x3D, 0x3E,
               0x4C, 0x4D, 0x4E, 0x5D, 0x5E,
               0x6C, 0x6D, 0x6E, 0x7C, 0x7D, 0x7E,
               0x8C, 0x8D, 0x8E, 0x9C, 0x9D, 0x9E,
               0xAC, 0xAD, 0xAE, 0xBC, 0xBD, 0xBE,
               0xCC, 0xCD, 0xCE, 0xDD, 0xDE,
               0xEC, 0xED, 0xEE, 0xFD, 0xFE,
               0x20]:
        tbl[op] = (3, None)

    # PEA / PEI / PER
    tbl[0xF4] = (3, None)  # PEA
    tbl[0xD4] = (2, None)  # PEI (dp)
    tbl[0x62] = (3, None)  # PER

    # Absolute Long — 4 bytes
    for op in [0x0F, 0x1F, 0x2F, 0x3F, 0x4F, 0x5F,
               0x6F, 0x7F, 0x8F, 0x9F, 0xAF, 0xBF,
               0xCF, 0xDF, 0xEF, 0xFF,
               0x22, 0x5C]:
        tbl[op] = (4, None)

    # Absolute Indirect Long — 3 bytes [JML [abs]]
    tbl[0xDC] = (3, None)

    # Block Move — 3 bytes
    tbl[0x44] = (3, None)  # MVN
    tbl[0x54] = (3, None)  # MVP

    return tbl


INST_TABLE = build_instruction_table()


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    bank: int
    offset: int
    name: str
    rom_offset: int  # linear offset into ROM file


def snes_to_rom(bank: int, offset: int) -> int:
    """Convert SNES HiROM address to linear ROM file offset."""
    if bank >= 0xC0:
        bank -= 0xC0
    elif bank >= 0x80:
        bank -= 0x80

    if bank <= 0x3F:
        if offset >= 0x8000:
            return bank * 0x8000 + (offset - 0x8000)
        else:
            return bank * 0x10000 + offset
    elif bank <= 0x7D:
        return bank * 0x10000 + offset
    return -1


def parse_symbols(sym_path: str) -> list[Symbol]:
    """Parse sym file, return ROM-only symbols sorted by rom_offset."""
    syms = []
    seen_offsets = set()

    with open(sym_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            m = re.match(r'^([0-9a-fA-F]+):([0-9a-fA-F]+)\s+(.+)$', line)
            if not m:
                continue
            bank = int(m.group(1), 16)
            offset = int(m.group(2), 16)
            name = m.group(3)

            # Skip WRAM symbols
            if bank in (0x7E, 0x7F):
                continue

            # Skip enums/constants (bank 0, offset < $2000)
            if bank == 0 and offset < 0x2000:
                continue

            rom_off = snes_to_rom(bank, offset)
            if rom_off < 0:
                continue

            # Deduplicate by ROM offset (keep first name seen)
            if rom_off not in seen_offsets:
                seen_offsets.add(rom_off)
                syms.append(Symbol(bank, offset, name, rom_off))

    syms.sort(key=lambda s: s.rom_offset)
    return syms


# ---------------------------------------------------------------------------
# Data symbol classification
# ---------------------------------------------------------------------------

# Patterns that indicate data, not code
_DATA_NAME_PATTERNS = re.compile(
    r'(?:'
    # OOP framework metadata (class tables, method name strings)
    r'T_CLSS_|T_EXCP_|\.CLS$|'
    # Lookup tables, data arrays, tile/font/palette data
    r'Lut|Table|Data|Tiles|Font|Pal$|Palette|Chr|'
    # Costume/sprite/OAM data
    r'Costume.*Oam|OamEnd|Costume_pic|CostumeFrame|SPRITE\.|oamClear|VramClear|'
    # Walk cycle / dispatch tables
    r'WalkCycleTable|dispatchTable|'
    # Memory allocation metadata (WRAM/VRAM/CGRAM blocks)
    r'GLOBAL\.|CGRAM_ALLOCATE|VRAM_ALLOCATE|WRAM_ALLOCATE|'
    r'DMA_QUEUE|DMA_TRANSFER|'
    # Audio driver blob (SPC700, not 65816)
    r'Tad_Audio|_Tad_Audio|'
    # ROM header
    r'Header|headerChecksum|headerChecksumComplement|'
    # UI data tables
    r'ExcFont|OamTable|VerbHdmaTable|CgramHdmaTable|'
    r'_scummTalkColorLUT|defaultVerb|initDefaultVerbs|'
    # Walkbox data
    r'boxData|walkboxMatrix|'
    # Pattern/byte data for DMA/clear operations
    r'\.pattern|Patterns|'
    # Size/end markers
    r'\.LEN$|\.SIZE$|\.END$|\.end$|\.START$|\.start$|'
    # Error message/data templates
    r'T_max$'
    r')',
    re.IGNORECASE
)

# Prefixes for OOP class metadata — always data
_CLS_SUFFIX_RE = re.compile(r'\.CLS$')


def is_data_symbol(name: str, bank: int, offset: int) -> bool:
    """Heuristic: return True if symbol is likely data, not code."""
    # SNES header/vectors area: $FFC0-$FFFF in bank 0
    if bank == 0 and offset >= 0xFFC0:
        return True

    # OOP class metadata structs (e.g., "Player.CLS", "Script.CLS")
    if _CLS_SUFFIX_RE.search(name):
        return True

    # Pattern-based classification
    if _DATA_NAME_PATTERNS.search(name):
        return True

    return False


# ---------------------------------------------------------------------------
# Forward disassembler + BRK detector
# ---------------------------------------------------------------------------

@dataclass
class BrkHit:
    rom_offset: int
    snes_bank: int
    snes_offset: int
    nearest_symbol: str
    symbol_distance: int
    context_bytes: bytes
    m_flag: int  # 0=16-bit, 1=8-bit
    x_flag: int


@dataclass
class ScanResult:
    hits: list[BrkHit] = field(default_factory=list)
    regions_scanned: int = 0
    regions_skipped: int = 0
    total_bytes_scanned: int = 0


# Opcodes that terminate a basic block (unconditional control transfer)
_TERMINATOR_OPCODES = frozenset([
    0x4C, 0x5C, 0x6C, 0x7C, 0xDC,  # JMP variants, JML
    0x60, 0x6B, 0x40,  # RTS, RTL, RTI
    0x80, 0x82,  # BRA, BRL (unconditional branches)
    0xDB,  # STP
])


def scan_code_region(rom: bytes, start: int, end: int, m: int, x: int,
                     sym_name: str, sym_bank: int, sym_offset: int) -> list[BrkHit]:
    """Forward disassemble a code region, return any BRK hits."""
    hits = []
    pc = start
    rom_len = len(rom)

    while pc < end and pc < rom_len:
        opcode = rom[pc]

        # BRK detection
        if opcode == 0x00:
            ctx_start = max(0, pc - 4)
            ctx_end = min(rom_len, pc + 8)
            context = rom[ctx_start:ctx_end]

            dist = pc - start
            snes_addr_offset = sym_offset + dist
            hits.append(BrkHit(
                rom_offset=pc,
                snes_bank=sym_bank,
                snes_offset=snes_addr_offset,
                nearest_symbol=sym_name,
                symbol_distance=dist,
                context_bytes=context,
                m_flag=m,
                x_flag=x,
            ))
            # After BRK, stop scanning this basic block — remaining bytes
            # may be unreachable or data. BRK transfers to interrupt vector.
            break

        # Track M/X flag changes through SEP/REP
        if opcode == 0xE2 and pc + 1 < rom_len:  # SEP
            operand = rom[pc + 1]
            if operand & 0x20:
                m = 1
            if operand & 0x10:
                x = 1
        elif opcode == 0xC2 and pc + 1 < rom_len:  # REP
            operand = rom[pc + 1]
            if operand & 0x20:
                m = 0
            if operand & 0x10:
                x = 0

        # Calculate instruction length
        base_len, flag_dep = INST_TABLE[opcode]
        inst_len = base_len
        if flag_dep == 'M' and m == 0:
            inst_len += 1
        elif flag_dep == 'X' and x == 0:
            inst_len += 1

        pc += inst_len

        # Stop after unconditional control flow — bytes after may be data or
        # branch targets (which get their own symbol regions)
        if opcode in _TERMINATOR_OPCODES:
            break

    return hits


def scan_rom(sym_path: str, rom_path: str) -> ScanResult:
    """Main entry point — scan entire ROM for unexpected BRK opcodes."""
    rom = Path(rom_path).read_bytes()
    symbols = parse_symbols(sym_path)
    result = ScanResult()

    if not symbols:
        return result

    # Find TAD audio data range to skip entirely
    tad_start = None
    tad_end = None
    for s in symbols:
        if s.name == 'Tad_AudioData':
            tad_start = s.rom_offset
        elif s.name == '_Tad_AudioData_End':
            tad_end = s.rom_offset

    for i in range(len(symbols) - 1):
        sym = symbols[i]
        next_sym = symbols[i + 1]

        region_start = sym.rom_offset
        region_end = next_sym.rom_offset

        # Skip zero-length regions
        if region_start >= region_end:
            result.regions_skipped += 1
            continue

        # Skip regions within TAD audio blob (SPC700, not 65816)
        if tad_start is not None and tad_end is not None:
            if region_start >= tad_start and region_end <= tad_end:
                result.regions_skipped += 1
                continue

        # Skip SNES header/vectors
        if sym.bank == 0 and sym.offset >= 0xFFC0:
            result.regions_skipped += 1
            continue

        # Skip data symbols
        if is_data_symbol(sym.name, sym.bank, sym.offset):
            result.regions_skipped += 1
            continue

        # Skip if region is outside ROM bounds
        if region_start >= len(rom):
            result.regions_skipped += 1
            continue

        # Scan code region with default entry state: M=0, X=0 (16-bit both)
        hits = scan_code_region(rom, region_start, min(region_end, len(rom)),
                                m=0, x=0, sym_name=sym.name,
                                sym_bank=sym.bank, sym_offset=sym.offset)
        result.hits.extend(hits)
        result.regions_scanned += 1
        result.total_bytes_scanned += min(region_end, len(rom)) - region_start

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def format_hit(hit: BrkHit) -> str:
    """Format a single BRK hit for display."""
    ctx_hex = ' '.join(f'{b:02X}' for b in hit.context_bytes)
    return (
        f"  BRK at ${hit.snes_bank:02X}:{hit.snes_offset:04X} "
        f"(ROM 0x{hit.rom_offset:06X})  "
        f"near {hit.nearest_symbol}+{hit.symbol_distance}  "
        f"M={'8' if hit.m_flag else '16'} X={'8' if hit.x_flag else '16'}  "
        f"ctx: [{ctx_hex}]"
    )


def main():
    if len(sys.argv) < 3:
        print("Usage: brk_scanner.py <sym_file> <rom_file> [--verbose] [--baseline N]")
        sys.exit(1)

    sym_path = sys.argv[1]
    rom_path = sys.argv[2]
    verbose = '--verbose' in sys.argv

    # Baseline: expected number of known false positives from linear disassembly.
    # The forward disassembler tracks M/X flags linearly (same as WLA-DX) so it
    # can lose sync at conditional branch targets. The baseline count represents
    # known-benign hits in the clean ROM (legitimate $00 operand bytes in DMA
    # register addresses, small constants, etc.). Any NEW hits above baseline
    # warrant investigation.
    baseline = 15  # known false positives in clean ROM
    for i, arg in enumerate(sys.argv):
        if arg == '--baseline' and i + 1 < len(sys.argv):
            baseline = int(sys.argv[i + 1])

    if not Path(sym_path).exists():
        print(f"ERROR: sym file not found: {sym_path}")
        sys.exit(1)
    if not Path(rom_path).exists():
        print(f"ERROR: ROM file not found: {rom_path}")
        sys.exit(1)

    result = scan_rom(sym_path, rom_path)
    hit_count = len(result.hits)

    if hit_count > baseline:
        new_hits = hit_count - baseline
        print(f"BRK SCAN: WARNING -- {hit_count} BRK(s) detected "
              f"({new_hits} above baseline of {baseline})")
        print()
        for hit in result.hits:
            print(format_hit(hit))
        sys.exit(1)
    else:
        print(f"BRK scan: CLEAN ({hit_count} detected, baseline {baseline}, "
              f"{result.regions_scanned} regions scanned)")

    if verbose:
        print(f"\n  Regions scanned: {result.regions_scanned}")
        print(f"  Regions skipped (data): {result.regions_skipped}")
        print(f"  Total bytes scanned: {result.total_bytes_scanned:,}")
        print(f"  ROM size: {Path(rom_path).stat().st_size:,}")
        if result.hits:
            print(f"\n  Known false positives ({hit_count}):")
            for hit in result.hits:
                print(format_hit(hit))


if __name__ == '__main__':
    main()
