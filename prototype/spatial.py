"""정적 지형·그림·내비게이션 점이 공유하는 지연 생성 공간 인덱스."""

from shapely import STRtree, box


class BoundsIndex:
    def __init__(self):
        self.items = {}
        self.rects = {}
        self.tree = None

    def add(self, key, rect, value):
        if key in self.items:
            raise ValueError(f"중복 공간 항목: {key}")
        self.items[key] = rect, value
        self.rects[key] = rect
        self.tree = None

    @staticmethod
    def prepare(rects):
        keys = tuple(sorted(rects))
        tree = STRtree([box(rect.x, rect.y, rect.right, rect.bottom) for rect in (rects[key] for key in keys)])
        return keys, tree, rects

    def use_prepared(self, prepared):
        keys, tree, rects = prepared
        if self.rects == rects:
            self.keys, self.tree = keys, tree

    def _ensure_tree(self):
        if self.tree is None:
            self.keys, self.tree, _ = self.prepare(self.rects)

    def _query(self, shape, predicate=None):
        self._ensure_tree()
        return sorted(self.tree.query(shape, predicate=predicate))

    def remove(self, key):
        del self.items[key]
        del self.rects[key]
        self.tree = None

    def query_candidates(self, shape):
        # 호출자가 선분과 확장 사각형의 실제 충돌을 검사한다.
        return [self.items[self.keys[index]][1] for index in self._query(shape)]

    def candidate_pairs(self, shapes):
        self._ensure_tree()
        rows, columns = self.tree.query(shapes)
        return ((row, self.items[self.keys[column]][1]) for row, column in zip(rows, columns))

    def query(self, rect):
        return [self.items[self.keys[index]][1]
                for index in self._query(box(rect.x, rect.y, rect.right, rect.bottom))
                if self.items[self.keys[index]][0].overlaps(rect)]
