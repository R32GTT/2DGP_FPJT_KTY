"""시드로 작은 야외 시험 맵을 만들어 기존 장면 입력에 전달한다."""

import random

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

from .geometry import Rect


GENERATION_VERSION = 2


def _density(seed, kind):
    rng = np.random.default_rng(random.Random(f"{kind}:{GENERATION_VERSION}:{seed}").getrandbits(64))
    field = gaussian_filter(rng.random((32, 32)), sigma=3, mode="reflect")
    return (field - field.min()) / max(float(np.ptp(field)), 1e-8)


def _sample(field, x, y):
    return float(map_coordinates(field, [[(y + 31) / 2], [(x + 31) / 2]],
                                 order=1, mode="nearest", prefilter=False)[0])


def generate_scene(seed=42, *, version=GENERATION_VERSION):
    if type(seed) is not int:
        raise ValueError("월드 시드는 정수여야 합니다")
    if type(version) is not int or version != GENERATION_VERSION:
        raise ValueError(f"지원하지 않는 맵 생성 버전: {version}")
    terrain = random.Random(f"terrain:{version}:{seed}")
    obstacles = random.Random(f"obstacles:{version}:{seed}")
    vegetation, soil = _density(seed, "vegetation"), _density(seed, "soil")
    layers, colliders = [], []
    for row in range(4):
        for col in range(4):
            layers.append({"id": f"ground/{row}/{col}", "rect": [-32 + col * 16, -32 + row * 16, 16, 16],
                           "style": "grass", "image": "", "color": [45, 65, 48],
                           "variant": terrain.randrange(4)})
    for index in range(80):
        x, y = terrain.uniform(-27, 27), terrain.uniform(-27, 27)
        density = _sample(soil, x, y)
        if density < 0.55:
            continue
        width, height = terrain.uniform(4, 9), terrain.uniform(3, 7)
        layers.append({"id": f"dirt/{index}", "rect": [x - width / 2, y - height / 2, width, height],
                       "style": "dirt", "image": "", "color": [104, 91, 62]})
    occupied = []
    # 연속된 밀도 분포에서 후보를 뽑고, 장애물 사이의 통행 공간을 침범하면 다시 뽑는다.
    for index in range(700):
        x, y = obstacles.uniform(-29, 29), obstacles.uniform(-29, 29)
        tree = obstacles.random() < 0.82
        density = _sample(vegetation if tree else soil, x, y)
        if obstacles.random() > 0.03 + 0.9 * density ** 2:
            continue
        width, height = (0.9, 0.9) if tree else (obstacles.uniform(1.5, 2.5), obstacles.uniform(1.2, 2.5))
        rect = Rect(x - width / 2, y - height / 2, width, height)
        if rect.expanded(4).segment_entry((0, 0), (0, 0)) is not None:
            continue
        if any(rect.expanded(1.8).overlaps(other) for other in occupied):
            continue
        occupied.append(rect)
        kind = "tree" if tree else "rock"
        colliders.append({"id": f"{kind}/{index}", "rect": [rect.x, rect.y, rect.w, rect.h],
                          "kind": kind, "material": "tree_trunk" if tree else "concrete"})
    return {"format": "revenant.image_scene.v1", "name": "절차 생성 / 야외 이동 시험",
            "generation": {"seed": seed, "version": GENERATION_VERSION}, "bounds": [-32, -32, 64, 64],
            "player_spawn": [0, 0], "safehouse_spawn": [0, 0], "supply_point": [0, 0],
            "campaign_facility": "depot_a", "player_weapons": [], "interaction_markers": False,
            "layers": layers, "colliders": colliders, "navigation": {}, "navigation_edges": [],
            "guards": [], "facilities": [], "roads": []}
