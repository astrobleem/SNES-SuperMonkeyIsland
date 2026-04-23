.include "src/config/config.inc"
.include "build/rom_data.inc"

;MSU-1 seek timeout (same value as msu1.h, duplicated to avoid pulling in full msu1 class)
.define ROOM_MSU1_SEEK_TIMEOUT $2000

;MSU-1 room data pack layout constants
.define ROOM_INDEX_OFFSET     $000100  ;offset to room index table in .msu file
.define ROOM_INDEX_ENTRY_SIZE 8        ;bytes per index entry (offset32 + size32)
.define ROOM_MAX_SLOTS        100      ;max room ID slots in index

;room data block layout (sequential after index seek)
.define ROOM_HEADER_SIZE      34       ;bytes (grew from 32 for cyc_size)
.define ROOM_PALETTE_SIZE     256      ;bytes (8 subpalettes x 16 colors x 2 bytes)

;VRAM layout
.define ROOM_TILES_VRAM       $0000    ;VRAM word address for BG1 tiles
.define ROOM_TILEMAP_VRAM     $7000    ;VRAM byte address for BG1 tilemap
.define ROOM_TILEMAP_VRAM_WORD $3800   ;VRAM word address ($7000 / 2)
.define ROOM_MAX_CHR_SIZE     $7000    ;28KB max tileset (896 tiles)
.define ROOM_MAX_TILES        896      ;max tiles that fit in 28KB VRAM
.define ROOM_TILEMAP_CLEAR_SIZE $1000  ;4KB tilemap area to clear (64x32 words)

;scroll constants
.define ROOM_SCROLL_SPEED        2      ;pixels/frame for D-pad testing
.define ROOM_VIEWPORT_WIDTH_PX   256
.define ROOM_VIEWPORT_TILES      32     ;256 / 8
.define ROOM_TILE_BYTES          32     ;bytes per 4bpp 8x8 tile

;bank $7F buffer addresses (long-addressed)
.define SCROLL_TILEMAP_WRAM      $7F0000  ;full room tilemap (max ~6.3KB)

;BG2 tilemap for dual-layer z-plane masking (column-major, same format as BG1)
;Foreground tile positions hold original (unmasked) tilemap word;
;background positions hold 0 (transparent). Loaded alongside BG1 tilemap.
.define SCROLL_BG2MAP_WRAM       $7F1600  ;max ~5.5KB (up to ~2800 tiles)

;ZP01 per-tile priority bitmap: 1 bit/tile, column-major matching
;SCROLL_TILEMAP_WRAM. 1024 bytes covers 8192 tiles (max 128x64 room).
;Priority-set tiles get SNES bit 13 OR'd into the VRAM tilemap word at
;remap time, placing them in front of actors with OAM priority 2.
.define SCROLL_PRIORITY_WRAM     $7F2C00  ;1024 bytes

;VRAM tile cache (Step 2) — bank $7F lookup tables
.define SCROLL_TILE2SLOT_WRAM    $7F3000  ;tileIdToSlot[2048] (4KB, 2 bytes per entry)
.define SCROLL_SLOT2TILE_WRAM    $7F4000  ;slotToTileId[896]  (1792 bytes)
.define SCROLL_TILE_STAGE_WRAM   $7F4800  ;tile data staging (1024B max, 32 tiles * 32B)
.define SCROLL_NMI_SLOTS_WRAM   $7F4C00  ;VRAM slot addresses for NMI tile writes
.define ROOM_CACHE_SLOTS         896
.define ROOM_TILE_NOT_CACHED     $FFFF
.define ROOM_MAX_LOOKUP_TILES    2048     ;max tile IDs in lookup table

;BG2 room tilemap at VRAM $7000 (separate from verb tilemap at $4800)
;With yScrollBG2=110, game area starts at tilemap row 13 (110/8).
.define ROOM_BG2_TILEMAP_VRAM    $7000    ;VRAM word address for room BG2 tilemap
.define ROOM_BG2_ROW_OFFSET      13       ;starting row for game area (110/8)

;BG1 register config values for room display
.define ROOM_BG1_TILEMAP_REG  $71    ;($7000 >> 8) & $FC | 1 = tilemap at $7000, 64x32
.define ROOM_BG_TILES12_REG   $00    ;BG1 tiles at VRAM $0000

