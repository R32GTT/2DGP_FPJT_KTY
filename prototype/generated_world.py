"""지역별 시설을 배치하고 월드 단위의 도로망을 조립한다."""

from hashlib import blake2b
import math

from shapely.errors import GEOSException

from .generation import (
    GENERATION_ATTEMPTS, GENERATION_VERSION, generate_scene, generation_catalog,
    road_records, site_colliders, site_instances, site_layers,
)
from .geometry import Rect
from .road_generation import validate_roads
from .road_visuals import road_layers
from .site_planning import REGION_PROFILES, LayoutUnavailable, plan_sites
from .world_layout import plan_world
from .world_roads import build_world_roads


WORLD_VERSION = 5
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
    catalog, settings["prefab_versions"] = generation_catalog(prefab_versions)
    if not basic:
        try:
            plans = plan_world(seed, LAYOUT_VERSION, region_count, sizes, importances)
        except LayoutUnavailable:
            plans = []
        if plans:
            for attempt in range(GENERATION_ATTEMPTS):
                try:
                    return _assemble_world(settings, plans, catalog, attempt)
                except (LayoutUnavailable, GEOSException):
                    continue
    plans = plan_world(seed, LAYOUT_VERSION, region_count, sizes, importances, basic=True)
    return _assemble_world(settings, plans, catalog, basic=True)


def _assemble_world(settings, plans, catalog, attempt=0, *, basic=False):
    regions, sites = [], []
    for plan in plans:
        key, origin = plan["id"], plan["origin"]
        local_seed = region_seed(settings["seed"], key, settings["region_version"])
        bounds, local_sites, _, _, _ = plan_sites(local_seed, settings["region_version"], plan["region_size"],
                                                   plan["importance"], attempt, catalog=catalog, entries=[], basic=basic)
        dx, dy = origin
        regions.append({**plan, "seed": local_seed, "name": "지역 " + key.rsplit("/", 1)[-1],
                        "bounds": [bounds.x + dx, bounds.y + dy, bounds.w, bounds.h]})
        for site in local_sites:
            placed = {**site, "id": f"{key}/{site['id']}", "region_id": key}
            for field in ("rect", "reserved", "position"):
                x, y, *size = site[field]
                placed[field] = [x + dx, y + dy, *size]
            connector = dict(site["connector"])
            for field in ("position", "approach"):
                x, y = connector[field]
                connector[field] = [x + dx, y + dy]
            sites.append({**placed, "connector": connector})
    areas = [Rect(*region["bounds"]) for region in regions]
    padding = 24 if len(regions) > 1 else 0
    left, right = min(area.x for area in areas) - padding, max(area.right for area in areas) + padding
    top, bottom = min(area.y for area in areas) - padding, max(area.bottom for area in areas) + padding
    bounds = Rect(left, top, right - left, bottom - top)
    try:
        network = build_world_roads(settings["seed"], bounds, sites, basic=basic)
        validate_roads(network, sites, bounds, site_colliders(sites, catalog))
    except ValueError as error:
        raise LayoutUnavailable(f"월드 도로 연결 조건 불충족: {error}") from error
    sizes, importances = settings["region_sizes"], settings["region_importances"]
    profile = "지역별 설정" if len(set(sizes)) > 1 or len(set(importances)) > 1 else f"{sizes[0]} · {importances[0]}"
    spawn = next(site["position"] for site in sites if site["kind"] == "safehouse")
    return {"format": "revenant.image_scene.v1", "name": f"절차 생성 / {len(regions)}개 지역 · {profile}",
            "bounds": [bounds.x, bounds.y, bounds.w, bounds.h], "generation": {**settings, **({"basic": True} if basic else {})},
            "planned_facilities": sites, "generated_regions": regions,
            "territories": {region["id"]: region for region in regions}, "stream_terrain": True,
            "road_network": network, "roads": road_records(network), "navigation": dict(network["nodes"]),
            "navigation_edges": list(network["edges"]), "campaign_entry": network["terminals"]["entry/0"],
            "campaign_exit": network["terminals"]["entry/1"], "player_spawn": spawn, "safehouse_spawn": spawn,
            "interaction_markers": False, "player_weapons": [], "guards": [], "facilities": [], "colliders": [],
            "prefab_instances": site_instances(sites, network), "layers": site_layers(sites) + list(road_layers(network))}
