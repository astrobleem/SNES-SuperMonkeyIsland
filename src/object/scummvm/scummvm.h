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

; Walkbox data in bank $7F (after scroll NMI slots at $7F4C00)
.define SCUMM_BOX_WRAM       $7F5000 ;long address — walkbox buffer start
.define SCUMM_BOX_MAX_SIZE   $1400   ;5120 bytes max (worst case: 62 boxes)

; Script cache in bank $7F (shifted to make room for walkbox data)
.define SCUMM_CACHE_BASE    $6400   ;offset within bank $7F
.define SCUMM_CACHE_SIZE    $9C00   ;39KB cache ($7F6400-$7FFFFF)
.define SCUMM_CACHE_WRAM    $7F6400 ;long address

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
  talkColor     db      ; SCUMM color index for dialog (0-15)
  pad           db      ; pad to 16 bytes (power of 2 for fast indexing)
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
SCUMM.egoFixupPending     dw      ;nonzero = force ego costume 17 after next scheduler pass
SCUMM.currentSlot         dw      ;current slot index (0-24)
SCUMM.currentSlotPtr      dw      ;byte offset into SCUMM.slots for current slot
SCUMM.currentOpcode       dw      ;last fetched opcode byte (zero-extended)
SCUMM.resultVar           dw      ;target variable for getResultPos
SCUMM.scratch             dw      ;general scratch register
SCUMM.scratch2            dw      ;second scratch
SCUMM.scratch3            dw      ;third scratch
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
SCUMM.hdmaChannel         db      ;allocated HDMA channel id for verb area split
SCUMM.hdmaCgramChannel    db      ;allocated HDMA channel id for CGRAM palette swap
SCUMM.cgramHdmaTable      ds 88   ;WRAM copy of CGRAM HDMA table (pal6 highlight + pal7 normal + sentence)
SCUMM.argBuffer           ds 50   ;temp buffer for startScript vararg passing (25 words max)
SCUMM.argCount            dw      ;byte count of args in argBuffer
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

; Object owner table (1 byte per object, 1024 objects)
; Owner 0 = unowned, owner 0x0F = room, 1-14 = actor/inventory
.ramsection "scumm object owner" bank 0 slot 1
SCUMM.objectOwner    ds SCUMM_MAX_OBJECTS
.ends

; Object class table (2 bytes per object, 1024 objects = 2048 bytes)
; Bit N = class (N+17).  Supports classes 17-32 (MI1 uses 20-32).
; Class 20=bit3, 22=bit5, 29=bit12, 30=bit13, 31=bit14, 32=bit15
.ramsection "scumm object class" bank 0 slot 1
SCUMM.objectClass    ds SCUMM_MAX_OBJECTS * 2
.ends

;---------------------------------------------------------------------------
; Per-room object metadata (loaded from MSU-1 .obj data)
;---------------------------------------------------------------------------
.define SCUMM_MAX_ROOM_OBJECTS  96   ; max objects per room (actual max: 90 in room 99)

; Per-room object entry (16 bytes, matches .obj binary format)
.struct roomObjectEntry
  obj_id       dw      ; +$00
  x_px         dw      ; +$02
  y_px         dw      ; +$04
  width_px     dw      ; +$06
  height_px    dw      ; +$08
  walk_x       dw      ; +$0A  signed walk-to X
  walk_y       dw      ; +$0C  signed walk-to Y
  actor_dir    db      ; +$0E  facing direction (3 bits)
  name_len     db      ; +$0F  name string length
.endst

.ramsection "scumm room objects" bank 0 slot 1
SCUMM.roomObjCount     dw                                              ; object count for current room
SCUMM.roomObjNameSize  dw                                              ; name table size in bytes
SCUMM.roomObjVerbSize  dw                                              ; verb data total size in bytes
SCUMM.roomObjTable     INSTANCEOF roomObjectEntry SCUMM_MAX_ROOM_OBJECTS ; 96 x 16 = 1536 bytes
SCUMM.roomObjNames     ds 512                                          ; packed name string table
.ends

; Per-object verb data index (4 bytes per object: offset LE16 + len LE16)
.ramsection "scumm room obj verbs" bank 0 slot 1
SCUMM.roomObjVerbIdx   ds SCUMM_MAX_ROOM_OBJECTS * 4                   ; 96 x 4 = 384 bytes
SCUMM.roomObjVerbData  ds 4096                                         ; concatenated verb data blobs (max ~3.4KB)
.ends

