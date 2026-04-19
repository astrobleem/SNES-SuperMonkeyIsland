;---------------------------------------------------------------------------
; SCUMM color cycling — constants, struct, and bank-$7F WRAM layout.
;
; Kept out of scummvm.h so the main scummvm.65816 translation unit does
; not duplicate cycle-related symbols (ramsections would be emitted twice).
; scummvm.65816 never touches these symbols directly; it invokes the cycle
; engine via JSL to scummvm.cycle.{reset,tick,writeToCgram}.
;---------------------------------------------------------------------------

; Sized to MI1's actual usage (measured across all 4 rooms with CYCL data):
;   max cycles per room      = 2 (room_065 hellhall)
;   max positions per cycle  = 16 (room_039 hellmaze, room_065 cycle 2)
;   max CGRAM entries/cycle  = 8 (room_039 hellmaze)
.define SCUMM_MAX_CYCLES              2
.define SCUMM_CYCLE_MAX_POSITIONS     16
.define SCUMM_CYCLE_MAX_ENTRIES       8

.struct cycleState
  framesLeft     db                               ; +$00  counts down per frame
  fps            db                               ; +$01  reload value (frames/step)
  flags          db                               ; +$02  bit 1 = reverse
  numPositions   db                               ; +$03  0..SCUMM_CYCLE_MAX_POSITIONS
  numEntries     db                               ; +$04  0..SCUMM_CYCLE_MAX_ENTRIES
  pad            db                               ; +$05
  positionColors ds SCUMM_CYCLE_MAX_POSITIONS * 2 ; +$06  BGR555 ring (32B)
  entries        ds SCUMM_CYCLE_MAX_ENTRIES * 2   ; +$26  (cgramSlot, posIdx) pairs (16B)
.endst

; Bank $7F region (before SCUMM_BOX_WRAM=$7F5000). NMI slots at $7F4C00
; are < 100 bytes; allocating at $7F4D00 leaves ample headroom.
; Engine sets DBR=$7F and uses `.w` with the low-16 aliases below — that
; lets us use dec.w / inc.w (the 65816 has no `.l` forms for those).
.define SCUMM_CYCLE_NUM_LO            $4D00    ; DBR=$7F + sta.w → $7F:4D00
.define SCUMM_CYCLE_STATES_LO         $4D01    ; cycleStates[SCUMM_MAX_CYCLES]

; Scratch area — loader + runtime temps. Lives in the same bank so DBR
; stays fixed at $7F throughout every cycle entrypoint.
.define SCUMM_CYCLE_SCRATCH_LO        $4D80
.define SCUMM_CYCLE_SCR_CYCLES_LEFT   (SCUMM_CYCLE_SCRATCH_LO + 0)  ; db
.define SCUMM_CYCLE_SCR_POS_COUNT     (SCUMM_CYCLE_SCRATCH_LO + 1)  ; db
.define SCUMM_CYCLE_SCR_POS_IDX       (SCUMM_CYCLE_SCRATCH_LO + 2)  ; db
.define SCUMM_CYCLE_SCR_SLOT_COUNT    (SCUMM_CYCLE_SCRATCH_LO + 3)  ; db
.define SCUMM_CYCLE_SCR_ENTRY_COUNT   (SCUMM_CYCLE_SCRATCH_LO + 4)  ; db
.define SCUMM_CYCLE_SCR_CYCLE_OFS     (SCUMM_CYCLE_SCRATCH_LO + 6)  ; dw (aligned)
.define SCUMM_CYCLE_SCR_SAVE0         (SCUMM_CYCLE_SCRATCH_LO + 8)  ; dw
.define SCUMM_CYCLE_SCR_SAVE1         (SCUMM_CYCLE_SCRATCH_LO + 10) ; dw
; Total footprint ≈ 1 + SCUMM_MAX_CYCLES * _sizeof_cycleState + 12 ≈ 121 bytes.
