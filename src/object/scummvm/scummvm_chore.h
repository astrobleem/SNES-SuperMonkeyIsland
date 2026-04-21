;---------------------------------------------------------------------------
; SCUMM v5 costume chore interpreter — WRAM layout + public API.
;
; Kept out of scummvm.h so the main scummvm.65816 translation unit does
; not duplicate chore-related ramsections (they would be emitted twice).
; scummvm.65816 never touches SCUMM.chore* symbols directly; it invokes
; the chore engine via JSL to scummvm.chore.{reset,tick,animateActor_impl}.
;---------------------------------------------------------------------------

; 16 limbs × 16 walk-slot actors. MI1 costumes use high limb numbers
; (bits 14/15 in limbMask for body/head). 16 limbs × 16 actors needs
; ~2 KB total in slot 1 — fits within the ~5 KB remaining headroom.
.define SCUMM_CHORE_LIMBS  16
.define SCUMM_WALK_ACTORS  16

.ramsection "scumm chore state" bank 0 slot 1
SCUMM.chorePic     ds SCUMM_WALK_ACTORS * SCUMM_CHORE_LIMBS       ;  64 B
SCUMM.choreCurpos  ds SCUMM_WALK_ACTORS * SCUMM_CHORE_LIMBS * 2   ; 128 B
SCUMM.choreStart   ds SCUMM_WALK_ACTORS * SCUMM_CHORE_LIMBS * 2   ; 128 B
SCUMM.choreEnd     ds SCUMM_WALK_ACTORS * SCUMM_CHORE_LIMBS * 2   ; 128 B
SCUMM.choreWait    ds SCUMM_WALK_ACTORS * SCUMM_CHORE_LIMBS       ;  64 B
SCUMM.choreStopped ds SCUMM_WALK_ACTORS                           ;  16 B

; Per-call scratch — used only during animateActor_impl / tick. Not
; persistent across frames; safe to trample between chore entry points.
SCUMM.choreScr_recCur    dw    ; animateActor: dispatch record-walk cursor
SCUMM.choreScr_byteBase  dw    ; actor_num * SCUMM_CHORE_LIMBS (chorePic/Wait idx base)
SCUMM.choreScr_wordBase  dw    ; actor_num * SCUMM_CHORE_LIMBS * 2 (word arrays base)
SCUMM.choreScr_csLo      dw    ; animateActor: per-limb cmd_start
SCUMM.choreScr_extra     dw    ; animateActor: per-limb extra byte
SCUMM.choreScr_limb      dw    ; limb loop counter
SCUMM.choreScr_mask      dw    ; animateActor: limbMask copy
SCUMM.choreScr_animId    dw    ; animateActor: anim_id
SCUMM.choreScr_actorIdx  dw    ; tick: actor loop counter
SCUMM.choreScr_curpos    dw    ; tick: working curpos offset into animCmds
SCUMM.choreScr_end       dw    ; tick: working end offset
SCUMM.choreScr_start     dw    ; tick: working start offset (loop anchor)
SCUMM.choreScr_cmd       dw    ; tick: current command byte (low 8 bits)
.ends

; JSL entry points (body in scummvm_chore.65816):
;   scummvm.chore.reset              — zero chore state (room change)
;   scummvm.chore.animateActor_impl  — actor.animateActor(frame) port
;   scummvm.chore.tick               — per-frame advance (call in play)
