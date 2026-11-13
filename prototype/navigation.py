"""그림 위에 지정한 소수 노드의 이동 그래프. 타일맵을 생성하지 않는다."""

import heapq
import math


class Navigation:
    def __init__(self, nodes, edges, geometry, clearance=0.25):
        self.nodes, self.geometry = nodes, geometry
        self.clearance = clearance
        self.edges = {key: [] for key in nodes}
        # 시설 제작 때 지정한 희소 연결만 읽는다. 모든 노드 쌍을 자동 연결하지 않는다.
        for start, end in edges:
            if start not in nodes or end not in nodes:
                raise ValueError("내비게이션 연결 노드 누락")
            if not self.clear(nodes[start], nodes[end], ignore_doors=True):
                raise ValueError(f"벽을 통과하는 내비게이션 연결: {start} -> {end}")
            self.edges[start].append(end)
            self.edges[end].append(start)

    def clear(self, start, end, *, ignore_doors=False):
        return self.geometry.raycast(start, end, padding=self.clearance, category="movement",
                                     ignore_doors=ignore_doors) is None

    def route(self, start, goal):
        if self.clear(start, goal):
            return [goal]
        points = {**self.nodes, "@start": start, "@goal": goal}
        edges = {key: list(values) for key, values in self.edges.items()}
        edges["@start"], edges["@goal"] = [], []
        for key, point in self.nodes.items():
            if self.clear(start, point):
                edges["@start"].append(key)
            if self.clear(point, goal):
                edges[key].append("@goal")
        pending, distances, parents = [(0.0, "@start")], {"@start": 0.0}, {}
        while pending:
            cost, current = heapq.heappop(pending)
            if cost != distances[current]:
                continue
            if current == "@goal":
                path = []
                while current != "@start":
                    path.append(points[current])
                    current = parents[current]
                return path[::-1]
            for neighbor in edges[current]:
                # 문 상태는 연결 후보만 확인한다. 전체 그래프를 다시 만들지 않는다.
                if not self.clear(points[current], points[neighbor]):
                    continue
                candidate = cost + math.dist(points[current], points[neighbor])
                if candidate < distances.get(neighbor, math.inf):
                    distances[neighbor], parents[neighbor] = candidate, current
                    heapq.heappush(pending, (candidate, neighbor))
        return []
