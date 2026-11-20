"""지역별 시드·ID를 분리하고 공유 도로 포털로 하나의 월드를 조립한다."""

from hashlib import blake2b
import math

from shapely.errors import GEOSException

from .generation import GENERATION_VERSION, generate_scene
from .geometry import Rect
from .road_generation import build_junctions, validate_roads
from .road_visuals import road_layers
from .site_planning import REGION_PROFILES, LayoutUnavailable
from .world_layout import plan_world
from .world_roads import connect_regions


WORLD_VERSION = 4
LAYOUT_VERSION = 3


def region_seed(seed, region_id, version=GENERATION_VERSION):
    return int.from_bytes(blake2b(f"{version}:{seed}:{region_id}".encode(), digest_size=8).digest(), "big")


def generate_region(seed, region_id, *, origin=(0, 0), version=GENERATION_VERSION, region_size="small",
                    importance="normal", prefab_versions=None, entries=None, detail=True, basic=False):
    if type(seed) is not int or not isinstance(region_id, str) or not region_id:
        raise ValueError("월드 시드는 정수, 지역 ID는 비어 있지 않은 문자열이어야 합니다")
    if len(origin) != 2 or not all(math.isfinite(value) for value in origin):
        raise ValueError("지역 원점은 유한한 두 좌표여야 합니다")
    local_seed = region_seed(seed, region_id, version)
    scene = generate_scene(local_seed, version=version, region_size=region_size, importance=importance,
                           prefab_versions=prefab_versions, entries=entries, detail=detail, basic=basic)
    entries = scene["generation"]["entries"]
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
               "junctions": [{**junction, 'node': prefix + junction['node'],
                              **{key: [point(value) for value in junction[key]] for key in ('polygon', 'vehicle_polygon')},
                              'surfaces': [{**surface, **{key: [point(value) for value in surface[key]]
                                                         for key in ('polygon', 'triangles')}}
                                           for surface in junction['surfaces']]}
                             for junction in scene['road_network']['junctions']]}
    metadata = {"id": region_id, "name": "지역 " + region_id.rsplit("/", 1)[-1], "seed": local_seed,
                "bounds": bounds, "origin": list(origin), "entries": entries,
                "importance": importance, "region_size": region_size, **({"basic": True} if basic else {})}
    return {**scene, "name": metadata["name"], "bounds": bounds, "layers": layers + list(road_layers(network)),
            "colliders": colliders, "planned_facilities": sites, "roads": roads, "road_network": network,
            "navigation": dict(nodes), "navigation_edges": list(network["edges"]),
            "prefab_instances": [{**row, "id": prefix + row["id"], "position": point(row["position"]),
                                  "road_node": prefix + row["road_node"]} for row in scene["prefab_instances"]],
            "player_spawn": point(scene["player_spawn"]), "safehouse_spawn": point(scene["safehouse_spawn"]),
            "campaign_facility": next(site["id"] for site in sites if site["kind"] == "warehouse"),
            "supply_point": point(scene["supply_point"]), "campaign_entry": network["terminals"][prefix + "entry/0"],
            "campaign_exit": network["terminals"][prefix + f"entry/{len(entries) - 1 if entries else 1}"],
            "generated_regions": [metadata],
            "territories": {region_id: metadata},
            "generation": {**scene["generation"], "seed": seed, "region_id": region_id, "origin": list(origin)}}


def generate_world(seed=42, *, region_count=1, version=WORLD_VERSION, region_version=GENERATION_VERSION,
                   region_size="small", importance="normal", prefab_versions=None,
                   region_sizes=None, region_importances=None, basic=False):
    if type(seed) is not int or type(region_count) is not int or not 1 <= region_count <= 4:
        raise ValueError("시드는 정수, 지역 수는 1~4여야 합니다")
    if type(version) is not int or type(region_version) is not int or version != WORLD_VERSION or region_version != GENERATION_VERSION:
        raise ValueError("지원하지 않는 월드·지역 생성 버전")
    if region_size not in REGION_PROFILES:
        raise ValueError("지원하지 않는 지역 크기")
    sizes = list(region_sizes) if region_sizes is not None else [region_size] * region_count
    importances = list(region_importances) if region_importances is not None else [importance] * region_count
    settings = dict(seed=seed, version=version, region_version=region_version, region_count=region_count,
                    region_size=region_size, importance=importance, region_sizes=sizes, region_importances=importances,
                    prefab_versions=prefab_versions)
    if not basic:
        try:
            return _assemble_world(settings, basic=False)
        except (LayoutUnavailable, GEOSException):
            pass
    return _assemble_world(settings, basic=True)


