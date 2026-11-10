"""Pico2D placeholder viewer for a generated world.

No tile or sprite asset is required.  The same semantic cells are drawn as flat
rectangles so generation, collision and navigation can be tested immediately.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from .constants import (
    Flag,
    PIXELS_PER_METER,
    PLAYER_COLLISION_RADIUS_M,
    PLAYER_DISPLAY_PX,
    TILE_METERS,
    Terrain,
)
from .world import Tree, WorldMap

_CELL_PX = int(round(TILE_METERS * PIXELS_PER_METER))
_TERRAIN_COLORS = {
    int(Terrain.GRASS): (58, 92, 52),
    int(Terrain.DIRT): (112, 96, 66),
    int(Terrain.ROCK): (105, 105, 108),
    int(Terrain.ROAD): (72, 74, 78),
    int(Terrain.FACILITY_FLOOR): (116, 112, 102),
    int(Terrain.FACILITY_WALL): (178, 178, 172),
    int(Terrain.WATER): (45, 85, 125),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class PlaceholderViewer:
    def __init__(self, world: WorldMap) -> None:
        self.world = world
        self.player_x, self.player_y = world.spawn
        self.keys: set[int] = set()
        self.debug = False
        self.cam_x = self.player_x
        self.cam_y = self.player_y
        self.last_time = 0.0

    def _visible_cells(self):
        world = self.world
        view_w = 1280.0 / PIXELS_PER_METER
        view_h = 720.0 / PIXELS_PER_METER
        cam_x = _clamp(self.player_x, view_w * 0.5, max(view_w * 0.5, world.width_m - view_w * 0.5))
        cam_y = _clamp(self.player_y, view_h * 0.5, max(view_h * 0.5, world.height_m - view_h * 0.5))
        self.cam_x, self.cam_y = cam_x, cam_y
        x0 = max(0, int((cam_x - view_w * 0.5) / TILE_METERS) - 1)
        y0 = max(0, int((cam_y - view_h * 0.5) / TILE_METERS) - 1)
        x1 = min(world.grid.width - 1, int((cam_x + view_w * 0.5) / TILE_METERS) + 1)
        y1 = min(world.grid.height - 1, int((cam_y + view_h * 0.5) / TILE_METERS) + 1)
        return x0, y0, x1, y1

    def _screen(self, x_m: float, y_m: float) -> tuple[float, float]:
        return (
            (x_m - self.cam_x) * PIXELS_PER_METER + 640.0,
            360.0 - (y_m - self.cam_y) * PIXELS_PER_METER,
        )

    def _cell_color(self, cx: int, cy: int) -> tuple[int, int, int]:
        grid = self.world.grid
        index = grid.index(cx, cy)
        flags = grid.flags[index]
        if flags & int(Flag.BLOCKED):
            if flags & int(Flag.FACILITY):
                return _TERRAIN_COLORS[int(Terrain.FACILITY_WALL)]
            return (48, 48, 46)
        if flags & int(Flag.FACILITY):
            return _TERRAIN_COLORS[int(Terrain.FACILITY_FLOOR)]
        if flags & int(Flag.ROAD):
            return _TERRAIN_COLORS[int(Terrain.ROAD)]
        if flags & int(Flag.APRON):
            return (128, 112, 78)
        return _TERRAIN_COLORS.get(
            int(grid.terrain[index]), _TERRAIN_COLORS[int(Terrain.GRASS)]
        )

    def _draw_ground(self, pico2d, x0: int, y0: int, x1: int, y1: int) -> None:
        for cy in range(y0, y1 + 1):
            run_start = x0
            run_color = self._cell_color(x0, cy)
            for cx in range(x0 + 1, x1 + 1):
                color = self._cell_color(cx, cy)
                if color == run_color:
                    continue
                self._draw_cell_run(pico2d, run_start, cx - 1, cy, run_color)
                run_start = cx
                run_color = color
            self._draw_cell_run(pico2d, run_start, x1, cy, run_color)

    def _draw_cell_run(
        self, pico2d, cx0: int, cx1: int, cy: int, color: tuple[int, int, int]
    ) -> None:
        left, top = self._screen(cx0 * TILE_METERS, cy * TILE_METERS)
        right, bottom = self._screen(
            (cx1 + 1) * TILE_METERS, (cy + 1) * TILE_METERS
        )
        # Overlap by one pixel to hide seams from integer conversion.
        pico2d.draw_rectangle(
            left - 1,
            bottom,
            right + 1,
            top + 1,
            color[0],
            color[1],
            color[2],
            255,
            True,
        )

    def _visible_trees(self, x0: int, y0: int, x1: int, y1: int) -> list[Tree]:
        world = self.world
        margin = 3.0
        min_x = x0 * TILE_METERS - margin
        max_x = (x1 + 1) * TILE_METERS + margin
        min_y = y0 * TILE_METERS - margin
        max_y = (y1 + 1) * TILE_METERS + margin
        return [
            tree
            for tree in world.trees
            if min_x <= tree.x <= max_x and min_y <= tree.y <= max_y
        ]

    def _draw_trees(self, pico2d, trees: list[Tree]) -> None:
        for tree in sorted(trees, key=lambda item: item.y):
            sx, sy = self._screen(tree.x, tree.y)
            radius = max(4, int(tree.canopy_radius_m * PIXELS_PER_METER))
            pico2d.draw_circle(sx, sy, radius, 27, 61, 35, 255, True)
            pico2d.draw_circle(sx, sy, 4, 72, 51, 31, 255, True)

    def _draw_markers(self, pico2d, x0: int, y0: int, x1: int, y1: int) -> None:
        for placement in self.world.facilities:
            sx, sy = self._screen(*placement.entrance)
            if -20 <= sx <= 1300 and -20 <= sy <= 740:
                pico2d.draw_circle(sx, sy, 7, 70, 220, 230, 255, False)
            if self.debug:
                for node in placement.nav_nodes:
                    nx, ny = self._screen(node["x"], node["y"])
                    pico2d.draw_circle(nx, ny, 5, 250, 220, 70, 255, True)
        if self.debug and self.world.roads is not None:
            for node in self.world.roads.nodes.values():
                sx, sy = self._screen(node.x, node.y)
                pico2d.draw_circle(sx, sy, 6, 245, 245, 245, 255, False)
            for edge in self.world.roads.edges:
                previous = None
                for point in edge.points:
                    current = self._screen(*point)
                    if previous is not None:
                        pico2d.draw_line(
                            previous[0], previous[1], current[0], current[1],
                            235, 235, 235, 180,
                        )
                    previous = current

    def _blocked_at(self, x_m: float, y_m: float) -> bool:
        grid = self.world.grid
        radius = PLAYER_COLLISION_RADIUS_M
        cx0 = max(0, int(math.floor((x_m - radius) / TILE_METERS)))
        cy0 = max(0, int(math.floor((y_m - radius) / TILE_METERS)))
        cx1 = min(grid.width - 1, int(math.floor((x_m + radius) / TILE_METERS)))
        cy1 = min(grid.height - 1, int(math.floor((y_m + radius) / TILE_METERS)))
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                if grid.is_blocked(cx, cy):
                    return True
        return False

    def _move(self, dx: float, dy: float) -> None:
        if dx:
            nx = self.player_x + dx
            if not self._blocked_at(nx, self.player_y):
                self.player_x = nx
        if dy:
            ny = self.player_y + dy
            if not self._blocked_at(self.player_x, ny):
                self.player_y = ny

    def _load_font(self, pico2d):
        try:
            data_path = Path(pico2d.__file__).resolve().parent / "data"
            return pico2d.load_font(str(data_path / "ConsolaMalgun.ttf"), 16)
        except Exception:
            return None

    def run(self) -> int:
        try:
            import pico2d
        except ModuleNotFoundError:
            print("pico2d is required. Install it into the project venv:")
            print(r"  .\.venv\Scripts\python.exe -m pip install -r requirements-worldgen.txt")
            return 1

        pico2d.open_canvas(1280, 720)
        pico2d.lattice_on = False
        font = self._load_font(pico2d)
        self.last_time = pico2d.get_time()
        running = True
        print("WASD move, Shift sprint, F1 debug, ESC quit")
        try:
            while running:
                for event in pico2d.get_events():
                    if event.type == pico2d.SDL_QUIT:
                        running = False
                    elif event.type == pico2d.SDL_KEYDOWN:
                        if event.key == pico2d.SDLK_ESCAPE:
                            running = False
                        elif event.key == pico2d.SDLK_F1:
                            self.debug = not self.debug
                        else:
                            self.keys.add(event.key)
                    elif event.type == pico2d.SDL_KEYUP:
                        self.keys.discard(event.key)

                now = pico2d.get_time()
                dt = min(0.05, max(0.0, now - self.last_time))
                self.last_time = now

                dx = 0.0
                dy = 0.0
                if pico2d.SDLK_a in self.keys:
                    dx -= 1.0
                if pico2d.SDLK_d in self.keys:
                    dx += 1.0
                if pico2d.SDLK_w in self.keys:
                    dy -= 1.0
                if pico2d.SDLK_s in self.keys:
                    dy += 1.0
                if dx or dy:
                    length = math.hypot(dx, dy)
                    speed = 5.0 if pico2d.SDLK_LSHIFT in self.keys else 3.0
                    self._move(dx / length * speed * dt, dy / length * speed * dt)

                pico2d.clear_canvas()
                x0, y0, x1, y1 = self._visible_cells()
                self._draw_ground(pico2d, x0, y0, x1, y1)
                self._draw_trees(pico2d, self._visible_trees(x0, y0, x1, y1))
                self._draw_markers(pico2d, x0, y0, x1, y1)

                sx, sy = self._screen(self.player_x, self.player_y)
                half_w = PLAYER_DISPLAY_PX[0] * 0.5
                half_h = PLAYER_DISPLAY_PX[1] * 0.5
                pico2d.draw_rectangle(
                    sx - half_w, sy - half_h, sx + half_w, sy + half_h,
                    235, 198, 72, 255, True,
                )
                pico2d.draw_rectangle(
                    sx - half_w, sy - half_h, sx + half_w, sy + half_h,
                    40, 34, 26, 255, False,
                )

                if font is not None:
                    text = (
                        f"seed {self.world.seed}  "
                        f"pos {self.player_x:5.1f},{self.player_y:5.1f}  "
                        f"player 48x24  trees {len(self.world.trees)}  F1 debug"
                    )
                    font.draw(12, 24, text, (245, 245, 245))

                pico2d.update_canvas()
        finally:
            pico2d.close_canvas()
        return 0


def run_demo(world: WorldMap) -> int:
    return PlaceholderViewer(world).run()
