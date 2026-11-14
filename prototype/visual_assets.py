"""외부 그림이 없을 때 사용할 작은 GPU 그림을 시작 시 한 번 만든다."""

import ctypes
import math
import random

import pico2d


class Painter:
    def __init__(self, backend):
        self.backend = backend

    def rect(self, x, y, w, h, color):
        backend = self.backend
        backend.SDL_SetRenderDrawColor(backend.renderer, *color[:3], color[3] if len(color) > 3 else 255)
        backend.SDL_RenderFillRect(backend.renderer, backend.SDL_Rect(int(x), int(y), max(1, int(w)), max(1, int(h))))


def painted_image(backend, width, height, paint):
    texture = backend.SDL_CreateTexture(backend.renderer, backend.SDL_PIXELFORMAT_RGBA8888,
                                        backend.SDL_TEXTUREACCESS_TARGET, width, height)
    if not texture:
        raise RuntimeError("임시 그래픽 텍스처 생성 실패")
    previous = backend.SDL_GetRenderTarget(backend.renderer)
    try:
        backend.SDL_SetTextureBlendMode(texture, backend.SDL_BLENDMODE_BLEND)
        backend.SDL_SetRenderTarget(backend.renderer, texture)
        backend.SDL_SetRenderDrawColor(backend.renderer, 0, 0, 0, 0)
        backend.SDL_RenderClear(backend.renderer)
        paint(Painter(backend))
    except Exception:
        backend.SDL_DestroyTexture(texture)
        raise
    finally:
        backend.SDL_SetRenderTarget(backend.renderer, previous)
    return pico2d.Image(texture)


def actor_image(backend, player):
    def paint(p):
        # 몸통 중심 (24,12). 위쪽이 정면이며 어깨가 좌우로 넓게 펼쳐진다.
        p.rect(4, 5, 40, 17, (17, 25, 27))
        p.rect(10, 6, 28, 14, (68, 126, 132) if player else (136, 133, 91))
        p.rect(15, 18, 18, 5, (42, 70, 68))
        p.rect(15, 9, 18, 10, (35, 49, 48))
        p.rect(18, 1, 12, 12, (182, 171, 136))
        p.rect(19, 1, 10, 9, (74, 98, 91) if player else (107, 109, 77))
        p.rect(4, 8, 8, 12, (91, 151, 151) if player else (161, 156, 107))
        p.rect(36, 8, 8, 12, (91, 151, 151) if player else (161, 156, 107))
    return painted_image(backend, 48, 24, paint)


def weapon_image(backend, alternate=False):
    def paint(p):
        p.rect(7, 9, 34, 5, (27, 33, 34))
        p.rect(34, 10, 23, 2, (161, 166, 154))
        p.rect(0, 8, 12, 8, (111, 106, 79) if alternate else (69, 85, 80))
        p.rect(12, 8, 25, 7, (70, 77, 72))
        p.rect(18, 6, 6, 3, (172, 177, 159))
        p.rect(19, 14, 5, 7, (37, 43, 42))
        p.rect(28, 9, 10, 5, (110, 112, 94))
    return painted_image(backend, 64, 24, paint)


def tree_image(backend):
    def paint(p):
        p.rect(58, 55, 12, 25, (84, 71, 47))
        for radius, color, offset in ((48, (21, 36, 29, 140), 5), (45, (32, 62, 43), 0),
                                      (34, (44, 79, 50), -5), (19, (60, 93, 57), -9)):
            for y in range(-radius, radius + 1):
                half = int(math.sqrt(max(0, radius * radius - y * y)))
                p.rect(64 - half + offset, 62 + y + offset, half * 2, 1, color)
    return painted_image(backend, 128, 128, paint)


def truck_image(backend):
    def paint(p):
        p.rect(5, 2, 11, 4, (16, 24, 23))
        p.rect(5, 24, 11, 4, (16, 24, 23))
        p.rect(45, 2, 11, 4, (16, 24, 23))
        p.rect(45, 24, 11, 4, (16, 24, 23))
        p.rect(2, 6, 59, 18, (56, 78, 64))
        p.rect(4, 7, 37, 16, (102, 115, 80))
        for x in (8, 19, 30):
            p.rect(x, 8, 2, 14, (75, 91, 67))
        p.rect(43, 7, 16, 16, (116, 130, 95))
        p.rect(52, 9, 5, 12, (35, 62, 62))
        p.rect(59, 7, 3, 4, (225, 224, 161))
        p.rect(59, 19, 3, 4, (225, 224, 161))
    return painted_image(backend, 64, 30, paint)


