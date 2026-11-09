"""Compile Tiled JSON into data used by the game and AI review tools.

The compiler deliberately does not try to judge whether a module is fun or
reachable. It normalizes author-authored data and performs a cheap geometry
compilation step so the game does not need to inspect every wall tile.
"""

from __future__ import annotations

from collections import defaultdict
import html
import json
import math
from pathlib import Path
import shutil
from typing import Any, Iterable


FORMAT_VERSION = 1
WORLD_PIXELS_PER_METER = 64.0

_KIND_ALIASES = {
    "road_connector": "connector",
    "walk_connector": "connector",
    "pedestrian_connector": "connector",
    "connector": "connector",
    "enemy_spawn": "enemy_spawn",
    "spawn": "enemy_spawn",
    "patrol_node": "patrol_node",
    "nav_node": "nav_node",
    "navigation_node": "nav_node",
    "nav_edge": "nav_edge",
    "navigation_edge": "nav_edge",
    "guard_point": "guard_point",
    "cover_point": "cover_point",
    "cover": "cover_point",
    "decoration": "decoration",
    "door": "door",
    "window": "window",
    "stairs": "stairs",
    "wall": "wall",
    "wall_edge": "wall_edge",
    "occluder": "occluder",
    "obstacle": "obstacle",
    "collision": "obstacle",
}


def _properties(properties: Any) -> dict[str, Any]:
    if isinstance(properties, dict):
        return dict(properties)
    result: dict[str, Any] = {}
    for item in properties or []:
        if isinstance(item, dict) and "name" in item:
            result[item["name"]] = item.get("value")
    return result


def _list_property(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return list(default or [])
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(value)]


