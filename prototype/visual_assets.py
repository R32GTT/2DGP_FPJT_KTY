"""외부 그림이 없을 때 사용할 작은 GPU 그림을 시작 시 한 번 만든다."""

import ctypes
import math
import random

import pico2d


def draw_polygon(backend, points, color, indices=None):
    if indices is None:
        indices = [index for end in range(1, len(points) - 1) for index in (0, end, end + 1)]
    vertices = (backend.SDL_Vertex * len(indices))(*(
        backend.SDL_Vertex((points[index][0], pico2d.get_canvas_height() - points[index][1]), color)
        for index in indices))
    backend.SDL_RenderGeometry(backend.renderer, None, vertices, len(indices), None, 0)


class Painter:
    def __init__(self, backend, renderer):
        self.backend = backend
        self.renderer = renderer

    def rect(self, x, y, w, h, color):
        backend = self.backend
        backend.SDL_SetRenderDrawColor(self.renderer, *color[:3], color[3] if len(color) > 3 else 255)
        backend.SDL_RenderFillRect(self.renderer, backend.SDL_Rect(int(x), int(y), max(1, int(w)), max(1, int(h))))

    def ellipse(self, x, y, radius_x, radius_y, color):
        for row in range(-radius_y, radius_y + 1):
            half = int(radius_x * math.sqrt(1 - (row / radius_y)**2))
            self.rect(x - half, y + row, 2 * half + 1, 1, color)


def painted_image(backend, width, height, paint):
    # Direct3D는 창 크기가 바뀌면 렌더 타깃의 내용을 잃는다. 완성된 그림은 정적 텍스처로 보관한다.
    surface = backend.SDL_CreateRGBSurfaceWithFormat(0, width, height, 32, backend.SDL_PIXELFORMAT_RGBA32)
    if not surface:
        raise RuntimeError("임시 그래픽 표면 생성 실패")
    try:
        renderer = backend.SDL_CreateSoftwareRenderer(surface)
        if not renderer:
            raise RuntimeError("임시 그래픽 소프트웨어 렌더러 생성 실패")
        try:
            backend.SDL_SetRenderDrawBlendMode(renderer, backend.SDL_BLENDMODE_BLEND)
            backend.SDL_SetRenderDrawColor(renderer, 0, 0, 0, 0)
            backend.SDL_RenderClear(renderer)
            paint(Painter(backend, renderer))
            if backend.SDL_RenderFlush(renderer) < 0:
                raise RuntimeError("임시 그래픽 그리기 실패")
        finally:
            backend.SDL_DestroyRenderer(renderer)
        texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
    finally:
        backend.SDL_FreeSurface(surface)
    if not texture:
        raise RuntimeError("임시 그래픽 텍스처 생성 실패")
    backend.SDL_SetTextureBlendMode(texture, backend.SDL_BLENDMODE_BLEND)
    return pico2d.Image(texture)


def smoke_image(backend):
    def paint(p):
        p.ellipse(48, 48, 47, 47, (102, 109, 107, 44))
        for x, y, radius in ((26, 42, 24), (45, 30, 27), (65, 39, 27), (66, 60, 24),
                             (48, 66, 28), (31, 58, 23), (48, 47, 32)):
            for inset in (0, 2, 5, 8, 11):
                p.ellipse(x, y, radius - inset, radius - inset, (134, 140, 136, 35))
    return painted_image(backend, 96, 96, paint)


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


def weapon_image(backend, alternate=False, category="rifle"):
    def paint(p):
        if category == "pistol":
            p.rect(3, 7, 48, 7, (62, 71, 71))
            p.rect(11, 14, 15, 8, (35, 43, 45))
            p.rect(20, 8, 31, 2, (151, 157, 143))
            return
        p.rect(7, 9, 34, 5, (27, 33, 34))
        p.rect(34, 10, 23, 2, (161, 166, 154))
        p.rect(0, 8, 12, 8, (111, 106, 79) if alternate else (69, 85, 80))
        p.rect(12, 8, 25, 7, (70, 77, 72))
        p.rect(18, 6, 6, 3, (172, 177, 159))
        p.rect(19, 14, 5, 7, (37, 43, 42))
        p.rect(28, 9, 10, 5, (110, 112, 94))
        if category == "shotgun":
            p.rect(28, 7, 23, 9, (125, 91, 57))
        elif category == "sniper":
            p.rect(21, 4, 22, 7, (88, 103, 99))
            p.rect(37, 10, 24, 3, (164, 173, 162))
        elif category == "machine_gun":
            p.rect(21, 13, 14, 10, (83, 91, 69))
            p.rect(41, 7, 12, 9, (97, 106, 94))
    return painted_image(backend, 64, 24, paint)


