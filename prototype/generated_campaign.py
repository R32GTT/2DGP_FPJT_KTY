"""생성된 부지·실외 도로를 기존 캠페인 입력으로 연결한다. 운영 수치는 검증용이다."""

from copy import deepcopy
import json
import math
from pathlib import Path

from campaign.scenario import build_state


SETUP_PATH = Path(__file__).parent / "data/campaign_setup.json"


def bind_sites(scene):
    network = scene["road_network"]
    regions = scene.get("generated_regions", ())
    names = {row["id"]: row["name"] for row in regions} if len(regions) > 1 else {}
    placed = {row["id"]: row for row in scene.get("placed_prefabs", ())}
    facilities = []
    for site in scene["planned_facilities"]:
        instance = placed.get(site["id"])
        terminal = scene["navigation"][instance["terminal"]] if instance else site["connector"]["position"]
        name = (names[site["region_id"]] + " · " if site.get("region_id") in names else "") + site["name"]
        facilities.append({"id": site["id"], "name": name, "kind": site["kind"], "rect": site["rect"],
                           "campaign_id": "" if site["kind"] == "safehouse" else site["id"],
                           "service_node": network["terminals"][site["id"]], "position": site["position"],
                           "terminal": terminal, "guard_posts": instance["guard_posts"] if instance else []})
    depot = next(row for row in facilities if row["kind"] == "warehouse")
    safehouse = next(row for row in facilities if row["kind"] == "safehouse")
    return {**scene, "facilities": facilities, "campaign_facility": depot["id"],
            "supply_point": depot["terminal"], "safehouse_spawn": safehouse["position"],
            "player_spawn": safehouse["position"]}


def build_generated_state(scene, setup=None):
    setup = deepcopy(setup) if setup is not None else json.loads(SETUP_PATH.read_text(encoding="utf-8"))
    if setup["version"] != 1:
        raise ValueError("지원하지 않는 캠페인 초기 설정 버전")
    network = scene["road_network"]
    facilities, units, vehicles = [], [], []

    def assets(key, node, definition):
        units.extend({"id": f"{key}/unit/{index}", "soldiers": count, "node_id": node}
                     for index, count in enumerate(definition["units"]))
        for profile, count in definition["vehicles"].items():
            vehicles.extend({**setup["vehicle_profiles"][profile], "id": f"{key}/{profile}/{index}", "node_id": node}
                            for index in range(count))

    for site in scene["planned_facilities"]:
        kind, key = site["kind"], site["id"]
        if kind == "safehouse":
            continue
        node = network["terminals"][key]
        definition = deepcopy(setup["facilities"][kind])
        facilities.append({**{name: definition[name] for name in ("capabilities", "inventory", "minimum_stock", "target_stock")},
                           "id": key, "region_id": site.get("region_id", "generated"), "node_id": node})
        if key == scene["campaign_facility"]:
            facilities[-1]["inventory"] = dict(setup["collection_inventory"])
        assets(key, node, definition)
        if site["prefab"] and definition.get("security_soldiers"):
            units.append({"id": f"{key}/security", "soldiers": definition["security_soldiers"],
                          "node_id": node, "can_escort": False})
    regions = scene.get("generated_regions", [{"id": "generated", "importance": scene["generation"]["importance"]}])
    source = {"id": "external_source", "node_id": scene.get("campaign_entry", network["terminals"].get("entry/0")),
              "inventory": setup["external"]["inventory"]}
    assets("external", source["node_id"], setup["external"])
    roads = [{"id": row["id"], "start": row["nodes"][0], "end": row["nodes"][1],
              "length_m": math.dist(row["start"], row["end"])} for row in scene["roads"]]
    data = {"nodes": [{"id": key, "position": {"x": point[0], "y": point[1]}} for key, point in network["nodes"].items()],
            "roads": roads, "facilities": facilities, "units": units, "vehicles": vehicles, "source": source,
            "regions": [{"id": region["id"], "facility_ids": [row["id"] for row in facilities if row["region_id"] == region["id"]],
                         "minimum_garrison": setup["minimum_garrison"], "importance": setup["importance"][region["importance"]]}
                        for region in regions],
            "demand": {**setup["demand"], "node_id": scene.get("campaign_exit", network["terminals"].get("entry/1")),
                       "collection_facility_id": scene["campaign_facility"]},
            "rules": {**setup["rules"], "setup_version": setup["version"]}, "resource_priority": setup["resource_priority"]}
    return build_state(data, seed=scene["generation"]["seed"])
