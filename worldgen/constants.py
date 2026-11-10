"""Shared constants for the map generator and the game runtime.

The canonical generation grid is 0.5m because the art tiles are 32px while the
world scale is 64px per meter.  Do not derive world scale from a tileset.
"""

from enum import IntEnum, IntFlag

PIXELS_PER_METER = 64
TILE_PIXELS = 32
TILE_METERS = TILE_PIXELS / PIXELS_PER_METER
CELLS_PER_METER = int(round(1.0 / TILE_METERS))

GENERATION_VERSION = 1
DEFAULT_WORLD_METERS = 128.0
DEFAULT_ROAD_WIDTH_M = 6.0
DEFAULT_AGENT_RADIUS_M = 0.30
DEFAULT_FACILITY_MARGIN_M = 8.0

# Authoring reference from PRD 4.2.5.  The sprite canvas is 96x48px, the
# in-world display is 48x24px at 100% zoom, and collision is separate.
PLAYER_SOURCE_SPRITE_PX = (96, 48)
PLAYER_DISPLAY_PX = (48, 24)
PLAYER_RENDER_SCALE = 0.5
PLAYER_DISPLAY_WIDTH_M = PLAYER_DISPLAY_PX[0] / PIXELS_PER_METER
PLAYER_DISPLAY_HEIGHT_M = PLAYER_DISPLAY_PX[1] / PIXELS_PER_METER
PLAYER_COLLISION_RADIUS_M = 0.30


class Terrain(IntEnum):
    GRASS = 1
    DIRT = 2
    ROCK = 3
    ROAD = 4
    FACILITY_FLOOR = 5
    FACILITY_WALL = 6
    WATER = 7


class Flag(IntFlag):
    WALKABLE = 1
    BLOCKED = 2
    ROAD = 4
    FACILITY = 8
    APRON = 16
    TREE_TRUNK = 32
    BORDER = 64
    SPAWN = 128


class Zone(IntEnum):
    MEADOW = 1
    FOREST = 2
    ROCKY = 3