def prop_image(backend, kind):
    def paint(p):
        p.rect(4, 6, 120, 54, (22, 31, 29, 150))
        if kind == "bed":
            p.rect(2, 2, 122, 56, (88, 91, 77))
            p.rect(9, 7, 108, 44, (111, 129, 97))
            p.rect(12, 9, 27, 40, (195, 194, 160))
            p.rect(45, 7, 3, 44, (150, 157, 123))
        elif kind == "rack":
            p.rect(2, 2, 122, 56, (38, 53, 55))
            for x in range(8, 118, 28):
                p.rect(x, 8, 22, 42, (73, 91, 87))
                for y in (16, 27, 38):
                    p.rect(x + 4, y, 12, 3, (31, 47, 46))
                    p.rect(x + 16, y, 2, 2, (125, 201, 143))
        elif kind == "desk":
            p.rect(2, 2, 122, 56, (128, 119, 88))
            p.rect(8, 8, 112, 44, (153, 144, 106))
            p.rect(18, 12, 36, 24, (36, 52, 52))
            p.rect(22, 15, 28, 17, (98, 153, 133))
            p.rect(70, 13, 31, 30, (207, 204, 168))
            p.rect(19, 41, 32, 7, (62, 72, 65))
        elif kind == "antenna":
            p.rect(12, 12, 104, 40, (103, 119, 109))
            for y in range(-25, 26):
                radius = int(math.sqrt(max(0, 625 - y * y)) * 1.7)
                p.rect(64 - radius, 30 + y, radius * 2, 1, (184, 190, 163))
            p.rect(62, 4, 4, 53, (76, 94, 84))
            p.rect(21, 29, 86, 3, (76, 94, 84))
        else:
            p.rect(2, 2, 122, 56, (119, 101, 67))
            p.rect(8, 8, 110, 44, (159, 140, 87))
            for x in (11, 55, 99):
                p.rect(x, 6, 8, 48, (100, 89, 62))
            p.rect(6, 24, 114, 6, (189, 170, 112))
    return painted_image(backend, 128, 64, paint)


def layer_image(backend, layer):
    role = layer.role
    if role == "walls":
        return None
    road = role.startswith("road")
    vertical = role == "road_v"
    width, height = ((64, 512) if vertical else (512, 64)) if road else (768, 512)

    def paint(p):
        rng = random.Random(f"prototype-art:{role}:{layer.variant}")
        if road:
            p.rect(0, 0, width, height, (144, 135, 107))
            if vertical:
                p.rect(5, 0, width - 10, height, (75, 81, 78))
                for y in range(0, height, 45):
                    p.rect(width / 2, y, 2, 22, (185, 180, 145))
            else:
                p.rect(0, 5, width, height - 10, (75, 81, 78))
                for x in range(0, width, 45):
                    p.rect(x, height / 2, 22, 2, (185, 180, 145))
        elif role in {"floor", "yard"}:
            p.rect(0, 0, width, height, layer.color)
            for y in range(0, height, 85):
                p.rect(0, y, width, 1, (106, 115, 105))
            for x in range(0, width, 128):
                p.rect(x, 0, 1, height, (106, 115, 105))
            p.rect(20, 20, width - 40, 5, (164, 161, 121))
            p.rect(20, height - 26, width - 40, 5, (164, 161, 121))
            p.rect(20, 20, 5, height - 40, (164, 161, 121))
            p.rect(width - 25, 20, 5, height - 40, (164, 161, 121))
        elif role == "dirt":
            for y in range(height):
                fraction = (y / height - 0.5) * 2
                radius = math.sqrt(max(0, 1 - fraction * fraction)) * width * 0.47
                p.rect(width / 2 - radius, y, radius * 2, 1, (*layer.color[:3], 180))
        else:
            p.rect(0, 0, width, height, layer.color)
            for _ in range(1200):
                x, y = rng.randrange(width), rng.randrange(height)
                shade = rng.choice((-6, -3, 4, 7))
                color = tuple(max(0, min(255, component + shade)) for component in layer.color[:3])
                p.rect(x, y, rng.randrange(2, 14), rng.randrange(1, 6), color)
    return painted_image(backend, width, height, paint)


def vignette_image(backend):
    width, height = 256, 144
    surface = backend.SDL_CreateRGBSurfaceWithFormat(0, width, height, 32, backend.SDL_PIXELFORMAT_RGBA32)
    if not surface:
        raise RuntimeError("비네팅 표면 생성 실패")
    pixels = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            edge = max(abs(x / (width - 1) * 2 - 1), abs(y / (height - 1) * 2 - 1))
            alpha = int(max(0, (edge - 0.45) / 0.55) ** 1.7 * 240)
            index = (y * width + x) * 4
            pixels[index:index + 4] = bytes((205, 18, 30, alpha))
    ctypes.memmove(surface.contents.pixels, bytes(pixels), len(pixels))
    texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
    backend.SDL_FreeSurface(surface)
    if not texture:
        raise RuntimeError("비네팅 텍스처 생성 실패")
    backend.SDL_SetTextureBlendMode(texture, backend.SDL_BLENDMODE_BLEND)
    return pico2d.Image(texture)
