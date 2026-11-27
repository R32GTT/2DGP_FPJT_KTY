"""월드의 고정 지형과 도로를 지도용 이미지로 한 번 합성한다."""

import math
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from .chunk_terrain import ChunkTerrain
from .geometry import Rect
from .hud_theme import THEME, map_tone


MAX_MAP_PIXELS = 8192


def create_background(scene, path):
    path = Path(path)
    if path.is_file():
        try:
            with Image.open(path) as cached:
                cached.verify()
            return path
        except (OSError, ValueError):
            pass

    left, top, width, height = scene["bounds"]
    scale = min(1.0, MAX_MAP_PIXELS / max(width, height))
    image = Image.new("RGB", (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))),
                      THEME.map_ground)
    draw = ImageDraw.Draw(image)

    def point(x, y):
        return ((x - left) * scale, (y - top) * scale)

    def rectangle(bounds, color):
        x, y, w, h = bounds
        draw.rectangle((*point(x, y), *point(x + w, y + h)), fill=map_tone(color))

    if scene.get("stream_terrain"):
        for row in ChunkTerrain(scene).ground(Rect(*scene["bounds"])):
            rectangle(row["rect"], row["color"])

    for row in scene["layers"]:
        if row.get("style", "").startswith("road") or row.get("style") == "pavement":
            continue
        if row.get("style") in {"water", "roof"}:
            rectangle(row["rect"], row["color"])

    network = scene.get("road_network", {})
    for road in scene.get("roads", ()):
        color = (THEME.map_facility if road.get("kind") == "footpath" else THEME.map_road)
        draw.line((point(*road["start"]), point(*road["end"])), fill=color,
                  width=max(1, round(road.get("width", 4) * scale)))
    for junction in network.get("junctions", ()):
        for surface in junction["surfaces"]:
            vertices = surface["triangles"]
            color = THEME.map_road
            for index in range(0, len(vertices), 3):
                draw.polygon([point(*vertex) for vertex in vertices[index:index + 3]], fill=color)

    for site in scene.get("planned_facilities", ()):
        x, y, w, h = site["rect"]
        draw.rectangle((*point(x, y), *point(x + w, y + h)),
                       fill=THEME.map_safehouse if site["kind"] == "safehouse" else THEME.map_facility)

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="map-", suffix=".png", dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
    try:
        image.save(temporary, format="PNG")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