def _assemble_world(settings, *, basic):
    seed, region_count = settings["seed"], settings["region_count"]
    sizes, importances = settings["region_sizes"], settings["region_importances"]
    plans, links = plan_world(seed, LAYOUT_VERSION, region_count, sizes, importances, basic=basic)
    regions = [generate_region(seed, plan['id'], origin=plan['origin'], entries=plan['entries'],
                               version=settings["region_version"], region_size=plan['region_size'], importance=plan['importance'],
                               prefab_versions=settings["prefab_versions"], detail=False, basic=basic) for plan in plans]
    by_id = {region['generated_regions'][0]['id']: region for region in regions}
    aliases = {}
    for link in links:
        for side, entry in (('source', 'source_entry'), ('destination', 'destination_entry')):
            region = by_id[link[side]]
            terminal = region['road_network']['terminals'][f"{link[side]}/entry/{link[entry]}"]
            aliases[terminal] = link['id'] + ('/a' if side == 'source' else '/b')
    nodes, terminals, roads = {}, {}, []
    for region in regions:
        for key, value in region["road_network"]["nodes"].items():
            target = aliases.get(key, key)
            if target in nodes and nodes[target] != value:
                raise ValueError("지역 경계 도로 포털 위치 불일치")
            nodes[target] = value
        terminals.update({key: aliases.get(value, value) for key, value in region["road_network"]["terminals"].items()})
        roads.extend({**row, "nodes": sorted(aliases.get(key, key) for key in row["nodes"])} for row in region["roads"])
    left = min(region['bounds'][0] for region in regions)
    top = min(region['bounds'][1] for region in regions)
    right = max(region['bounds'][0] + region['bounds'][2] for region in regions)
    bottom = max(region['bounds'][1] + region['bounds'][3] for region in regions)
    padding = 24 if region_count > 1 else 0
    bounds = [left - padding, top - padding, right - left + padding * 2, bottom - top + padding * 2]
    connections = connect_regions(seed, regions, links, nodes, Rect(*bounds), basic=basic)
    roads.extend(connections)
    network = {"nodes": dict(sorted(nodes.items())), "edges": sorted(tuple(row["nodes"]) for row in roads),
               "segments": roads, "terminals": terminals, "spawn_node": regions[0]["road_network"]["spawn_node"],
               "junctions": [junction for region in regions for junction in region['road_network']['junctions']]
                            + build_junctions(nodes, connections, Rect(*bounds))}
    profile = "지역별 설정" if len(set(sizes)) > 1 or len(set(importances)) > 1 else f"{sizes[0]} · {importances[0]}"
    scene = {**regions[0], "name": f"절차 생성 / {region_count}개 지역 · {profile}", "bounds": bounds,
             **{key: [row for region in regions for row in region[key]]
                for key in ("colliders", "planned_facilities", "prefab_instances", "generated_regions")},
             "layers": [row for region in regions for row in region["layers"] if not row["style"].startswith("road")],
             "roads": roads, "road_network": network, "navigation": dict(nodes), "navigation_edges": list(network["edges"]),
             "territories": {key: value for region in regions for key, value in region["territories"].items()},
             "region_links": links, "stream_terrain": True,
             "campaign_exit": regions[-1]["campaign_exit"],
             "generation": {**settings, **({"basic": True} if basic else {}),
                            "prefab_versions": regions[0]["generation"]["prefab_versions"]}}
    scene["layers"].extend(road_layers(network))
    try:
        validate_roads(network, scene["planned_facilities"], Rect(*bounds), scene["colliders"])
    except ValueError as error:
        raise LayoutUnavailable(f"지역 간 연결 조건 불충족: {error}") from error
    return scene