def tree_canopy_image(backend):
    def paint(p):
        clusters = ((66, 29, 31, 23), (34, 46, 27, 29), (92, 49, 29, 26),
                    (28, 78, 23, 25), (55, 95, 31, 26), (87, 89, 31, 26))
        for x, y, rx, ry in clusters:
            p.ellipse(x, y, rx, ry, (31, 56, 40))
        for index, (x, y, rx, ry) in enumerate(clusters):
            shade = 4 * (index % 3)
            p.ellipse(x - 2, y - 3, rx - 4, ry - 6, (47 + shade, 73 + shade, 48 + shade))
            p.ellipse(x - 5, y - 5, rx // 2, ry // 2, (61 + shade, 86 + shade, 55 + shade))
        rng = random.Random(42)
        for x, y, rx, ry in clusters:
            for _ in range(22):
                angle, radius = rng.uniform(0, math.tau), rng.uniform(0.2, 0.76)
                p.rect(x + rx * math.cos(angle) * radius, y + ry * math.sin(angle) * radius,
                       rng.randrange(2, 5), 2, rng.choice(((49, 70, 46), (75, 94, 58))))
    return painted_image(backend, 128, 128, paint)


def tree_trunk_image(backend):
    def paint(p):
        p.rect(7, 15, 114, 98, (39, 32, 25))
        p.rect(14, 7, 100, 114, (39, 32, 25))
        p.rect(17, 18, 91, 91, (138, 111, 75))
        p.ellipse(62, 62, 42, 43, (90, 68, 49))
        p.ellipse(61, 61, 34, 34, (157, 123, 82))
        p.ellipse(60, 60, 23, 24, (115, 84, 55))
        p.ellipse(60, 60, 17, 17, (164, 130, 85))
        p.ellipse(58, 58, 6, 7, (107, 78, 51))
        p.rect(64, 30, 3, 13, (96, 68, 46))
        p.rect(24, 75, 13, 4, (102, 73, 47))
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
    road = role.startswith("road") and role != "road_junction"
    vertical = role == "road_v"
    if road or role in {"trail", "footbridge"}:
        width, height = (64, 512) if vertical else (512, 64)
    else:
        width, height = 768, 512

    def paint(p):
        rng = random.Random(f"prototype-art:{role}:{layer.variant}")
        if role == "road_dirt":
            p.rect(0, 0, width, height, layer.color)
            for y in (height * 0.25, height * 0.7):
                p.rect(0, y, width, height * 0.08, (108, 94, 66))
            for _ in range(200):
                p.rect(rng.randrange(width), rng.randrange(height), 2, 1, (142, 125, 89))
        elif role == "road_asphalt":
            p.rect(0, 0, width, height, layer.color)
            if not layer.variant:
                for x in range(0, width, 45):
                    p.rect(x, height / 2, 22, 2, (185, 180, 145))
        elif road:
            p.rect(0, 0, width, height, (144, 135, 107))
            if vertical:
                p.rect(5, 0, width - 10, height, (75, 81, 78))
                for y in range(0, height, 45):
                    p.rect(width / 2, y, 2, 22, (185, 180, 145))
            else:
                p.rect(0, 5, width, height - 10, (75, 81, 78))
                for x in range(0, width, 45):
                    p.rect(x, height / 2, 22, 2, (185, 180, 145))
        elif role == "footbridge":
            p.rect(0, 0, width, height, (75, 65, 52))
            for x in range(0, width, 18):
                p.rect(x, 5, 16, height - 10, layer.color)
        elif role == "roof":
            p.rect(0, 0, width, height, (55, 61, 55))
            p.rect(10, 10, width - 20, height - 20, layer.color)
            for y in range(26, height - 10, 38):
                p.rect(10, y, width - 20, 3, (69, 73, 66))
            p.rect(10, height / 2 - 5, width - 20, 10, (179, 168, 131))
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
