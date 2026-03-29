#!/usr/bin/env python3
"""Generate ROM costume data assembly file from converted costume directories.

Scans data/snes_converted/costumes/cost_NNN/ and generates:
  - CostumeDirTable: indexed by costume number -> palette ptr, frame count, frame table ptr
  - Per-costume FrameTable: indexed by pic index -> CHR ptr/bank/len, OAM ptr/bank
  - FILEINC sections for all CHR/OAM/palette binary data
  - Walk cycle table (shared across all humanoid costumes)
  - Head cycle tables and CostumeHeadDirTable for multi-limb compositing

Output: src/data/costumes/costume_data.inc (assembly include file)

Usage:
    python tools/gen_costume_rom.py
"""

import json
import os
import sys
from pathlib import Path

# Add tools dir to path for imports
TOOLS_DIR = Path(__file__).parent
PROJECT_ROOT = TOOLS_DIR.parent
CONVERTED_DIR = PROJECT_ROOT / "data" / "snes_converted" / "costumes"
OUTPUT_DIR = PROJECT_ROOT / "src" / "data" / "costumes"
OUTPUT_FILE = OUTPUT_DIR / "costume_data.inc"

# Max SCUMM costume resource ID (MI1 uses IDs 1-125)
MAX_COSTUME = 128

# Max pics per costume we'll include in ROM
MAX_PICS_PER_COSTUME = 50

# Max head pics per costume
MAX_HEAD_PICS = 20


def build_dcos_remap():
    """Build SCUMM resource ID -> extraction index mapping from DCOS directory.

    Returns dict {scumm_id: extraction_index} so CostumeDirTable can be
    indexed by SCUMM resource ID (what game scripts use) instead of
    extraction order (what the filesystem has).
    """
    index_path = PROJECT_ROOT / "data" / "monkeypacks" / "talkie" / "monkey.000"
    data_path = PROJECT_ROOT / "data" / "monkeypacks" / "talkie" / "monkey.001"

    if not index_path.exists() or not data_path.exists():
        print("WARNING: MI1 data files not found, skipping DCOS remap")
        return {}

    try:
        import io
        from dcos_mapping import build_dcos_mapping
        costumes_dir = PROJECT_ROOT / "data" / "scumm_extracted" / "costumes"
        # Suppress verbose output from dcos_mapping
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        mapping = build_dcos_mapping(str(index_path), str(data_path), costumes_dir)
        sys.stdout = old_stdout
        # mapping is list of (scumm_id, room, extractor_idx, extractor_file)
        remap = {}
        for scumm_id, room, ext_idx, ext_file in mapping:
            remap[scumm_id] = ext_idx
        print(f"DCOS remap: {len(remap)} SCUMM IDs mapped to extraction indices")
        return remap
    except Exception as e:
        sys.stdout = old_stdout
        print(f"WARNING: DCOS remap failed: {e}")
        return {}


def scan_costumes():
    """Scan converted costume directories and return dict of {costume_num: info}."""
    costumes = {}
    if not CONVERTED_DIR.exists():
        print(f"Warning: {CONVERTED_DIR} not found")
        return costumes

    for d in sorted(CONVERTED_DIR.iterdir()):
        if not d.is_dir() or not d.name.startswith("cost_"):
            continue
        try:
            num = int(d.name.split("_")[1])
        except (IndexError, ValueError):
            continue

        # Find all body pic files (limb 0)
        pics = []
        for i in range(MAX_PICS_PER_COSTUME):
            chr_file = d / f"pic{i:02d}.chr"
            oam_file = d / f"pic{i:02d}.oam"
            if chr_file.exists() and oam_file.exists():
                json_file = d / f"pic{i:02d}.json"
                meta = {}
                if json_file.exists():
                    with open(json_file) as f:
                        meta = json.load(f)
                pics.append({
                    "index": i,
                    "chr_path": chr_file,
                    "oam_path": oam_file,
                    "chr_size": chr_file.stat().st_size,
                    "oam_size": oam_file.stat().st_size,
                    "meta": meta,
                })

        # Find all head pic files (limb 1)
        head_pics = []
        for i in range(MAX_HEAD_PICS):
            chr_file = d / f"head_pic{i:02d}.chr"
            oam_file = d / f"head_pic{i:02d}.oam"
            if chr_file.exists() and oam_file.exists():
                json_file = d / f"head_pic{i:02d}.json"
                meta = {}
                if json_file.exists():
                    with open(json_file) as f:
                        meta = json.load(f)
                head_pics.append({
                    "index": i,
                    "chr_path": chr_file,
                    "oam_path": oam_file,
                    "chr_size": chr_file.stat().st_size,
                    "oam_size": oam_file.stat().st_size,
                    "meta": meta,
                })

        pal_file = d / "palette.pal"
        if not pal_file.exists() or not pics:
            continue

        costumes[num] = {
            "dir": d,
            "palette": pal_file,
            "pics": pics,
            "num_pics": len(pics),
            "max_pic_idx": max((p["index"] for p in pics), default=0),
            "head_pics": head_pics,
            "num_head_pics": len(head_pics),
            "max_head_idx": max((p["index"] for p in head_pics), default=0) if head_pics else 0,
        }

    return costumes


