"""지역 유지 능력과 실병력의 철수 결과로 해방을 판정한다."""

from . import clock
from .models import QRF_OPERATIONS, Operation, stock
from .resources import TERMINAL_PHASES, cancel, reserve
from .routes import find_route, position, road_between


WITHDRAWAL_DELAY_S = 15
ASSEMBLY_S = 5


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
        known_blocked = sorted({road_id for op in state.operations.values()
                                if op.kind == "withdrawal" and op.origin_id == origin.id
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
    for region in sorted(state.regions.values(), key=lambda item: item.id):
        if region.owner != "enemy":
            continue
        facilities = [state.facilities[key] for key in region.facility_ids]
        if not facilities:
            continue
        nodes = {facility.node_id for facility in facilities}
        stationed = [unit for unit in state.units.values()
                     if unit.faction == "enemy" and unit.soldiers and unit.node_id in nodes]
        if not _unsustainable(region, facilities, stationed):
            if region.withdrawal_started_at is not None:
                for operation in state.operations.values():
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
        active = [op for op in state.operations.values() if op.phase not in TERMINAL_PHASES]
        local_orders = [op for op in active if op.home_node in nodes]
        if region.withdrawal_started_at is None:
            region.withdrawal_started_at = state.world_time
            state.record("withdrawal_started", region_id=region.id)
            for operation in local_orders:
                if operation.kind in QRF_OPERATIONS | {"supply", "export", "redeployment", "equipment"}:
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
