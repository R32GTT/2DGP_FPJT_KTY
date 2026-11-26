"""월드 좌표에서 재현하는 지면과 장애물. 청크 방문 순서에 의존하지 않는다."""

from functools import lru_cache
import math
import random

from shapely import box, union_all
from shapely.prepared import prep

from .generation import _density, _sample, FOREST_DENSITY, FOREST_TREE_GAP
from .geometry import Rect
from .road_generation import road_surface


CHUNK_SIZE = 64


class ChunkTerrain:
    def __init__(self, data):
        self.seed, self.bounds = data['generation']['seed'], Rect(*data['bounds'])
        self.center = self.bounds.x + self.bounds.w / 2, self.bounds.y + self.bounds.h / 2
        extent = math.ceil(max(self.bounds.w, self.bounds.h) / 2) * 2
        if data.get("terrain_sampling") == "coordinate-v1":
            self._noise_vertex = lru_cache(maxsize=4096)(self._noise_vertex)
            self.vegetation = self.soil = None
        else:
            self.vegetation = _density(self.seed, 'world-vegetation', extent)
            self.soil = _density(self.seed, 'world-soil', extent)
        self.reserved = prep(union_all([road_surface(data['road_network']).buffer(1.8)] +
                             [box(x, y, x + w, y + h) for x, y, w, h in
                              (site['reserved'] for site in data['planned_facilities'])]))
        # 후보만 제한적으로 캐시한다. 방문했던 청크의 상세 데이터는 보유하지 않는다.
        self.candidate = lru_cache(maxsize=4096)(self._candidate)

    def _noise_vertex(self, kind, spacing, x, y):
        return random.Random(f"{self.seed}:{kind}:{spacing}:{x}:{y}").random()

    def _density_at(self, tree, x, y):
        field = self.vegetation if tree else self.soil
        if field is not None:
            return _sample(field, x - self.center[0], y - self.center[1])
        layers = []
        for spacing in (16, 40):
            ix, iy = math.floor(x / spacing), math.floor(y / spacing)
            sx, sy = x / spacing - ix, y / spacing - iy
            sx, sy = sx * sx * (3 - 2 * sx), sy * sy * (3 - 2 * sy)
            left = self._noise_vertex(tree, spacing, ix, iy)
            right = self._noise_vertex(tree, spacing, ix + 1, iy)
            top = left + (right - left) * sx
            left = self._noise_vertex(tree, spacing, ix, iy + 1)
            right = self._noise_vertex(tree, spacing, ix + 1, iy + 1)
            bottom = left + (right - left) * sx
            layers.append(top + (bottom - top) * sy)
        return layers[0] * .7 + layers[1] * .3

    def ground(self, area):
        layers = []
        for iy in range(math.floor(area.y / 16), math.ceil(area.bottom / 16)):
            for ix in range(math.floor(area.x / 16), math.ceil(area.right / 16)):
                rng = random.Random(f'{self.seed}:ground:{ix}:{iy}')
                x, y = max(self.bounds.x, ix * 16), max(self.bounds.y, iy * 16)
                w, h = min(self.bounds.right, ix * 16 + 16) - x, min(self.bounds.bottom, iy * 16 + 16) - y
                if min(w, h) <= 0:
                    continue
                key = f'terrain/ground/{ix}/{iy}'
                layers.append(dict(id=key, rect=[x, y, w, h], image='', style='grass',
                                   color=[45, 65, 48], variant=rng.randrange(4)))
                px, py = x + w / 2, y + h / 2
                if self._density_at(False, px, py) >= .5:
                    dw, dh = min(w, rng.uniform(7, 14)), min(h, rng.uniform(6, 13))
                    layers.append(dict(id=key + '/soil', rect=[px - dw / 2, py - dh / 2, dw, dh],
                                       image='', style='dirt', color=[104, 91, 62]))
        return layers

    def _candidate(self, ix, iy):
        rng = random.Random(f'{self.seed}:obstacle:{ix}:{iy}')
        x, y = ix * 2 + rng.uniform(.4, 1.6), iy * 2 + rng.uniform(.4, 1.6)
        tree = rng.random() < .9
        density = self._density_at(tree, x, y)
        if rng.random() > .03 + .9 * density ** 2:
            return None
        w, h = (.9, .9) if tree else (rng.uniform(1.5, 2.5), rng.uniform(1.2, 2.5))
        rect = Rect(x - w / 2, y - h / 2, w, h)
        if (rect.x < self.bounds.x or rect.y < self.bounds.y or rect.right > self.bounds.right
                or rect.bottom > self.bounds.bottom or self.reserved.intersects(box(rect.x, rect.y, rect.right, rect.bottom))):
            return None
        gap = FOREST_TREE_GAP if tree and density >= FOREST_DENSITY else 1.8
        kind = 'tree' if tree else 'rock'
        return rng.random(), rect, gap, dict(id=f'terrain/{ix}/{iy}/{kind}', rect=[rect.x, rect.y, w, h],
                                           kind=kind, material='tree_trunk' if tree else 'concrete')

    def generate(self, key):
        x, y = key[0] * CHUNK_SIZE, key[1] * CHUNK_SIZE
        colliders = []
        for ix in range(x // 2, (x + CHUNK_SIZE) // 2):
            for iy in range(y // 2, (y + CHUNK_SIZE) // 2):
                candidate = self.candidate(ix, iy)
                if candidate is None:
                    continue
                priority, rect, gap, row = candidate
                # 양쪽 청크가 같은 이웃 후보와 우선순위를 검사하므로 경계에서도 간격이 일정하다.
                neighbors = (self.candidate(a, b) for a in range(ix - 2, ix + 3) for b in range(iy - 2, iy + 3)
                             if (a, b) != (ix, iy))
                if any(other and (other[0], other[3]['id']) < (priority, row['id'])
                       and rect.expanded(max(gap, other[2])).overlaps(other[1]) for other in neighbors):
                    continue
                colliders.append(row)
        return self.ground(Rect(x, y, CHUNK_SIZE, CHUNK_SIZE)), colliders
