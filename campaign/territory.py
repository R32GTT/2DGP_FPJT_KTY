"""지역 유지 능력과 실병력의 철수 결과로 해방을 판정한다."""

from . import clock
from .models import QRF_OPERATIONS, Operation, stock
from .qrf import external_support_link, order_troop_transport
from .repair import REPAIR_PER_PART
from .resources import TERMINAL_PHASES, cancel, consume, finish_relocation, reserve
from .routes import find_route, position, road_between


WITHDRAWAL_DELAY_S = 15
ASSEMBLY_S = 5
FOOTHOLD_MAX_DAMAGE = 75


def _usable(facilities, capability):
    return any(facility.damage < 100 and capability in facility.capabilities for facility in facilities)


def _unsustainable(region, facilities, stationed):
    if _usable(facilities, "communication"):
        return False
    depots = [facility for facility in facilities if facility.damage < 100 and "logistics" in facility.capabilities]
    shortage = not depots or all(facility.inventory["supplies"] <= facility.minimum_stock["supplies"]
                                 for facility in depots)
    return (not _usable(facilities, "command") or shortage
            or sum(unit.soldiers for unit in stationed) < region.minimum_garrison)


def _plan_withdrawals(state, region, facilities, stationed):
    refuges = [facility for facility in state.facilities.values() if facility.region_id != region.id
               and state.regions[facility.region_id].control_state == "occupied"
               and facility.damage < 100 and "command" in facility.capabilities]
    for origin in facilities:
        units = sorted(unit.id for unit in stationed if unit.node_id == origin.node_id and unit.status == "available")
        if not units:
            continue
        known_blocked = sorted({road_id for op in state.operations_of_kind("withdrawal")
                                if op.origin_id == origin.id
                                for road_id in op.known_blocked if state.roads[road_id].blocked})
        routes = []
        for refuge in refuges:
            route = find_route(state, origin.node_id, refuge.node_id, known_blocked)
            if route:
                distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
                routes.append((distance, refuge.id, route))
        if routes:
            _, target_id, route = min(routes)
        else:
            target_id = state.source.id
            route = find_route(state, origin.node_id, state.source.node_id, known_blocked)
            if not route or len(route) < 2:
                continue
        operation = Operation(state.new_id(), "지역 유지 불가로 철수", "withdrawal", origin.id, target_id,
                              route[0], route[-1], [], units, state.world_time, route=route,
                              known_blocked=known_blocked)
        reserve(state, operation, stock())
        clock.schedule(state, state.world_time + ASSEMBLY_S, "depart", priority=clock.TRANSFER,
                       operation_id=operation.id)


def _has_field_troops(state, region, nodes, operations):
    for op in operations:
        if (op.phase == "loading" or not op.route
                or not any(state.units[key].faction == "enemy" and state.units[key].soldiers for key in op.unit_ids)):
            continue
        if region.bounds is None:
            if op.route[op.edge_index] in nodes:
                return True
            continue
        x, y, width, height = region.bounds
        point = position(state, op)
        if x <= point.x < x + width and y <= point.y < y + height:
            return True
    return False


def evaluate_regions(state):
    units_by_node = {}
    for unit in state.units.values():
        if unit.faction == "enemy" and unit.soldiers and unit.node_id is not None:
            units_by_node.setdefault(unit.node_id, []).append(unit)
    for region in sorted(state.regions.values(), key=lambda item: item.id):
        if region.owner != "enemy":
            continue
        facilities = [state.facilities[key] for key in region.facility_ids]
        if not facilities:
            continue
        nodes = {facility.node_id for facility in facilities}
        stationed = [unit for facility in facilities for unit in units_by_node.get(facility.node_id, ())]
        if not _unsustainable(region, facilities, stationed):
            if region.withdrawal_started_at is not None:
                for operation in list(state.active_operations()):
                    if operation.kind == "withdrawal" and operation.home_node in nodes and operation.phase == "loading":
                        cancel(state, operation.id)
                state.record("withdrawal_cancelled", region_id=region.id, reason="지역 유지 능력 회복")
            region.weak_since = region.withdrawal_started_at = None
            continue
        if region.weak_since is None:
            region.weak_since = state.world_time
            state.record("region_unstable", region_id=region.id)
        if state.world_time - region.weak_since < WITHDRAWAL_DELAY_S:
            continue
        active = list(state.active_operations())
        local_orders = [op for op in active if op.home_node in nodes]
        if region.withdrawal_started_at is None:
            region.withdrawal_started_at = state.world_time
            state.record("withdrawal_started", region_id=region.id)
            for operation in local_orders:
                if operation.kind in QRF_OPERATIONS | {"supply", "export", "redeployment", "equipment", "recapture"}:
                    cancel(state, operation.id)
        on_site = _has_field_troops(state, region, nodes, active)
        pending_withdrawal = region.bounds is None and any(op.kind == "withdrawal" for op in local_orders)
        if not stationed and not on_site and not pending_withdrawal:
            region.owner = "player"
            region.liberated_at = state.world_time
            region.weak_since = region.withdrawal_started_at = None
            for facility in facilities:
                state.repair_requests.pop(facility.id, None)
            state.record("region_liberated", region_id=region.id)
        else:
            _plan_withdrawals(state, region, facilities, stationed)


