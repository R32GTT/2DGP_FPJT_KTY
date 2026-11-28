"""플레이어 추적과 월드·화면 좌표 변환."""

import math

from .geometry import Rect


CANVAS_SIZE = (1280, 720)
PIXELS_PER_METER = 96


class PlayCamera:
    def __init__(self, width=CANVAS_SIZE[0], height=CANVAS_SIZE[1], zoom=1.0):
        self.width, self.height = width, height
        self.offset_x, self.offset_y = 0.0, 0.0
        self.look_x, self.look_y = 0.0, 0.0
        self.set_zoom(zoom)

    def screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.offset_x, y * self.scale + self.offset_y

    def world(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale

    @property
    def magnification(self):
        return self.scale / PIXELS_PER_METER

    def set_zoom(self, zoom):
        self.scale = PIXELS_PER_METER * max(0.5, min(4.0, zoom))

    def follow(self, position, mouse=None, *, aiming=False, look_multiplier=1.0, dt=None):
        half_w, half_h = self.width / 2, self.height / 2
        limit_x, limit_y = (7.2, 7.2) if aiming else (2.4, 4.2)
        mx, my = mouse if mouse is not None else (half_w, half_h)
        if aiming:
            target_x = max(-1.0, min(1.0, (mx - half_w) / half_w)) * limit_x
        else:
            target_x = max(-limit_x, min(limit_x, (mx - half_w) / self.scale * 0.24))
        target_y = max(-1.0, min(1.0, (my - half_h) / half_h)) * limit_y
        if aiming:
            target_x *= look_multiplier
            target_y *= look_multiplier
        blend = 1.0 if dt is None else -math.expm1(-12.0 * dt)
        self.look_x += (target_x - self.look_x) * blend
        self.look_y += (target_y - self.look_y) * blend
        self.recenter(position)

    def recenter(self, position):
        self.offset_x = self.width / 2 - (position[0] + self.look_x) * self.scale
        self.offset_y = self.height / 2 - (position[1] + self.look_y) * self.scale

    def visible(self, rect):
        x0, y0 = self.world(0, 0)
        x1, y1 = self.world(self.width, self.height)
        return rect.right >= x0 and rect.x <= x1 and rect.bottom >= y0 and rect.y <= y1

    def viewport(self, padding=0):
        left, top = self.world(0, 0)
        return Rect(left - padding, top - padding, self.width / self.scale + padding * 2,
                    self.height / self.scale + padding * 2)

    def pico(self, x, y):
        screen_x, screen_y = self.screen(x, y)
        return screen_x, self.height - screen_y
