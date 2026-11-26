"""주변 청크의 상세 객체만 유지하고, 변경된 충돌 상태만 별도로 보존한다."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import json
import math
from zlib import error as CompressionError

from .chunk_terrain import CHUNK_SIZE, ChunkTerrain
from .geometry import Rect
from .spatial import BoundsIndex
from .world_files import MAX_CHUNK_BYTES, read_bytes


PREFETCH_DISTANCE = CHUNK_SIZE / 4
PREFETCH_LEAD_SECONDS = 4


class WorldChunks:
    def __init__(self, world, files=None):
        self.world, self.terrain = world, ChunkTerrain(world.data)
        self.files = files
        self.chunk_data = lru_cache(maxsize=64)(self._chunk_data)
        self.prepared = OrderedDict()
        self.worker = self.pending = None
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

    @property
    def loaded_bounds(self):
        if not self.loaded:
            return self.world.bounds
        x, y = zip(*self.loaded)
        return Rect(min(x) * CHUNK_SIZE, min(y) * CHUNK_SIZE,
                    (max(x) - min(x) + 1) * CHUNK_SIZE, (max(y) - min(y) + 1) * CHUNK_SIZE)

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

    def _chunk_data(self, key):
        path = self.files.chunk_path(key) if self.files else None
        if path is not None:
            try:
                layers, colliders = json.loads(read_bytes(path, MAX_CHUNK_BYTES))
                if isinstance(layers, list) and isinstance(colliders, list):
                    return layers, colliders
            except (OSError, ValueError, EOFError, TypeError, CompressionError):
                pass
        chunk = self.terrain.generate(key)
        if self.files is not None:
            self.files.cache_chunk(key, chunk)
        return chunk

    def _desired(self, center):
        cx, cy = center
        # 최대 사거리와 줌아웃 화면을 위해 주변 두 청크를 유지한다.
        return {(x, y) for x in range(cx - 2, cx + 3) for y in range(cy - 2, cy + 3)
                if Rect(x * CHUNK_SIZE, y * CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE).overlaps(self.world.bounds)}

    def _prepare(self, desired, retained):
        chunks = {}
        static, layers, colliders = {}, {}, {}
        for key in sorted(desired):
            area = Rect(key[0] * CHUNK_SIZE, key[1] * CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE)
            for kind, row, order in self.static.query(area):
                static[kind, row['id']] = row, order
            chunks[key] = retained[key] if key in retained else self.chunk_data(key)
            chunk_layers, chunk_colliders = chunks[key]
            layers.update((row['id'], Rect(*row['rect'])) for row in chunk_layers)
            colliders.update((row['id'], Rect(*row['rect'])) for row in chunk_colliders)
        for (kind, key), (row, _) in static.items():
            (layers if kind == 'layer' else colliders)[key] = Rect(*row['rect'])
        return chunks, static, BoundsIndex.prepare(layers), BoundsIndex.prepare(colliders)

    def start_prefetch(self):
        if self.worker is None:
            self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='chunk')

    def close(self):
        if self.worker is not None:
            # 준비 작업은 활성 월드를 변경하지 않는다.
            self.worker.shutdown(cancel_futures=True)
            self.worker = self.pending = None

    def _window(self, desired):
        key = frozenset(desired)
        if self.pending is not None:
            pending_key, future = self.pending
            if future.done() or pending_key == key:
                self.pending = None
                self._remember_window(pending_key, future.result())
        if key in self.prepared:
            self.prepared.move_to_end(key)
            return self.prepared[key]
        window = self._prepare(key, self.loaded)
        self._remember_window(key, window)
        return window

    def _remember_window(self, key, window):
        self.prepared[key] = window
        self.prepared.move_to_end(key)
        if len(self.prepared) > 5:
            self.prepared.popitem(last=False)

    def _prefetch(self, point, velocity):
        if self.worker is None:
            return
        if self.pending is not None:
            key, future = self.pending
            if not future.done():
                return
            self.pending = None
            self._remember_window(key, future.result())
        cx, cy = self.center
        x, y = point[0] - cx * CHUNK_SIZE, point[1] - cy * CHUNK_SIZE
        x_neighbors = ((0, 0), (-1, x), (1, CHUNK_SIZE - x))
        y_neighbors = ((0, 0), (-1, y), (1, CHUNK_SIZE - y))
        vx, vy = velocity
        lead = min(CHUNK_SIZE / 2, math.hypot(vx, vy) * PREFETCH_LEAD_SECONDS)
        projected = self.key((point[0] + vx * PREFETCH_LEAD_SECONDS, point[1] + vy * PREFETCH_LEAD_SECONDS))
        neighbors = sorted((key != projected, math.hypot(a, b), key)
                           for dx, a in x_neighbors for dy, b in y_neighbors if dx or dy
                           for key in ((cx + dx, cy + dy),))
        for other_direction, distance, center in neighbors:
            limit = PREFETCH_DISTANCE if other_direction else max(PREFETCH_DISTANCE, lead)
            if distance > limit:
                continue
            desired = frozenset(self._desired(center))
            if desired != self.loaded.keys() and desired not in self.prepared:
                self.pending = (desired, self.worker.submit(self._prepare, desired, self.loaded.copy()))
                break

    def update(self, point, velocity=(0.0, 0.0)):
        center = self.key(point)
        if center == self.center:
            self._prefetch(point, velocity)
            return
        desired = self._desired(center)
        if desired == self.loaded.keys():
            self.center = center
            self._prefetch(point, velocity)
            return
        chunks, static, layer_index, collider_index = self._window(desired)
        self.remember()
        for key in self.loaded.keys() - desired:
            layers, colliders = self.loaded[key]
            for row in layers:
                self.world.layer_index.remove(row['id'])
            for row in colliders:
                self.remove_collider(row['id'])
        for key in sorted(desired - self.loaded.keys()):
            layers, colliders = chunks[key]
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
        self.loaded = chunks
        self.static_loaded = set(static)
        self.center = center
        self.world.layer_index.use_prepared(layer_index)
        self.world.collider_index.use_prepared(collider_index)
        self.world.geometry.index.use_prepared(collider_index)
        self.world.layers = [item[1] for item in self.world.layer_index.items.values()]
        self.world.revision += 1
        self.world.navigation.invalidate()
        self._prefetch(point, velocity)
