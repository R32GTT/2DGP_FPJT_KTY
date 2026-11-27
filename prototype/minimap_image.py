"""작은 지도에 표시할 정적 지형을 한 번 래스터화한다."""

import math

from PIL import Image, ImageDraw


MINIMAP_SCALE = 3.0


def build_minimap_image(world):
    bounds = world.bounds
    scale = MINIMAP_SCALE
    width = max(1, math.ceil(bounds.w * scale))
    height = max(1, math.ceil(bounds.h * scale))
    image = Image.new("RGBA", (width, height), (29, 44, 47, 255))
    painter = ImageDraw.Draw(image)

    def point(x, y):
        return round((x - bounds.x) * scale), round((y - bounds.y) * scale)

    for layer in world.visible_layers(bounds):
        if layer.role in {"water", "floor", "roof", "pavement"}:
            rect = layer.rect
            painter.rectangle((*point(rect.x, rect.y), *point(rect.right, rect.bottom)), fill=(*layer.color[:3], 255))
    for road in world.roads:
        painter.line((point(*road["start"]), point(*road["end"])), fill=(84, 108, 108, 255),
                     width=max(2, round(road.get("width", 4) * scale)))
    for collider in world.visible_colliders(bounds):
        if collider.kind not in {"terrain", "tree"}:
            rect = collider.rect
            color = (67, 81, 79, 255) if collider.active else (98, 110, 105, 255)
            painter.rectangle((*point(rect.x, rect.y), *point(rect.right, rect.bottom)), fill=color)
    return image
