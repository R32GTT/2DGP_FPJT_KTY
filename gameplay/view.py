"""Pico2D vertical slice: player, hitscan shooting, and dummy AI."""

from __future__ import annotations

import math

from worldgen.constants import (
    PIXELS_PER_METER,
    PLAYER_DISPLAY_PX,
    TILE_METERS,
)
from worldgen.demo import PlaceholderViewer
from worldgen.world import WorldMap

from .actors import Player, spawn_enemies
from .navigation import NavigationWorld


class GameViewer(PlaceholderViewer):
    def __init__(self, world: WorldMap) -> None:
        super().__init__(world)
        self.nav = NavigationWorld(world)
        self.player = Player(world.spawn[0], world.spawn[1], armor=20.0)
        self.enemies = spawn_enemies(world, self.nav, count=3)
        self.mouse_x = 640
        self.mouse_y = 360
        self.aim_world = (self.player.x + 5.0, self.player.y)
        self.tracers: list[list[float]] = []
        self.noise_events: list[tuple[float, float, float]] = []
        self.message = ""
        self.message_timer = 0.0

    def _aim_world(self) -> tuple[float, float]:
        return (
            self.cam_x + (self.mouse_x - 640.0) / PIXELS_PER_METER,
            self.cam_y + (self.mouse_y - 360.0) / PIXELS_PER_METER,
        )

    def _shoot(self) -> None:
        if self.player.shoot_cooldown > 0.0:
            return
        self.player.shoot_cooldown = 0.16
        origin = (self.player.x, self.player.y)
        aim = self._aim_world()
        dx = aim[0] - origin[0]
        dy = aim[1] - origin[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        dx /= length
        dy /= length

        grid = self.world.grid
        hit = None
        end = (origin[0] + dx * 32.0, origin[1] + dy * 32.0)
        for step in range(1, 321):
            distance = step * 0.1
            x = origin[0] + dx * distance
            y = origin[1] + dy * distance
            cx, cy = grid.world_to_cell(x, y)
            if grid.is_blocked(cx, cy):
                end = (x, y)
                break
            for enemy in self.enemies:
                if enemy.hp <= 0.0:
                    continue
                if math.hypot(enemy.x - x, enemy.y - y) <= enemy.radius + 0.12:
                    hit = enemy
                    end = (x, y)
                    break
            if hit is not None:
                break

        if hit is not None:
            hit.apply_damage(34.0)
            self.message = "hit"
            self.message_timer = 0.35
        self.tracers.append([origin[0], origin[1], end[0], end[1], 0.09])
        self.noise_events.append((origin[0], origin[1], 30.0))

    def _update(self, dt: float) -> None:
        self.player.shoot_cooldown = max(0.0, self.player.shoot_cooldown - dt)
        self.message_timer = max(0.0, self.message_timer - dt)
        if self.message_timer <= 0.0:
            self.message = ""

        dx = 0.0
        dy = 0.0
        if 97 in self.keys:  # A
            dx -= 1.0
        if 100 in self.keys:  # D
            dx += 1.0
        if 119 in self.keys:  # W
            dy -= 1.0
        if 115 in self.keys:  # S
            dy += 1.0
        speed = 5.0 if 1073742049 in self.keys else 3.0  # LSHIFT
        self.player.move(self.world, dx, dy, speed, dt)

        self.player_x, self.player_y = self.player.x, self.player.y
        self._visible_cells()
        self.aim_world = self._aim_world()
        self.player.aim_x, self.player.aim_y = self.aim_world

        for enemy in self.enemies:
            enemy.update(dt, self.world, self.nav, self.player, self.noise_events)
        self.noise_events.clear()
        self.enemies = [enemy for enemy in self.enemies if enemy.hp > 0.0]

        alive_tracers = []
        for tracer in self.tracers:
            tracer[4] -= dt
            if tracer[4] > 0.0:
                alive_tracers.append(tracer)
        self.tracers = alive_tracers

        if self.player.hp <= 0.0:
            self.player.x, self.player.y = self.world.spawn
            self.player.hp = self.player.max_hp
            self.player.armor = 20.0
            self.player.shoot_cooldown = 0.0
            self.message = "respawn"
            self.message_timer = 1.5

    def _draw_enemies(self, pico2d) -> None:
        for enemy in sorted(self.enemies, key=lambda item: item.y):
            sx, sy = self._screen(enemy.x, enemy.y)
            if not (-40 <= sx <= 1320 and -40 <= sy <= 760):
                continue
            pico2d.draw_circle(sx, sy, 14, 174, 66, 54, 255, True)
            pico2d.draw_circle(sx, sy, 14, 42, 28, 24, 255, False)
            bar_w = 32
            bar_y = sy + 18
            pico2d.draw_rectangle(
                sx - bar_w / 2, bar_y, sx + bar_w / 2, bar_y + 4,
                42, 42, 42, 255, True,
            )
            hp_w = bar_w * max(0.0, enemy.hp / enemy.max_hp)
            pico2d.draw_rectangle(
                sx - bar_w / 2, bar_y, sx - bar_w / 2 + hp_w, bar_y + 4,
                220, 72, 58, 255, True,
            )
            if self.debug:
                pico2d.draw_line(
                    sx, sy, *self._screen(self.player.x, self.player.y),
                    255, 190, 80, 80,
                )

    def _draw_paths(self, pico2d) -> None:
        if not self.debug:
            return
        for enemy in self.enemies:
            if enemy.path is None:
                continue
            for segment in enemy.path.segments:
                color = (235, 235, 235) if segment.domain == "outdoor" else (60, 220, 220)
                previous = None
                for point in segment.points:
                    current = self._screen(*point)
                    if previous is not None:
                        pico2d.draw_line(
                            previous[0], previous[1], current[0], current[1],
                            color[0], color[1], color[2], 180,
                        )
                    previous = current

    def _draw_tracers(self, pico2d) -> None:
        for x0, y0, x1, y1, _ in self.tracers:
            sx0, sy0 = self._screen(x0, y0)
            sx1, sy1 = self._screen(x1, y1)
            pico2d.draw_line(sx0, sy0, sx1, sy1, 255, 226, 110, 255)

    def _draw_crosshair(self, pico2d) -> None:
        x = self.mouse_x
        y = 720 - self.mouse_y
        pico2d.draw_line(x - 7, y, x - 2, y, 240, 240, 240, 255)
        pico2d.draw_line(x + 2, y, x + 7, y, 240, 240, 240, 255)
        pico2d.draw_line(x, y - 7, x, y - 2, 240, 240, 240, 255)
        pico2d.draw_line(x, y + 2, x, y + 7, 240, 240, 240, 255)

    def run(self) -> int:
        try:
            import pico2d
        except ModuleNotFoundError:
            print("pico2d is required. Install requirements-worldgen.txt into .venv.")
            return 1

        pico2d.open_canvas(1280, 720)
        pico2d.lattice_on = False
        pico2d.hide_cursor()
        font = self._load_font(pico2d)
        self.last_time = pico2d.get_time()
        running = True
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
                        elif event.key is not None:
                            self.keys.add(event.key)
                    elif event.type == pico2d.SDL_KEYUP:
                        self.keys.discard(event.key)
                    elif event.type == pico2d.SDL_MOUSEMOTION:
                        self.mouse_x, self.mouse_y = event.x, event.y
                    elif event.type == pico2d.SDL_MOUSEBUTTONDOWN and event.button == 1:
                        self._shoot()

                now = pico2d.get_time()
                dt = min(0.05, max(0.0, now - self.last_time))
                self.last_time = now
                self._update(dt)

                pico2d.clear_canvas()
                x0, y0, x1, y1 = self._visible_cells()
                self._draw_ground(pico2d, x0, y0, x1, y1)
                self._draw_trees(pico2d, self._visible_trees(x0, y0, x1, y1))
                self._draw_markers(pico2d, x0, y0, x1, y1)
                self._draw_tracers(pico2d)
                self._draw_enemies(pico2d)
                self._draw_paths(pico2d)

                sx, sy = self._screen(self.player.x, self.player.y)
                half_w = PLAYER_DISPLAY_PX[0] * 0.5
                half_h = PLAYER_DISPLAY_PX[1] * 0.5
                pico2d.draw_line(
                    sx, sy, self.mouse_x, 720 - self.mouse_y,
                    235, 220, 120, 120,
                )
                pico2d.draw_rectangle(
                    sx - half_w, sy - half_h, sx + half_w, sy + half_h,
                    235, 198, 72, 255, True,
                )
                pico2d.draw_rectangle(
                    sx - half_w, sy - half_h, sx + half_w, sy + half_h,
                    40, 34, 26, 255, False,
                )
                self._draw_crosshair(pico2d)

                if font is not None:
                    states = " ".join(
                        f"{enemy.id}:{enemy.state[0].upper()}" for enemy in self.enemies
                    )
                    font.draw(
                        12,
                        24,
                        f"HP {self.player.hp:3.0f}/{self.player.max_hp:.0f}  "
                        f"ARMOR {self.player.armor:3.0f}  "
                        f"ENEMIES {len(self.enemies)}  {states}  "
                        f"LMB shoot  F1 paths",
                        (245, 245, 245),
                    )
                    if self.message:
                        font.draw(12, 46, self.message, (255, 220, 120))

                pico2d.update_canvas()
        finally:
            pico2d.show_cursor()
            pico2d.close_canvas()
        return 0


def run_game(world: WorldMap) -> int:
    return GameViewer(world).run()
