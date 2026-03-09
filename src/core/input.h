.include "src/config/config.inc"

.def INPUT.DEVICE.COUNT 1

.enum 0 export
  INPUT.DEVICE.ID.0 db
  INPUT.DEVICE.ID.MAX ds 0
.ende

.struct input
  press dw
  trigger dw
  mask dw
  old dw
.endst
		 
.ramsection "global.input" bank 0 slot 1
  CheckJoypadMode	db
  inputDevice INSTANCEOF input INPUT.DEVICE.COUNT
  mouseDetected     dw    ; $0001 = mouse on port 1, $0000 = joypad
  mouseButtons      dw    ; raw button bits from auto-read (bit7=right, bit6=left)
  mouseDeltaX       dw    ; signed 16-bit X displacement (consumed per frame)
  mouseDeltaY       dw    ; signed 16-bit Y displacement (consumed per frame)
  mouseSensitivity  db    ; current sensitivity (0=slow, 1=normal, 2=fast)
  mouseOldButtons   dw    ; previous frame remapped buttons (for trigger calc)
.ends

.base BSL

