;---------------------------------------------------------------------------
; LucasArts logo sparkle — consumes pre-built MI1 cost_005 assets already
; linked into the ROM via src/data/costumes/costume_data.inc.
;
; cost_005_room010 is MI1's sparkle costume — a 4-point starburst with
; yellow-white center fading through pink to magenta tips. The existing
; SCUMM costume decoder + SNES costume converter already produced per-pic
; CHR, OAM tables, and a 16-color OBJ sub-palette.  We use pic04 (9x9,
; 3 OAM entries) as the one rendered frame and cycle visibility per slot
; for twinkle. Upgrading to grow/shrink by swapping pics between frames
; is a later pass.
;
; State at $7F:4E00. Engine sets DBR=$7F for .w access.
;---------------------------------------------------------------------------

.define SPARKLE_NUM_SLOTS       5
.define SPARKLE_FIRST_OBJ_ID    110
.define SPARKLE_LAST_OBJ_ID     114
.define SPARKLE_ROOM            10

; VRAM tile base for sparkle CHR. Sprite CHR window is word $6000+. Tile 16
; = word $6000 + 16*16 = word $6100. Room 10 runs no actors, so tiles
; 16..18 are free; when we leave room 10 the next room's actor costume
; load overwrites them.
.define SPARKLE_VRAM_TILE_BASE  16
.define SPARKLE_VRAM_DEST_WORD  $6100

; CGRAM color index for OBJ pal 6 (128 + 6*16 = 224).
.define SPARKLE_CGRAM_DEST_BYTE $E0
.define SPARKLE_OBJ_PAL         6

; OAM attr: priority 3 ($30) + pal 6 ($0C) = $3C.
.define SPARKLE_OAM_ATTR        $3C

.struct sparkleSlot
  visible db
  x_lo    db
  x_hi    db
  y       db
.endst

.define SPARKLE_STATE_LO        $4E00
.define SPARKLE_ANIM_COUNTER    $4E20
