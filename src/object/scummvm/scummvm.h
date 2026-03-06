.include "src/config/config.inc"

;---------------------------------------------------------------------------
; SCUMM v5 Interpreter — Constants, Structs, WRAM layout
;---------------------------------------------------------------------------

; TAD audio driver commands (from tad_interface.h)
.define TadCommand_PAUSE                 0
.define TadCommand_UNPAUSE               4
.define TadCommand_STOP_SOUND_EFFECTS    8

; Music mode constants
.define SCUMM_MUSIC_MODE_TAD   0
.define SCUMM_MUSIC_MODE_MSU1  1

; Slot status values
.define SCUMM_SLOT_DEAD     0
.define SCUMM_SLOT_RUNNING  1
.define SCUMM_SLOT_PAUSED   2

; Slot limits
.define SCUMM_MAX_SLOTS     25
.define SCUMM_MAX_GLOBALS   800     ;global variables (16-bit each)
.define SCUMM_MAX_BITVARS   2048    ;bit variables
.define SCUMM_MAX_LOCALS    25      ;local variables per slot (16-bit each)

; Variable encoding masks
.define SCUMM_VAR_GLOBAL_MASK   $0000   ;top 2 bits = 00
.define SCUMM_VAR_LOCAL_MASK    $4000   ;top 2 bits = 01
.define SCUMM_VAR_BIT_MASK      $8000   ;top 2 bits = 10
.define SCUMM_VAR_TYPE_MASK     $C000
.define SCUMM_VAR_INDEX_MASK    $3FFF

; Flag bit constants for getVarOrDirect* parameter decoding
.define SCUMM_FLAG_BIT7     $0080
.define SCUMM_FLAG_BIT6     $0040
.define SCUMM_FLAG_BIT5     $0020

; Script cache in bank $7F (after scroll system buffers)
.define SCUMM_CACHE_BASE    $5000   ;offset within bank $7F
.define SCUMM_CACHE_SIZE    $B000   ;44KB cache ($7F5000-$7FFFFF)
.define SCUMM_CACHE_WRAM    $7F5000 ;long address

; Script WHERE constants (for slot.where field)
.define SCUMM_WHERE_GLOBAL  0
.define SCUMM_WHERE_LOCAL   1
.define SCUMM_WHERE_ROOM    2

; LSCR table dimensions
.define SCUMM_LSCR_BASE     200     ;LSCR numbers are 200-255
.define SCUMM_LSCR_MAX      56      ;table entries (200..255)

; Actor limits
.define SCUMM_MAX_ACTORS     256    ;full byte range (MI1 uses actors 0-255)

; MSU-1 header offsets for script section
.define SCUMM_MSU_SCRIPT_HDR_OFFSET $0024   ;32-bit pointer in MSU header

; Script section header layout (32 bytes at script section start)
; Byte 0-3:  "SCPT" magic
; Byte 4:    version
; Byte 5:    reserved
; Byte 6-7:  global_slots (LE16)
; Byte 8-9:  room_slots (LE16)
; Byte 10-15: reserved
; Byte 16-19: global_index_offset (LE32) — absolute MSU address
; Byte 20-23: room_index_offset (LE32) — absolute MSU address
; Byte 24-27: global_data_size (LE32)
; Byte 28-31: room_data_size (LE32)

; Index entry: 8 bytes (offset32 + size32)
.define SCUMM_INDEX_ENTRY_SIZE  8

;---------------------------------------------------------------------------
; SCUMM Slot Structure
;---------------------------------------------------------------------------
.struct scummSlot
  status          db      ; 0=dead, 1=running, 2=paused
  number          db      ; script number (0-255)
  where           db      ; 0=global, 1=local, 2=room
  freezeCount     db      ; >0 = frozen
  pc              dw      ; bytecode offset within cached script
  cachePtr        dw      ; offset into script cache ($7F:5000 base)
  cacheLen        dw      ; cached bytecode length
  delay           dw      ; delay frames remaining
  cutsceneOverride db
  pad             db      ; pad to even
  localVars       ds 50   ; 25 local vars x 2 bytes
.endst
; _sizeof_scummSlot = 64 bytes per slot

;---------------------------------------------------------------------------
; LSCR table entry (local room scripts 200-255)
;---------------------------------------------------------------------------
.struct lscrEntry
  cachePtr  dw    ; offset in $7F cache (0 = not loaded)
  cacheLen  dw    ; bytecode length
.endst

