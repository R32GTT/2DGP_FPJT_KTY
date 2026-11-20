"""정적 지형·그림·내비게이션 점이 공유하는 지연 생성 공간 인덱스."""

from shapely import STRtree, box


class BoundsIndex:
    def __init__(self):
        self.items = {}
        self.tree = None

    def add(self, key, rect, value):
        if key in self.items:
            raise ValueError(f"중복 공간 항목: {key}")
        self.items[key] = rect, value
        self.tree = None

    def _query(self, shape, predicate=None):
        if self.tree is None:
            self.keys = tuple(sorted(self.items))
            self.tree = STRtree([box(rect.x, rect.y, rect.right, rect.bottom)
                                 for rect, _ in (self.items[key] for key in self.keys)])
        return sorted(self.tree.query(shape, predicate=predicate))

    def remove(self, key):
        del self.items[key]
        self.tree = None

    def query_shape(self, shape):
        return [self.items[self.keys[index]][1] for index in self._query(shape, "intersects")]

    def query(self, rect):
        return [self.items[self.keys[index]][1]
                for index in self._query(box(rect.x, rect.y, rect.right, rect.bottom))
                if self.items[self.keys[index]][0].overlaps(rect)]
