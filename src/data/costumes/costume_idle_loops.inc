; Hand-authored idle-loop animation tables.
; Not auto-generated — extend freely for new looping props (torches, flames,
; waving flags, etc). Safe to edit this file directly.
;
; Runtime consumers:
;   - _scummvm.tickActorAnims (scummvm.65816) walks CostumeIdleLoops once per
;     frame for actors flagged active in SCUMM.actorIdleLoop.
;   - op_animateActor (scummvm.65816) scans CostumeIdleLoops when an
;     animateActor opcode fires with anim_id >= 8 and the actor isn't walking.
;     A match sets actorIdleLoop[slot] = entry_index + 1 (0 = inactive).
;
; Dispatch entry layout (8 bytes per entry):
;   +0  cost          (u8)  — costume ID matched against actor.costume
;   +1  anim_id_lo    (u8)  — low byte of op_animateActor's anim_id arg
;   +2  anim_id_hi    (u8)  — high byte (always 0 for v5 8-bit anim ids)
;   +3  pic_count     (u8)  — number of pics in the cycle
;   +4  pic_table_lo  (u16) — pic table long addr (low 16 bits)
;   +6  pic_table_bnk (u8)  — pic table bank byte
;   +7  tick_rate     (u8)  — frames per pic (0 → default 8)
;
; CostumeIdleLoops_end marks the end sentinel (op_animateActor uses this to
; bound the scan). Keep the sentinel updated when adding entries.

.section "CostumeIdleLoops" superfree

; ---- Cost033: campfire flame (4-frame loop) ----
; MI1 intro script calls animateActor(3, 248). cost_033 has 72 pics; pics 1-4
; are a reasonable first guess for the flame loop (verify visually after build).
CostumeIdleLoop_Cost033_Campfire:
  .db 0, 1, 2, 3

CostumeIdleLoops:
  ; cost, anim_lo, anim_hi, pic_count, ptr_lo, ptr_hi, ptr_bank, tick_rate
  .db 33, 248, 0, 4
  .dw CostumeIdleLoop_Cost033_Campfire
  .db :CostumeIdleLoop_Cost033_Campfire, 8
  ; Sentinel — cost=$FF terminates the op_animateActor scan. Keep last.
  .db $FF, 0, 0, 0, 0, 0, 0, 0
CostumeIdleLoops_end:

.ends
