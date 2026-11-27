"""전체 지도의 확대와 이동 상태. 화면 좌표는 SDL의 왼쪽 위 원점을 쓴다."""

from dataclasses import dataclass


RESPAWN_SIDEBAR = 360


@dataclass(frozen=True)
class MapTransform:
    viewport: tuple[int, int, int, int]
    scale: float
    center_x: float
    center_y: float
    origin_x: float
    origin_y: float
    pan_x: float
    pan_y: float

    def screen(self, x, y):
        return (self.center_x + (x - self.origin_x) * self.scale + self.pan_x,
                self.center_y + (y - self.origin_y) * self.scale + self.pan_y)


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

    def zoom_at(self, factor, cursor, size, *, sidebar=0):
        zoom = max(1.0, min(8.0, self.zoom * factor))
        if zoom == self.zoom:
            return
        ratio = zoom / self.zoom
        center_x, center_y = (size[0] - sidebar) / 2, size[1] / 2
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

    def transform(self, bounds, size, *, sidebar=0):
        width, height = size
        viewport = (24, 24, width - 48 - sidebar, height - 48)
        fit = min((viewport[2] - 24) / bounds.w, (viewport[3] - 24) / bounds.h)
        scale = fit * self.zoom
        self.clamp((bounds.w * scale, bounds.h * scale), viewport[2:])
        return MapTransform(viewport, scale,
                            viewport[0] + viewport[2] / 2, viewport[1] + viewport[3] / 2,
                            bounds.x + bounds.w / 2, bounds.y + bounds.h / 2,
                            self.pan_x, self.pan_y)

    def focus(self, position, bounds, size, *, sidebar=0):
        projection = self.transform(bounds, size, sidebar=sidebar)
        x, y = projection.screen(*position)
        self.pan_x += projection.center_x - x
        self.pan_y += projection.center_y - y
        self.clamp((bounds.w * projection.scale, bounds.h * projection.scale), projection.viewport[2:])
