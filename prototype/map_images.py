"""화면 주변 그림만 준비하는 제한 크기 LRU. 매 프레임 전체 맵을 생성하지 않는다."""

from collections import OrderedDict

import pico2d

from .visual_assets import layer_image
from .world import ROOT


class MapImages:
    def __init__(self, backend, capacity=24):
        self.backend = backend
        self.capacity = capacity
        self.cache = OrderedDict()
        self.paths = {}
        self.builds = 0

    def _path(self, name):
        if not name:
            return None
        if name not in self.paths:
            path = ROOT / name
            self.paths[name] = path if path.is_file() else None
        return self.paths[name]

    def prepare(self, layers, budget=2):
        result = {}
        for layer in layers:
            path = self._path(layer.image) or self._path(layer.fallback)
            key = ("file", str(path)) if path else (layer.role, layer.color, layer.variant)
            if key in self.cache:
                self.cache.move_to_end(key)
                result[layer.id] = self.cache[key]
            elif layer.role == "walls" and path is None:
                result[layer.id] = None
            elif budget > 0:
                image = pico2d.load_image(str(path)) if path else layer_image(self.backend, layer)
                self.cache[key] = result[layer.id] = image
                self.builds += 1
                budget -= 1
                while len(self.cache) > self.capacity:
                    self.cache.popitem(last=False)
            else:
                result[layer.id] = None
        return result

    def close(self):
        self.cache.clear()
