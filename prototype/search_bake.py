"""프리팹의 정적 수색 구역을 로컬 좌표로 저장하고 배치한다."""

from hashlib import sha256
import json
from types import SimpleNamespace

from .geometry import Collider, Rect, SpatialIndex
from .indoor_search import build_search_area
from .navigation import Navigation


FORMAT = "revenant.search_bake.v1"


def source_hash(definition):
    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def bake_definition(definition):
    geometry = SpatialIndex()
    for row in definition["colliders"]:
        collider = Collider(row["id"], Rect(*row["rect"]), row.get("kind", "wall"),
                            row.get("active", True))
        if "blocks" in row:
            collider.blocks = tuple(row["blocks"])
        geometry.add(collider)
    navigation = Navigation({}, (), geometry, bounds=Rect(*definition["bounds"]))
    world = SimpleNamespace(geometry=geometry, navigation=navigation)
    floors = tuple(Rect(*row["rect"]) for row in definition["layers"] if row.get("style") == "floor")
    area = build_search_area(world, definition["id"], floors, definition.get("search_layout", {}))
    if area is None:
        return None
    point_index = {point: index for index, point in enumerate(area.points)}
    return {
        "format": FORMAT,
        "source_hash": source_hash(definition),
        "points": area.points,
        "room_ids": [area.rooms[point] for point in area.points],
        "corners": {str(room): [point_index[point] for point in corners]
                    for room, corners in area.room_corners.items()},
        "observations": [[*point, room] for point, room in area.observations.items()],
        "entry_poses": [[*pose.position, *pose.look, pose.room] for pose in area.entry_poses],
    }


def place_bake(bake, placement):
    return {
        "points": [placement.point(point) for point in bake["points"]],
        "room_ids": bake["room_ids"],
        "corners": bake["corners"],
        "observations": [[*placement.point(row[:2]), row[2]] for row in bake["observations"]],
        "entry_poses": [[*placement.point(row[:2]), *placement.point(row[2:4]), row[4]]
                        for row in bake["entry_poses"]],
    }
