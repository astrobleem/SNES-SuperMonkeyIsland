.include "src/config/config.inc"


.def BG.PALETTE.BITS %11100

;this is a hack, should be defined in animation file instead
.def BG.TILEMAP.LENGTH $800
.export BG.TILEMAP.LENGTH

;zp-vars,just a reference
.enum 0
  iterator INSTANCEOF iteratorStruct
  dimension INSTANCEOF dimensionStruct
  animation INSTANCEOF animationStruct
zpLen ds 0
.ende

;object class static flags, default properties and zero page 
.define CLASS.FLAGS OBJECT.FLAGS.Present
.define CLASS.PROPERTIES 0
.define CLASS.ZP_LENGTH zpLen
.define CLASS.IMPLEMENTS interface.dimension

.bank 3 slot 0
.base BSL

.section "BgBitflagLUT" free
BgBitflagLUT:
  .db T_BG1_ENABLE
  .db T_BG2_ENABLE
  .db T_BG3_ENABLE
  .db T_BG4_ENABLE
.ends

.bank 3 slot 0
.base BSL
.section "palette_granularity_lut" free
PALETTE.GRANULARITY.LUT:
  .dw PALETTE.GRANULARITY.1BPP
  .dw PALETTE.GRANULARITY.2BPP
  .dw PALETTE.GRANULARITY.4BPP
  .dw PALETTE.GRANULARITY.8BPP
  .dw PALETTE.GRANULARITY.8BPP
.ends

.bank 3 slot 0
.base BSL
.section "tilemap_length_lut" free
TILEMAP.LENGTH.LUT:
  .dw TILEMAP.SIZE.SINGLE
  .dw TILEMAP.SIZE.DUAL
  .dw TILEMAP.SIZE.DUAL
  .dw TILEMAP.SIZE.QUADRUPLE
.ends

.bank 3 slot 0
.base BSL
.section "tiles_mask_lut" free
TILES.MASK.LUT:
  .dw $fff0
  .dw $ff0f
  .dw $f0ff
  .dw $0fff
.ends

.bank 3 slot 0
.base BSL
.section "tiles_shift_lut" free
TILES.SHIFT.LUT:
  .dw 0
  .dw 4
  .dw 8
  .dw 12
.ends

.bank 3 slot 0
.base BSL
.Section "BackgroundAnimationLUT" free
BackgroundAnimationLUT:
  PTRLONG BackgroundAnimationLUT BG.msu1

.ends
; BG.msu1 pinned to bank 3 (~28KB)
.bank 3 slot 0
.base BSL
.section "msu1.gfx_bg.animation" free
  BG.msu1:
  .incbin "build/data/backgrounds/msu1.gfx_bg.animation"
.ends

