;=============================================================================
; Named palette slot contract — SNES Super Monkey Island
;=============================================================================
;
; CGRAM layout (Mode 1, 4bpp BG1/BG2 + 2bpp BG3):
;
;   Palette 0 ($00-$0F)  — SCUMM UI (setPalColor 0..15 targets)
;   Palette 1 ($10-$1F)  — Art
;   Palette 2 ($20-$2F)  — Art
;   Palette 3 ($30-$3F)  — Art
;   Palette 4 ($40-$4F)  — Art
;   Palette 5 ($50-$5F)  — Art
;   Palette 6 ($60-$6F)  — Verb highlight font (c1-c3) + universal warm
;   Palette 7 ($70-$7F)  — Verb normal font (c1-c3) + universal cool
;
; Pal6/pal7 slots are CANONICAL — room art converter reserves them; runtime
; writeVerbColors installs c1-c3; room palette files hold c4-c15.
;
; Any code that wants a "known color" should reference these CGRAM slot
; constants rather than issuing setPalColor. The values are in sync with
; PAL6_VERB_PLUS_UNIVERSAL / PAL7_VERB_PLUS_UNIVERSAL in snes_room_converter.py.
;=============================================================================

; --- CGRAM byte offsets (use as CGADD values) ---

; Verb highlight font (pal6 c1-c3) — written by scummvm.writeVerbColors
.define CGRAM_VERB_HL_FILL          $61    ; $6318 light grey
.define CGRAM_VERB_HL_SHADOW        $62    ; $294A dark grey
.define CGRAM_VERB_HL_BODY          $63    ; $03FF yellow body

; Verb normal font (pal7 c1-c3) — written by scummvm.writeVerbColors
.define CGRAM_VERB_FILL             $71    ; $6318 light grey
.define CGRAM_VERB_SHADOW           $72    ; $294A dark grey
.define CGRAM_VERB_BODY             $73    ; $7FFF white body

; Universal warm (pal6 c4-c15) — baked into every room's .pal at convert time
.define CGRAM_UNI_WHITE             $64    ; $7FFF
.define CGRAM_UNI_MAGENTA           $65    ; $7C1F
.define CGRAM_UNI_RED               $66    ; $001F
.define CGRAM_UNI_GREEN             $67    ; $03E0
.define CGRAM_UNI_BLUE              $68    ; $7C00
.define CGRAM_UNI_YELLOW            $69    ; $03FF
.define CGRAM_UNI_ORANGE            $6A    ; $0CA5
.define CGRAM_UNI_TAN               $6B    ; $2F3F
.define CGRAM_UNI_BROWN             $6C    ; $18C6
.define CGRAM_UNI_OLIVE             $6D    ; $039F
.define CGRAM_UNI_PALE_SKY          $6E    ; $7FDB

; Universal cool (pal7 c4-c15) — baked into every room's .pal at convert time
.define CGRAM_UNI_GREY_MID          $74    ; $4A52
.define CGRAM_UNI_GREY_DARK         $75    ; $0C63
.define CGRAM_UNI_PURPLE            $76    ; $2805
.define CGRAM_UNI_BLUE_DARK         $77    ; $1841
.define CGRAM_UNI_BLUE_MID          $78    ; $4E94
.define CGRAM_UNI_TEAL              $79    ; $2A95
.define CGRAM_UNI_GREEN_FOREST      $7A    ; $0240
.define CGRAM_UNI_GREEN_LIGHT       $7B    ; $3FC0

; --- BGR555 values (matching PAL6/PAL7_VERB_PLUS_UNIVERSAL in Python) ---

.define COLOR_VERB_FILL             $6318
.define COLOR_VERB_SHADOW           $294A
.define COLOR_VERB_BODY             $7FFF    ; normal (white)
.define COLOR_VERB_HL_BODY          $03FF    ; highlight (yellow)

.define COLOR_WHITE                 $7FFF
.define COLOR_BLACK                 $0000
.define COLOR_RED                   $001F
.define COLOR_GREEN                 $03E0
.define COLOR_BLUE                  $7C00
.define COLOR_YELLOW                $03FF
.define COLOR_MAGENTA               $7C1F

;=============================================================================
