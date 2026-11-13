"""실제 수요·재고·가용 수송 능력으로 편성하는 자율 보급."""

from .clock import TRANSFER, schedule
from .models import CampaignState, Operation, RESOURCE_TYPES, stock
from .resources import available, minimum, owner, quantities, reserve
from .routes import find_route, road_between


EXTERNAL_FRONT = "external_front"


def incoming(state: CampaignState, destination_id: str) -> dict[str, int]:
    result = stock()
    for operation in state.operations.values():
        if operation.destination_id != destination_id or operation.phase not in {"loading", "outbound", "unloading"}:
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
    route = find_route(state, origin.node_id, destination_node)
    if not route or origin.node_id == destination_node:
        return None
    if origin_id == state.source.id:
        if (state.world_time < state.source.next_departure
                or any(op.origin_id == origin_id and op.phase == "loading"
                       for op in state.operations.values())):
            return None
        limit = min(sum(requested.values()), state.rules["external_cargo_limit"])
    else:
        limit = sum(requested.values())
    pool = sorted(v.id for v in state.vehicles.values()
                  if v.node_id == origin.node_id and v.status == "available")
    vehicle_ids, capacity = [], 0
    while pool and capacity - len(vehicle_ids) * state.rules["provisions_per_vehicle"] < limit:
        chosen = state.choose(pool)
        pool.remove(chosen)
        vehicle_ids.append(chosen)
        capacity += state.vehicles[chosen].capacity
    if not vehicle_ids:
        return None
    provision_count = len(vehicle_ids) * state.rules["provisions_per_vehicle"]
    space = min(limit, capacity - provision_count)
    cargo = stock()
    for name in state.resource_priority:
        floor = minimum(state, origin_id, name)
        if origin_id != state.source.id and (kind == "export" or
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
    units = sorted((u for u in state.units.values()
                    if u.node_id == origin.node_id and u.status == "available" and u.soldiers),
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
               for op in state.operations.values()):
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
                  for name in RESOURCE_TYPES}
        if not any(needed.values()):
            continue
        candidates = []
        for facility in state.facilities.values():
            if (facility.id == destination.id or "logistics" not in facility.capabilities
                    or facility.damage == 100 or state.regions[facility.region_id].owner != "enemy"):
                continue
            route = find_route(state, facility.node_id, destination.node_id)
            if route:
                distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
                candidates.append((facility.region_id != destination.region_id, distance, facility.id))
        for _, _, origin_id in sorted(candidates):
            if plan_transport(state, origin_id, destination.id, needed) is not None:
                break
        else:
            if "logistics" in destination.capabilities:
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
