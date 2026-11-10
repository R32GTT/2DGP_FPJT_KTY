"""Deterministic world generation for Revenant: L.O.S.T.

The package deliberately separates semantic map data from rendering.  A map can
be generated and played with flat-colour placeholder cells before any tile art
exists, then the same data can be rendered with a 32px tileset later.
"""

from .constants import (
    CELLS_PER_METER,
    GENERATION_VERSION,
    PIXELS_PER_METER,
    TILE_METERS,
    TILE_PIXELS,
)
from .generation import generate_world
from .world import WorldMap

__all__ = [
    "CELLS_PER_METER",
    "GENERATION_VERSION",
    "PIXELS_PER_METER",
    "TILE_METERS",
    "TILE_PIXELS",
    "WorldMap",
    "generate_world",
]
