---
name: snes-sprite-gen
description: "Generate SNES-hardware-compliant pixel art sprites as PNG from a text description or source image. Use this skill whenever the user wants to create, generate, or convert pixel art for the Super Nintendo — including requests for SNES sprites, 4bpp pixel art, retro game graphics, SNES tiles, OBJ graphics, or any task involving SNES palette/color constraints. Produces five output files: 1:1 PNG, 8x scaled preview PNG, indexed PNG (tilemap-ready), .pal file, and a palette swatch image."
---

# SNES Sprite Generator

Generates SNES-compliant pixel art sprites from a text prompt or source image.

## Hardware Constraints

| Property | Value |
|---|---|
| Color depth | 4bpp — 15 opaque colors + index 0 (transparent) |
| Valid sizes | 8×8, 16×16, 32×32, 64×64 px |
| Color precision | 5 bits per channel — snap all RGB values to multiples of 8 (0–248) |
| Palette slots | 8 OBJ palette slots available; index 0 in every slot = transparent |

## Step 1 — Infer Sprite Size

If the user doesn't specify a size, infer from context:

| Context | Size |
|---|---|
| Tile, cursor, small icon, coin | 8×8 |
| Item, small enemy, UI element | 16×16 |
| Character, NPC, protagonist, enemy | 32×32 |
| Boss, large vehicle, wide object | 64×64 |

## Step 2 — Design the Sprite as SVG

**Use a Python generator script — do not hand-write raw `<rect>` tags.** Hand-writing rects at any size beyond 8x8 is error-prone. Instead, write a short Python script that:

1. Defines a `colors` dict mapping single characters to BGR555-snapped hex values (all R/G/B divisible by 8)
2. Defines each row as a string of characters (one char per pixel, `'.'` = transparent)
3. Uses a `build_row()` helper to construct rows from `(col, char)` tuples for sparse rows
4. Asserts every row is exactly `size` characters wide before emitting
5. Generates SVG `<rect>` elements only for non-transparent pixels
6. Writes the SVG to `/home/Codex/sprite_source.svg`

**Pixel art design rules:**
- 1px black outline everywhere the sprite meets transparency
- Upper-left light source: specular highlight top-left, deepest shadow bottom-right
- 3-5 tone gradient per surface (specular -> bright -> mid -> shadow -> darkest)
- Lower half of any rounded shape drops one shading tier (implies underside of sphere/volume)
- Keep shading to 2-4 tones per surface — SNES sprites read at small sizes on CRTs
- Think in 8x8 tile blocks: align key features to 8px boundaries where possible

**Palette:** Define all colors upfront. Every R/G/B value must be a multiple of 8. Max 15 colors (index 0 = transparent).

**Verify before writing:** print the grid to stdout and assert all row lengths equal the target size.

## Step 3 — Run the Processing Pipeline

Install dependencies:

```bash
pip install pillow cairosvg numpy --break-system-packages 2>/dev/null
```

Then run the pipeline script:

```bash
python /home/Codex/snes-sprite-gen/scripts/process_sprite.py \
  --input /home/Codex/sprite_source.svg \
  --size <WIDTH> \
  --output-dir /mnt/user-data/outputs \
  --name sprite
```

For a source image input instead of SVG, pass the image path directly to `--input`. The script detects file type and skips SVG rasterization automatically.

## Step 4 — Present Outputs

Use `present_files` to surface all five outputs to the user:

```
/mnt/user-data/outputs/sprite.png           # 1:1 native resolution
/mnt/user-data/outputs/sprite_preview.png   # 8× nearest-neighbor scaled
/mnt/user-data/outputs/sprite_indexed.png   # Mode-P indexed PNG, tilemap-ready
/mnt/user-data/outputs/sprite.pal           # JASC-PAL, 16 entries (index 0 = transparent)
/mnt/user-data/outputs/sprite_swatch.png    # Horizontal palette swatch, 15 color blocks × 24px
```

Lead with `sprite_preview.png` so the user sees a legible version first.

## Source Image Input

When the user provides a reference image (photo, artwork, sketch) instead of a description:
1. Skip SVG generation entirely
2. Pass the image path to `--input`
3. The script resizes to target size (Lanczos for photos, nearest-neighbor for existing pixel art) then quantizes

## Notes

- **BGR555 round-trip**: SNES color registers store 5 bits per channel. Values not divisible by 8 will be truncated by hardware. Snapping in the palette ensures what you see in the preview is exactly what the SNES will display.
- **Index 0 is always transparent** on SNES OBJ (sprite) layers regardless of the color stored there. Never assign a visible color meaning to index 0 in the .pal file or indexed PNG.
- **Tilemap use**: For 32×32 and 64×64 sprites, each sprite is composed of multiple 8×8 CHR tiles in VRAM. The indexed PNG is arranged row-major so it can be sliced directly.
