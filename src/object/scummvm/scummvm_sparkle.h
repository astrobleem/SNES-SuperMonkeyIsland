;---------------------------------------------------------------------------
; LucasArts logo sparkle — OBJ-layer sprite effect.
;
; The MI1 intro drives sparkle animation by calling drawObject(110..114, x, y)
; at varying positions. Objects 110-114 are 16x32 rectangles entirely filled
; with color 88 (bright magenta). Our BG tilemap is ill-suited to flashing
; overlays (tile-cache pressure), so we render them on the OBJ layer instead.
;
; State lives at $7F:4E00 (bank $7F WRAM, after cycle state at $7F:4D00).
; Engine code sets DBR=$7F and uses .w aliases so inc.w / stz.w / etc. work.
;---------------------------------------------------------------------------

.define SPARKLE_NUM_SLOTS       5
.define SPARKLE_FIRST_OBJ_ID    110
.define SPARKLE_LAST_OBJ_ID     114
.define SPARKLE_ROOM            10

; VRAM: sprite CHR base is word $6000 (ObjSel=$03). Tile $F0 is the CURSOR
; tile — avoid. Actors use tile ids 0..$EF. Use tile $F8 for sparkle.
.define SPARKLE_VRAM_TILE_ID    $F8
.define SPARKLE_VRAM_DEST_WORD  $6F80
.define SPARKLE_CHR_BYTES       32             ; 1 solid 8x8 4bpp tile

; CGRAM: OBJ palettes start at byte $100. Pal 5 = cursor; pal 6 is free.
.define SPARKLE_OBJ_PAL         6
.define SPARKLE_CGRAM_DEST_BYTE $E0            ; CGADD color index; OBJ pal N = 128 + N*16
.define SPARKLE_PAL_BYTES       32

; OAM attr byte: priority 3 ($30) + OBJ pal 6 ($0C) = $3C.
.define SPARKLE_OAM_ATTR        $3C

; Each sparkle = 2 wide x 4 tall grid of 8x8 hw sprites = 8 OAM entries.
; 5 sparkles * 8 = 40 entries, well under 128 OAM slots.
.define SPARKLE_ENTRIES_PER     8

.struct sparkleSlot
  visible db                                   ; +$00  0=hidden, 1=visible
  x_lo    db                                   ; +$01  room-space X low
  x_hi    db                                   ; +$02  room-space X high
  y       db                                   ; +$03  screen-space Y
.endst

; Slot array at $7F:4E00. Engine sets DBR=$7F to reach these with .w.
.define SPARKLE_STATE_LO        $4E00

; Byte at $7F:4E20 holds a free-running frame counter used to drive the
; auto-animation. buildOam increments it every call; position index and
; visibility mask derive from its bits.
.define SPARKLE_ANIM_COUNTER    $4E20
