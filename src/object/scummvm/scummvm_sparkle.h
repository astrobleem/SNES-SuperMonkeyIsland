;---------------------------------------------------------------------------
; LucasArts logo sparkle — OBJ-layer rendering driven by MI1's own costume art.
;
; cost_005_room010 in MI1 talkie contains 6 "twinkle" frames (5x5, 7x7, 9x9,
; 11x11, 13x13, 19x19), each a 4-point starburst with bright-yellow center
; fading through pink to magenta tips. Our CHR converter picks frames
; 7/9/11/13, centers each in a 16x16 SNES sprite block, and packs them into
; a 1024-byte CHR (32 tiles, 16 per CHR row so 16x16 hw-sprite addressing
; hits the right quadrant tiles).
;
; Runtime: 5 sparkles cycle through the 4 frames on a grow-hold-shrink
; schedule, staggered in phase so the logo always has 1-2 sparkles bright
; while others pulse. Positions are hand-picked on LUCASFILM GAMES letters.
;
; Slot state and runtime counter live in bank $7F. Engine sets DBR=$7F for
; .w access.
;---------------------------------------------------------------------------

.define SPARKLE_NUM_SLOTS       5
.define SPARKLE_FIRST_OBJ_ID    110
.define SPARKLE_LAST_OBJ_ID     114
.define SPARKLE_ROOM            10

; VRAM: sprite CHR base is word $6000 (ObjSel=$03). Room 10 has no actors
; competing for these tiles; actor costume load in non-logo rooms rewrites
; them later, which is fine.
.define SPARKLE_VRAM_DEST_WORD  $6000
.define SPARKLE_CHR_BYTES       1024           ; 32 tiles * 32 bytes

; OBJ palette 6 is free; pal 5 is the cursor's.
.define SPARKLE_OBJ_PAL         6
.define SPARKLE_CGRAM_DEST_BYTE $E0             ; CGADD color index = 128+6*16
.define SPARKLE_PAL_BYTES       32

; OAM attr byte: priority 3 ($30) + OBJ pal 6 ($0C) = $3C.
.define SPARKLE_OAM_ATTR        $3C

; Frame tile IDs (first of each 16x16 sprite's 2x2 block). The converter
; lays out frames sequentially on the top CHR row (tiles 0..15) with the
; bottom halves on CHR row 2 (tiles 16..31). A 16x16 sprite at tile N
; references N, N+1, N+16, N+17 — exactly the right quadrant tiles.
.define SPARKLE_FRAME0_TILE     0               ; 7x7   (smallest in active cycle)
.define SPARKLE_FRAME1_TILE     2               ; 9x9
.define SPARKLE_FRAME2_TILE     4               ; 11x11
.define SPARKLE_FRAME3_TILE     6               ; 13x13 (peak)

.struct sparkleSlot
  visible db                                   ; +$00
  x_lo    db                                   ; +$01
  x_hi    db                                   ; +$02
  y       db                                   ; +$03
.endst

; Slot state at $7F:4E00 (5 * 4 = 20 bytes).
.define SPARKLE_STATE_LO        $4E00

; Free-running animation counter at $7F:4E20 (8-bit). Drives phase and
; frame selection per slot.
.define SPARKLE_ANIM_COUNTER    $4E20