; Cursor object tracking
.ramsection "scumm cursor object" bank 0 slot 1
SCUMM.cursorObject     dw      ; object ID under cursor (0 = none)
SCUMM.sentenceObject   dw      ; object ID shown in sentence line (0 = none)
.ends

; Object rendering patch state + data buffer (all in bank $7E)
.define SCUMM_OCHR_MAX_SIZE_7E  6016    ; max OCHR data (covers room 28 = 5986B)
.ramsection "scumm object render" bank 0 slot 1
SCUMM.ochrDataSize     dw      ; loaded OCHR data size (0 = none)
SCUMM.ochrObjCount     dw      ; number of objects with visual patches
SCUMM.objDirtyFlag     dw      ; nonzero = tilemap columns need refresh
SCUMM.objDirtyColMin   dw      ; leftmost dirty column
SCUMM.objDirtyColMax   dw      ; rightmost dirty column
SCUMM.objDirtyNext     dw      ; next dirty column to stage for NMI
SCUMM.ochrData         ds SCUMM_OCHR_MAX_SIZE_7E  ; OCHR data buffer (~6KB)
.ends

; Actor rendering scratch (used by renderActors)
.ramsection "scumm actor render" bank 0 slot 1
SCUMM.actorScreenX   dw      ; computed screen X for current actor
SCUMM.actorScreenY   dw      ; computed screen Y for current actor
SCUMM.actorOamCount  dw      ; OAM entry counter
SCUMM.actorFlipMask  db      ; OAM flag OR mask ($30=normal, $70=H-flip for west facing)
SCUMM.chrDmaPending  db      ; LEGACY: kept for single-slot compat (slot 0 only)
SCUMM.chrDmaSrcLo   dw      ; CHR source ROM address (low 16)
SCUMM.chrDmaSrcHi   db      ; CHR source ROM bank
SCUMM.chrDmaLen      dw      ; CHR transfer length
SCUMM.cursorPalBuf   db      ; cursor OBJ pal5 color1 low byte
SCUMM.cursorPalC1Hi  db      ; cursor OBJ pal5 color1 high byte
SCUMM.cursorPalC2Lo  db      ; cursor OBJ pal5 color2 low byte
SCUMM.cursorPalC2Hi  db      ; cursor OBJ pal5 color2 high byte
.ends

; Multi-actor render slots (5 visible actors max)
.define SCUMM_MAX_RENDER_SLOTS 5
.ramsection "scumm render slots" bank 0 slot 1
SCUMM.renderSlotActor    ds SCUMM_MAX_RENDER_SLOTS      ; actor number per slot (0=empty)
SCUMM.renderSlotCostume  ds SCUMM_MAX_RENDER_SLOTS      ; costume number in slot
SCUMM.renderSlotPalSlot  ds SCUMM_MAX_RENDER_SLOTS      ; OBJ palette index (0-4)
SCUMM.renderSlotTileBase ds SCUMM_MAX_RENDER_SLOTS * 2  ; VRAM tile ID base per slot (word)
SCUMM.renderSlotLastPic  ds SCUMM_MAX_RENDER_SLOTS      ; last rendered pic ($FF=dirty)
SCUMM.renderSlotDirty    ds SCUMM_MAX_RENDER_SLOTS      ; nonzero = needs CHR DMA
.ends

; Per-slot CHR DMA parameters (filled by renderActors, consumed by registerPendingDma)
.ramsection "scumm slot chr dma" bank 0 slot 1
SCUMM.slotChrSrcLo   ds SCUMM_MAX_RENDER_SLOTS * 2  ; CHR source ROM addr low (word per slot)
SCUMM.slotChrSrcHi   ds SCUMM_MAX_RENDER_SLOTS      ; CHR source ROM bank (byte per slot)
SCUMM.slotChrLen      ds SCUMM_MAX_RENDER_SLOTS * 2  ; CHR transfer length (word per slot)
SCUMM.slotChrVram     ds SCUMM_MAX_RENDER_SLOTS * 2  ; VRAM byte target addr (word per slot)
.ends

