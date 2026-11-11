"""Minimal player/enemy runtime for the navigation vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Iterable

from worldgen.constants import PLAYER_COLLISION_RADIUS_M, TILE_METERS
from worldgen.nav import nearest_passable
from worldgen.world import WorldMap

from .navigation import NavPath, NavigationWorld, can_stand, has_line_of_sight
from .weapons import Weapon


@dataclass
class Actor:
    x: float
    y: float
    hp: float = 100.0
    max_hp: float = 100.0
    armor: float = 0.0
    radius: float = 0.30
    weapon: Weapon | None = field(default=None, repr=False)

    @property
    def position(self) -> tuple[float, float]:
        return self.x, self.y

    def equip_weapon(self, weapon: Weapon | None) -> Weapon | None:
        """Accept any firearm and return the previous instance, retaining its ammo."""
        previous = self.weapon
        if previous is not weapon:
            if previous is not None:
                previous.cancel_reload()
            self.weapon = weapon
        return previous

    def apply_damage(self, amount: float) -> None:
        effective = max(1.0, amount - self.armor * 0.5)
        self.hp -= effective
        if self.hp < 0.0:
            self.hp = 0.0


@dataclass
class Player(Actor):
    aim_x: float = 0.0
    aim_y: float = 0.0
    radius: float = PLAYER_COLLISION_RADIUS_M

    def move(self, world: WorldMap, dx: float, dy: float, speed: float, dt: float) -> None:
        if dx or dy:
            length = math.hypot(dx, dy)
            step_x = dx / length * speed * dt
            step_y = dy / length * speed * dt
        else:
            step_x = step_y = 0.0
        if step_x:
            nx = self.x + step_x
            if can_stand(world, nx, self.y, self.radius):
                self.x = nx
        if step_y:
            ny = self.y + step_y
            if can_stand(world, self.x, ny, self.radius):
                self.y = ny


def _patrol_points(
    world: WorldMap, home: tuple[float, float], rng: random.Random
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = [home]
    for _ in range(10):
        angle = rng.uniform(0.0, math.tau)
        distance = rng.uniform(4.0, 11.0)
        x = home[0] + math.cos(angle) * distance
        y = home[1] + math.sin(angle) * distance
        cell = nearest_passable(
            world.grid, world.grid.world_to_cell(x, y), max_radius=8
        )
        if cell is None:
            continue
        px, py = world.grid.cell_center(*cell)
        if can_stand(world, px, py, 0.30):
            points.append((px, py))
        if len(points) >= 4:
            break
    return points


def spawn_enemies(
    world: WorldMap, nav: NavigationWorld, count: int = 3
) -> list["Enemy"]:
    candidates: list[tuple[float, float]] = []
    for placement in world.facilities:
        candidates.append(
            (
                placement.entrance[0] + placement.outward[0] * 6.0,
                placement.entrance[1] + placement.outward[1] * 6.0,
            )
        )
    if world.gate is not None:
        candidates.append((world.gate[0] - 9.0, world.gate[1]))
    if world.roads is not None:
        candidates.extend((node.x, node.y) for node in world.roads.nodes.values())

    rng = random.Random(world.seed ^ 0xE11E)
    spawn_points: list[tuple[float, float]] = []
    for x, y in candidates:
        cell = nearest_passable(
            world.grid, world.grid.world_to_cell(x, y), max_radius=16
        )
        if cell is None:
            continue
        px, py = world.grid.cell_center(*cell)
        if math.hypot(px - world.spawn[0], py - world.spawn[1]) < 14.0:
            continue
        if not can_stand(world, px, py, 0.30):
            continue
        if any(math.hypot(px - sx, py - sy) < 8.0 for sx, sy in spawn_points):
            continue
        spawn_points.append((px, py))
        if len(spawn_points) >= count:
            break

    while len(spawn_points) < count:
        x = rng.uniform(10.0, world.width_m - 10.0)
        y = rng.uniform(10.0, world.height_m - 10.0)
        cell = nearest_passable(
            world.grid, world.grid.world_to_cell(x, y), max_radius=12
        )
        if cell is None:
            continue
        px, py = world.grid.cell_center(*cell)
        if math.hypot(px - world.spawn[0], py - world.spawn[1]) < 14.0:
            continue
        if can_stand(world, px, py, 0.30):
            spawn_points.append((px, py))

    enemies: list[Enemy] = []
    for index, (x, y) in enumerate(spawn_points[:count]):
        enemy_rng = random.Random(world.seed ^ (0xA11E + index * 7919))
        enemies.append(
            Enemy(
                id=index + 1,
                x=x,
                y=y,
                hp=80.0,
                max_hp=80.0,
                armor=10.0,
                radius=0.30,
                home=(x, y),
                patrol_points=_patrol_points(world, (x, y), enemy_rng),
                rng=enemy_rng,
            )
        )
    return enemies


@dataclass
class Enemy(Actor):
    id: int = 0
    state: str = "patrol"
    home: tuple[float, float] = (0.0, 0.0)
    patrol_points: list[tuple[float, float]] = field(default_factory=list)
    patrol_index: int = 0
    path: NavPath | None = None
    path_index: int = 0
    repath_timer: float = 0.0
    lost_timer: float = 0.0
    state_timer: float = 0.0
    attack_timer: float = 0.0
    stuck_timer: float = 0.0
    investigate_point: tuple[float, float] = (0.0, 0.0)
    last_seen: tuple[float, float] = (0.0, 0.0)
    rng: random.Random = field(default_factory=random.Random)

    def _set_path(self, path: NavPath | None) -> None:
        self.path = path
        self.path_index = 0
        self.stuck_timer = 0.0

    def _move_towards(
        self, world: WorldMap, target: tuple[float, float], speed: float, dt: float
    ) -> bool:
        dx = target[0] - self.x
        dy = target[1] - self.y
        distance = math.hypot(dx, dy)
        if distance < 0.25:
            return True
        step = min(distance, speed * dt)
        moved = False
        nx = self.x + dx / distance * step
        if can_stand(world, nx, self.y, self.radius):
            self.x = nx
            moved = True
        ny = self.y + dy / distance * step
        if can_stand(world, self.x, ny, self.radius):
            self.y = ny
            moved = True
        if moved:
            self.stuck_timer = 0.0
        else:
            self.stuck_timer += dt
            if self.stuck_timer > 0.6:
                self.path = None
        return False

    def _follow(self, world: WorldMap, speed: float, dt: float) -> bool:
        if self.path is None:
            return True
        points = self.path.points
        if self.path_index >= len(points):
            return True
        if self._move_towards(world, points[self.path_index], speed, dt):
            self.path_index += 1
        return self.path_index >= len(points)

    def _search_point(self, world: WorldMap) -> tuple[float, float]:
        for _ in range(12):
            angle = self.rng.uniform(0.0, math.tau)
            distance = self.rng.uniform(3.0, 9.0)
            x = self.last_seen[0] + math.cos(angle) * distance
            y = self.last_seen[1] + math.sin(angle) * distance
            cell = nearest_passable(
                world.grid, world.grid.world_to_cell(x, y), max_radius=8
            )
            if cell is None:
                continue
            px, py = world.grid.cell_center(*cell)
            if can_stand(world, px, py, self.radius):
                return px, py
        return self.last_seen

    def update(
        self,
        dt: float,
        world: WorldMap,
        nav: NavigationWorld,
        player: Player,
        noise_events: Iterable[tuple[float, float, float]],
    ) -> None:
        if self.hp <= 0.0:
            return
        self.attack_timer = max(0.0, self.attack_timer - dt)
        self.repath_timer -= dt
        distance_to_player = math.hypot(player.x - self.x, player.y - self.y)
        can_see = distance_to_player < 22.0 and has_line_of_sight(
            world, self.position, player.position
        )

        if can_see:
            if self.state != "chase":
                self.state = "chase"
                self.path = None
            self.last_seen = player.position
            self.lost_timer = 0.0
        elif self.state == "chase":
            self.lost_timer += dt
            if self.lost_timer > 2.5:
                self.state = "search"
                self.state_timer = 4.0
                self.path = None

        if self.state in ("patrol", "search"):
            for nx, ny, radius in noise_events:
                if math.hypot(nx - self.x, ny - self.y) <= radius:
                    self.state = "investigate"
                    self.investigate_point = (nx, ny)
                    self.state_timer = 8.0
                    self.path = None
                    break

        if self.state == "patrol":
            if self.path is None or self.path_index >= len(self.path.points):
                if not self.patrol_points:
                    self.patrol_points = [self.home]
                target = self.patrol_points[self.patrol_index % len(self.patrol_points)]
                self.patrol_index = (self.patrol_index + 1) % len(self.patrol_points)
                self._set_path(nav.find_path(self.position, target, self.radius))
            self._follow(world, 1.8, dt)
        elif self.state == "investigate":
            if self.path is None:
                self._set_path(
                    nav.find_path(self.position, self.investigate_point, self.radius)
                )
            if self._follow(world, 2.2, dt):
                self.state = "search"
                self.state_timer = 4.0
                self.path = None
        elif self.state == "chase":
            # Never repath to the player's true position while the player is
            # hidden.  The enemy only knows the last position it actually saw.
            target = player.position if can_see else self.last_seen
            if self.path is None or self.repath_timer <= 0.0:
                self._set_path(nav.find_path(self.position, target, self.radius))
                self.repath_timer = 0.55
            self._follow(world, 2.8, dt)
        elif self.state == "search":
            if self.path is None:
                self._set_path(
                    nav.find_path(self.position, self._search_point(world), self.radius)
                )
            if self._follow(world, 1.8, dt):
                self.state_timer -= dt
                if self.state_timer <= 0.0:
                    self.state = "patrol"
                    self.path = None

        if (
            distance_to_player < 1.25
            and can_see
            and self.attack_timer <= 0.0
        ):
            player.apply_damage(12.0)
            self.attack_timer = 0.9
