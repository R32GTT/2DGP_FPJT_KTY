"""월드의 광원과 차폐를 논리 화면의 저해상도 조명 버퍼에 합성한다."""

import ctypes
from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np

from .camera import CANVAS_SIZE
from .geometry import Rect
from .map_batch import MapBatch


LIGHT_SIZE = tuple(size // 2 for size in CANVAS_SIZE)
EDGE_SAMPLE = 0.0001
SHADOW_HEIGHT = {"wall": 1.0, "door": 1.0, "tree": 0.65}
OPTICS_MODES = ("off", "white", "infrared")


@dataclass(frozen=True)
class Light:
    id: str
    position: tuple[float, float]
    radius: float
    color: tuple[int, int, int]
    intensity: float = 1.0
    direction: float = 0.0
    half_angle: float = math.pi
    infrared: bool = False
    floor: int = 0

    @property
    def bounds(self):
        return Rect(*self.position, 0, 0).expanded(self.radius)


def light_blockers(world, bounds):
    # 건물의 포괄 경계와 장식 레이어 대신 실제 벽·문·엄폐물만 사용한다.
    return (collider for collider in world.geometry.query(bounds, "sight")
            if collider.kind not in {"building", "terrain"})


def smooth(value):
    t = min(1.0, max(0.0, value))
    return t * t * (3 - 2 * t)


@dataclass(frozen=True)
class Sky:
    daylight: float
    ambient: tuple[int, int, int]
    direct: tuple[int, int, int]
    shadow: tuple[float, float]

    @property
    def infrared(self):
        return 1 - self.daylight


@lru_cache(maxsize=1)
def sky_at(seconds):
    hour = seconds % 86400 / 3600
    dawn = smooth((hour - 5.0) / 2.5)
    dusk = smooth((20.5 - hour) / 2.5)
    daylight = dawn * dusk
    direction = (seconds // 30 % 2880 / 120 - 6) * math.tau / 24
    sun = math.sin(direction)
    # 같은 시각에 연속적인 방향을 사용하고, 고도가 낮을수록 그림자를 늘린다.
    length = 4.0 - 2.65 * abs(sun)
    ambient = tuple(round(night + (day - night) * daylight)
                    for night, day in zip((23, 29, 42), (108, 117, 126)))
    direct = tuple(round(night + (day - night) * daylight)
                   for night, day in zip((32, 42, 55), (131, 123, 109)))
    return Sky(daylight, ambient, direct, (-math.cos(direction) * length, -math.sin(direction) * length))


def actor_optics(actor, sky):
    if actor.hp <= 0:
        return "off", False, False
    if actor.team == "player" or actor.lamp != "off" or actor.night_vision or actor.ir_laser:
        return actor.lamp, actor.night_vision, actor.ir_laser
    slot = (actor.member if actor.member >= 0 else sum(actor.id.encode("utf-8"))) % 4
    if sky.infrared > 0.35:
        if slot == 0:
            return "infrared", True, actor.state in {"combat", "combat_guard"}
        if slot == 1:
            return "white", False, False
    return "off", False, False


def beam_end(world, origin, direction, distance):
    x, y = origin
    dx, dy = math.cos(direction) * distance, math.sin(direction) * distance
    end = x + dx, y + dy
    bounds = Rect(min(x, end[0]), min(y, end[1]), abs(dx), abs(dy)).expanded(0.001)
    entries = (collider.rect.segment_entry(origin, end) for collider in light_blockers(world, bounds))
    fraction = min((entry for entry in entries if entry is not None), default=None)
    if fraction is not None:
        fraction = max(0.0, fraction - 0.01 / distance)
        return x + dx * fraction, y + dy * fraction
    return None


def incident_light(world, point, sky, lights):
    """화면과 같은 광원·가림 규칙으로 AI 관측 지점의 밝기를 구한다."""
    view = Rect(*point, 0, 0).expanded(0.001)
    indoors = any(rect.segment_entry(point, point) is not None for rect in world.light_roofs.query(view))
    shade = False
    if not indoors:
        dx, dy = sky.shadow
        origin = point[0] - dx, point[1] - dy
        bounds = Rect(min(origin[0], point[0]), min(origin[1], point[1]), abs(dx), abs(dy)).expanded(0.001)
        for collider in light_blockers(world, bounds):
            height = SHADOW_HEIGHT.get(collider.kind, 0.36)
            end = point[0] - dx * height, point[1] - dy * height
            if collider.rect.segment_entry(point, end) is not None:
                shade = True
                break
    visible = sum(sky.ambient) / 765
    infrared = 0.0
    if not (indoors or shade):
        visible += sum(sky.direct) / 765
    for light in lights:
        if light.intensity <= 0 or light.infrared and not sky.infrared:
            continue
        dx, dy = point[0] - light.position[0], point[1] - light.position[1]
        distance = math.hypot(dx, dy)
        if distance >= light.radius:
            continue
        direction = math.atan2(dy, dx)
        if distance and (abs(math.remainder(direction - light.direction, math.tau)) > light.half_angle
                         or beam_end(world, light.position, direction, distance) is not None):
            continue
        strength = light.intensity * (1 - (distance / light.radius)**2)**2
        strength *= sum(light.color) / 765
        if light.infrared:
            infrared += strength * sky.infrared
        else:
            visible += strength
    return visible, infrared


def _quad(a, b, c, d):
    return a, b, c, a, c, d


def _corners(rect):
    return (rect.x, rect.y), (rect.right, rect.y), (rect.right, rect.bottom), (rect.x, rect.bottom)


def _sun_shadows(roofs, blockers, shadow):
    black = (0, 0, 0)
    for rect in roofs:
        yield _quad(*_corners(rect)), black
    for collider in blockers:
        height = SHADOW_HEIGHT.get(collider.kind, 0.36)
        dx, dy = shadow[0] * height, shadow[1] * height
        a, b, c, d = _corners(collider.rect)
        x_side = (b, c) if dx >= 0 else (a, d)
        y_side = (d, c) if dy >= 0 else (a, b)
        yield _quad(a, b, c, d), black
        for p, q in (x_side, y_side):
            yield _quad(p, q, (q[0] + dx, q[1] + dy), (p[0] + dx, p[1] + dy)), black


def light_outline(light, blockers):
    """형상 모서리 양옆과 반경 둘레를 표본화해 가장 가까운 교차점을 구한다."""
    x, y = light.position
    angles = np.linspace(-light.half_angle, light.half_angle,
                         max(8, math.ceil(light.half_angle * 64 / math.pi)) + 1)
    if blockers:
        bounds = np.asarray([(rect.x - x, rect.y - y, rect.right - x, rect.bottom - y)
                             for rect in blockers])
        low, high = bounds[:, :2], bounds[:, 2:]
        if np.any(np.all((low < 0) & (high > 0), axis=1)):
            return ()
        corner_x = bounds[:, [0, 0, 2, 2]]
        corner_y = bounds[:, [1, 3, 1, 3]]
        bearings = (np.arctan2(corner_y, corner_x) - light.direction + math.pi) % math.tau - math.pi
        bearings = (bearings[..., None] + (-EDGE_SAMPLE, EDGE_SAMPLE) + math.pi) % math.tau - math.pi
        angles = np.sort(np.concatenate((angles, bearings[np.abs(bearings) < light.half_angle])))
    angles += light.direction
    directions = np.column_stack((np.cos(angles), np.sin(angles)))
    distances = np.full(len(angles), light.radius, dtype=float)
    if blockers:
        # 묶음 단위로 처리해 빽빽한 숲에서도 임시 배열의 크기를 제한한다.
        denominator = np.where(np.abs(directions) < 1e-12, 1e-12, directions)[:, None, :]
        for start in range(0, len(low), 32):
            first = low[None, start:start + 32] / denominator
            second = high[None, start:start + 32] / denominator
            near = np.minimum(first, second).max(axis=2)
            far = np.maximum(first, second).min(axis=2)
            hits = np.where((near <= far) & (far > 0) & (near >= 0), near, light.radius)
            np.minimum(distances, hits.min(axis=1), out=distances)
    return tuple(map(tuple, directions * distances[:, None]))


class Lighting:
    def __init__(self, backend):
        self.sdl = backend
        self.target = self.make_target(backend.SDL_BLENDMODE_MOD)
        self.sky_target = self.make_target(backend.SDL_BLENDMODE_ADD) if self.target else None
        self.falloff = self._falloff_texture() if self.target else None
        self.cache = {}
        self.world = None
        self.contact_mesh = self.shadow_mesh = None
        self.mesh_area = self.mesh_shadow = None

    def make_target(self, blend):
        sdl = self.sdl
        target = sdl.SDL_CreateTexture(sdl.renderer, sdl.SDL_PIXELFORMAT_RGBA8888,
                                       sdl.SDL_TEXTUREACCESS_TARGET, *LIGHT_SIZE)
        if target:
            if sdl.SDL_SetTextureBlendMode(target, blend) < 0:
                sdl.SDL_DestroyTexture(target)
                return None
            sdl.SDL_SetTextureScaleMode(target, sdl.SDL_ScaleModeLinear)
        return target

    def _falloff_texture(self):
        sdl = self.sdl
        size = 96
        axis = np.linspace(-1, 1, size)
        distance = np.hypot(axis[:, None], axis)
        pixels = np.full((size, size, 4), 255, dtype=np.uint8)
        pixels[:, :, 3] = (255 * np.maximum(0, 1 - distance**2)**2).astype(np.uint8)
        texture = sdl.SDL_CreateTexture(sdl.renderer, sdl.SDL_PIXELFORMAT_RGBA32,
                                        sdl.SDL_TEXTUREACCESS_STATIC, size, size)
        if texture and (sdl.SDL_UpdateTexture(texture, None, pixels.ctypes.data, size * 4) < 0
                        or sdl.SDL_SetTextureBlendMode(texture, sdl.SDL_BLENDMODE_ADD) < 0):
            sdl.SDL_DestroyTexture(texture)
            return None
        if texture:
            sdl.SDL_SetTextureScaleMode(texture, sdl.SDL_ScaleModeLinear)
        return texture

    def _outline(self, world, light):
        shape = (light.position, light.radius, light.direction, light.half_angle)
        cached = self.cache.get(light.id)
        if cached and cached[:2] == (shape, world.revision):
            return cached[3]
        rects = frozenset(collider.rect for collider in light_blockers(world, light.bounds))
        mesh = cached[3] if cached and cached[0] == shape and cached[2] == rects else self._light_mesh(light, rects)
        self.cache[light.id] = shape, world.revision, rects, mesh
        return mesh

    @staticmethod
    def _light_mesh(light, rects):
        outline = light_outline(light, rects)
        if not outline:
            return None
        offsets = np.asarray([(0, 0), *outline], dtype=np.float32)
        uv = np.clip(0.5 + offsets * (0.5 / light.radius), 0, 1)
        edges = np.arange(1, len(outline), dtype=np.int32)
        indices = np.zeros((len(edges), 3), dtype=np.int32)
        indices[:, 1], indices[:, 2] = edges, edges + 1
        return offsets, uv, indices

    def _draw_light(self, world, view, light, color):
        mesh = self._outline(world, light)
        if mesh is None:
            return
        sdl = self.sdl
        offsets, uv, indices = mesh
        positions = offsets * (view.scale * 0.5)
        positions += np.asarray(view.screen(*light.position), dtype=np.float32) * 0.5
        rgba = sdl.SDL_Color(*color, 255)
        sdl.SDL_RenderGeometryRaw(sdl.renderer, self.falloff,
                                  positions.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), positions.strides[0],
                                  ctypes.byref(rgba), 0,
                                  uv.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), uv.strides[0], len(positions),
                                  indices.ctypes.data, indices.size, indices.itemsize)

    def _prepare_sky_meshes(self, world, viewport, shadow):
        grid = 6
        left, top = math.floor(viewport.x / grid) * grid, math.floor(viewport.y / grid) * grid
        right, bottom = math.ceil(viewport.right / grid) * grid, math.ceil(viewport.bottom / grid) * grid
        area = Rect(left, top, right - left, bottom - top)
        key = (area, world.revision)
        if self.mesh_area == key and self.mesh_shadow == shadow:
            return
        blockers = tuple(light_blockers(world, area.expanded(4.25)))
        if self.mesh_area != key:
            contact = ((_quad(*_corners(collider.rect.expanded(padding))), (0, 0, 0, alpha))
                       for collider in blockers if collider.kind in SHADOW_HEIGHT
                       for padding, alpha in ((0.18, 28), (0.08, 30)))
            self.contact_mesh = self._world_mesh(contact)
        self.shadow_mesh = self._world_mesh(_sun_shadows(world.light_roofs.query(area), blockers, shadow))
        self.mesh_area, self.mesh_shadow = key, shadow

    def _world_mesh(self, triangles):
        batch = MapBatch(self.sdl, triangles)
        return batch, batch.positions.copy()

    def _draw_world_mesh(self, mesh, view):
        batch, world_points = mesh
        if batch.count:
            np.multiply(world_points, view.scale * 0.5, out=batch.positions)
            batch.positions += (view.offset_x * 0.5, view.offset_y * 0.5)
            batch.draw(self.sdl)

    @staticmethod
    def _sensor_color(color, gain):
        level = sum(color) / 3 * gain
        return tuple(min(255, round(level * channel)) for channel in (0.62, 1.12, 0.74))

    def sky_colors(self, sky, night_vision):
        if night_vision:
            return self._sensor_color(sky.ambient, 2.9), self._sensor_color(sky.direct, 1.65)
        return sky.ambient, sky.direct

    def draw(self, world, view, lights, sky, *, night_vision=False, floor=0):
        sdl = self.sdl
        if not self.target:
            return
        if self.world is not world:
            self.world = world
            self.cache.clear()
            self.mesh_area = None
        viewport = view.viewport()
        self._prepare_sky_meshes(world, viewport, sky.shadow)
        if sdl.SDL_SetRenderTarget(sdl.renderer, self.target) < 0:
            return
        try:
            ambient, direct = self.sky_colors(sky, night_vision)
            sdl.SDL_SetRenderDrawColor(sdl.renderer, *ambient, 255)
            sdl.SDL_RenderClear(sdl.renderer)
            self._draw_world_mesh(self.contact_mesh, view)
            if self.sky_target and sdl.SDL_SetRenderTarget(sdl.renderer, self.sky_target) == 0:
                sdl.SDL_SetRenderDrawColor(sdl.renderer, *direct, 255)
                sdl.SDL_RenderClear(sdl.renderer)
                self._draw_world_mesh(self.shadow_mesh, view)
                if sdl.SDL_SetRenderTarget(sdl.renderer, self.target) < 0:
                    return
                sdl.SDL_RenderCopy(sdl.renderer, self.sky_target, None, sdl.SDL_Rect(0, 0, *LIGHT_SIZE))
            visible = set()
            for light in lights:
                if (not self.falloff or light.floor != floor or light.intensity <= 0
                        or light.infrared and not (night_vision and sky.infrared)
                        or not light.bounds.overlaps(viewport)):
                    continue
                visible.add(light.id)
                intensity = light.intensity * (sky.infrared if light.infrared else 1)
                if night_vision:
                    intensity /= 0.7 + intensity
                    color = self._sensor_color(light.color, intensity)
                else:
                    color = tuple(min(255, round(channel * intensity)) for channel in light.color)
                self._draw_light(world, view, light, color)
            for key in self.cache.keys() - visible:
                del self.cache[key]
        finally:
            sdl.SDL_SetRenderTarget(sdl.renderer, None)
        # 창의 논리 뷰포트 전체에만 합성한다. None은 SDL 백엔드에 따라 전체 타깃으로 해석된다.
        sdl.SDL_RenderCopy(sdl.renderer, self.target, None, sdl.SDL_Rect(0, 0, *CANVAS_SIZE))

    def close(self):
        for texture in (self.falloff, self.sky_target, self.target):
            if texture:
                self.sdl.SDL_DestroyTexture(texture)
