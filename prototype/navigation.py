"""희소 이동 그래프. 근처 연결점과 문 상태 캐시를 사용한다."""

from collections import OrderedDict
import heapq
import math

from .geometry import Rect
from .spatial import BoundsIndex


class Navigation:
    def __init__(self, nodes, edges, geometry, clearance=0.25):
        self.nodes, self.geometry = nodes, geometry
        self.clearance = clearance
        self.edges = {key: [] for key in nodes}
        self.point_index = BoundsIndex(32)
        self.edge_cache, self.goal_cache = {}, OrderedDict()
        for key, point in nodes.items():
            if key.startswith("@"):
                raise ValueError("내비게이션 노드의 @ 접두사는 예약되어 있음")
            self.point_index.add(key, Rect(point[0] - 0.01, point[1] - 0.01, 0.02, 0.02), key)
        for start, end in edges:
            if start not in nodes or end not in nodes:
                raise ValueError("내비게이션 연결 노드 누락")
            if not self.clear(nodes[start], nodes[end], ignore_doors="all"):
                raise ValueError(f"벽을 통과하는 내비게이션 연결: {start} -> {end}")
            self.edges[start].append(end)
            self.edges[end].append(start)

    def invalidate(self):
        self.edge_cache.clear()
        self.goal_cache.clear()

    def clear(self, start, end, *, ignore_doors=False):
        return self.geometry.raycast(start, end, padding=self.clearance, category="movement",
                                     ignore_doors=ignore_doors) is None

    def _attach(self, point, allow_doors):
        found, seen = [], set()
        for radius in (16, 48, 128):
            candidates = self.point_index.query(Rect(point[0] - radius, point[1] - radius, radius * 2, radius * 2))
            for key in sorted(candidates, key=lambda key: math.dist(point, self.nodes[key]))[:16]:
                if key in seen:
                    continue
                seen.add(key)
                if self.clear(point, self.nodes[key], ignore_doors=allow_doors):
                    found.append(key)
                    if len(found) == 4:
                        return found
            if found:
                return found
        for key in sorted(self.nodes, key=lambda key: math.dist(point, self.nodes[key]))[:8]:
            if key not in seen and self.clear(point, self.nodes[key], ignore_doors=allow_doors):
                found.append(key)
        return found

    def route(self, start, goal, *, allow_doors=False):
        if self.clear(start, goal, ignore_doors=allow_doors):
            return [goal]
        starts = self._attach(start, allow_doors)
        cache_key = (tuple(goal), allow_doors)
        if cache_key not in self.goal_cache:
            self.goal_cache[cache_key] = self._attach(goal, allow_doors)
            if len(self.goal_cache) > 64:
                self.goal_cache.popitem(last=False)
        else:
            self.goal_cache.move_to_end(cache_key)
        goals = set(self.goal_cache[cache_key])
        if not starts or not goals:
            return []
        points = {"@start": start, "@goal": goal}
        position = lambda key: points[key] if key in points else self.nodes[key]
        pending, distances, parents = [(0.0, "@start")], {"@start": 0.0}, {}
        while pending:
            cost, current = heapq.heappop(pending)
            if cost != distances[current]:
                continue
            if current == "@goal":
                path = []
                while current != "@start":
                    path.append(position(current))
                    current = parents[current]
                return path[::-1]
            neighbors = starts if current == "@start" else self.edges[current] + (["@goal"] if current in goals else [])
            for neighbor in neighbors:
                if current != "@start" and neighbor != "@goal":
                    edge_key = (*sorted((current, neighbor)), allow_doors)
                    if edge_key not in self.edge_cache:
                        self.edge_cache[edge_key] = self.clear(position(current), position(neighbor), ignore_doors=allow_doors)
                    if not self.edge_cache[edge_key]:
                        continue
                candidate = cost + math.dist(position(current), position(neighbor))
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor], parents[neighbor] = candidate, current
                    heapq.heappush(pending, (candidate, neighbor))
        return []
