"""발사 시 파일 I/O 없이 진행하는 제한된 탄환·연속 충돌 판정."""

import math

from .actors import Bullet
from .geometry import Collider, Rect, SpatialIndex


class Projectiles:
    def __init__(self):
        self.bullets = []
        self.next_shot = 0.0
        self.shots = self.hits = 0
        self.actor_index = SpatialIndex()

    def fire(self, actor, time):
        if time < self.next_shot or len(self.bullets) >= 128:
            return
        self.next_shot = time + 0.17
        self.shots += 1
        # 총구가 얇은 벽 너머에 놓여도 벽을 무시하지 않도록 몸체에서 sweep한다.
        self.bullets.append(Bullet(*actor.xy, math.cos(actor.facing) * 60, math.sin(actor.facing) * 60))

    def update(self, dt, world, actors):
        index = self.actor_index
        index.objects.clear()
        index.cells.clear()
        alive = {actor.id: actor for actor in actors if actor.hp > 0}
        for actor in alive.values():
            radius = actor.radius
            index.add(Collider(actor.id, Rect(actor.position.x - radius, actor.position.y - radius,
                                              radius * 2, radius * 2), blocks=("bullet",)))
        remaining = []
        for bullet in self.bullets:
            start, end = (bullet.x, bullet.y), (bullet.x + bullet.vx * dt, bullet.y + bullet.vy * dt)
            wall_hit = world.geometry.raycast(start, end, category="bullet")
            actor_hit = index.raycast(start, end, category="bullet")
            if actor_hit is not None and (wall_hit is None or actor_hit[0] < wall_hit[0]):
                actor = alive[actor_hit[1]]
                if actor.hp > 0:
                    actor.hp = max(0, actor.hp - bullet.damage)
                    self.hits += 1
                continue
            if wall_hit is not None:
                continue
            bullet.x, bullet.y = end
            bullet.ttl -= dt
            if bullet.ttl > 0:
                remaining.append(bullet)
        self.bullets = remaining