def rel_path(p):
    """Return path relative to project root, using forward slashes."""
    return str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")


def generate_assembly(costumes, dcos_remap=None):
    """Generate the assembly include file.

    Args:
        costumes: dict {extraction_idx: info} from scan_costumes()
        dcos_remap: dict {scumm_id: extraction_idx} from build_dcos_remap()
    """
    lines = []
    lines.append("; Auto-generated by tools/gen_costume_rom.py - DO NOT EDIT")
    lines.append("; Costume ROM data for multi-actor rendering")
    lines.append("")

    # Determine which costume numbers we have
    costume_nums = sorted(costumes.keys())
    print(f"Generating ROM data for {len(costume_nums)} costumes: {costume_nums}")

    # --- Body walk cycle tables ---
    # Costume 1 (SCUMM ID 1) = standard Guybrush. 6-step walk per direction.
    # ROM pic index = SCUMM pic index (NULLs at 1,22 exist as files, not skipped):
    #   South/front: stand=pic0, walk=pics2-7 (pic1 is NULL, skipped in table)
    #   East/side:   stand=pic8, walk=pics9-14
    #   North/rear:  stand=pic21, walk=pics15-20 (pic22 is NULL, not in table)
    #   West = East H-flipped by renderer
    lines.append(";===================================================================")
    lines.append("; Walk cycle tables (ROM pic index per animation step)")
    lines.append("; Index 0 = stand frame, indices 1-6 = walk cycle (costume 1)")
    lines.append(";===================================================================")
    lines.append(".section \"CostumeWalkCycle\" superfree")
    lines.append("CostumeWalkCycleNorth:                  ; north/rear (stand=pic21, walk=pics15-20)")
    lines.append("  .db 21, 15, 16, 17, 18, 19, 20")
    lines.append("CostumeWalkCycleSide:                   ; east-west/side (stand=pic8, walk=pics9-14)")
    lines.append("  .db 8, 9, 10, 11, 12, 13, 14")
    lines.append("CostumeWalkCycleFront:                  ; south/front (stand=pic0, walk=pics2-7)")
    lines.append("  .db 0, 2, 3, 4, 5, 6, 7")
    lines.append("")

    # --- Head cycle tables (parallel to body, $FF = no head needed) ---
    # talkStop defaults: N=head12 (rear), E=head6 (side), S=head0 (front)
    # Walk frames have head baked in, so $FF during walk
    lines.append("; Head cycle tables (limb 1 pic index per step, $FF = no head)")
    lines.append("; Index 0 = stand head, indices 1-6 = $FF (walk has head baked in)")
    lines.append("CostumeHeadCycleNorth:                  ; rear head (talkStop W = head pic 12)")
    lines.append("  .db 12, $FF, $FF, $FF, $FF, $FF, $FF")
    lines.append("CostumeHeadCycleSide:                   ; side head (talkStop E = head pic 6)")
    lines.append("  .db 6, $FF, $FF, $FF, $FF, $FF, $FF")
    lines.append("CostumeHeadCycleFront:                  ; front head (talkStop S = head pic 0)")
    lines.append("  .db 0, $FF, $FF, $FF, $FF, $FF, $FF")
    lines.append("")
    lines.append(".define COSTUME_WALK_CYCLE_LEN 7")
    lines.append(".define COSTUME_WALK_ANIM_SPEED 4")
    lines.append(".define COSTUME_HEAD_TILE_OFFSET $18   ; head tiles start 24 tiles after body base")
    lines.append(".define COSTUME_HEAD_VRAM_OFFSET $300  ; head VRAM = body VRAM + $300 bytes")
    lines.append(".ends")
    lines.append("")

    # --- Per-costume data sections ---
    for num in costume_nums:
        info = costumes[num]
        prefix = f"Cost{num:03d}"
        pal_rel = rel_path(info["palette"])

        head_label = ""
        if info["head_pics"]:
            head_label = f", {info['num_head_pics']} head pics"

        lines.append(f";===================================================================")
        lines.append(f"; Costume {num} - {info['num_pics']} body pics{head_label}")
        lines.append(f";===================================================================")
        lines.append(f".section \"{prefix}_Data\" superfree")
        lines.append("")

        # Palette
        lines.append(f"{prefix}_Pal:")
        lines.append(f'  .incbin "{pal_rel}"')
        lines.append("")

        # Body frame lookup tables — indexed by pic FILE number (sparse, gaps → pic00 fallback)
        # This ensures walk cycle tables (which use file-number indices) work correctly
        # even when some pic numbers are missing (e.g., pic01 absent from Cost058)
        max_pic_idx = max(p["index"] for p in info["pics"])
        pic_by_idx = {p["index"]: p for p in info["pics"]}
        fallback_pic = info["pics"][0]  # pic00 as fallback for gaps

        lines.append(f"; Body frame CHR lookup (word addr, bank, length)")
        lines.append(f"; Sparse table: {max_pic_idx + 1} entries indexed by pic file number")
        lines.append(f"{prefix}_ChrLo:")
        for i in range(max_pic_idx + 1):
            p = pic_by_idx.get(i, fallback_pic)
            gap = "  ; gap->pic00" if i not in pic_by_idx else ""
            lines.append(f"  .dw {prefix}_Pic{p['index']:02d}_Chr{gap}")
        lines.append("")

        lines.append(f"{prefix}_ChrHi:")
        for i in range(max_pic_idx + 1):
            p = pic_by_idx.get(i, fallback_pic)
            lines.append(f"  .db :{prefix}_Pic{p['index']:02d}_Chr")
        lines.append("")

        lines.append(f"{prefix}_ChrLen:")
        for i in range(max_pic_idx + 1):
            p = pic_by_idx.get(i, fallback_pic)
            lines.append(f"  .dw {prefix}_Pic{p['index']:02d}_ChrEnd - {prefix}_Pic{p['index']:02d}_Chr")
        lines.append("")

        lines.append(f"; Body frame OAM lookup (word addr, bank)")
        lines.append(f"{prefix}_OamLo:")
        for i in range(max_pic_idx + 1):
            p = pic_by_idx.get(i, fallback_pic)
            lines.append(f"  .dw {prefix}_Pic{p['index']:02d}_Oam")
        lines.append("")

        lines.append(f"{prefix}_OamHi:")
        for i in range(max_pic_idx + 1):
            p = pic_by_idx.get(i, fallback_pic)
            lines.append(f"  .db :{prefix}_Pic{p['index']:02d}_Oam")
        lines.append("")

        # Body frame data (CHR + OAM binary includes)
        for pic in info["pics"]:
            chr_rel = rel_path(pic["chr_path"])
            oam_rel = rel_path(pic["oam_path"])
            lines.append(f"{prefix}_Pic{pic['index']:02d}_Chr:")
            lines.append(f'  .incbin "{chr_rel}"')
            lines.append(f"{prefix}_Pic{pic['index']:02d}_ChrEnd:")
            lines.append(f"{prefix}_Pic{pic['index']:02d}_Oam:")
            lines.append(f'  .incbin "{oam_rel}"')
            lines.append("")

        # Head frame tables (if limb 1 exists) — sparse like body tables
        if info["head_pics"]:
            max_head_idx = max(p["index"] for p in info["head_pics"])
            head_by_idx = {p["index"]: p for p in info["head_pics"]}
            fallback_head = info["head_pics"][0]

            lines.append(f"; Head frame CHR lookup (limb 1, sparse: {max_head_idx + 1} entries)")
            lines.append(f"{prefix}_HeadChrLo:")
            for i in range(max_head_idx + 1):
                p = head_by_idx.get(i, fallback_head)
                lines.append(f"  .dw {prefix}_HeadPic{p['index']:02d}_Chr")
            lines.append("")

            lines.append(f"{prefix}_HeadChrHi:")
            for i in range(max_head_idx + 1):
                p = head_by_idx.get(i, fallback_head)
                lines.append(f"  .db :{prefix}_HeadPic{p['index']:02d}_Chr")
            lines.append("")

            lines.append(f"{prefix}_HeadChrLen:")
            for i in range(max_head_idx + 1):
                p = head_by_idx.get(i, fallback_head)
                lines.append(f"  .dw {prefix}_HeadPic{p['index']:02d}_ChrEnd - {prefix}_HeadPic{p['index']:02d}_Chr")
            lines.append("")

            lines.append(f"; Head frame OAM lookup (limb 1)")
            lines.append(f"{prefix}_HeadOamLo:")
            for i in range(max_head_idx + 1):
                p = head_by_idx.get(i, fallback_head)
                lines.append(f"  .dw {prefix}_HeadPic{p['index']:02d}_Oam")
            lines.append("")

            lines.append(f"{prefix}_HeadOamHi:")
            for i in range(max_head_idx + 1):
                p = head_by_idx.get(i, fallback_head)
                lines.append(f"  .db :{prefix}_HeadPic{p['index']:02d}_Oam")
            lines.append("")

            # Head frame data
            for pic in info["head_pics"]:
                chr_rel = rel_path(pic["chr_path"])
                oam_rel = rel_path(pic["oam_path"])
                lines.append(f"{prefix}_HeadPic{pic['index']:02d}_Chr:")
                lines.append(f'  .incbin "{chr_rel}"')
                lines.append(f"{prefix}_HeadPic{pic['index']:02d}_ChrEnd:")
                lines.append(f"{prefix}_HeadPic{pic['index']:02d}_Oam:")
                lines.append(f'  .incbin "{oam_rel}"')
                lines.append("")

        lines.append(".ends")
        lines.append("")

    # --- Costume directory table ---
    # 8 bytes per entry: palLo(2) + palHi(1) + numPics(1) + chrLoPtr(2) + chrLoBank(1) + pad(1)
    lines.append(";===================================================================")
    lines.append("; CostumeDirTable - indexed by SCUMM resource ID (via DCOS remap)")
    lines.append("; 8 bytes per entry: palLo, palHi, numPics, pad, chrLoPtr, chrLoBank")
    lines.append("; Entry at offset = scumm_costume_id * 8")
    lines.append("; Unsupported costumes point to Guybrush walking as fallback")
    lines.append(";===================================================================")
    lines.append('.section "CostumeDirTable" superfree')
    lines.append("")

    # Build SCUMM ID -> extraction index remap table
    # CostumeDirTable is indexed by SCUMM resource ID (what game scripts use).
    # dcos_remap maps SCUMM ID -> extraction index (what filesystem has).
    costume_remap = {}
    if dcos_remap:
        for scumm_id, ext_idx in dcos_remap.items():
            if ext_idx in costumes:
                costume_remap[scumm_id] = ext_idx

    # Determine fallback costume — use SCUMM ID 13 (Guybrush walking) if remapped,
    # otherwise extraction index 1
    fallback_ext = costume_remap.get(13, costume_remap.get(5, 1))
    fallback = fallback_ext if fallback_ext in costumes else (costume_nums[0] if costume_nums else None)

    lines.append("CostumeDirTable:")
    for i in range(MAX_COSTUME):
        num = costume_remap.get(i, i) if i in costumes or i in costume_remap else fallback
        if num is not None:
            prefix = f"Cost{num:03d}"
            # Use sparse table size (max pic index + 1) not count of existing pics
            n_pics = costumes[num]["max_pic_idx"] + 1
            lines.append(f"  ; costume {i}" + (f" -> fallback {num}" if i not in costumes else ""))
            lines.append(f"  .dw {prefix}_Pal")
            lines.append(f"  .db :{prefix}_Pal")
            lines.append(f"  .db {n_pics}")
            lines.append(f"  .dw {prefix}_ChrLo")
            lines.append(f"  .db :{prefix}_ChrLo")
            lines.append(f"  .db 0")
        else:
            lines.append(f"  ; costume {i} - no data")
            lines.append(f"  .dw 0")
            lines.append(f"  .db 0, 0")
            lines.append(f"  .dw 0")
            lines.append(f"  .db 0, 0")

    lines.append("")

    # OAM lookup table (same structure, separate for clarity)
    lines.append("CostumeDirOamTable:")
    for i in range(MAX_COSTUME):
        num = costume_remap.get(i, i) if i in costumes or i in costume_remap else fallback
        if num is not None:
            prefix = f"Cost{num:03d}"
            lines.append(f"  .dw {prefix}_OamLo")
            lines.append(f"  .db :{prefix}_OamLo")
            lines.append(f"  .db 0")
            lines.append(f"  .dw {prefix}_OamHi")
            lines.append(f"  .db :{prefix}_OamHi")
            lines.append(f"  .db 0")
        else:
            lines.append(f"  .dw 0, 0, 0, 0")

    lines.append("")

    # --- Head directory table ---
    # 4 bytes per entry: headChrLoPtr(2) + headChrLoBank(1) + numHeadPics(1)
    # Entry = 0,0,0,0 means no head for this costume
    lines.append(";===================================================================")
    lines.append("; CostumeHeadDirTable - indexed by costume number")
    lines.append("; 4 bytes per entry: headBasePtr(2) + headBaseBank(1) + numHeadPics(1)")
    lines.append("; headBasePtr points to HeadChrLo; HeadChrHi, HeadChrLen, HeadOamLo,")
    lines.append("; HeadOamHi follow at computed offsets (same layout as body tables)")
    lines.append(";===================================================================")
    lines.append("CostumeHeadDirTable:")
    for i in range(MAX_COSTUME):
        # Head table: only populate for costumes that actually have head pics
        # No fallback — costumes without heads get 0 (skip head rendering)
        actual = costume_remap.get(i, i) if i in costumes or i in costume_remap else None
        if actual is not None and actual in costumes and costumes[actual]["head_pics"]:
            prefix = f"Cost{actual:03d}"
            n_head = costumes[actual]["max_head_idx"] + 1
            lines.append(f"  .dw {prefix}_HeadChrLo          ; costume {i}")
            lines.append(f"  .db :{prefix}_HeadChrLo, {n_head}")
        else:
            lines.append(f"  .dw 0")
            lines.append(f"  .db 0, 0                          ; costume {i} - no head")

    lines.append("")
    lines.append(".ends")
    lines.append("")

    return "\n".join(lines) + "\n"


def main():
    costumes = scan_costumes()
    if not costumes:
        print("No converted costumes found!")
        sys.exit(1)

    print(f"Found {len(costumes)} converted costumes")
    for num, info in sorted(costumes.items()):
        total_chr = sum(p["chr_size"] for p in info["pics"])
        head_info = ""
        if info["head_pics"]:
            total_head = sum(p["chr_size"] for p in info["head_pics"])
            head_info = f", {info['num_head_pics']} head pics ({total_head:,}B)"
        print(f"  cost_{num:03d}: {info['num_pics']} body pics, "
              f"{total_chr:,} bytes CHR{head_info}")

    dcos_remap = build_dcos_remap()
    asm = generate_assembly(costumes, dcos_remap)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(asm)
    print(f"\nWrote {OUTPUT_FILE} ({len(asm):,} bytes)")


if __name__ == "__main__":
    main()
