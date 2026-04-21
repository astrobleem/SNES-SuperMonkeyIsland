# TODO

## 2026-04-21: drawObject multi-state renderer (doors, interactive props)
- **Status**: Sparkle resolved — objects 110-114 are transparent; the sparkle effect is a costume (#111) driven by actors + chore engine, NOT drawObject. LucasFilm logo sparkle confirmed working at frame 400.
- **Remaining**: drawObject with multi-state OBIM images (doors, inventory items, props with visible state changes). The OCHR system only pre-computes patches for state 0. Objects with multiple IM00/IM01/etc images that change via setState need per-state OCHR patches or runtime rendering.
- **Blocked by**: No immediate scene requires this. The intro + lookout work without it. Becomes relevant when the game reaches rooms with interactive doors/props that change visual state.
- **Entry points**: `op_drawObject` at scummvm.65816:8675, `drawObjectSetStateN` at :16625. Room converter at `snes_room_converter.py:498` filters out `_state` images.

## 2026-04-21: Cloud flicker (OAM overflow)
- **Status**: RESOLVED root cause — clouds are costume #59 (Cost004) rendered as actor sprites, NOT palette effects. Cloud flicker is OAM sprite overflow (66-94 entries per frame × 2 actors = 132-188, exceeding 128 hardware limit).
- **Mitigation applied**: OAM priority rotation distributes dropout evenly across all sprites. Z-clip priority mapping puts clouds behind BG layers (behind title text).
- **Full fix (future)**: MaxTile-style SA-1 OAM buffer compression, or render clouds as BG tiles instead of OAM sprites.
