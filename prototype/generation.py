"""시드로 작은 야외 시험 맵을 만들어 기존 장면 입력에 전달한다."""

import random


GENERATION_VERSION = 1


def generate_scene(seed=42, *, version=GENERATION_VERSION):
    if type(seed) is not int:
        raise ValueError("월드 시드는 정수여야 합니다")
    if type(version) is not int or version != GENERATION_VERSION:
        raise ValueError(f"지원하지 않는 맵 생성 버전: {version}")
    terrain = random.Random(f"terrain:{version}:{seed}")
    obstacles = random.Random(f"obstacles:{version}:{seed}")
    layers, colliders = [], []
    for row in range(4):
        for col in range(4):
            layers.append({"id": f"ground/{row}/{col}", "rect": [-32 + col * 16, -32 + row * 16, 16, 16],
                           "style": "grass", "image": "", "color": [45, 65, 48],
                           "variant": terrain.randrange(4)})
    for index in range(12):
        width, height = terrain.uniform(4, 10), terrain.uniform(3, 8)
        layers.append({"id": f"dirt/{index}", "rect": [terrain.uniform(-30, 30 - width),
                       terrain.uniform(-30, 30 - height), width, height],
                       "style": "dirt", "image": "", "color": [104, 91, 62]})
    # 셀마다 장애물 하나만 두고 가장자리를 비워 셀 사이의 통행 공간을 보장한다.
    for row in range(8):
        for col in range(8):
            if obstacles.random() > 0.65:
                continue
            x = -28 + col * 8 + obstacles.uniform(-1.25, 1.25)
            y = -28 + row * 8 + obstacles.uniform(-1.25, 1.25)
            if min(abs(x), abs(y)) < 4:
                continue
            tree = obstacles.random() < 0.8
            width, height = (0.9, 0.9) if tree else (obstacles.uniform(1.5, 2.5), obstacles.uniform(1.2, 2.5))
            kind = "tree" if tree else "rock"
            colliders.append({"id": f"{kind}/{row}/{col}", "rect": [x - width / 2, y - height / 2, width, height],
                              "kind": kind, "material": "tree_trunk" if tree else "concrete"})
    return {"format": "revenant.image_scene.v1", "name": "절차 생성 / 야외 이동 시험",
            "generation": {"seed": seed, "version": GENERATION_VERSION}, "bounds": [-32, -32, 64, 64],
            "player_spawn": [0, 0], "safehouse_spawn": [0, 0], "supply_point": [0, 0],
            "campaign_facility": "depot_a", "player_weapons": [], "interaction_markers": False,
            "layers": layers, "colliders": colliders, "navigation": {}, "navigation_edges": [],
            "guards": [], "facilities": [], "roads": []}