;---------------------------------------------------------------------------
; Actor Structure (16 bytes per actor)
;---------------------------------------------------------------------------
.struct scummActor
  room          db      ; room number (0 = not placed)
  costume       db      ; costume resource number (0 = none)
  x             dw      ; X position in room coordinates
  y             dw      ; Y position in room coordinates
  facing        dw      ; direction (0=north, 90=right, 180=south, 270=left)
  elevation     dw      ; Y offset for depth
  moving        db      ; 0=stationary
  visible       db      ; nonzero = render
  initFrame     db      ; animation frame
  scalex        db      ; scale (255=full)
  pad           dw      ; pad to 16 bytes (power of 2 for fast indexing)
.endst

;---------------------------------------------------------------------------
; WRAM sections — Bank $7E (auto-placed by linker)
;---------------------------------------------------------------------------

; SCUMM global variables (800 x 16-bit = 1600 bytes)
.ramsection "scumm global vars" bank 0 slot 1
SCUMM.globalVars    ds 1600
.ends

; SCUMM bit variables (2048 bits = 256 bytes)
.ramsection "scumm bit vars" bank 0 slot 1
SCUMM.bitVars       ds 256
.ends

; SCUMM script slots (25 x 64 = 1600 bytes)
.ramsection "scumm slots" bank 0 slot 1
SCUMM.slots         INSTANCEOF scummSlot SCUMM_MAX_SLOTS
.ends

; VM state variables
.ramsection "scumm vm state" bank 0 slot 1
SCUMM.running             dw      ;nonzero = VM is active
SCUMM.currentSlot         dw      ;current slot index (0-24)
SCUMM.currentSlotPtr      dw      ;byte offset into SCUMM.slots for current slot
SCUMM.currentOpcode       dw      ;last fetched opcode byte (zero-extended)
SCUMM.resultVar           dw      ;target variable for getResultPos
SCUMM.scratch             dw      ;general scratch register
SCUMM.scratch2            dw      ;second scratch
SCUMM.cacheWritePtr       dw      ;next free offset in script cache
SCUMM.scriptSectionLo     dw      ;MSU-1 script section offset (low 16)
SCUMM.scriptSectionHi     dw      ;MSU-1 script section offset (high 16)
SCUMM.globalIndexLo       dw      ;MSU-1 global index offset (low 16)
SCUMM.globalIndexHi       dw      ;MSU-1 global index offset (high 16)
SCUMM.roomIndexLo         dw      ;MSU-1 room index offset (low 16)
SCUMM.roomIndexHi         dw      ;MSU-1 room index offset (high 16)
SCUMM.globalSlots         dw      ;number of global index slots
SCUMM.roomSlots           dw      ;number of room index slots
SCUMM.cutsceneNest        dw      ;cutscene nesting depth
SCUMM.currentRoom         dw      ;current room number (0=none)
SCUMM.newRoom             dw      ;pending room to load (0=none)
SCUMM.bgInitDone          dw      ;PPU BG1 mode setup done flag
SCUMM.musicMode           dw      ;0=SPC700/TAD, 1=MSU-1 PCM
SCUMM.gcInProgress        dw      ;nonzero = cache GC in progress (prevent recursion)
.ends

; Room script tracking (ENCD/EXCD/LSCR)
.ramsection "scumm room scripts" bank 0 slot 1
SCUMM.roomEncdPtr    dw      ; cache offset for ENCD (0 = none)
SCUMM.roomEncdLen    dw      ; ENCD bytecode length
SCUMM.roomExcdPtr    dw      ; cache offset for EXCD (0 = none)
SCUMM.roomExcdLen    dw      ; EXCD bytecode length
SCUMM.roomLscrCount  dw      ; number of LSCR entries loaded
SCUMM.roomLscrTable  INSTANCEOF lscrEntry SCUMM_LSCR_MAX  ; 56 x 4 = 224B
.ends

; Actor state table (256 x 16 = 4096 bytes)
.ramsection "scumm actor state" bank 0 slot 1
SCUMM.actors         INSTANCEOF scummActor SCUMM_MAX_ACTORS
.ends

; Object state table (1 byte per object, 1024 objects)
.define SCUMM_MAX_OBJECTS     1024
.ramsection "scumm object state" bank 0 slot 1
SCUMM.objectState    ds SCUMM_MAX_OBJECTS
.ends

;---------------------------------------------------------------------------
; OOP Class Config
;---------------------------------------------------------------------------

; ScummVM uses minimal ZP (just the iteratorStruct from OOP framework)
.enum 0
  iterator INSTANCEOF iteratorStruct
  zpLen ds 0
.ende

.define CLASS.FLAGS OBJECT.FLAGS.Present | OBJECT.FLAGS.Singleton
.define CLASS.PROPERTIES 0
.define CLASS.ZP_LENGTH zpLen

.base BSL
.bank 0 slot 0
