.include "src/config/config.inc"

;MSU-1 seek timeout (same value as msu1.h, duplicated to avoid pulling in full msu1 class)
.define ROOM_MSU1_SEEK_TIMEOUT $2000

;MSU-1 room data pack layout constants
.define ROOM_INDEX_OFFSET     $000100  ;offset to room index table in .msu file
.define ROOM_INDEX_ENTRY_SIZE 8        ;bytes per index entry (offset32 + size32)
.define ROOM_MAX_SLOTS        100      ;max room ID slots in index

;room data block layout (sequential after index seek)
.define ROOM_HEADER_SIZE      32       ;bytes
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

;VRAM tile cache (Step 2) — bank $7F lookup tables
.define SCROLL_TILE2SLOT_WRAM    $7F3000  ;tileIdToSlot[2048] (4KB, 2 bytes per entry)
.define SCROLL_SLOT2TILE_WRAM    $7F4000  ;slotToTileId[896]  (1792 bytes)
.define SCROLL_TILE_STAGE_WRAM   $7F4800  ;tile data staging (576B max, 18 tiles * 32B)
.define ROOM_CACHE_SLOTS         896
.define ROOM_TILE_NOT_CACHED     $FFFF
.define ROOM_MAX_LOOKUP_TILES    2048     ;max tile IDs in lookup table

;BG1 register config values for room display
.define ROOM_BG1_TILEMAP_REG  $71    ;($7000 >> 8) & $FC | 1 = tilemap at $7000, 64x32
.define ROOM_BG_TILES12_REG   $00    ;BG1 tiles at VRAM $0000

;room header struct (matches snes_room_converter.py output, 32 bytes)
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
  box_size      dw      ;+$1C  walkbox data size (bytes)
  box_size_hi   dw      ;+$1E
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
GLOBAL.room.tileDataMsuBase_lo dw             ;MSU offset to tile data (low 16)
GLOBAL.room.tileDataMsuBase_hi dw             ;MSU offset to tile data (high 16)
GLOBAL.room.nmiTileFlag    db                 ;nonzero = NMI should write tile data
GLOBAL.room.nmiTileCount   dw                 ;number of new tiles for NMI
GLOBAL.room.nmiTileStageLen dw                ;bytes of tile data in staging buffer
GLOBAL.room.refreshIdx     dw                 ;background refresh column offset (0-31)
.ends

;room object metadata limits (must match scummvm.h defines)
.define ROOM_MAX_ROOM_OBJECTS   96   ;max objects per room
.define ROOM_OBJ_ENTRY_SIZE     16   ;bytes per roomObjectEntry struct
.define ROOM_OBJ_NAME_BUF_SIZE  512  ;max bytes for packed name strings

.base BSL
.bank 0 slot 0