; Actor walk targets (parallel arrays — 16 actors max, avoids changing struct size)
.define SCUMM_WALK_ACTORS 16
.ramsection "scumm actor walk" bank 0 slot 1
SCUMM.actorTargetX   ds SCUMM_WALK_ACTORS * 2  ; 32B — walk destination X
SCUMM.actorTargetY   ds SCUMM_WALK_ACTORS * 2  ; 32B — walk destination Y
SCUMM.actorAnimFrame ds SCUMM_WALK_ACTORS      ; 16B — walk cycle index (0-11)
SCUMM.actorAnimTimer ds SCUMM_WALK_ACTORS      ; 16B — frame delay countdown
SCUMM.actorLastFrame ds SCUMM_WALK_ACTORS      ; 16B — last rendered pic index
.ends

; Walkbox BOXD entry struct (20 bytes per walkbox, matches SCUMM v5 format)
.struct scummBoxEntry
  ulx  dw    ;+$00  upper-left X (signed)
  uly  dw    ;+$02  upper-left Y (signed)
  urx  dw    ;+$04  upper-right X
  ury  dw    ;+$06  upper-right Y
  lrx  dw    ;+$08  lower-right X
  lry  dw    ;+$0A  lower-right Y
  llx  dw    ;+$0C  lower-left X
  lly  dw    ;+$0E  lower-left Y
  mask db    ;+$10
  flags db   ;+$11
  scale dw   ;+$12
.endst

; Walkbox data — bulk data in $7F:4C00 (contiguous: count + BOXD + matrix)
; Small state vars in bank $7E lowram for fast opcode access
.define SCUMM_MAX_BOXES      62
.define WALK_MAX_WAYPOINTS   8

; SCUMM v5 box flag constants (ScummVM: kBoxLocked, kBoxInvisible)
.define SCUMM_BOX_LOCKED     $40   ; excluded from pathfinding as intermediate
.define SCUMM_BOX_INVISIBLE  $80   ; excluded from findBox AND pathfinding

.ramsection "scumm walkbox state" bank 0 slot 1
SCUMM.boxCount        dw      ; number of walkboxes in current room
SCUMM.boxMatrixPtr    dw      ; offset within $7F bank to routing matrix start
SCUMM.boxOrigMatrixPtr dw     ; offset within $7F to saved original routing matrix
SCUMM.boxDataSize     dw      ; total size of walkbox data loaded (for validation)
.ends

; buildWalkPath scratch (WRAM, survives subroutine calls that trash DP tmp)
.ramsection "scumm bwp scratch" bank 0 slot 1
SCUMM.bwpTargetX    dw
SCUMM.bwpTargetY    dw
SCUMM.bwpCurBox     dw
SCUMM.bwpDestBox    dw
SCUMM.bwpArrayOfs   dw
SCUMM.bwpPathLen    dw
.ends

; Walk path arrays (per-actor waypoint data, bank $7E lowram)
.ramsection "scumm walk path" bank 0 slot 1
SCUMM.walkPathX    ds SCUMM_WALK_ACTORS * WALK_MAX_WAYPOINTS * 2  ; 256B
SCUMM.walkPathY    ds SCUMM_WALK_ACTORS * WALK_MAX_WAYPOINTS * 2  ; 256B
SCUMM.walkPathLen  ds SCUMM_WALK_ACTORS                           ; 16B — waypoints in path
SCUMM.walkPathIdx  ds SCUMM_WALK_ACTORS                           ; 16B — current waypoint
SCUMM.actorWalkBox ds SCUMM_WALK_ACTORS                           ; 16B — current box per actor
SCUMM.actorIgnoreBoxes ds SCUMM_WALK_ACTORS                       ; 16B — 1=straight-line walk
SCUMM.actorWidth       ds SCUMM_WALK_ACTORS                       ; 16B — pixel width (default 24)
SCUMM.actorWalkSpeedX  ds SCUMM_WALK_ACTORS                       ; 16B — walk speed X component
SCUMM.actorWalkSpeedY  ds SCUMM_WALK_ACTORS                       ; 16B — walk speed Y component
SCUMM.actorAnimSpeed   ds SCUMM_WALK_ACTORS                       ; 16B — animation speed
SCUMM.actorWalkAnimNr  ds SCUMM_WALK_ACTORS                       ; 16B — walk animation number
SCUMM.actorStandFrame  ds SCUMM_WALK_ACTORS                       ; 16B — stand animation frame
SCUMM.actorTalkAnimStart ds SCUMM_WALK_ACTORS                     ; 16B — talk anim start frame
SCUMM.actorTalkAnimEnd ds SCUMM_WALK_ACTORS                       ; 16B — talk anim end frame
SCUMM.actorZClip       ds SCUMM_WALK_ACTORS                       ; 16B — Z clipping plane
.ends

