"""월드의 광원과 차폐를 논리 화면의 저해상도 조명 버퍼에 합성한다."""

import ctypes
from dataclasses import dataclass
import math

import numpy as np

from .camera import CANVAS_SIZE
from .geometry import Rect


LIGHT_SIZE = tuple(size // 2 for size in CANVAS_SIZE)
EDGE_SAMPLE = 0.0001


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

    @property
    def bounds(self):
        return Rect(*self.position, 0, 0).expanded(self.radius)


def light_blockers(world, bounds):
    # 건물의 포괄 경계와 장식 레이어 대신 실제 벽·문·엄폐물만 사용한다.
    return (collider for collider in world.geometry.query(bounds, "sight")
            if collider.kind not in {"building", "terrain"})


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
        self.falloff = self._falloff_texture() if self.target else None
        self.cache = {}
        self.world = None

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
        outline = (cached[3] if cached and cached[0] == shape and cached[2] == rects
                   else light_outline(light, rects))
        self.cache[light.id] = shape, world.revision, rects, outline
        return outline

    def _draw_light(self, world, view, light, color):
        outline = self._outline(world, light)
        if not outline:
            return
        sdl = self.sdl
        x, y = (coordinate * 0.5 for coordinate in view.screen(*light.position))
        scale = view.scale * 0.5
        uv_scale = 0.5 / light.radius
        vertices = (sdl.SDL_Vertex * (len(outline) + 1))(
            sdl.SDL_Vertex((x, y), (*color, 255), (0.5, 0.5)),
            *(sdl.SDL_Vertex((x + dx * scale, y + dy * scale), (*color, 255),
                            (0.5 + dx * uv_scale, 0.5 + dy * uv_scale)) for dx, dy in outline))
        indices = (ctypes.c_int * ((len(outline) - 1) * 3))(
            *(index for end in range(1, len(outline)) for index in (0, end, end + 1)))
        sdl.SDL_RenderGeometry(sdl.renderer, self.falloff, vertices, len(vertices), indices, len(indices))

    def draw(self, world, view, lights):
        sdl = self.sdl
        if not self.target or not self.falloff:
            return
        if self.world is not world:
            self.world = world
            self.cache.clear()
        if sdl.SDL_SetRenderTarget(sdl.renderer, self.target) < 0:
            return
        try:
            sdl.SDL_SetRenderDrawColor(sdl.renderer, 172, 174, 178, 255)
            sdl.SDL_RenderClear(sdl.renderer)
            viewport = view.viewport()
            visible = set()
            for light in lights:
                if light.infrared or light.intensity <= 0 or not light.bounds.overlaps(viewport):
                    continue
                visible.add(light.id)
                color = tuple(min(255, round(channel * light.intensity)) for channel in light.color)
                self._draw_light(world, view, light, color)
            for key in self.cache.keys() - visible:
                del self.cache[key]
        finally:
            sdl.SDL_SetRenderTarget(sdl.renderer, None)
        # 창의 논리 뷰포트 전체에만 합성한다. None은 SDL 백엔드에 따라 전체 타깃으로 해석된다.
        sdl.SDL_RenderCopy(sdl.renderer, self.target, None, sdl.SDL_Rect(0, 0, *CANVAS_SIZE))

    def close(self):
        for texture in (self.falloff, self.target):
            if texture:
                self.sdl.SDL_DestroyTexture(texture)
