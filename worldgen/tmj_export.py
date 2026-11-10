"""Export a generated world into a Tiled-compatible .tmj file.

The semantic layers are the important part: Ground, Collision, Occluder and
Objects.  A missing or different tileset only changes the preview, not the
data that maptool compiles.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .constants import Flag, PIXELS_PER_METER, TILE_PIXELS
from .world import WorldMap


def _property(name: str, value, value_type: str | None = None) -> dict:
    if value_type is None:
        if isinstance(value, bool):
            value_type = "bool"
        elif isinstance(value, int):
            value_type = "int"
        elif isinstance(value, float):
            value_type = "float"
        else:
            value_type = "string"
    return {"name": name, "type": value_type, "value": value}


def _direction_name(outward: tuple[float, float]) -> str:
    x, y = outward
    if abs(x) >= abs(y):
        return "east" if x >= 0 else "west"
    return "south" if y >= 0 else "north"


def _tileset_source(output_path: Path) -> list[dict]:
    root = Path(__file__).resolve().parent.parent
    tileset = root / "assets" / "generated" / "revenant_tileset.tsx"
    if not tileset.exists():
        return []
    try:
        relative = Path(os.path.relpath(tileset, output_path.parent)).as_posix()
    except ValueError:
        return []
    return [{"firstgid": 1, "source": relative}]


def _object(
    object_id: int,
    name: str,
    class_name: str,
    x_m: float,
    y_m: float,
    *,
    width_m: float = 0.0,
    height_m: float = 0.0,
    properties: list[dict] | None = None,
) -> dict:
    result = {
        "id": object_id,
        "name": name,
        "class": class_name,
        "type": "",
        "x": round(x_m * PIXELS_PER_METER, 3),
        "y": round(y_m * PIXELS_PER_METER, 3),
        "width": round(width_m * PIXELS_PER_METER, 3),
        "height": round(height_m * PIXELS_PER_METER, 3),
        "rotation": 0.0,
        "visible": True,
    }
    if properties:
        result["properties"] = properties
    return result


def export_tmj(world: WorldMap, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    grid = world.grid

    ground = [int(value) for value in grid.terrain]
    collision = [
        1 if flag & int(Flag.BLOCKED) else 0 for flag in grid.flags
    ]
    occluder = [1 if value else 0 for value in grid.occlusion]

    objects: list[dict] = []
    next_id = 1

    objects.append(
        _object(
            next_id,
            "player_spawn",
            "world_spawn",
            world.spawn[0],
            world.spawn[1],
            properties=[_property("spawn_id", "player")],
        )
    )
    next_id += 1

    if world.gate is not None:
        objects.append(
            _object(
                next_id,
                "world_gate",
                "world_gate",
                world.gate[0],
                world.gate[1],
                properties=[_property("gate_id", "east")],
            )
        )
        next_id += 1

    for placement in world.facilities:
        objects.append(
            _object(
                next_id,
                placement.id,
                "facility",
                placement.x,
                placement.y,
                width_m=placement.width_m,
                height_m=placement.height_m,
                properties=[
                    _property("facility_id", placement.id),
                    _property("prefab_id", placement.prefab_id),
                    _property("facility_type", placement.facility_type),
                    _property("rotation", placement.rotation),
                    _property("entrance_x", round(placement.entrance[0], 3)),
                    _property("entrance_y", round(placement.entrance[1], 3)),
                ],
            )
        )
        next_id += 1
        objects.append(
            _object(
                next_id,
                f"{placement.id}_connector",
                "connector",
                placement.entrance[0],
                placement.entrance[1],
                properties=[
                    _property("connector_type", placement.connector_type),
                    _property("direction", _direction_name(placement.outward)),
                    _property("connector_width", 2.0),
                ],
            )
        )
        next_id += 1

    if world.roads is not None:
        for node in world.roads.nodes.values():
            objects.append(
                _object(
                    next_id,
                    f"road_{node.id}",
                    "road_node",
                    node.x,
                    node.y,
                    properties=[
                        _property("node_id", node.id),
                        _property("node_kind", node.kind),
                    ],
                )
            )
            next_id += 1

    for tree in world.trees:
        objects.append(
            _object(
                next_id,
                f"tree_{tree.id}",
                "tree",
                tree.x,
                tree.y,
                properties=[
                    _property("radius_m", round(tree.trunk_radius_m, 3)),
                    _property("canopy_radius_m", round(tree.canopy_radius_m, 3)),
                ],
            )
        )
        next_id += 1

    # Keep authored facility nav nodes and edges in the TMJ so maptool can
    # inspect or edit the same data the generator placed.
    node_positions: dict[str, tuple[float, float]] = {}
    for placement in world.facilities:
        for node in placement.nav_nodes:
            node_positions[node["id"]] = (node["x"], node["y"])
            objects.append(
                _object(
                    next_id,
                    f"nav_{placement.id}_{node['id']}",
                    "nav_node",
                    node["x"],
                    node["y"],
                    properties=[
                        _property("node_id", node["id"]),
                        _property("node_kind", node["kind"]),
                    ],
                )
            )
            next_id += 1
        for edge in placement.nav_edges:
            start = node_positions.get(edge["from"])
            end = node_positions.get(edge["to"])
            if start is None or end is None:
                continue
            objects.append(
                _object(
                    next_id,
                    f"nav_edge_{edge['from']}_{edge['to']}",
                    "nav_edge",
                    (start[0] + end[0]) * 0.5,
                    (start[1] + end[1]) * 0.5,
                    properties=[
                        _property("from_id", edge["from"]),
                        _property("to_id", edge["to"]),
                        _property("mode", edge.get("mode", "normal")),
                        _property("cost", float(edge.get("cost", 1.0))),
                        _property("width_m", float(edge.get("width_m", 1.0))),
                    ],
                )
            )
            next_id += 1

    layers = [
        {
            "id": 1,
            "name": "Ground",
            "type": "tilelayer",
            "x": 0,
            "y": 0,
            "width": grid.width,
            "height": grid.height,
            "visible": True,
            "opacity": 1,
            "data": ground,
        },
        {
            "id": 2,
            "name": "Collision",
            "type": "tilelayer",
            "x": 0,
            "y": 0,
            "width": grid.width,
            "height": grid.height,
            "visible": True,
            "opacity": 1,
            "data": collision,
        },
        {
            "id": 3,
            "name": "Occluder",
            "type": "tilelayer",
            "x": 0,
            "y": 0,
            "width": grid.width,
            "height": grid.height,
            "visible": True,
            "opacity": 1,
            "data": occluder,
        },
        {
            "id": 4,
            "name": "Objects",
            "type": "objectgroup",
            "x": 0,
            "y": 0,
            "visible": True,
            "opacity": 1,
            "objects": objects,
        },
    ]

    data = {
        "compressionlevel": -1,
        "height": grid.height,
        "width": grid.width,
        "infinite": False,
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "tileheight": TILE_PIXELS,
        "tilewidth": TILE_PIXELS,
        "type": "map",
        "version": "1.10",
        "tiledversion": "1.11.0",
        "nextlayerid": 5,
        "nextobjectid": next_id,
        "properties": [
            _property("module_id", f"world_{world.seed}", "string"),
            _property("module_type", "world", "string"),
            _property("seed", world.seed, "int"),
            _property("generation_version", 1, "int"),
            _property("width_m", round(world.width_m, 3), "float"),
            _property("height_m", round(world.height_m, 3), "float"),
            _property("road_width_m", 6.0, "float"),
        ],
        "tilesets": _tileset_source(output),
        "layers": layers,
    }
    output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
