"""주변 청크의 상세 객체만 유지하고, 변경된 충돌 상태만 별도로 보존한다."""

import math

from .chunk_terrain import CHUNK_SIZE, ChunkTerrain
from .geometry import Rect
from .spatial import BoundsIndex


class WorldChunks:
    def __init__(self, world):
        self.world, self.terrain = world, ChunkTerrain(world.data)
        self.loaded, self.changes, self.baselines = {}, {}, {}
        self.center = None
        self.static = BoundsIndex()
        for order, row in enumerate(world.data['layers']):
            self.static.add(('layer', row['id']), Rect(*row['rect']), ('layer', row, order))
        for row in world.data['colliders']:
            self.static.add(('collider', row['id']), Rect(*row['rect']), ('collider', row, 0))
        self.static_loaded = set()
        for key in list(world.geometry.objects):
            self.remove_collider(key)
        for key in list(world.layer_index.items):
            world.layer_index.remove(key)
        world.layers = []

    @staticmethod
    def key(point):
        return tuple(math.floor(value / CHUNK_SIZE) for value in point)

    def contains(self, point):
        return self.key(point) in self.loaded

    def overlaps(self, rect):
        left, top = self.key((rect.x, rect.y))
        right, bottom = self.key((rect.right, rect.bottom))
        return any((x, y) in self.loaded for x in range(left, right + 1) for y in range(top, bottom + 1))

    def remember(self):
        for key, baseline in self.baselines.items():
            active = self.world.geometry.objects[key].active
            if active == baseline:
                self.changes.pop(key, None)
            else:
                self.changes[key] = active
        return dict(self.changes)

    def add_collider(self, row):
        self.baselines[row['id']] = row.get('active', True)
        self.world.add_collider({**row, 'active': self.changes.get(row['id'], row.get('active', True))})

    def remove_collider(self, key):
        self.baselines.pop(key, None)
        self.world.geometry.remove(key)
        self.world.collider_index.remove(key)

    def update(self, point):
        cx, cy = self.key(point)
        if (cx, cy) == self.center:
            return
        # 128m의 여유를 두어 최대 사거리와 줌아웃 화면의 지형도 함께 유지한다.
        desired = {(x, y) for x in range(cx - 2, cx + 3) for y in range(cy - 2, cy + 3)
                   if Rect(x * CHUNK_SIZE, y * CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE).overlaps(self.world.bounds)}
        if desired == self.loaded.keys():
            self.center = cx, cy
            return
        self.remember()
        for key in self.loaded.keys() - desired:
            layers, colliders = self.loaded.pop(key)
            for row in layers:
                self.world.layer_index.remove(row['id'])
            for row in colliders:
                self.remove_collider(row['id'])
        static = {}
        for key in sorted(desired):
            area = Rect(key[0] * CHUNK_SIZE, key[1] * CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE)
            for kind, row, order in self.static.query(area):
                static[kind, row['id']] = row, order
            if key not in self.loaded:
                layers, colliders = self.loaded[key] = self.terrain.generate(key)
                for row in layers:
                    self.world.add_layer(row, -2 if row['style'] == 'grass' else -1)
                for row in colliders:
                    self.add_collider(row)
        for kind, key in self.static_loaded - static.keys():
            if kind == 'layer':
                self.world.layer_index.remove(key)
            else:
                self.remove_collider(key)
        for kind, key in sorted(static.keys() - self.static_loaded):
            row, order = static[kind, key]
            self.world.add_layer(row, order) if kind == 'layer' else self.add_collider(row)
        self.static_loaded = set(static)
        self.center = cx, cy
        self.world.layers = [item[1] for item in self.world.layer_index.items.values()]
        self.world.revision += 1
        self.world.navigation.invalidate()
