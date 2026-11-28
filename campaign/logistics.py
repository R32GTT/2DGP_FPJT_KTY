"""실제 수요·재고·가용 수송 능력으로 편성하는 자율 보급."""

from .clock import TRANSFER, schedule
from .models import CampaignState, Operation, RESOURCE_TYPES, stock
from .qrf import external_support_link, regional_command
from .resources import available, consume, minimum, owner, quantities, reserve
from .routes import find_route, route_distance, transport_provisions


EXTERNAL_FRONT = "external_front"


def incoming(state: CampaignState, destination_id: str) -> dict[str, int]:
    result = stock()
    for operation in state.active_operations():
        if (operation.kind not in {"supply", "export", "equipment"} or operation.destination_id != destination_id
                or operation.phase not in {"loading", "outbound", "unloading"}):
            continue
        cargo = (state.reservations[operation.id].cargo if operation.phase == "loading"
                 else operation.cargo)
        for name in RESOURCE_TYPES:
            result[name] += cargo[name]
    return result


def plan_transport(state: CampaignState, origin_id: str, destination_id: str,
                   requested: dict[str, int], *, kind: str = "supply",
                   reason: str = "시설 목표 비축량 부족", minimum_batch: int = 1) -> Operation | None:
    requested = quantities(requested)
    origin = owner(state, origin_id)
    destination_node = (state.demand.node_id if kind == "export"
                        else state.facilities[destination_id].node_id)
    if origin.node_id == destination_node:
        return None
    if origin_id == state.source.id:
        if (state.world_time < state.source.next_departure
                or any(op.origin_id == origin_id and op.phase == "loading"
                       for op in state.active_operations())):
            return None
        limit = min(sum(requested.values()), state.rules["external_cargo_limit"])
    else:
        limit = sum(requested.values())
    pool = sorted(v.id for v in state.vehicles.values()
                  if v.node_id == origin.node_id and v.status == "available" and not v.troop_seats)
    if not pool:
        return None
    if kind == "supply":
        if route_distance(state, origin.node_id, destination_node) is None:
            return None
        route = None
    else:
        route = find_route(state, origin.node_id, destination_node)
        if not route:
            return None
    provisions = transport_provisions(state, route) if kind == "equipment" else state.rules["provisions_per_vehicle"]
    vehicle_ids, capacity = [], 0
    while pool and capacity - len(vehicle_ids) * provisions < limit:
        chosen = state.choose(pool)
        pool.remove(chosen)
        vehicle_ids.append(chosen)
        capacity += state.vehicles[chosen].capacity
    if not vehicle_ids:
        return None
    provision_count = len(vehicle_ids) * provisions
    space = min(limit, capacity - provision_count)
    cargo = stock()
    for name in state.resource_priority:
        floor = minimum(state, origin_id, name)
        if origin_id != state.source.id and kind != "repair" and (kind == "export" or
                "logistics" in state.facilities[destination_id].capabilities):
            floor = max(floor, origin.target_stock[name])
        spare = available(state, origin_id, name) - floor
        if name == "supplies":
            spare -= provision_count
        amount = int(max(0, min(requested[name], spare, space)))
        cargo[name] = amount
        space -= amount
    if sum(cargo.values()) < minimum_batch:
        return None
    if route is None:
        route = find_route(state, origin.node_id, destination_node)
    units = sorted((u for u in state.units.values()
                    if u.node_id == origin.node_id and u.status == "available" and u.soldiers and u.can_escort),
                   key=lambda unit: (unit.soldiers, unit.id))
    unit_ids, soldiers = [], 0
    for unit in units:
        unit_ids.append(unit.id)
        soldiers += unit.soldiers
        if soldiers >= state.rules["escort_soldiers"] * len(vehicle_ids):
            break
    operation = Operation(state.new_id(), reason, kind, origin_id, destination_id,
                          origin.node_id, destination_node, vehicle_ids, unit_ids,
                          state.world_time, route=route)
    try:
        reserve(state, operation, cargo, stock(supplies=provision_count))
    except ValueError:
        return None
    schedule(state, state.world_time + state.rules["loading_s"], "depart",
             priority=TRANSFER, operation_id=operation.id)
    return operation


