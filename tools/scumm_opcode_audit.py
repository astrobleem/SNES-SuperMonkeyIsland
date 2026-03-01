#!/usr/bin/env python3
"""SCUMM v5 Opcode Audit — catalogs every opcode used by MI1 CD Talkie scripts.

Walks all extracted bytecode files, decodes variable-length opcodes,
and reports coverage: which of the 105 base opcodes MI1 actually uses.

Usage:
    python tools/scumm_opcode_audit.py
"""

import json
import sys
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path

# Resolve project root (tools/ parent)
TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.scumm.opcodes_v5 import (
    BASE_OPCODES,
    NUM_BASE_OPCODES,
    OPCODE_CATEGORIES,
    OPCODE_MAP,
    UNIQUE_OPCODES,
)


# ---------------------------------------------------------------------------
# Script file discovery
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / "data" / "scumm_extracted"


def discover_scripts():
    """Find all script .bin files, grouped by type.

    Returns dict: {type_name: [(path, start_offset), ...]}
    - SCRP: global scripts (offset 0)
    - ENCD: room entry scripts (offset 0)
    - EXCD: room exit scripts (offset 0)
    - LSCR: room local scripts (offset 1 — skip script number prefix)
    """
    scripts = {"SCRP": [], "ENCD": [], "EXCD": [], "LSCR": []}

    # Global scripts
    for f in sorted(DATA_DIR.glob("scripts/scrp_*.bin")):
        scripts["SCRP"].append((f, 0))

    # Per-room scripts
    for room_dir in sorted(DATA_DIR.glob("rooms/room_*")):
        script_dir = room_dir / "scripts"
        if not script_dir.is_dir():
            continue

        encd = script_dir / "encd.bin"
        if encd.exists():
            scripts["ENCD"].append((encd, 0))

        excd = script_dir / "excd.bin"
        if excd.exists():
            scripts["EXCD"].append((excd, 0))

        for f in sorted(script_dir.glob("lscr_*.bin")):
            scripts["LSCR"].append((f, 1))  # skip 1-byte script number prefix

    return scripts


# ---------------------------------------------------------------------------
# Bytecode walker
# ---------------------------------------------------------------------------

