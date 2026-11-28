"""프리팹의 정적 수색 구역을 로컬 좌표로 저장하고 배치한다."""

from hashlib import sha256
import json
from types import SimpleNamespace

from .geometry import Collider, Rect, SpatialIndex
from .indoor_search import STATIONARY_SCAN_RANGE_M, build_search_area
from .navigation import Navigation


FORMAT = "revenant.search_bake.v2"


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
    viewpoints, view_coverage = {}, []
    for room, samples in area.room_points.items():
        candidates = tuple(dict.fromkeys((*[point for point, owner in area.observations.items() if owner == room],
                                          *samples)))
        coverage = {}
        for point in candidates:
            nearby = [sample for sample in samples if
                      (sample[0] - point[0]) ** 2 + (sample[1] - point[1]) ** 2 <= STATIONARY_SCAN_RANGE_M ** 2]
            coverage[point] = frozenset(sample for sample, clear in zip(
                nearby, geometry.sight_flags(point, nearby)) if clear)
        unseen, selected = set(samples), []
        while unseen:
            best = max(candidates, key=lambda point: (len(coverage[point] & unseen),
                                                        point in area.observations, point))
            if not coverage[best] & unseen:
                best = min(unseen)
                coverage[best] = frozenset((best,))
            selected.append(best)
            unseen.difference_update(coverage[best])
            candidates = tuple(point for point in candidates if point != best)
        viewpoints[str(room)] = selected
        view_coverage.extend([[point, sorted(coverage[point])] for point in selected])
    return {
        "format": FORMAT,
        "source_hash": source_hash(definition),
        "points": area.points,
        "room_ids": [area.rooms[point] for point in area.points],
        "corners": {str(room): [point_index[point] for point in corners]
                    for room, corners in area.room_corners.items()},
        "observations": [[*point, room] for point, room in area.observations.items()],
        "entry_poses": [[*pose.position, *pose.look, pose.room] for pose in area.entry_poses],
        "viewpoints": viewpoints,
        "view_coverage": view_coverage,
    }


def place_bake(bake, placement):
    return {
        "points": [placement.point(point) for point in bake["points"]],
        "room_ids": bake["room_ids"],
        "corners": bake["corners"],
        "observations": [[*placement.point(row[:2]), row[2]] for row in bake["observations"]],
        "entry_poses": [[*placement.point(row[:2]), *placement.point(row[2:4]), row[4]]
                        for row in bake["entry_poses"]],
        "viewpoints": {room: [placement.point(point) for point in rows]
                       for room, rows in bake["viewpoints"].items()},
        "view_coverage": [[placement.point(point), [placement.point(sample) for sample in rows]]
                          for point, rows in bake["view_coverage"]],
    }