def plan_supplies(state: CampaignState) -> None:
    destinations = sorted(state.facilities.values(), key=lambda facility: (
        "logistics" in facility.capabilities,
        -state.regions[facility.region_id].importance, facility.id))
    for destination in destinations:
        if destination.damage == 100 or state.regions[destination.region_id].owner != "enemy":
            continue
        if any(op.kind == "supply" and op.destination_id == destination.id
               and op.phase in {"loading", "outbound", "unloading"}
               for op in state.active_operations()):
            continue
        inbound = incoming(state, destination.id)
        extra = stock()
        if destination.id == state.demand.collection_facility_id:
            exports = incoming(state, EXTERNAL_FRONT)
            extra = {name: max(0, state.demand.outstanding[name] - exports[name])
                     for name in RESOURCE_TYPES}
            if any(extra.values()):
                extra["supplies"] += state.rules["export_provision_buffer"]
        needed = {name: max(0, destination.target_stock[name] + extra[name]
                            - available(state, destination.id, name) - inbound[name])
                  for name in RESOURCE_TYPES if name != "night_gear"}
        if not any(needed.values()):
            continue
        candidates = []
        for facility in state.facilities.values():
            if (facility.id == destination.id or "logistics" not in facility.capabilities
                    or facility.damage == 100 or state.regions[facility.region_id].owner != "enemy"):
                continue
            distance = route_distance(state, facility.node_id, destination.node_id)
            if distance is not None:
                candidates.append((facility.region_id != destination.region_id, distance, facility.id))
        for _, _, origin_id in sorted(candidates):
            if plan_transport(state, origin_id, destination.id, needed) is not None:
                break
        else:
            if "logistics" in destination.capabilities and not state.rules.get(
                    "local_supply_only", state.rules.get("setup_version", 0)):
                plan_transport(state, state.source.id, destination.id, needed,
                               reason="외부 정기 보급 수요")


def plan_exports(state: CampaignState) -> None:
    inbound = incoming(state, EXTERNAL_FRONT)
    needed = {name: max(0, state.demand.outstanding[name] - inbound[name])
              for name in RESOURCE_TYPES}
    if any(needed.values()):
        plan_transport(state, state.demand.collection_facility_id, EXTERNAL_FRONT,
                       needed, kind="export", reason="외부 전선 출하 수요·일정",
                       minimum_batch=state.rules["minimum_export_batch"])


def issue_night_gear(state, facility):
    limit = state.rules.get("night_kits_per_garrison", 0)
    if (not limit or "garrison" not in facility.capabilities or facility.damage == 100
            or state.regions[facility.region_id].owner != "enemy"):
        return 0
    units = sorted((unit for unit in state.units.values() if unit.node_id == facility.node_id
                    and unit.faction == "enemy" and unit.soldiers), key=lambda unit: (unit.mobile, unit.id))
    assigned = sum(unit.night_kits for unit in units)
    target = min(limit, sum(unit.soldiers for unit in units))
    eligible = [unit for unit in units if unit.status == "available"]
    remaining = min(max(0, target - assigned), available(state, facility.id, "night_gear"),
                    sum(unit.soldiers - unit.night_kits for unit in eligible))
    issued = remaining
    if remaining > 0:
        consume(state, facility.inventory, {"night_gear": remaining}, reason="garrison_night_gear", owner_id=facility.id)
        for unit in eligible:
            count = min(remaining, unit.soldiers - unit.night_kits)
            unit.night_kits += count
            remaining -= count
            if not remaining:
                break
    return max(0, target - assigned - issued)


def plan_equipment(state):
    if "night_kits_per_garrison" not in state.rules:
        return
    for target in sorted(state.facilities.values(), key=lambda site: (-state.regions[site.region_id].importance, site.id)):
        needed = issue_night_gear(state, target)
        if not needed or not regional_command(state, target.region_id):
            continue
        if incoming(state, target.id)["night_gear"]:
            continue
        candidates = []
        for origin in state.facilities.values():
            if (origin is target or "logistics" not in origin.capabilities or origin.damage == 100
                    or state.regions[origin.region_id].owner != "enemy"
                    or available(state, origin.id, "night_gear") <= minimum(state, origin.id, "night_gear")):
                continue
            if origin.region_id != target.region_id and not all(external_support_link(state, key)
                                                                for key in (origin.region_id, target.region_id)):
                continue
            distance = route_distance(state, origin.node_id, target.node_id)
            if distance is not None:
                candidates.append((distance, origin.id))
        for _, origin_id in sorted(candidates):
            if plan_transport(state, origin_id, target.id, {"night_gear": needed}, kind="equipment",
                              reason="주둔지 야간 장비 요청") is not None:
                break
