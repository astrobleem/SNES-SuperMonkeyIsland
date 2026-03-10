.include "src/config/config.inc"

;MSU-1 seek timeout (must match room.h)
.define ROOM_MSU1_SEEK_TIMEOUT $2000

;room object limits (must match room.h / scummvm.h)
.define ROOM_MAX_ROOM_OBJECTS   96
.define ROOM_OBJ_NAME_BUF_SIZE  512

.base BSL
