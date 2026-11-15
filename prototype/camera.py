"""기존 좌표 변환을 재사용하는 플레이어 추적 카메라."""

from campaign.viewmodel import Camera
from .geometry import Rect


class PlayCamera(Camera):
    def __init__(self, width=1280, height=720, zoom=1.0):
        super().__init__()
        self.width, self.height = width, height
        self.set_zoom(zoom)

    @property
    def magnification(self):
        return self.scale / 64

    def set_zoom(self, zoom):
        self.scale = 64 * max(0.5, min(4.0, zoom))

    def follow(self, position, mouse=None):
        ahead_x = max(-2, min(2, (mouse[0] - self.width / 2) / self.scale * 0.2)) if mouse else 0
        ahead_y = max(-2, min(2, (mouse[1] - self.height / 2) / self.scale * 0.2)) if mouse else 0
        self.offset_x = self.width / 2 - (position[0] + ahead_x) * self.scale
        self.offset_y = self.height / 2 - (position[1] + ahead_y) * self.scale

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