def _bool_property(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _facility_metadata(map_props: dict[str, Any], module_type: str) -> dict[str, Any] | None:
    if module_type != "facility":
        return None
    return {
        "facility_type": str(map_props.get("facility_type", "generic")),
        "allowed_region_sizes": _list_property(
            map_props.get("allowed_region_sizes"),
            ["small", "medium", "large", "extra_large"],
        ),
        "allowed_importance_tiers": _list_property(
            map_props.get("allowed_importance_tiers"),
            ["general", "important", "core"],
        ),
        "supports": _list_property(map_props.get("supports")),
        "has_vehicle_entry": _bool_property(map_props.get("has_vehicle_entry")),
        "has_pedestrian_entry": _bool_property(
            map_props.get("has_pedestrian_entry"), True
        ),
        "attached_watchposts": int(map_props.get("attached_watchposts", 0) or 0),
    }


def _class_name(obj: dict[str, Any]) -> str:
    value = obj.get("class") or obj.get("type") or obj.get("name") or ""
    return str(value).strip().lower()


def _kind(obj: dict[str, Any]) -> str:
    raw = _class_name(obj)
    return _KIND_ALIASES.get(raw, raw)


def _flatten_layers(layers: Iterable[dict[str, Any]], prefix: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    for layer in layers:
        name = f"{prefix}/{layer.get('name', '')}" if prefix else str(layer.get("name", ""))
        yield name, layer
        if layer.get("type") == "group":
            yield from _flatten_layers(layer.get("layers", []), name)


def _tile_cells(layer: dict[str, Any]) -> set[tuple[int, int]]:
    """Return non-empty tile coordinates from finite or infinite Tiled layers."""
    cells: set[tuple[int, int]] = set()
    if layer.get("chunks"):
        for chunk in layer["chunks"]:
            width = int(chunk.get("width", 0))
            for index, gid in enumerate(chunk.get("data", [])):
                if gid:
                    x = int(chunk.get("x", 0)) + index % width
                    y = int(chunk.get("y", 0)) + index // width
                    cells.add((x, y))
        return cells

    width = int(layer.get("width", 0))
    if width <= 0:
        return cells
    offset_x = int(layer.get("x", 0))
    offset_y = int(layer.get("y", 0))
    for index, gid in enumerate(layer.get("data", [])):
        if gid:
            cells.add((offset_x + index % width, offset_y + index // width))
    return cells


def _tile_layer_data(layer: dict[str, Any], width: int, height: int) -> list[int]:
    """Normalize a finite visual tile layer to a flat map-sized GID array."""
    result = [0] * (width * height)
    if layer.get("chunks"):
        for chunk in layer.get("chunks", []):
            chunk_width = int(chunk.get("width", 0))
            for index, gid in enumerate(chunk.get("data", [])):
                x = int(chunk.get("x", 0)) + index % chunk_width
                y = int(chunk.get("y", 0)) + index // chunk_width
                if 0 <= x < width and 0 <= y < height:
                    result[y * width + x] = gid
        return result
    layer_width = int(layer.get("width", width))
    offset_x = int(layer.get("x", 0))
    offset_y = int(layer.get("y", 0))
    for index, gid in enumerate(layer.get("data", [])):
        x = offset_x + index % layer_width
        y = offset_y + index // layer_width
        if 0 <= x < width and 0 <= y < height:
            result[y * width + x] = gid
    return result


def _object_cells(objects: Iterable[dict[str, Any]], tile_meters: float) -> set[tuple[int, int]]:
    """Rasterize authored rectangular movement blockers for the local nav grid."""
    cells: set[tuple[int, int]] = set()
    for item in objects:
        if item["kind"] not in {"wall", "obstacle"}:
            continue
        geometry = item["geometry"]
        if geometry.get("shape") != "rectangle":
            continue
        start_x = math.floor(float(geometry.get("x", 0)) / tile_meters)
        start_y = math.floor(float(geometry.get("y", 0)) / tile_meters)
        end_x = math.ceil((float(geometry.get("x", 0)) + float(geometry.get("width", 0))) / tile_meters - 0.0001) - 1
        end_y = math.ceil((float(geometry.get("y", 0)) + float(geometry.get("height", 0))) / tile_meters - 0.0001) - 1
        for y in range(start_y, end_y + 1):
            for x in range(start_x, end_x + 1):
                cells.add((x, y))
    return cells


def _wall_edge_geometry(item: dict[str, Any], default_thickness: float, tile_meters: float) -> dict[str, Any]:
    props = item.get("properties", {})
    cell_x = int(props.get("cell_x", item["geometry"].get("x", 0)))
    cell_y = int(props.get("cell_y", item["geometry"].get("y", 0)))
    edge = str(props.get("edge", "north")).lower()
    thickness = float(props.get("thickness_m", default_thickness))
    half = thickness / 2.0
    if edge in {"north", "south", "n", "s"}:
        y = cell_y * tile_meters if edge in {"north", "n"} else (cell_y + 1) * tile_meters
        return {"x": cell_x * tile_meters, "y": y - half, "width": tile_meters, "height": thickness}
    x = cell_x * tile_meters if edge in {"west", "w"} else (cell_x + 1) * tile_meters
    return {"x": x - half, "y": cell_y * tile_meters, "width": thickness, "height": tile_meters}


def _merge_cells(cells: set[tuple[int, int]]) -> list[dict[str, int]]:
    """Merge occupied grid cells into deterministic axis-aligned rectangles."""
    if not cells:
        return []

    row_runs: list[tuple[int, int, int]] = []
    for y in sorted({cell[1] for cell in cells}):
        xs = sorted(x for x, cell_y in cells if cell_y == y)
        start = previous = xs[0]
        for x in xs[1:]:
            if x != previous + 1:
                row_runs.append((y, start, previous - start + 1))
                start = x
            previous = x
        row_runs.append((y, start, previous - start + 1))

    rectangles: list[dict[str, int]] = []
    active: dict[tuple[int, int], dict[str, int]] = {}
    last_y: int | None = None
    for y, x, width in row_runs:
        if last_y is None or y != last_y + 1:
            rectangles.extend(active.values())
            active = {}
        next_active: dict[tuple[int, int], dict[str, int]] = {}
        for key, rect in active.items():
            if key == (x, width):
                rect["height"] += 1
                next_active[key] = rect
            else:
                rectangles.append(rect)
        next_active[(x, width)] = {"x": x, "y": y, "width": width, "height": 1}
        active = next_active
        last_y = y
    rectangles.extend(active.values())
    return sorted(rectangles, key=lambda item: (item["y"], item["x"]))


def _point(obj: dict[str, Any], scale: float) -> dict[str, float]:
    return {"x": round(float(obj.get("x", 0)) / scale, 4),
            "y": round(float(obj.get("y", 0)) / scale, 4)}


def _geometry(obj: dict[str, Any], scale: float) -> dict[str, Any]:
    result: dict[str, Any] = {"x": round(float(obj.get("x", 0)) / scale, 4),
                              "y": round(float(obj.get("y", 0)) / scale, 4)}
    if "width" in obj:
        result["width"] = round(float(obj.get("width", 0)) / scale, 4)
    if "height" in obj:
        result["height"] = round(float(obj.get("height", 0)) / scale, 4)
    if obj.get("ellipse"):
        result["shape"] = "ellipse"
    elif obj.get("polygon") is not None:
        result["shape"] = "polygon"
        result["points"] = [
            {"x": round(float(point.get("x", 0)) / scale, 4),
             "y": round(float(point.get("y", 0)) / scale, 4)}
            for point in obj.get("polygon", [])
        ]
    elif obj.get("polyline") is not None:
        result["shape"] = "polyline"
        result["points"] = [
            {"x": round(float(point.get("x", 0)) / scale, 4),
             "y": round(float(point.get("y", 0)) / scale, 4)}
            for point in obj.get("polyline", [])
        ]
    elif obj.get("point") or not obj.get("width") and not obj.get("height"):
        result["shape"] = "point"
    else:
        result["shape"] = "rectangle"
    return result


def _object_record(obj: dict[str, Any], layer_name: str, scale: float) -> dict[str, Any]:
    props = _properties(obj.get("properties"))
    kind = _kind(obj)
    record = {
        "id": obj.get("id"),
        "name": obj.get("name", ""),
        "kind": kind,
        "source_class": _class_name(obj),
        "layer": layer_name,
        "floor": props.get("floor", 0),
        "geometry": _geometry(obj, scale),
        "properties": props,
    }
    if kind in {"door", "window"}:
        blocks_movement = _bool_property(props.get("blocks_movement"), kind == "door")
        blocks_vision = _bool_property(props.get("blocks_vision"), kind == "door")
        blocks_sound = _bool_property(props.get("blocks_sound"), kind == "door")
        record["state_rules"] = {
            "closed": {
                "blocks_movement": blocks_movement,
                "blocks_vision": blocks_vision,
                "sound_transmission": float(props.get("sound_transmission_closed", 0.0 if blocks_sound else 1.0)),
            },
            "open": {
                "blocks_movement": False,
                "blocks_vision": False,
                "sound_transmission": float(props.get("sound_transmission_open", 1.0)),
            },
            "broken": {
                "blocks_movement": False,
                "blocks_vision": False,
                "sound_transmission": float(props.get("sound_transmission_broken", 1.0)),
            },
        }
        if kind == "door":
            record["interaction"] = {
                "access": props.get("access", "free"),
                "locked": _bool_property(props.get("locked"), props.get("access", "free") == "locked"),
                "hinge": props.get("hinge", "left"),
                "closed_angle": float(props.get("closed_angle", 0)),
                "open_angle": float(props.get("open_angle", 90)),
            }
        else:
            record["interaction"] = {
                "state": props.get("state", "intact"),
                "breaks_as_one": True,
            }
    return record


def _pixel_rect_to_meters(rect: dict[str, int], tile_meters: float) -> dict[str, float]:
    return {
        "x": round(rect["x"] * tile_meters, 4),
        "y": round(rect["y"] * tile_meters, 4),
        "width": round(rect["width"] * tile_meters, 4),
        "height": round(rect["height"] * tile_meters, 4),
    }


def _route_data(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    route_options: dict[str, dict[str, Any]] = {}
    for node in nodes:
        props = node["properties"]
        route_id = str(props.get("route_id", "default"))
        grouped[route_id].append(node)
        route_options[route_id] = {
            "loop": bool(props.get("loop", True)),
            "group_id": props.get("group_id"),
        }

    routes = []
    for route_id, route_nodes in sorted(grouped.items()):
        ordered = sorted(route_nodes, key=lambda item: (
            float(item["properties"].get("order", 0)), int(item["id"] or 0)))
        routes.append({
            "id": route_id,
            **route_options[route_id],
            "nodes": [
                {
                    "id": item["id"],
                    "position": {
                        "x": item["geometry"]["x"],
                        "y": item["geometry"]["y"],
                    },
                    "wait_seconds": item["properties"].get("wait_seconds", 0),
                    "facing_degrees": item["properties"].get("facing_degrees"),
                }
                for item in ordered
            ],
        })
    return routes


def compile_module(source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("type") != "map":
        raise ValueError(f"Not a Tiled map: {source}")

    tile_width = float(data.get("tilewidth", 64))
    tile_height = float(data.get("tileheight", tile_width))
    if tile_width != tile_height:
        raise ValueError("Only square tiles are supported by the first compiler")
    world_scale = WORLD_PIXELS_PER_METER
    tile_meters = tile_width / world_scale
    map_props = _properties(data.get("properties"))
    module_id = str(map_props.get("module_id", source.stem))
    module_type = str(map_props.get("module_type", "module"))
    variant_id = str(map_props.get("variant_id", source.stem))
    facility_metadata = _facility_metadata(map_props, module_type)
    default_wall_thickness = float(map_props.get("wall_thickness_m", 0.375) or 0.375)

    all_objects: list[dict[str, Any]] = []
    collision_cells: set[tuple[int, int]] = set()
    occlusion_cells: set[tuple[int, int]] = set()
    walkable_cells: set[tuple[int, int]] = set()
    has_walkable_layer = False
    visual_layers: dict[str, dict[str, Any]] = {}
    for layer_name, layer in _flatten_layers(data.get("layers", [])):
        if layer.get("type") == "objectgroup":
            all_objects.extend(
                _object_record(obj, layer_name, world_scale)
                for obj in layer.get("objects", [])
            )
        elif layer.get("type") == "tilelayer":
            lower_name = layer_name.lower()
            if lower_name in {"ground", "architecture", "decoration", "decorations"}:
                visual_name = "decoration" if lower_name in {"decoration", "decorations"} else lower_name
                visual_layers[visual_name] = {
                    "width": int(data.get("width", 0)),
                    "height": int(data.get("height", 0)),
                    "tiles": _tile_layer_data(
                        layer,
                        int(data.get("width", 0)),
                        int(data.get("height", 0)),
                    ),
                }
            if any(token in lower_name for token in ("collision", "obstacle")):
                collision_cells.update(_tile_cells(layer))
            if any(token in lower_name for token in ("occluder", "occlusion", "wall", "vision")):
                occlusion_cells.update(_tile_cells(layer))
            if any(token in lower_name for token in ("walkable", "navigation", "nav")):
                has_walkable_layer = True
                walkable_cells.update(_tile_cells(layer))

    geometry_objects = [item for item in all_objects if item["kind"] in {"wall", "occluder", "obstacle"}]
    wall_edges = [item for item in all_objects if item["kind"] == "wall_edge"]
    wall_edge_rects = [_wall_edge_geometry(item, default_wall_thickness, tile_meters) for item in wall_edges]
    collision_cells.update(_object_cells(geometry_objects, tile_meters))
    interactions = [item for item in all_objects if item["kind"] in {"door", "window", "stairs"}]
    connectors = [item for item in all_objects if item["kind"] == "connector"]
    nav_nodes = [item for item in all_objects if item["kind"] == "nav_node"]
    nav_edges = [item for item in all_objects if item["kind"] == "nav_edge"]
    decoration_entities = [item for item in all_objects if item["kind"] == "decoration"]
    enemy_spawns = [item for item in all_objects if item["kind"] == "enemy_spawn"]
    patrol_nodes = [item for item in all_objects if item["kind"] == "patrol_node"]
    cover_points = [item for item in all_objects if item["kind"] == "cover_point"]
    tactical = [item for item in all_objects if item["kind"] == "guard_point"]

    bounds_px = {
        "width": int(data.get("width", 0)) * tile_width,
        "height": int(data.get("height", 0)) * tile_height,
    }
    grid_width = int(data.get("width", 0))
    grid_height = int(data.get("height", 0))
    blocked_cells = sorted(collision_cells)
    if has_walkable_layer:
        nav_cells = sorted(walkable_cells - collision_cells)
    else:
        nav_cells = [
            [x, y]
            for y in range(grid_height)
            for x in range(grid_width)
            if (x, y) not in collision_cells
        ]
    dynamic_portals = [item for item in interactions if item["kind"] in {"door", "stairs"}]
    runtime = {
        "format": "revenant_lost.module",
        "format_version": FORMAT_VERSION,
        "coordinate_system": {
            "unit": "meter",
            "pixels_per_meter": world_scale,
            "tile_pixels": tile_width,
            "tile_meters": tile_meters,
            "origin": "top_left",
            "y_axis": "down",
        },
        "module": {
            "id": module_id,
            "type": module_type,
            "variant_id": variant_id,
            "properties": map_props,
            "source": source.name,
        },
        "facility": facility_metadata,
        "bounds": {
            "width_m": round(bounds_px["width"] / world_scale, 4),
            "height_m": round(bounds_px["height"] / world_scale, 4),
            "width_px": bounds_px["width"],
            "height_px": bounds_px["height"],
        },
        "visual_layers": visual_layers,
        "geometry": {
            "collision_rects": [
                *[_pixel_rect_to_meters(rect, tile_meters) for rect in _merge_cells(collision_cells)],
                *wall_edge_rects,
            ],
            "occlusion_rects": [
                *[_pixel_rect_to_meters(rect, tile_meters) for rect in _merge_cells(occlusion_cells)],
                *wall_edge_rects,
            ],
            "objects": geometry_objects,
            "wall_edges": wall_edges,
        },
        "navigation": {
            "connectors": connectors,
            "patrol_nodes": patrol_nodes,
            "stairs": [item for item in interactions if item["kind"] == "stairs"],
            "grid": {
                "cell_size_m": tile_meters,
                "width": grid_width,
                "height": grid_height,
                "blocked_cells": blocked_cells,
                "blocked_edges": [
                    {
                        "x": int(item["properties"].get("cell_x", item["geometry"].get("x", 0))),
                        "y": int(item["properties"].get("cell_y", item["geometry"].get("y", 0))),
                        "edge": str(item["properties"].get("edge", "north")),
                    }
                    for item in wall_edges
                ],
                "walkable_cells": nav_cells,
                "neighbor_mode": "8-way",
            },
            "dynamic_portals": dynamic_portals,
            "graph": {
                "nodes": [
                    {
                        "id": str(item["properties"].get("node_id", item["id"])),
                        "kind": item["properties"].get("node_kind", "corridor"),
                        "position": {
                            "x": item["geometry"]["x"],
                            "y": item["geometry"]["y"],
                        },
                        "floor": item["floor"],
                    }
                    for item in nav_nodes
                ],
                "edges": [
                    {
                        "from": str(item["properties"].get("from_id", "")),
                        "to": str(item["properties"].get("to_id", "")),
                        "mode": item["properties"].get("mode", "normal"),
                        "cost": float(item["properties"].get("cost", 1.0)),
                        "width_m": float(item["properties"].get("width_m", 1.0)),
                        "one_way": _bool_property(item["properties"].get("one_way")),
                    }
                    for item in nav_edges
                ],
            },
        },
        "tactical": {
            "cover_points": cover_points,
            "guard_points": [item for item in tactical if item["kind"] == "guard_point"],
        },
        "interactions": interactions,
        "enemy": {"spawns": enemy_spawns},
        "decoration_entities": decoration_entities,
        "routes": {"patrol_routes": _route_data(patrol_nodes)},
        "counts": {
            "connectors": len(connectors),
            "patrol_routes": len(_route_data(patrol_nodes)),
            "enemy_spawns": len(enemy_spawns),
            "cover_points": len(cover_points),
            "doors": sum(item["kind"] == "door" for item in interactions),
            "wall_edges": len(wall_edges),
            "navigation_nodes": len(nav_nodes),
            "navigation_edges": len(nav_edges),
            "decoration_entities": len(decoration_entities),
            "decoration_tiles": sum(
                value != 0
                for value in visual_layers.get("decoration", {}).get("tiles", [])
            ),
        },
        "objects": all_objects,
    }
    return runtime


def write_runtime(runtime: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_template_map(
    name: str,
    output_dir: Path,
    module_type: str = "module",
    variant_id: str | None = None,
    facility_type: str | None = None,
    allowed_region_sizes: list[str] | None = None,
    allowed_importance_tiers: list[str] | None = None,
    supports: list[str] | None = None,
    has_vehicle_entry: bool = False,
    has_pedestrian_entry: bool = True,
    attached_watchposts: int = 0,
    width: int = 32,
    height: int = 24,
) -> Path:
    """Create a blank, convention-based Tiled map for a new module."""
    safe_name = Path(name).stem
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError("A module name is required")
    if width <= 0 or height <= 0:
        raise ValueError("Map width and height must be positive")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{safe_name}.tmj"
    layers: list[dict[str, Any]] = []
    for layer_id, layer_name in enumerate(
        ("Ground", "Architecture", "Decoration", "Collision", "Occluder", "Objects", "AI"),
        start=1,
    ):
        if layer_name in {"Objects", "AI"}:
            layers.append({
                "draworder": "topdown",
                "id": layer_id,
                "name": layer_name,
                "objects": [],
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            })
        else:
            layers.append({
                "data": [0] * (width * height),
                "height": height,
                "id": layer_id,
                "name": layer_name,
                "opacity": 1,
                "type": "tilelayer",
                "visible": True,
                "width": width,
                "x": 0,
                "y": 0,
            })
    document = {
        "class": "",
        "height": height,
        "infinite": False,
        "layers": layers,
        "nextlayerid": len(layers) + 1,
        "nextobjectid": 1,
        "orientation": "orthogonal",
        "properties": [
            {"name": "module_id", "type": "string", "value": safe_name},
            {"name": "module_type", "type": "string", "value": module_type},
            {"name": "variant_id", "type": "string", "value": variant_id or safe_name},
            {"name": "wall_thickness_m", "type": "float", "value": 0.375},
        ],
        "renderorder": "right-down",
        "tileheight": 32,
        "tilewidth": 32,
        "tilesets": [],
        "type": "map",
        "version": "1.10",
        "width": width,
    }
    if module_type == "facility":
        document["properties"].extend([
            {"name": "facility_type", "type": "string", "value": facility_type or "generic"},
            {
                "name": "allowed_region_sizes",
                "type": "string",
                "value": json.dumps(
                    allowed_region_sizes or ["small", "medium", "large", "extra_large"]
                ),
            },
            {
                "name": "allowed_importance_tiers",
                "type": "string",
                "value": json.dumps(
                    allowed_importance_tiers or ["general", "important", "core"]
                ),
            },
            {
                "name": "supports",
                "type": "string",
                "value": json.dumps(supports or []),
            },
            {"name": "has_vehicle_entry", "type": "bool", "value": has_vehicle_entry},
            {"name": "has_pedestrian_entry", "type": "bool", "value": has_pedestrian_entry},
            {"name": "attached_watchposts", "type": "int", "value": attached_watchposts},
        ])
    destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def _svg_rect(rect: dict[str, Any], color: str, scale: float) -> str:
    return (
        f'<rect x="{rect["x"] * scale:.2f}" y="{rect["y"] * scale:.2f}" '
        f'width="{rect["width"] * scale:.2f}" height="{rect["height"] * scale:.2f}" '
        f'fill="{color}" fill-opacity="0.55" stroke="{color}" />'
    )


def _svg_point(item: dict[str, Any], color: str, scale: float, label: str) -> str:
    geometry = item["geometry"]
    x = geometry.get("x", 0) * scale
    y = geometry.get("y", 0) * scale
    return (
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{color}" />'
        f'<text x="{x + 7:.2f}" y="{y - 7:.2f}" font-size="12">'
        f'{html.escape(label)}</text>'
    )


def _layout_svg(runtime: dict[str, Any]) -> str:
    bounds = runtime["bounds"]
    width = max(float(bounds["width_px"]), 1)
    height = max(float(bounds["height_px"]), 1)
    geometry = runtime["geometry"]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        '<rect width="100%" height="100%" fill="#eeeeee" />',
        '<g id="collision">',
    ]
    # Runtime coordinates are meters; use pixels_per_meter to make the diagram
    # line up with the source Tiled map.
    scale = float(runtime["coordinate_system"]["pixels_per_meter"])
    parts.extend(_svg_rect(rect, "#333333", scale) for rect in geometry["collision_rects"])
    parts.append('</g><g id="occlusion">')
    parts.extend(_svg_rect(rect, "#111111", scale) for rect in geometry["occlusion_rects"])
    parts.append('</g><g id="objects">')
    for item in runtime["interactions"]:
        color = {"door": "#cc8800", "window": "#3388cc", "stairs": "#8844aa"}.get(item["kind"], "#666666")
        geometry_item = item["geometry"]
        if geometry_item.get("shape") == "rectangle":
            parts.append(_svg_rect(geometry_item, color, scale))
        else:
            parts.append(_svg_point(item, color, scale, item["kind"]))
    parts.append('</g><g id="markers">')
    for item in runtime["enemy"]["spawns"]:
        parts.append(_svg_point(item, "#cc2222", scale, "enemy"))
    for item in runtime["navigation"]["connectors"]:
        parts.append(_svg_point(item, "#2266cc", scale, "connector"))
    for item in runtime["tactical"]["cover_points"]:
        parts.append(_svg_point(item, "#ccaa22", scale, "cover"))
    parts.append('</g><g id="patrol">')
    for route in runtime["routes"]["patrol_routes"]:
        nodes = route["nodes"]
        for first, second in zip(nodes, nodes[1:]):
            x1 = first["position"]["x"] * scale
            y1 = first["position"]["y"] * scale
            x2 = second["position"]["x"] * scale
            y2 = second["position"]["y"] * scale
            parts.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                'stroke="#229944" stroke-width="3" />'
            )
    parts.append('</g></svg>')
    return "\n".join(parts) + "\n"


def _layout_png(runtime: dict[str, Any], destination: Path) -> None:
    """Write a compact schematic image suitable for multimodal model review."""
    from PIL import Image, ImageDraw

    bounds = runtime["bounds"]
    source_width = max(float(bounds["width_px"]), 1)
    source_height = max(float(bounds["height_px"]), 1)
    display_scale = min(1.0, 2048.0 / max(source_width, source_height))
    width = max(1, round(source_width * display_scale))
    height = max(1, round(source_height * display_scale))
    image = Image.new("RGB", (width, height), "#eeeeee")
    draw = ImageDraw.Draw(image)
    world_scale = float(runtime["coordinate_system"]["pixels_per_meter"])

    def pixel(value: float) -> int:
        return round(value * world_scale * display_scale)

    def rect(geometry: dict[str, Any], outline: str, fill: str | None = None) -> None:
        x1 = pixel(float(geometry.get("x", 0)))
        y1 = pixel(float(geometry.get("y", 0)))
        x2 = pixel(float(geometry.get("x", 0)) + float(geometry.get("width", 0)))
        y2 = pixel(float(geometry.get("y", 0)) + float(geometry.get("height", 0)))
        draw.rectangle((x1, y1, x2, y2), outline=outline, fill=fill, width=max(1, round(2 * display_scale)))

    for item in runtime["geometry"]["collision_rects"]:
        rect(item, "#222222", "#555555")
    for item in runtime["geometry"]["occlusion_rects"]:
        rect(item, "#111111", "#333333")
    for item in runtime["interactions"]:
        color = {"door": "#cc8800", "window": "#3388cc", "stairs": "#8844aa"}.get(item["kind"], "#666666")
        geometry = item["geometry"]
        if geometry.get("shape") == "rectangle":
            rect(geometry, color, "#f0c060")

    for route in runtime["routes"]["patrol_routes"]:
        points = [
            (pixel(node["position"]["x"]), pixel(node["position"]["y"]))
            for node in route["nodes"]
        ]
        if len(points) > 1:
            draw.line(points, fill="#229944", width=max(1, round(3 * display_scale)))

    def marker(item: dict[str, Any], color: str) -> None:
        geometry = item["geometry"]
        x = pixel(float(geometry.get("x", 0)))
        y = pixel(float(geometry.get("y", 0)))
        radius = max(3, round(5 * display_scale))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline="#111111")

    for item in runtime["enemy"]["spawns"]:
        marker(item, "#cc2222")
    for item in runtime["navigation"]["connectors"]:
        marker(item, "#2266cc")
    for item in runtime["tactical"]["cover_points"]:
        marker(item, "#ccaa22")

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)


def write_ai_package(runtime: dict[str, Any], destination: Path, source: Path | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    runtime_path = destination / "module.runtime.json"
    write_runtime(runtime, runtime_path)
    (destination / "layout.svg").write_text(_layout_svg(runtime), encoding="utf-8")
    preview_path = destination / "layout.png"
    _layout_png(runtime, preview_path)

    manifest = {
        "format": "revenant_lost.ai_review_package",
        "format_version": FORMAT_VERSION,
        "module": runtime["module"],
        "bounds": runtime["bounds"],
        "counts": runtime["counts"],
        "files": {
            "runtime": "module.runtime.json",
            "layout": "layout.svg",
            "preview": "layout.png",
        },
        "instructions": (
            "This package describes a hand-authored module. Review geometry, "
            "navigation, enemy routes, tactical points, doors, and connectors. "
            "Do not treat it as a complete world map."
        ),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if source is not None:
        shutil.copy2(source, destination / source.name)
        try:
            source_data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source_data = {}
        copied_tilesets: list[str] = []
        for tileset in source_data.get("tilesets", []):
            image_name = tileset.get("image")
            if not image_name:
                continue
            image_path = (source.parent / image_name).resolve()
            if not image_path.is_file():
                continue
            target_dir = destination / "tilesets"
            target_dir.mkdir(exist_ok=True)
            target = target_dir / image_path.name
            shutil.copy2(image_path, target)
            copied_tilesets.append(f"tilesets/{target.name}")
        if copied_tilesets:
            manifest["files"]["tilesets"] = copied_tilesets
            (destination / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
