"""지역별 시드·ID를 분리하고 공유 도로 포털로 하나의 월드를 조립한다."""

from hashlib import blake2b
import math
import random

from .generation import GENERATION_VERSION, generate_scene
from .geometry import Rect
from .road_generation import build_junctions, validate_roads
from .road_visuals import road_layers
from .site_planning import REGION_PROFILES


WORLD_VERSION = 1


def region_seed(seed, region_id, version=GENERATION_VERSION):
    return int.from_bytes(blake2b(f"{version}:{seed}:{region_id}".encode(), digest_size=8).digest(), "big")


def generate_region(seed, region_id, *, origin=(0, 0), version=GENERATION_VERSION, region_size="small",
                    importance="normal", prefab_versions=None, entry_offsets=None):
    if type(seed) is not int or not isinstance(region_id, str) or not region_id:
        raise ValueError("월드 시드는 정수, 지역 ID는 비어 있지 않은 문자열이어야 합니다")
    if len(origin) != 2 or not all(math.isfinite(value) for value in origin):
        raise ValueError("지역 원점은 유한한 두 좌표여야 합니다")
    local_seed = region_seed(seed, region_id, version)
    scene = generate_scene(local_seed, version=version, region_size=region_size, importance=importance,
                           prefab_versions=prefab_versions, entry_offsets=entry_offsets)
    prefix = region_id + "/"

    def point(value):
        return [value[0] + origin[0], value[1] + origin[1]]

    def rect(value):
        return point(value) + list(value[2:])

    layers = [{**row, "id": prefix + row["id"], "rect": rect(row["rect"])} for row in scene["layers"]
              if not row.get("style", "").startswith("road")]
    colliders = [{**row, "id": prefix + row["id"], "rect": rect(row["rect"])} for row in scene["colliders"]]
    sites = [{**row, "id": prefix + row["id"], "region_id": region_id, "rect": rect(row["rect"]),
              "reserved": rect(row["reserved"]), "position": point(row["position"]),
              "connector": {**row["connector"], "position": point(row["connector"]["position"]),
                            "approach": point(row["connector"]["approach"])}} for row in scene["planned_facilities"]]
    roads = [{**row, "id": prefix + row["id"], "nodes": [prefix + key for key in row["nodes"]],
              "route": prefix + row["route"], "start": point(row["start"]), "end": point(row["end"])} for row in scene["roads"]]
    bounds = rect(scene["bounds"])
    nodes = {prefix + key: point(value) for key, value in scene["road_network"]["nodes"].items()}
    network = {"nodes": nodes, "edges": [tuple(row["nodes"]) for row in roads], "segments": roads,
               "terminals": {prefix + key: prefix + value for key, value in scene["road_network"]["terminals"].items()},
               "spawn_node": prefix + scene["road_network"]["spawn_node"],
               "junctions": build_junctions(nodes, roads, Rect(*bounds))}
    metadata = {"id": region_id, "name": "지역 " + region_id.rsplit("/", 1)[-1], "seed": local_seed,
                "bounds": bounds, "origin": list(origin), "entry_offsets": entry_offsets,
                "importance": importance, "region_size": region_size}
    return {**scene, "name": metadata["name"], "bounds": bounds, "layers": layers + list(road_layers(network)),
            "colliders": colliders, "planned_facilities": sites, "roads": roads, "road_network": network,
            "navigation": dict(nodes), "navigation_edges": list(network["edges"]),
            "prefab_instances": [{**row, "id": prefix + row["id"], "position": point(row["position"]),
                                  "road_node": prefix + row["road_node"]} for row in scene["prefab_instances"]],
            "player_spawn": point(scene["player_spawn"]), "safehouse_spawn": point(scene["safehouse_spawn"]),
            "campaign_facility": next(site["id"] for site in sites if site["kind"] == "warehouse"),
            "supply_point": point(scene["supply_point"]), "campaign_entry": network["terminals"][prefix + "entry/0"],
            "campaign_exit": network["terminals"][prefix + "entry/1"], "generated_regions": [metadata],
            "territories": {region_id: metadata},
            "generation": {**scene["generation"], "seed": seed, "region_id": region_id, "origin": list(origin)}}


def generate_world(seed=42, *, region_count=1, version=WORLD_VERSION, region_version=GENERATION_VERSION,
                   region_size="small", importance="normal", prefab_versions=None):
    if type(seed) is not int or type(region_count) is not int or region_count not in (1, 2):
        raise ValueError("시드는 정수, 지역 수는 1~2여야 합니다")
    if type(version) is not int or type(region_version) is not int or version != WORLD_VERSION or region_version != GENERATION_VERSION:
        raise ValueError("지원하지 않는 월드·지역 생성 버전")
    if region_size not in REGION_PROFILES:
        raise ValueError("지원하지 않는 지역 크기")
    extent = REGION_PROFILES[region_size][0]
    rng = random.Random(f"world-portals:{version}:{seed}")
    offsets = [rng.randrange(-extent // 4, extent // 4, 2) for _ in range(region_count + 1)]
    regions = [generate_region(seed, f"region/{index + 1}", origin=(index * extent, 0), version=region_version,
                               region_size=region_size, importance=importance, prefab_versions=prefab_versions,
                               entry_offsets=offsets[index:index + 2]) for index in range(region_count)]
    aliases = {key: f"portal/{index + 1}" for index in range(region_count - 1)
               for key in (regions[index]["campaign_exit"], regions[index + 1]["campaign_entry"])}
    nodes, terminals, roads = {}, {}, []
    for region in regions:
        for key, value in region["road_network"]["nodes"].items():
            target = aliases.get(key, key)
            if target in nodes and nodes[target] != value:
                raise ValueError("지역 경계 도로 포털 위치 불일치")
            nodes[target] = value
        terminals.update({key: aliases.get(value, value) for key, value in region["road_network"]["terminals"].items()})
        roads.extend({**row, "nodes": sorted(aliases.get(key, key) for key in row["nodes"])} for row in region["roads"])
    bounds = [-extent / 2, -extent / 2, extent * region_count, extent]
    network = {"nodes": dict(sorted(nodes.items())), "edges": sorted(tuple(row["nodes"]) for row in roads),
               "segments": roads, "terminals": terminals, "spawn_node": regions[0]["road_network"]["spawn_node"],
               "junctions": build_junctions(nodes, roads, Rect(*bounds))}
    scene = {**regions[0], "name": f"절차 생성 / {region_count}개 지역 · {region_size} · {importance}", "bounds": bounds,
             **{key: [row for region in regions for row in region[key]]
                for key in ("colliders", "planned_facilities", "prefab_instances", "generated_regions")},
             "layers": [row for region in regions for row in region["layers"] if not row["style"].startswith("road")],
             "roads": roads, "road_network": network, "navigation": dict(nodes), "navigation_edges": list(network["edges"]),
             "territories": {key: value for region in regions for key, value in region["territories"].items()},
             "campaign_exit": regions[-1]["campaign_exit"],
             "generation": {"seed": seed, "version": version, "region_version": region_version, "region_count": region_count,
                            "region_size": region_size, "importance": importance,
                            "prefab_versions": regions[0]["generation"]["prefab_versions"]}}
    scene["layers"].extend(road_layers(network))
    validate_roads(network, scene["planned_facilities"], Rect(*bounds), scene["colliders"])
    return scene
