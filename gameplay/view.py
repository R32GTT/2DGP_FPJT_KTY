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
from .weapons import Weapon, load_weapon_specs


class GameViewer(PlaceholderViewer):
    def __init__(self, world: WorldMap) -> None:
        super().__init__(world)
        self.nav = NavigationWorld(world)
        self.player = Player(world.spawn[0], world.spawn[1], armor=20.0)
        self.weapon = Weapon(load_weapon_specs()["pistol_45"])
        self._weapon_actions: list[str] = []
        self.aiming = False
        self.enemies = spawn_enemies(world, self.nav, count=3)
        self.mouse_x = 640
        self.mouse_y = 360
        self.aim_world = (self.player.x + 5.0, self.player.y)
        self.tracers: list[list[float]] = []
        self.noise_events: list[tuple[float, float, float]] = []
        self.noise_markers: list[list[float]] = []
        self.message = ""
        self.message_timer = 0.0

    def _aim_world(self) -> tuple[float, float]:
        return (
            self.cam_x + (self.mouse_x - 640.0) / PIXELS_PER_METER,
            self.cam_y + (self.mouse_y - 360.0) / PIXELS_PER_METER,
        )

    def _shoot(self) -> None:
        origin = (self.player.x, self.player.y)
        aim = self._aim_world()
        dx = aim[0] - origin[0]
        dy = aim[1] - origin[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            self.weapon.cancel_reload()
            return
        if not self.weapon.fire():
            if not self.weapon.chambered:
                self.message = "empty - R reload"
                self.message_timer = 0.6
            return
        angle = math.atan2(dy, dx) + math.radians(self.weapon.shot_offset_deg)
        dx = math.cos(angle)
        dy = math.sin(angle)

        grid = self.world.grid
        hit = None
        range_m = self.weapon.spec.range_m
        end = (origin[0] + dx * range_m, origin[1] + dy * range_m)
        for step in range(1, math.ceil(range_m / 0.1) + 1):
            distance = min(step * 0.1, range_m)
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
            damage = self.weapon.spec.damage_at(distance)
            if damage > 0.0:
                hit.apply_damage(damage)
                self.message = "hit"
                self.message_timer = 0.35
        self.tracers.append([origin[0], origin[1], end[0], end[1], 0.09])
        noise_radius = self.weapon.spec.noise_radius_m
        self.noise_events.append((origin[0], origin[1], noise_radius))
        self.noise_markers.append([origin[0], origin[1], noise_radius, 1.4])

    def _update(self, dt: float) -> None:
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
        sprinting = 1073742049 in self.keys  # LSHIFT
        speed = 5.0 if sprinting else 3.0
        if self.weapon.reloading:
            speed *= self.weapon.spec.reload_move_multiplier
        previous_x, previous_y = self.player.position
        self.player.move(self.world, dx, dy, speed, dt)
        movement_speed = (
            math.hypot(self.player.x - previous_x, self.player.y - previous_y) / dt
            if dt > 0.0 else 0.0
        )
        self.weapon.update(
            dt, aiming=self.aiming, movement_speed_mps=movement_speed, sprinting=sprinting,
        )

        self.player_x, self.player_y = self.player.x, self.player.y
        self._visible_cells()
        self.aim_world = self._aim_world()
        self.player.aim_x, self.player.aim_y = self.aim_world

        # Apply this frame's input after ticking existing weapon timers.
        for action in self._weapon_actions:
            if action == "reload":
                self.weapon.start_reload()
            else:
                self._shoot()
        self._weapon_actions.clear()

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

        alive_noise = []
        for marker in self.noise_markers:
            marker[3] -= dt
            if marker[3] > 0.0:
                alive_noise.append(marker)
        self.noise_markers = alive_noise

        if self.player.hp <= 0.0:
            self.player.x, self.player.y = self.world.spawn
            self.player.hp = self.player.max_hp
            self.player.armor = 20.0
            self.weapon = Weapon(self.weapon.spec)
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

    def _draw_noise(self, pico2d, font) -> None:
        if not self.debug:
            return
        for x, y, radius, _ in self.noise_markers:
            sx, sy = self._screen(x, y)
            pico2d.draw_circle(
                sx, sy, int(radius * PIXELS_PER_METER),
                255, 220, 80, 150, False,
            )
            if font is not None:
                font.draw(sx + 8, sy + 8, f"NOISE {radius:.0f}m", (255, 220, 80))
        for enemy in self.enemies:
            if enemy.state == "investigate":
                sx, sy = self._screen(*enemy.investigate_point)
                pico2d.draw_circle(sx, sy, 8, 255, 180, 60, 220, False)
            elif enemy.state == "search":
                sx, sy = self._screen(*enemy.last_seen)
                pico2d.draw_circle(sx, sy, 10, 180, 200, 255, 180, False)

    def _draw_tracers(self, pico2d) -> None:
        for x0, y0, x1, y1, _ in self.tracers:
            sx0, sy0 = self._screen(x0, y0)
            sx1, sy1 = self._screen(x1, y1)
            pico2d.draw_line(sx0, sy0, sx1, sy1, 255, 226, 110, 255)

    def _draw_crosshair(self, pico2d) -> None:
        x = self.mouse_x
        y = 720 - self.mouse_y
        sx, sy = self._screen(self.player.x, self.player.y)
        distance_px = math.hypot(x - sx, y - sy)
        gap = max(2.0, distance_px * math.tan(math.radians(self.weapon.spread_deg)))
        color = (170, 230, 240, 255) if self.aiming else (240, 240, 240, 255)
        pico2d.draw_line(x - gap - 5, y, x - gap, y, *color)
        pico2d.draw_line(x + gap, y, x + gap + 5, y, *color)
        pico2d.draw_line(x, y - gap - 5, x, y - gap, *color)
        pico2d.draw_line(x, y + gap, x, y + gap + 5, *color)

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
                            if event.key == pico2d.SDLK_r and event.key not in self.keys:
                                self._weapon_actions.append("reload")
                            self.keys.add(event.key)
                    elif event.type == pico2d.SDL_KEYUP:
                        self.keys.discard(event.key)
                    elif event.type == pico2d.SDL_MOUSEMOTION:
                        self.mouse_x, self.mouse_y = event.x, event.y
                    elif event.type == pico2d.SDL_MOUSEBUTTONDOWN:
                        if event.button == 1:
                            self._weapon_actions.append("fire")
                        elif event.button == 3:
                            self.aiming = True
                    elif event.type == pico2d.SDL_MOUSEBUTTONUP and event.button == 3:
                        self.aiming = False

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
                self._draw_noise(pico2d, font)

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
                        f"F1 paths/noise",
                        (245, 245, 245),
                    )
                    weapon = self.weapon
                    reload_text = (
                        f"  RELOAD {weapon.reload_stage} {weapon.reload_remaining_s:.1f}s"
                        if weapon.reloading else ""
                    )
                    font.draw(
                        12, 46,
                        f"{weapon.spec.name}  {weapon.spec.ammo_type}  "
                        f"AMMO {weapon.magazine_rounds}+{int(weapon.chambered)} / "
                        f"{weapon.reserve_rounds}{reload_text}  "
                        f"SPREAD {weapon.spread_deg:.2f}deg  LMB fire  RMB aim  R reload",
                        (245, 245, 245),
                    )
                    if self.message:
                        font.draw(12, 68, self.message, (255, 220, 120))

                pico2d.update_canvas()
        finally:
            pico2d.show_cursor()
            pico2d.close_canvas()
        return 0


def run_game(world: WorldMap) -> int:
    try:
        viewer = GameViewer(world)
    except ValueError as exc:
        print(exc)
        return 1
    return viewer.run()
