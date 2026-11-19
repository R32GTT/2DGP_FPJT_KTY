"""생성된 부지·실외 도로를 기존 캠페인 입력으로 연결한다. 운영 수치는 검증용이다."""

from copy import deepcopy
import json
import math

from campaign.scenario import DEFAULT_SCENARIO, build_state


FACILITY_TEMPLATES = {"hq": "hq_a", "comms": "comms_a", "warehouse": "depot_a",
                      "garrison": "hq_a", "sam": "comms_a"}
CAPABILITIES = {"hq": ["command", "garrison", "qrf"], "comms": ["communication"],
                "warehouse": ["logistics"], "garrison": ["garrison", "qrf"], "sam": ["air_defense"]}


def bind_sites(scene):
    network = scene["road_network"]
    placed = {row["id"]: row for row in scene.get("placed_prefabs", ())}
    facilities = []
    for site in scene["planned_facilities"]:
        instance = placed.get(site["id"])
        terminal = scene["navigation"][instance["terminal"]] if instance else site["connector"]["position"]
        facilities.append({"id": site["id"], "name": site["name"], "kind": site["kind"], "rect": site["rect"],
                           "campaign_id": "" if site["kind"] == "safehouse" else site["id"],
                           "service_node": network["terminals"][site["id"]], "position": site["position"],
                           "terminal": terminal})
    depot = next(row for row in facilities if row["kind"] == "warehouse")
    safehouse = next(row for row in facilities if row["kind"] == "safehouse")
    return {**scene, "facilities": facilities, "campaign_facility": depot["id"],
            "supply_point": depot["terminal"], "safehouse_spawn": safehouse["position"]}


def build_generated_state(scene):
    template = json.loads(DEFAULT_SCENARIO.read_text(encoding="utf-8"))
    prototypes = {row["id"]: row for row in template["facilities"]}
    network = scene["road_network"]
    facilities, units, vehicles = [], [], []
    for site in scene["planned_facilities"]:
        kind, key = site["kind"], site["id"]
        if kind == "safehouse":
            continue
        node = network["terminals"][key]
        facilities.append({**deepcopy(prototypes[FACILITY_TEMPLATES[kind]]), "id": key, "region_id": "generated",
                           "node_id": node, "capabilities": list(CAPABILITIES[kind])})
        count = 10 if kind in {"hq", "garrison"} else 2
        for index in range(2 if kind == "warehouse" else 1):
            units.append({"id": f"{key}/unit/{index}", "soldiers": count, "node_id": node})
            if kind == "warehouse":
                vehicles.append({**template["vehicles"][2], "id": f"{key}/truck/{index}", "node_id": node})
    source = {**deepcopy(template["source"]), "node_id": network["terminals"]["entry/0"]}
    for index in range(2):
        units.append({"id": f"external/unit/{index}", "soldiers": 4, "node_id": source["node_id"]})
        vehicles.append({**template["vehicles"][index], "id": f"external/truck/{index}", "node_id": source["node_id"]})
    roads = [{"id": row["id"], "start": row["nodes"][0], "end": row["nodes"][1],
              "length_m": math.dist(row["start"], row["end"])} for row in scene["roads"]]
    data = {"nodes": [{"id": key, "position": {"x": point[0], "y": point[1]}} for key, point in network["nodes"].items()],
            "roads": roads, "facilities": facilities, "units": units, "vehicles": vehicles, "source": source,
            "regions": [{"id": "generated", "facility_ids": [row["id"] for row in facilities], "minimum_garrison": 10,
                         "importance": ("normal", "important", "core").index(scene["generation"]["importance"]) + 1}],
            "demand": {**template["demand"], "node_id": network["terminals"]["entry/1"],
                       "collection_facility_id": scene["campaign_facility"]},
            "rules": {**template["rules"], "road_cost_distance_m": 100},
            "resource_priority": template["resource_priority"]}
    return build_state(data, seed=scene["generation"]["seed"])
