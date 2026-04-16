.include "src/config/config.inc"

;scroll constants (must match room.h)
.define ROOM_SCROLL_SPEED        2
.define ROOM_VIEWPORT_WIDTH_PX   256
.define ROOM_VIEWPORT_TILES      32
.define ROOM_TILEMAP_VRAM_WORD   $3800
.define ROOM_TILE_BYTES          32
.define SCROLL_TILEMAP_WRAM      $7F0000
.define SCROLL_PRIORITY_WRAM     $7F2C00

;VRAM tile cache defines (must match room.h)
.define SCROLL_TILE2SLOT_WRAM    $7F3000
.define SCROLL_SLOT2TILE_WRAM    $7F4000
.define SCROLL_TILE_STAGE_WRAM   $7F4800
.define SCROLL_NMI_SLOTS_WRAM   $7F4C00
.define ROOM_CACHE_SLOTS         896
.define ROOM_TILE_NOT_CACHED     $FFFF
.define ROOM_MAX_LOOKUP_TILES    2048
.define ROOM_TILES_VRAM          $0000
.define ROOM_MAX_CHR_SIZE        $7000

.base BSL
.bank 0 slot 0
