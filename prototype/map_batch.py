"""미니맵과 전체 지도에 쓰는 색상 삼각형 배치."""

import ctypes
import math

import numpy as np

from .road_visuals import ROAD_STYLES


def _quad(a, b, c, d):
    return a, b, c, a, c, d


class MapBatch:
    def __init__(self, backend, triangles):
        positions, colors = [], []
        for points, color in triangles:
            positions.extend(points)
            colors.extend([color if len(color) == 4 else (*color, 255)] * len(points))
        self.count = len(positions)
        self.positions = np.asarray(positions, dtype=np.float32)
        self.colors = np.asarray(colors, dtype=np.uint8)
        self.xy = self.positions.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        self.rgba = self.colors.ctypes.data_as(ctypes.POINTER(backend.SDL_Color))

    def draw(self, backend):
        if self.count:
            backend.SDL_RenderGeometryRaw(backend.renderer, None, self.xy, self.positions.strides[0],
                                          self.rgba, self.colors.strides[0],
                                          None, 0, self.count, None, 0, 0)


def _screen_bounds(rect, point, canvas_height):
    left_top, right_bottom = point(rect.x, rect.y), point(rect.right, rect.bottom)
    left, top = int(left_top[0]), int(canvas_height - left_top[1] - 1)
    right = left + int(right_bottom[0] - left_top[0] + 1)
    bottom = top + int(left_top[1] - right_bottom[1] + 1)
    return left, top, right, bottom


def _screen_rectangle(left, top, right, bottom):
    return _quad((left, top), (right, top), (right, bottom), (left, bottom))


def map_rectangles(rectangles, point, canvas_height):
    for rect, color in rectangles:
        yield _screen_rectangle(*_screen_bounds(rect, point, canvas_height)), color


def map_colliders(colliders, point, canvas_height):
    for collider in colliders:
        left, top, right, bottom = _screen_bounds(collider.rect, point, canvas_height)
        color = (242, 190, 65) if collider.kind == "door" else (36, 43, 41)
        if collider.active or right - left <= 2 or bottom - top <= 2:
            yield _screen_rectangle(left, top, right, bottom), color
        else:
            yield _screen_rectangle(left, top, right, top + 1), color
            yield _screen_rectangle(left, bottom - 1, right, bottom), color
            yield _screen_rectangle(left, top + 1, left + 1, bottom - 1), color
            yield _screen_rectangle(right - 1, top + 1, right, bottom - 1), color


def map_roads(world, point, scale, canvas_height, blocked):
    def project(x, y):
        px, py = point(x, y)
        return px, canvas_height - py

    commands = []
    for road in world.roads:
        material = road.get("material", "")
        color = ((231, 111, 85) if road["id"] in blocked else
                 (114, 103, 77) if road.get("kind") == "footpath" else
                 ROAD_STYLES[material]["map_color"] if material else (153, 155, 123))
        a, b = project(*road["start"]), project(*road["end"])
        dx, dy = b[0] - a[0], b[1] - a[1]
        half = max(0.5, road.get("width", 4) * scale / 2)
        factor = half / max(1e-8, math.hypot(dx, dy))
        x, y = dy * factor, -dx * factor
        triangles = _quad((a[0] + x, a[1] + y), (b[0] + x, b[1] + y),
                          (b[0] - x, b[1] - y), (a[0] - x, a[1] - y))
        commands.append((material, triangles, color))
    for junction in world.data.get("road_network", {}).get("junctions", ()):
        for surface in junction["surfaces"]:
            material = surface["material"]
            commands.append((material, [project(*vertex) for vertex in surface["triangles"]],
                             ROAD_STYLES[material]["map_color"]))
    for _, triangles, color in sorted(commands, key=lambda command: command[0] == "asphalt"):
        yield triangles, color