def walk_script(data: bytes, start_offset: int, path: str):
    """Walk bytecode from start_offset, decoding each opcode.

    Returns:
        opcodes: Counter of base opcode names
        errors: list of (offset, description) for decode failures
    """
    opcodes = Counter()
    errors = []
    stream = BytesIO(data)
    stream.seek(start_offset)
    length = len(data)

    while stream.tell() < length:
        pos = stream.tell()
        op_byte_raw = stream.read(1)
        if not op_byte_raw:
            break
        op_byte = op_byte_raw[0]
        name = OPCODE_MAP[op_byte]

        try:
            decoder = BASE_OPCODES[name]
            consumed = decoder(op_byte, stream)
            opcodes[name] += 1
        except Exception as e:
            errors.append((pos, f"${op_byte:02X} ({name}): {e}"))
            # Try to resync: skip 1 byte from error position
            stream.seek(pos + 1)

        # Sanity: if stream went past end, record error
        if stream.tell() > length:
            errors.append((pos, f"${op_byte:02X} ({name}): read past end of script "
                          f"(pos={pos}, consumed to {stream.tell()}, len={length})"))
            break

    return opcodes, errors


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(all_opcodes, per_type_opcodes, total_scripts, type_counts,
                 total_bytes, all_errors):
    """Print the formatted audit report."""
    total_ops = sum(all_opcodes.values())
    used = sorted([name for name in UNIQUE_OPCODES if all_opcodes[name] > 0])
    unused = sorted([name for name in UNIQUE_OPCODES if all_opcodes[name] == 0])

    print("=" * 65)
    print("  SCUMM v5 Opcode Audit — MI1 CD Talkie")
    print("=" * 65)
    print()

    # Script summary
    print(f"Scripts analyzed: {total_scripts} "
          f"({type_counts['SCRP']} SCRP, "
          f"{type_counts['ENCD']} ENCD, "
          f"{type_counts['EXCD']} EXCD, "
          f"{type_counts['LSCR']} LSCR)")
    print(f"Total bytecode: {total_bytes:,} bytes ({total_bytes / 1024:.1f} KB)")
    print(f"Total opcodes decoded: {total_ops:,}")
    print()

    # Used opcodes table
    print(f"BASE OPCODES USED: {len(used)} / {NUM_BASE_OPCODES}")
    print(f"{'  Name':<30s} {'Count':>8s} {'%':>7s}")
    print(f"  {'-'*28} {'-'*8} {'-'*7}")
    for name in sorted(used, key=lambda n: -all_opcodes[n]):
        count = all_opcodes[name]
        pct = 100.0 * count / total_ops if total_ops else 0
        print(f"  {name:<28s} {count:>8,d} {pct:>6.1f}%")
    print()

    # Unused opcodes
    print(f"UNUSED BASE OPCODES ({len(unused)} — can skip in interpreter):")
    for name in unused:
        # Find hex values for this opcode
        hex_vals = [f"${i:02X}" for i in range(256) if OPCODE_MAP[i] == name]
        print(f"  {name:<28s} ({', '.join(hex_vals[:4])}{'...' if len(hex_vals) > 4 else ''})")
    print()

    # By category
    print("BY CATEGORY:")
    for category, members in OPCODE_CATEGORIES.items():
        cat_used = [m for m in members if all_opcodes.get(m, 0) > 0]
        cat_total = sum(all_opcodes.get(m, 0) for m in members)
        if cat_used:
            names_str = ", ".join(f"{m}({all_opcodes[m]})" for m in
                                  sorted(cat_used, key=lambda n: -all_opcodes[n])[:8])
            overflow = len(cat_used) - 8
            if overflow > 0:
                names_str += f", +{overflow} more"
            print(f"  {category} ({len(cat_used)} used, {cat_total:,} total): {names_str}")
        else:
            print(f"  {category}: (none used)")
    print()

    # Per-type breakdown
    print("PER SCRIPT TYPE:")
    for stype in ("SCRP", "ENCD", "EXCD", "LSCR"):
        type_ops = per_type_opcodes[stype]
        type_total = sum(type_ops.values())
        type_unique = len([n for n in type_ops if type_ops[n] > 0])
        top3 = ", ".join(f"{n}({type_ops[n]})" for n in
                         sorted(type_ops, key=lambda n: -type_ops[n])[:3])
        print(f"  {stype}: {type_counts[stype]} scripts, {type_total:,} opcodes, "
              f"{type_unique} unique — top: {top3}")
    print()

    # Errors
    if all_errors:
        print(f"DECODE ERRORS: {len(all_errors)}")
        for path, offset, desc in all_errors[:20]:
            print(f"  {path}:{offset}: {desc}")
        if len(all_errors) > 20:
            print(f"  ... and {len(all_errors) - 20} more")
    else:
        print("DECODE ERRORS: 0")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    scripts_by_type = discover_scripts()

    total_scripts = sum(len(v) for v in scripts_by_type.values())
    type_counts = {k: len(v) for k, v in scripts_by_type.items()}

    print(f"Discovered {total_scripts} scripts "
          f"({type_counts['SCRP']} SCRP, {type_counts['ENCD']} ENCD, "
          f"{type_counts['EXCD']} EXCD, {type_counts['LSCR']} LSCR)")
    print("Walking bytecode...")

    all_opcodes = Counter()
    per_type_opcodes = defaultdict(Counter)
    all_errors = []
    total_bytes = 0

    for stype, file_list in scripts_by_type.items():
        for filepath, start_offset in file_list:
            data = filepath.read_bytes()
            total_bytes += len(data) - start_offset
            rel_path = filepath.relative_to(DATA_DIR)

            opcodes, errors = walk_script(data, start_offset, str(rel_path))
            all_opcodes += opcodes
            per_type_opcodes[stype] += opcodes
            for offset, desc in errors:
                all_errors.append((str(rel_path), offset, desc))

    print()
    print_report(all_opcodes, per_type_opcodes, total_scripts, type_counts,
                 total_bytes, all_errors)

    # Save JSON output
    output_json = DATA_DIR / "opcode_audit.json"
    json_data = {
        "total_scripts": total_scripts,
        "type_counts": type_counts,
        "total_bytes": total_bytes,
        "total_opcodes": sum(all_opcodes.values()),
        "base_opcodes_used": len([n for n in UNIQUE_OPCODES if all_opcodes[n] > 0]),
        "base_opcodes_total": NUM_BASE_OPCODES,
        "opcodes": {name: all_opcodes[name] for name in sorted(UNIQUE_OPCODES)},
        "per_type": {stype: dict(sorted(counts.items()))
                     for stype, counts in per_type_opcodes.items()},
        "errors": [{"file": f, "offset": o, "desc": d} for f, o, d in all_errors],
    }
    output_json.write_text(json.dumps(json_data, indent=2))
    print(f"JSON saved to {output_json.relative_to(PROJECT_ROOT)}")

    # Exit code: 0 if no errors, 1 if errors
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