;room header struct (matches snes_room_converter.py output, 34 bytes)
.struct roomHeader
  room_id       dw      ;+$00
  width_px      dw      ;+$02
  height_px     dw      ;+$04
  width_tiles   dw      ;+$06
  height_tiles  dw      ;+$08
  num_tiles     dw      ;+$0A
  pal_size      dw      ;+$0C
  chr_size_lo   dw      ;+$0E  clipped to 16-bit
  chr_size      dw      ;+$10  low 16 bits of full chr size
  chr_size_hi   dw      ;+$12  high 16 bits of full chr size
  map_size      dw      ;+$14  low 16 bits of tilemap size
  map_size_hi   dw      ;+$16
  col_size      dw      ;+$18  low 16 bits of column index size
  col_size_hi   dw      ;+$1A
  box_size      dw      ;+$1C  walkbox data size (bytes, always <64KB)
  ochr_size     dw      ;+$1E  object patch data size (repurposed from box_size_hi)
  cyc_size      dw      ;+$20  color cycling descriptor size (.cyc blob)
.endst

;room index entry struct (8 bytes from MSU-1 index table)
.struct roomIndexEntry
  offset_lo     dw      ;low 16 bits of data offset
  offset_hi     dw      ;high 16 bits of data offset
  size_lo       dw      ;low 16 bits of data size
  size_hi       dw      ;high 16 bits of data size
.endst

;WRAM buffers for room loader
.ramsection "global room vars" bank 0 slot 1
GLOBAL.room.hdr INSTANCEOF roomHeader         ;32-byte room header from MSU-1
GLOBAL.room.idx INSTANCEOF roomIndexEntry     ;8-byte index entry
GLOBAL.room.currentId      dw                 ;currently loaded room ID
.ends

;scroll state variables (bank $7E work RAM)
.ramsection "global room scroll" bank 0 slot 1
GLOBAL.room.cameraX        dw                 ;current camera X in pixels
GLOBAL.room.cameraXOld     dw                 ;previous frame camera X
GLOBAL.room.maxScrollX     dw                 ;max camera X (roomWidthPx - 256)
GLOBAL.room.roomWidthTiles dw                 ;cached from header
GLOBAL.room.roomHeightTiles dw                ;cached from header
GLOBAL.room.nmiColFlag     db                 ;nonzero = NMI should write column
GLOBAL.room.nmiColVramAddr dw                 ;VRAM word address for column
GLOBAL.room.nmiColRows     dw                 ;rows to write
GLOBAL.room.colStaging     ds 50              ;NMI column staging buffer (25 rows * 2 bytes)
GLOBAL.room.cacheNextSlot  dw                 ;ring buffer next allocation slot (0-895)
GLOBAL.room.cacheMissCount dw                 ;tiles to DMA in NMI this frame
GLOBAL.room.tileDataRomOfs_lo dw              ;ROM linear offset to tile data (low 16)
GLOBAL.room.tileDataRomOfs_hi dw              ;ROM linear offset to tile data (high 8 in low byte)
GLOBAL.room.romReadPos_lo     dw              ;current ROM read position (low 16)
GLOBAL.room.romReadPos_hi     dw              ;current ROM read position (high 8 in low byte)
GLOBAL.room.nmiTileFlag    db                 ;nonzero = NMI should write tile data
GLOBAL.room.nmiTileCount   dw                 ;number of new tiles for NMI
GLOBAL.room.nmiTileStageLen dw                ;bytes of tile data in staging buffer
GLOBAL.room.refreshIdx     dw                 ;background refresh column offset (0-31)
GLOBAL.room.viewportDirty  db                 ;nonzero = remap VRAM tilemap before next syncScroll
GLOBAL.room.vpTileStaging  ds 32              ;loadInitialViewport tile staging (32B, one 4bpp tile)
GLOBAL.room.hasBg2Mask     db                 ;nonzero = current room has BG2 z-plane mask layer
GLOBAL.room.nmiBg2ColFlag  db                 ;nonzero = NMI should write BG2 column
GLOBAL.room.nmiBg2ColAddr  dw                 ;VRAM word address for BG2 column write
GLOBAL.room.bg2ColStaging  ds 50              ;BG2 NMI column staging buffer (25 rows * 2 bytes)
.ends

;room object metadata limits (must match scummvm.h defines)
.define ROOM_MAX_ROOM_OBJECTS   96   ;max objects per room
.define ROOM_OBJ_ENTRY_SIZE     16   ;bytes per roomObjectEntry struct
.define ROOM_OBJ_NAME_BUF_SIZE  512  ;max bytes for packed name strings

;object patch (OCHR) — data buffer is SCUMM.ochrData in scummvm.h (bank $7E)
.define SCUMM_OCHR_MAX_SIZE_7E  6016    ;max OCHR data in bank $7E buffer

.base BSL
.bank 0 slot 0