; OAM scratch buffer (copy of current frame's OAM data, max ~80 bytes)
.ramsection "scumm oam scratch" bank 0 slot 1
SCUMM.oamScratch     ds 80
.ends

; updateActors scratch (survives across inner subroutine calls)
.ramsection "scumm updateActors scratch" bank 0 slot 1
SCUMM.uaActorStructOfs   dw    ; actor struct byte offset (actor * 16)
.ends

; Cursor state
.ramsection "scumm cursor state" bank 0 slot 1
SCUMM.cursorX         dw      ; screen X (0-255)
SCUMM.cursorY         dw      ; screen Y (0-223)
SCUMM.cursorEnabled   dw      ; nonzero = cursor active + visible
SCUMM.sentenceVerb    dw      ; currently selected verb ID (0=none)
SCUMM.sentenceObjectA dw      ; first object in sentence (0=none)
SCUMM.sentenceObjectB dw      ; second object in sentence (0=none)
SCUMM.sentenceDirty   dw      ; nonzero = re-render sentence line
SCUMM.highlightVerb   dw      ; verb slot offset currently highlighted ($FFFF=none)
SCUMM.cursorTileDone  dw      ; nonzero = cursor CHR already DMA'd to VRAM
SCUMM.mouseActive     dw      ; nonzero = mouse detected (set by updateCursor)
.ends

;---------------------------------------------------------------------------
; Verb Table
;---------------------------------------------------------------------------
.define SCUMM_MAX_VERBS       20
.define SCUMM_VERB_NAMES_SIZE 256
.define SCUMM_VERB_FLAG_ON    $01
.define SCUMM_VERB_FLAG_DIRTY $80

.struct scummVerb
  id          db      ; SCUMM verb ID (0 = unused slot)
  x           db      ; X position (tile units, 0-31)
  y           db      ; Y position (tile units, 0-3)
  color       db      ; normal color palette index
  hiColor     db      ; highlighted color palette index
  dimColor    db      ; dimmed color palette index
  flags       db      ; bit 0=on, bit 7=dirty
  key         db      ; shortcut key mapping
  namePtr     dw      ; offset into verbNames buffer
  nameLen     db      ; string length
  pad         ds 5    ; align to 16 bytes
.endst

.ramsection "scumm verb table" bank 0 slot 1
SCUMM.verbs          INSTANCEOF scummVerb SCUMM_MAX_VERBS  ; 320 bytes
SCUMM.verbNames      ds SCUMM_VERB_NAMES_SIZE              ; packed name strings
SCUMM.verbNamesPtr   dw      ; next free offset in name buffer
SCUMM.verbDirty      dw      ; nonzero = redraw BG2
SCUMM.verbDmaPending dw      ; nonzero = DMA tilemap to VRAM next frame
SCUMM.verbTilemap    ds 2048 ; BG2 WRAM tilemap buffer (32x32 x 2B)
.ends

;---------------------------------------------------------------------------
; Dialog text state
;---------------------------------------------------------------------------
.ramsection "scumm dialog state" bank 0 slot 1
SCUMM.dialogActive       dw      ; nonzero = dialog text on screen
SCUMM.dialogTimer        dw      ; frames remaining before auto-clear
SCUMM.dialogX            dw      ; text center X (SCUMM pixel coords)
SCUMM.dialogY            dw      ; text top Y (SCUMM pixel coords)
SCUMM.dialogColor        dw      ; text color — SCUMM color index (0-15), 0=default white
SCUMM.dialogActor        dw      ; actor ID (for positioning)
SCUMM.dialogDmaPending   dw      ; nonzero = DMA tilemap to VRAM next VBlank
SCUMM.dialogFontDone     dw      ; nonzero = font tiles already DMA'd
SCUMM.dialogCharCount    dw      ; number of printable chars (for timer calc)
SCUMM.dialogTilemap      ds 2048 ; BG3 WRAM tilemap buffer (32x32 x 2B)
SCUMM.dialogPalTrans     dw      ; dynamic palette color 0 (always $0000 = transparent)
SCUMM.dialogPalColor     dw      ; dynamic palette color 1 (talk color BGR555)
.ends

; BG3 VRAM layout constants
.define DIALOG_TILEMAP_VRAM  $9800   ; byte addr (word $4C00)
.define DIALOG_TILES_VRAM    $A000   ; byte addr (word $5000)
.define DIALOG_TILEMAP_SIZE  2048    ; 32x32 x 2 bytes

;---------------------------------------------------------------------------
; OOP Class Config
;---------------------------------------------------------------------------

; ScummVM ZP layout
.enum 0
  iterator INSTANCEOF iteratorStruct
  _oamSrcPtr ds 3
  _oamSrcLen ds 1
  _costDirPtr ds 3         ; 24-bit pointer into CostumeDirTable
  _costDirPtrPad ds 1      ; alignment
  zpLen ds 0
.ende

.define CLASS.FLAGS OBJECT.FLAGS.Present | OBJECT.FLAGS.Singleton
.define CLASS.PROPERTIES 0
.define CLASS.ZP_LENGTH zpLen

;---------------------------------------------------------------------------
; Camera state
;---------------------------------------------------------------------------
.ramsection "scumm camera state" bank 0 slot 1
SCUMM.cameraFollows   dw      ; actor number to follow (0 = none)
SCUMM.cameraDest      dw      ; target camera center X (for pan/follow)
.ends

; BG2 VRAM layout constants
.define VERB_FONT_VRAM_ADDR   $8000   ; byte addr for font tiles (word $4000)
.define VERB_TILEMAP_VRAM_ADDR $9000  ; byte addr for BG2 tilemap (word $4800)
.define VERB_TILEMAP_SIZE     2048    ; 32x32 x 2 bytes

;---------------------------------------------------------------------------
; Inventory state (cached list + name storage)
;---------------------------------------------------------------------------
.define INV_MAX_ITEMS       32     ; max inventory items tracked
.define INV_NAME_MAX_LEN    16     ; max chars per item name
.define INV_VISIBLE_ITEMS   4      ; items visible per row (64px each)

.ramsection "scumm inventory state" bank 0 slot 1
SCUMM.inventoryList      ds INV_MAX_ITEMS * 2    ; 32 x 2B obj_id (0=empty)
SCUMM.inventoryCount     dw                      ; items owned by ego
SCUMM.inventoryScroll    dw                      ; first visible item index
SCUMM.inventoryDirty     dw                      ; re-render flag
.ends

; Inventory name cache — indexed by cache slot (not inventory list order).
; Names are cached at pickup time because roomObjNames gets overwritten on room change.
.ramsection "scumm inventory names" bank 0 slot 1
SCUMM.invNameObjIds      ds INV_MAX_ITEMS * 2    ; 32 x 2B: obj_id per name slot
SCUMM.invNameLens        ds INV_MAX_ITEMS        ; 32 x 1B: name length
SCUMM.invNames           ds INV_MAX_ITEMS * INV_NAME_MAX_LEN  ; 32 x 16B: name chars
SCUMM.invNameCount       dw                      ; number of cached names
.ends

; Camera variable indices (SCUMM v5 MI1)
.define SCUMM_VAR_CAMERA_POS_X     2
.define SCUMM_VAR_CAMERA_MIN_X     17
.define SCUMM_VAR_CAMERA_MAX_X     18
.define SCUMM_VAR_CAMERA_THRESHOLD 29
.define SCUMM_VAR_CAMERA_FAST_X    36

; Cursor sprite — tile $F0 at end of OBJ VRAM (avoids actor VRAM regions)
; OBJ VRAM layout (name base $6000):
;   Slot 0: tiles $00-$2F (VRAM $6000-$62FF)  ego / Guybrush, 48 tiles max
;   Slot 1: tiles $30-$5F (VRAM $6300-$65FF)  NPC 1, 48 tiles
;   Slot 2: tiles $60-$8F (VRAM $6600-$68FF)  NPC 2, 48 tiles
;   Slot 3: tiles $90-$BF (VRAM $6900-$6BFF)  NPC 3, 48 tiles
;   Slot 4: tiles $C0-$EF (VRAM $6C00-$6EFF)  NPC 4, 48 tiles
;   Cursor: tile  $F0     (VRAM $6F00)
.define CURSOR_TILE_VRAM_WORD $6F00   ; VRAM word addr for cursor tile
.define CURSOR_TILE_ID        $F0     ; OAM tile index
.define CURSOR_SPEED          2       ; pixels per frame for d-pad movement
; Cursor uses OBJ palette 5 (CGRAM color 160-175)
.define CURSOR_OAM_FLAGS      $3A     ; priority 3 ($30) + OBJ palette 5 ($0A)

; Per-slot VRAM base addresses (byte addresses for DMA)
; Each slot = 48 tiles * 32 bytes = 1536 = $0600 bytes
.define ACTOR_VRAM_SLOT0      $C000   ; byte addr = word $6000
.define ACTOR_VRAM_SLOT1      $C600   ; byte addr = word $6300
.define ACTOR_VRAM_SLOT2      $CC00   ; byte addr = word $6600
.define ACTOR_VRAM_SLOT3      $D200   ; byte addr = word $6900
.define ACTOR_VRAM_SLOT4      $D800   ; byte addr = word $6C00
.define ACTOR_TILES_PER_SLOT  48      ; max tiles per render slot

.base BSL
.bank 0 slot 0

;---------------------------------------------------------------------------
; Verb font data (4bpp 8x8 font, 96 glyphs)
;---------------------------------------------------------------------------
.bank 4 slot 0
.base BSL
.section "VerbFontData" free
  FILEINC VerbFontTiles "build/data/font/fixed8x8.gfx_font4bpp.tiles"
.ends

;---------------------------------------------------------------------------
; HDMA table: disable BG1 below scanline 144 (verb area)
; Format: {count, value} pairs, $00 = end
; Register target: $212C (TM / MainScreen)
;---------------------------------------------------------------------------
.bank 5 slot 0
.base BSL
.section "VerbHdmaTable" free
VerbHdmaTable:
  .db 128                               ; scanlines 0-127: room area
  .db T_BG1_ENABLE | T_BG2_ENABLE | T_BG3_ENABLE | T_OBJ_ENABLE  ; $17
  .db 16                                ; scanlines 128-143: room area continued
  .db T_BG1_ENABLE | T_BG2_ENABLE | T_BG3_ENABLE | T_OBJ_ENABLE  ; $17
  .db 80                                ; scanlines 144-223: verb area
  .db T_BG2_ENABLE | T_BG3_ENABLE | T_OBJ_ENABLE  ; $16 (no BG1)
  .db 0                                 ; end of table
.ends

;---------------------------------------------------------------------------
; HDMA table: write verb font colors to CGRAM at scanline 144
; Mode 3 (DMAP_2_REG_WRITE_TWICE_EACH) targeting $21:
;   writes $2121 (CGADD), $2121, $2122 (CGDATA lo), $2122 (CGDATA hi)
; During room scanlines, writes to CGRAM[$70] (pal7 color0, always transparent).
; At scanlines 144-147 (repeat mode), writes verb font palette:
;   Font pixel mapping: idx3=letter body, idx1=inner highlight, idx2=shadow
;   - CGRAM[$00] = $0000 (backdrop = black for verb area)
;   - CGRAM[$71] = $6318 (pal7 color1 = light grey inner fill)
;   - CGRAM[$72] = $294A (pal7 color2 = dark grey shadow)
;   - CGRAM[$73] = $7FFF (pal7 color3 = white letter body)
; Room palette 7 preserved for room area (only color0 touched, always transparent).
;---------------------------------------------------------------------------
.bank 5 slot 0
.base BSL
.section "VerbCgramHdmaTable" free
VerbCgramHdmaTable:
  .db 128                               ; scanlines 0-127: room area
  .db $70, $70, $00, $00               ;   CGRAM[$70] = $0000 (pal7 color0, transparent)
  .db 12                                ; scanlines 128-139: room area continued
  .db $70, $70, $00, $00               ;   CGRAM[$70] = $0000 (pal7 color0, transparent)
  .db $84                               ; repeat 4 lines (scanlines 140-143)
  .db $00, $00, $00, $00               ;   CGRAM[$00] = $0000 (backdrop = black)
  .db $71, $71, $18, $63               ;   CGRAM[$71] = $6318 (pal7 color1 = light grey)
  .db $72, $72, $4A, $29               ;   CGRAM[$72] = $294A (pal7 color2 = dark grey)
  .db $73, $73, $FF, $7F               ;   CGRAM[$73] = $7FFF (pal7 color3 = white body)
  .db 80                                ; scanlines 144-223: verb area
  .db $70, $70, $00, $00               ;   CGRAM[$70] = $0000 (colors already set)
  .db 0                                 ; end of table
.ends
