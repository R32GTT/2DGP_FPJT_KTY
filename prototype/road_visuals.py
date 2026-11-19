"""도로의 표시 스타일. 그림 경로를 바꿔도 생성·통행 치수는 유지한다."""

from .region import strip_layers


ROAD_STYLES = {
    "asphalt": {"style": "road_asphalt", "image": "", "junction_image": "",
                "color": [75, 81, 78], "map_color": (153, 155, 143)},
    "dirt": {"style": "road_dirt", "image": "", "junction_image": "",
             "color": [128, 111, 78], "map_color": (145, 124, 86)},
}


def road_layers(network):
    for material in ('dirt', 'asphalt'):
        style = ROAD_STYLES[material]
        for index, segment in enumerate(network["segments"]):
            if segment['material'] != material:
                continue
            start, end = (network["nodes"][key] for key in segment["nodes"])
            for layer in strip_layers(segment.get("id", f"road/{index}"), start, end, segment["width"], style["style"], style["color"]):
                yield {**layer, "image": style["image"], "variant": int(segment["kind"] != "main")}
        for junction in network["junctions"]:
            for index, surface in enumerate(junction['surfaces']):
                if surface['material'] != material:
                    continue
                points = surface["polygon"]
                x, y = map(min, zip(*points))
                right, bottom = map(max, zip(*points))
                yield {"id": f"junction/{junction['node']}/{index}", "rect": [x, y, right - x, bottom - y],
                       "polygon": points, "triangles": surface['triangles'],
                       "style": "road_junction", "image": style["junction_image"], "color": style["color"]}
