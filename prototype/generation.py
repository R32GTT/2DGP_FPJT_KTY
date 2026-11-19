"""시드로 작은 야외 시험 맵을 만들어 기존 장면 입력에 전달한다."""

import random
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
from shapely import box, union_all
from shapely.prepared import prep

from .geometry import Rect
from .prefabs import load_catalog
from .road_generation import build_roads, road_surface, validate_roads
from .road_visuals import road_layers
from .site_planning import IMPORTANCE, REGION_PROFILES, plan_sites


GENERATION_VERSION = 10
GENERATION_ATTEMPTS = 8
FOREST_DENSITY = 0.55
FOREST_TREE_GAP = 0.75


def _density(seed, kind, extent):
    rng = np.random.default_rng(random.Random(f"{kind}:{GENERATION_VERSION}:{seed}").getrandbits(64))
    cells = extent // 2 + 1
    field = gaussian_filter(rng.random((cells, cells)), sigma=3, mode="reflect")
    return (field - field.min()) / max(float(np.ptp(field)), 1e-8)


def _sample(field, x, y):
    half = field.shape[0] - 1
    return float(map_coordinates(field, [[(y + half) / 2], [(x + half) / 2]],
                                 order=1, mode="nearest", prefilter=False)[0])


def generate_scene(seed=42, *, version=GENERATION_VERSION, region_size="small", importance="normal", prefab_versions=None,
                   entries=None):
    if type(seed) is not int:
        raise ValueError("월드 시드는 정수여야 합니다")
    if type(version) is not int or version != GENERATION_VERSION:
        raise ValueError(f"지원하지 않는 맵 생성 버전: {version}")
    if region_size not in REGION_PROFILES or importance not in IMPORTANCE:
        raise ValueError("지원하지 않는 지역 크기 또는 중요도")
    catalog = load_catalog(Path(__file__).resolve().parent.parent)
    manifest = {path: definition["version"] for path, definition in catalog.items()}
    if prefab_versions is not None and prefab_versions != manifest:
        raise ValueError("저장된 프리팹 목록·버전과 현재 생성 카탈로그가 다릅니다")
    extent = REGION_PROFILES[region_size][0]
    half, scale = extent / 2, (extent / 64) ** 2
    terrain = random.Random(f"terrain:{version}:{seed}")
    obstacles = random.Random(f"obstacles:{version}:{seed}")
    vegetation, soil = _density(seed, "vegetation", extent), _density(seed, "soil", extent)
    for attempt in range(GENERATION_ATTEMPTS):
        try:
            bounds, sites, points, gates, stubs = plan_sites(seed, version, region_size, importance, attempt,
                                                          catalog=catalog, entries=entries)
            network = build_roads(bounds, sites, points, gates, stubs, 1 + vegetation * 3 + (1 - soil) * 0.5,
                                  random.Random(f"roads:{version}:{seed}:{attempt}"))
            validate_roads(network, sites, bounds, [])
            break
        except ValueError as error:
            if attempt == GENERATION_ATTEMPTS - 1:
                raise ValueError(f"지역 생성 실패: {error}") from error
    reserved = prep(union_all([road_surface(network).buffer(1.8)] +
                              [box(x, y, x + w, y + h) for x, y, w, h in (site["reserved"] for site in sites)]))
    layers, colliders = [], []
    for row in range(extent // 16):
        for col in range(extent // 16):
            layers.append({"id": f"ground/{row}/{col}", "rect": [-half + col * 16, -half + row * 16, 16, 16],
                           "style": "grass", "image": "", "color": [45, 65, 48],
                           "variant": terrain.randrange(4)})
    for index in range(int(80 * scale)):
        x, y = terrain.uniform(-half + 5, half - 5), terrain.uniform(-half + 5, half - 5)
        density = _sample(soil, x, y)
        if density < 0.55:
            continue
        width, height = terrain.uniform(4, 9), terrain.uniform(3, 7)
        layers.append({"id": f"dirt/{index}", "rect": [x - width / 2, y - height / 2, width, height],
                       "style": "dirt", "image": "", "color": [104, 91, 62]})
    occupied = []
    # 연속된 밀도 분포에서 후보를 뽑고, 장애물 사이의 통행 공간을 침범하면 다시 뽑는다.
    for index in range(int(1100 * scale)):
        x, y = obstacles.uniform(-half + 3, half - 3), obstacles.uniform(-half + 3, half - 3)
        tree = index >= 700 * scale or obstacles.random() < 0.82
        density = _sample(vegetation if tree else soil, x, y)
        if obstacles.random() > 0.03 + 0.9 * density ** 2:
            continue
        width, height = (0.9, 0.9) if tree else (obstacles.uniform(1.5, 2.5), obstacles.uniform(1.2, 2.5))
        rect = Rect(x - width / 2, y - height / 2, width, height)
        if rect.expanded(4).segment_entry((0, 0), (0, 0)) is not None:
            continue
        spacing = rect.expanded(FOREST_TREE_GAP if tree and density >= FOREST_DENSITY else 1.8)
        if (reserved.intersects(box(rect.x, rect.y, rect.right, rect.bottom))
                or any(spacing.overlaps(other) for other in occupied)):
            continue
        occupied.append(rect)
        kind = "tree" if tree else "rock"
        colliders.append({"id": f"{kind}/{index}", "rect": [rect.x, rect.y, rect.w, rect.h],
                          "kind": kind, "material": "tree_trunk" if tree else "concrete"})
    roads = [{"id": f"road/{index}", "start": network["nodes"][segment["nodes"][0]],
              "end": network["nodes"][segment["nodes"][1]], **segment}
             for index, segment in enumerate(network["segments"])]
    layers.extend(road_layers(network))
    colors = {"safehouse": [69, 109, 91], "hq": [119, 126, 116], "comms": [102, 119, 126],
              "warehouse": [135, 125, 95], "garrison": [114, 122, 91], "sam": [126, 103, 101]}
    for site in sites:
        if not site["placeholder"]:
            continue
        layers.append({"id": f"site/{site['id']}", "rect": site["rect"], "style": "yard", "image": "",
                       "color": colors[site["kind"]]})
    validate_roads(network, sites, bounds, colliders)
    return {"format": "revenant.image_scene.v1", "name": f"절차 생성 / {region_size} · {importance}",
            "generation": {"seed": seed, "version": version, "region_size": region_size, "importance": importance,
                           "prefab_versions": manifest, "entries": entries},
            "bounds": [-half, -half, extent, extent], "planned_facilities": sites, "road_network": network,
            "player_spawn": [0, 0], "safehouse_spawn": [0, 0], "supply_point": [0, 0],
            "campaign_facility": "depot_a", "player_weapons": [], "interaction_markers": False,
            "prefab_instances": [{"id": site["id"], "prefab": site["prefab"], "version": site["prefab_version"],
                                  "position": site["position"], "rotation": site["rotation"],
                                  "road_node": network["terminals"][site["id"]]} for site in sites if not site["placeholder"]],
            "layers": layers, "colliders": colliders, "navigation": dict(network["nodes"]), "navigation_edges": list(network["edges"]),
            "guards": [], "facilities": [], "roads": roads}