def _recapture_cargo(state, target):
    damage = max(0, target.damage - FOOTHOLD_MAX_DAMAGE)
    return stock(supplies=state.rules["recapture_supplies"],
                 repair_parts=(damage + REPAIR_PER_PART - 1) // REPAIR_PER_PART)


def plan_recaptures(state):
    delay = state.rules.get("recapture_delay_s")
    targets = sorted((region for region in state.regions.values() if region.owner == "player"),
                     key=lambda region: (-region.importance, region.id))
    if delay is None or not targets or len(targets) == len(state.regions):
        return
    supporters = {region.id for region in state.regions.values()
                  if region.control_state == "occupied" and external_support_link(state, region.id)}
    if not supporters:
        return
    origins = [site for site in state.facilities.values() if site.region_id in supporters and site.damage < 100]
    origins.append(state.source)
    units = {}
    for unit in state.units.values():
        if (unit.faction == "enemy" and unit.mobile and unit.status == "available"
                and unit.soldiers >= state.rules["recapture_min_troops"]):
            units.setdefault(unit.node_id, []).append(unit)
    attempts = {}
    for op in state.operations_of_kind("recapture"):
        attempts.setdefault(state.facilities[op.destination_id].region_id, []).append(op)

    for region in targets:
        prior = attempts.get(region.id, ())
        if any(op.phase not in TERMINAL_PHASES and op.phase != "stranded" for op in prior):
            continue
        last = max((op.completed_at if op.completed_at is not None else op.created_at for op in prior), default=0)
        if state.world_time < max(region.liberated_at or 0, last) + delay:
            continue
        targets_with_signal = [state.facilities[key] for key in region.facility_ids
                               if "communication" in state.facilities[key].capabilities]
        known_blocked = sorted({key for op in prior for key in op.known_blocked if state.roads[key].blocked})
        candidates = []
        for origin in origins:
            if not units.get(origin.node_id):
                continue
            for target in targets_with_signal:
                route = find_route(state, origin.node_id, target.node_id, known_blocked)
                if route and len(route) > 1:
                    distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
                    candidates.extend((origin is state.source, distance, unit.id, target.id, origin, target, unit, route)
                                      for unit in units[origin.node_id] if unit.status == "available")
        for _, _, _, _, origin, target, unit, route in sorted(candidates, key=lambda row: row[:4]):
            operation = order_troop_transport(state, origin, target, unit, route, "recapture", "상실 지역 통신 거점 탈환",
                                             state.rules["loading_s"], cargo=_recapture_cargo(state, target),
                                             known_blocked=known_blocked)
            if operation is not None:
                state.record("recapture_ordered", region_id=region.id, operation_id=operation.id)
                break


def can_secure_foothold(state, operation):
    target = state.facilities[operation.destination_id]
    region = state.regions[target.region_id]
    required = _recapture_cargo(state, target)
    return (region.owner == "player" and region.recapture_operation_id in {None, operation.id}
            and sum(state.units[key].soldiers for key in operation.unit_ids) >= state.rules["recapture_min_troops"]
            and all(operation.cargo[name] >= amount for name, amount in required.items()))


def start_foothold(state, operation):
    target = state.facilities[operation.destination_id]
    region = state.regions[target.region_id]
    region.recapture_operation_id = operation.id
    operation.phase, operation.waiting_reason = "working", ""
    state.record("recapture_started", region_id=region.id, operation_id=operation.id, facility_id=target.id)
    clock.schedule(state, state.world_time + state.rules["recapture_work_s"], "recapture_work",
                   priority=clock.TRANSFER, operation_id=operation.id)


def clear_foothold(state, operation):
    region = state.regions[state.facilities[operation.destination_id].region_id]
    if region.recapture_operation_id == operation.id:
        region.recapture_operation_id = None


def foothold_route_open(state, operation):
    blocked = {road.id for road in state.roads.values() if road.blocked}
    if find_route(state, operation.home_node, operation.destination_node, blocked):
        return True
    traveled = {road_between(state, a, b).id for a, b in zip(operation.route, operation.route[1:])}
    operation.known_blocked = sorted(set(operation.known_blocked) | (traveled & blocked))
    return False


def finish_recapture(state, operation):
    target = state.facilities[operation.destination_id]
    region = state.regions[target.region_id]
    parts = _recapture_cargo(state, target)["repair_parts"]
    if parts:
        consume(state, operation.cargo, {"repair_parts": parts}, reason="recapture_foothold_repair")
        target.damage = max(0, target.damage - parts * REPAIR_PER_PART)
    clear_foothold(state, operation)
    region.owner, region.liberated_at = "enemy", None
    finish_relocation(state, operation)
    state.record("region_reoccupied", region_id=region.id, operation_id=operation.id, facility_id=target.id)
