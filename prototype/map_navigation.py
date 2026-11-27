"""전체 지도의 확대와 이동 상태. 화면 좌표는 SDL의 왼쪽 위 원점을 쓴다."""

from dataclasses import dataclass


@dataclass
class MapNavigation:
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    dragging: bool = False

    def reset(self):
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.dragging = False

    def zoom_at(self, factor, cursor, size):
        zoom = max(1.0, min(8.0, self.zoom * factor))
        if zoom == self.zoom:
            return
        ratio = zoom / self.zoom
        center_x, center_y = size[0] / 2, size[1] / 2
        self.pan_x = cursor[0] - center_x - (cursor[0] - center_x - self.pan_x) * ratio
        self.pan_y = cursor[1] - center_y - (cursor[1] - center_y - self.pan_y) * ratio
        self.zoom = zoom

    def move(self, dx, dy):
        self.pan_x += dx
        self.pan_y += dy

    def clamp(self, map_size, viewport_size):
        max_x = max(0.0, (map_size[0] - viewport_size[0]) / 2)
        max_y = max(0.0, (map_size[1] - viewport_size[1]) / 2)
        self.pan_x = max(-max_x, min(max_x, self.pan_x))
        self.pan_y = max(-max_y, min(max_y, self.pan_y))
