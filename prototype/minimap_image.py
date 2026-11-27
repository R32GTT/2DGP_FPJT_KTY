"""작은 지도에 표시할 정적 지형을 한 번 래스터화한다."""

import math

from PIL import Image, ImageDraw

from .hud_theme import THEME, map_tone


MINIMAP_SCALE = 3.0


def build_minimap_image(world):
    bounds = world.bounds
    scale = MINIMAP_SCALE
    width = max(1, math.ceil(bounds.w * scale))
    height = max(1, math.ceil(bounds.h * scale))
    image = Image.new("RGBA", (width, height), (*THEME.map_ground, 255))
    painter = ImageDraw.Draw(image)

    def point(x, y):
        return round((x - bounds.x) * scale), round((y - bounds.y) * scale)

    for layer in world.visible_layers(bounds):
        if layer.role in {"water", "floor", "roof", "pavement"}:
            rect = layer.rect
            painter.rectangle((*point(rect.x, rect.y), *point(rect.right, rect.bottom)),
                              fill=(*map_tone(layer.color), 255))
    for road in world.roads:
        painter.line((point(*road["start"]), point(*road["end"])), fill=(*THEME.map_road, 255),
                     width=max(2, round(road.get("width", 4) * scale)))
    for collider in world.visible_colliders(bounds):
        if collider.kind not in {"terrain", "tree"}:
            rect = collider.rect
            color = (*THEME.map_facility, 255) if collider.active else (*THEME.map_safehouse, 255)
            painter.rectangle((*point(rect.x, rect.y), *point(rect.right, rect.bottom)), fill=color)
    return image
