"""Compact semantic grid.

Cells are stored as typed arrays, not Python objects.  A 128m chunk is only
256x256 cells, so this stays cheap even in pure Python.
"""

from __future__ import annotations

from array import array
import math

from .constants import Flag, TILE_METERS, Terrain


class WorldGrid:
    def __init__(self, width_m: float, height_m: float) -> None:
        self.width_m = float(width_m)
        self.height_m = float(height_m)
        self.width = max(1, math.ceil(width_m / TILE_METERS - 1e-9))
        self.height = max(1, math.ceil(height_m / TILE_METERS - 1e-9))
        self.size = self.width * self.height

        self.terrain = bytearray([int(Terrain.GRASS)]) * self.size
        self.flags = bytearray([int(Flag.WALKABLE)]) * self.size
        self.occlusion = bytearray(self.size)
        self.clearance = bytearray(self.size)
        self.owner = array("H", [0]) * self.size

    def index(self, cx: int, cy: int) -> int:
        return cy * self.width + cx

    def in_bounds(self, cx: int, cy: int) -> bool:
        return 0 <= cx < self.width and 0 <= cy < self.height

    def world_to_cell(self, x_m: float, y_m: float) -> tuple[int, int]:
        return (
            max(0, min(self.width - 1, int(math.floor(x_m / TILE_METERS)))),
            max(0, min(self.height - 1, int(math.floor(y_m / TILE_METERS)))),
        )

    def cell_center(self, cx: int, cy: int) -> tuple[float, float]:
        return (
            (cx + 0.5) * TILE_METERS,
            (cy + 0.5) * TILE_METERS,
        )

    def get_flag(self, cx: int, cy: int, flag: Flag) -> bool:
        return bool(self.flags[self.index(cx, cy)] & int(flag))

    def add_flag(self, cx: int, cy: int, flag: Flag) -> None:
        i = self.index(cx, cy)
        self.flags[i] = (self.flags[i] | int(flag)) & 0xFF

    def remove_flag(self, cx: int, cy: int, flag: Flag) -> None:
        i = self.index(cx, cy)
        self.flags[i] = (self.flags[i] & ~int(flag)) & 0xFF

    def is_walkable(self, cx: int, cy: int) -> bool:
        if not self.in_bounds(cx, cy):
            return False
        f = self.flags[self.index(cx, cy)]
        return bool(f & int(Flag.WALKABLE)) and not bool(f & int(Flag.BLOCKED))

    def is_blocked(self, cx: int, cy: int) -> bool:
        if not self.in_bounds(cx, cy):
            return True
        return bool(self.flags[self.index(cx, cy)] & int(Flag.BLOCKED))

    def set_blocked(self, cx: int, cy: int, blocked: bool = True) -> None:
        i = self.index(cx, cy)
        if blocked:
            self.flags[i] = (self.flags[i] | int(Flag.BLOCKED)) & ~int(Flag.WALKABLE)
        else:
            self.flags[i] = (self.flags[i] & ~int(Flag.BLOCKED)) | int(Flag.WALKABLE)

    def set_terrain(self, cx: int, cy: int, terrain: Terrain) -> None:
        self.terrain[self.index(cx, cy)] = int(terrain)

    def paint_rect_m(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        add_flags: int = 0,
        remove_flags: int = 0,
        terrain: Terrain | None = None,
        owner: int | None = None,
        occlusion: int | None = None,
    ) -> int:
        """Paint all cells touching a world-space rectangle.  Returns cell count."""

        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        cx0 = max(0, int(math.floor(x0 / TILE_METERS)))
        cy0 = max(0, int(math.floor(y0 / TILE_METERS)))
        cx1 = min(self.width - 1, int(math.floor(max(x0, x1 - 1e-9) / TILE_METERS)))
        cy1 = min(self.height - 1, int(math.floor(max(y0, y1 - 1e-9) / TILE_METERS)))
        if cx0 > cx1 or cy0 > cy1:
            return 0

        count = 0
        for cy in range(cy0, cy1 + 1):
            base = cy * self.width
            for cx in range(cx0, cx1 + 1):
                i = base + cx
                if add_flags or remove_flags:
                    self.flags[i] = (
                        (self.flags[i] | add_flags) & ~remove_flags
                    ) & 0xFF
                if terrain is not None:
                    self.terrain[i] = int(terrain)
                if owner is not None:
                    self.owner[i] = owner
                if occlusion is not None:
                    self.occlusion[i] = occlusion
                count += 1
        return count

    def paint_circle_m(
        self,
        x: float,
        y: float,
        radius_m: float,
        *,
        add_flags: int = 0,
        remove_flags: int = 0,
        terrain: Terrain | None = None,
        owner: int | None = None,
        occlusion: int | None = None,
    ) -> int:
        if radius_m <= 0:
            cx, cy = self.world_to_cell(x, y)
            if add_flags or remove_flags:
                i = self.index(cx, cy)
                self.flags[i] = (self.flags[i] | add_flags) & ~remove_flags & 0xFF
            if terrain is not None:
                self.set_terrain(cx, cy, terrain)
            if owner is not None:
                self.owner[i] = owner
            if occlusion is not None:
                self.occlusion[i] = occlusion
            return 1

        cx0 = max(0, int(math.floor((x - radius_m) / TILE_METERS)))
        cy0 = max(0, int(math.floor((y - radius_m) / TILE_METERS)))
        cx1 = min(self.width - 1, int(math.ceil((x + radius_m) / TILE_METERS)))
        cy1 = min(self.height - 1, int(math.ceil((y + radius_m) / TILE_METERS)))
        r2 = radius_m * radius_m
        count = 0
        for cy in range(cy0, cy1 + 1):
            py = (cy + 0.5) * TILE_METERS
            for cx in range(cx0, cx1 + 1):
                px = (cx + 0.5) * TILE_METERS
                dx = px - x
                dy = py - y
                if dx * dx + dy * dy > r2:
                    continue
                i = cy * self.width + cx
                if add_flags or remove_flags:
                    self.flags[i] = (
                        (self.flags[i] | add_flags) & ~remove_flags
                    ) & 0xFF
                if terrain is not None:
                    self.terrain[i] = int(terrain)
                if owner is not None:
                    self.owner[i] = owner
                if occlusion is not None:
                    self.occlusion[i] = occlusion
                count += 1
        return count

    def iter_cells_in_rect_m(
        self, x0: float, y0: float, x1: float, y1: float
    ):
        cx0 = max(0, int(math.floor(x0 / TILE_METERS)))
        cy0 = max(0, int(math.floor(y0 / TILE_METERS)))
        cx1 = min(self.width - 1, int(math.floor(max(x0, x1 - 1e-9) / TILE_METERS)))
        cy1 = min(self.height - 1, int(math.floor(max(y0, y1 - 1e-9) / TILE_METERS)))
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                yield cx, cy

    def clamp_cell(self, cell: tuple[int, int]) -> tuple[int, int]:
        return (
            max(0, min(self.width - 1, cell[0])),
            max(0, min(self.height - 1, cell[1])),
        )
