"""정적 배치의 범위 조회. 그림·열린 문·내비게이션 점에 공통으로 사용한다."""

import math


class BoundsIndex:
    def __init__(self, cell_size=32):
        self.cell_size = cell_size
        self.cells = {}
        self.items = {}

    def _cells(self, rect):
        for x in range(math.floor(rect.x / self.cell_size), math.floor(rect.right / self.cell_size) + 1):
            for y in range(math.floor(rect.y / self.cell_size), math.floor(rect.bottom / self.cell_size) + 1):
                yield x, y

    def add(self, key, rect, value):
        if key in self.items:
            raise ValueError(f"중복 공간 항목: {key}")
        self.items[key] = rect, value
        for cell in self._cells(rect):
            self.cells.setdefault(cell, set()).add(key)

    def query(self, rect):
        keys = set()
        for cell in self._cells(rect):
            keys.update(self.cells.get(cell, ()))
        return [self.items[key][1] for key in sorted(keys)
                if self.items[key][0].overlaps(rect)]
