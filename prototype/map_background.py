"""월드의 고정 지형과 도로를 지도용 이미지로 한 번 합성한다."""

import math
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from .chunk_terrain import ChunkTerrain
from .geometry import Rect
from .road_visuals import ROAD_STYLES


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
                      (34, 49, 43))
    draw = ImageDraw.Draw(image)

    def point(x, y):
        return ((x - left) * scale, (y - top) * scale)

    def rectangle(bounds, color):
        x, y, w, h = bounds
        draw.rectangle((*point(x, y), *point(x + w, y + h)), fill=tuple(color[:3]))

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
        material = road.get("material", "")
        color = ((114, 103, 77) if road.get("kind") == "footpath" else
                 ROAD_STYLES[material]["map_color"] if material else (153, 155, 123))
        draw.line((point(*road["start"]), point(*road["end"])), fill=color,
                  width=max(1, round(road.get("width", 4) * scale)))
    for junction in network.get("junctions", ()):
        for surface in junction["surfaces"]:
            vertices = surface["triangles"]
            color = ROAD_STYLES[surface["material"]]["map_color"]
            for index in range(0, len(vertices), 3):
                draw.polygon([point(*vertex) for vertex in vertices[index:index + 3]], fill=color)

    for site in scene.get("planned_facilities", ()):
        rectangle(site["rect"], (94, 175, 150) if site["kind"] == "safehouse" else (162, 166, 128))

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
