"""현장 보급 상자 위치. 수량은 캠페인 시설 재고에서만 읽는다."""

import math

from campaign.models import RESOURCE_TYPES


STOCK_NODES = {
    "warehouse": ("supplies", "receiving_middle", "office_inside", "inside_watch"),
    "hq": ("storage_top", "storage_corner", "east_room", "west_room"),
    "comms": ("inside", "console", "yard", "gate_watch"),
}
REACH = 1.8


def build_stacks(world):
    navigation = world.data.get("navigation", {})
    stacks = []
    for site in world.facilities:
        if not site["campaign_id"]:
            continue
        nodes = STOCK_NODES.get(site["kind"], ())
        x, y = site["terminal"]
        for index, resource in enumerate(RESOURCE_TYPES):
            key = f"{site['id']}/{nodes[index]}" if nodes else ""
            point = tuple(navigation.get(key, (x + (index % 2) * 0.7 - 0.35,
                                                 y + (index // 2) * 0.7 - 0.35)))
            stacks.append((site["id"], resource, point))
    return tuple(stacks)


def nearby_stack(play, *, resource=None, site_id=None):
    origin = play.player.xy
    state = play.campaign.simulation.state
    return min(((site, item, point) for site, item, point in play.supply_stacks
                if (resource is None or item == resource) and (site_id is None or site == site_id)
                and state.facilities[site].inventory[item] > 0
                and math.dist(origin, point) <= REACH
                and play.world.geometry.raycast(origin, point, category="movement") is None),
               key=lambda row: math.dist(origin, row[2]), default=None)
