"""확인된 위협 보고를 받아 인접 주둔지의 실병력을 출동시킨다."""

import math

from .clock import TRANSFER, schedule
from .models import QRF_OPERATIONS, Operation, stock
from .resources import TERMINAL_PHASES, reserve
from .routes import find_route, road_between, transport_provisions


def _working(state, region, capability):
    return any(capability in state.facilities[key].capabilities and state.facilities[key].damage < 100
               for key in region.facility_ids)


def regional_command(state, region_id):
    region = state.regions[region_id]
    if region.owner == "enemy":
        return next((state.facilities[key] for key in region.facility_ids
                     if "command" in state.facilities[key].capabilities and state.facilities[key].damage < 100), None)
    return None


def regional_signal(state, region_id):
    region = state.regions[region_id]
    return region.owner == "enemy" and _working(state, region, "communication")


def external_support_link(state, region_id):
    command = regional_command(state, region_id)
    return command is not None and not command.relay_disabled and regional_signal(state, region_id)


def air_support_available(state, region_id):
    region = state.regions[region_id]
    return not regional_signal(state, region_id) and not (
        region.owner == "enemy" and _working(state, region, "air_defense"))


def order_troop_transport(state, origin, target, unit, route, kind, reason, delay, report_xy=None, *,
                          cargo=None, known_blocked=()):
    cargo = stock() if cargo is None else cargo
    provisions = transport_provisions(state, route)
    truck = min((vehicle for vehicle in state.vehicles.values()
                 if vehicle.node_id == origin.node_id and vehicle.status == "available"
                 and vehicle.troop_seats >= unit.soldiers
                 and vehicle.capacity >= provisions + sum(cargo.values())), key=lambda vehicle: vehicle.id, default=None)
    if truck is None:
        return None
    operation = Operation(state.new_id(), reason, kind, origin.id, target.id, route[0], route[-1],
                          [truck.id], [unit.id], state.world_time, route=route, report_xy=report_xy,
                          known_blocked=list(known_blocked))
    try:
        reserve(state, operation, cargo, stock(supplies=provisions))
    except ValueError:
        return None
    schedule(state, state.world_time + delay, "depart", priority=TRANSFER, operation_id=operation.id)
    return operation


def plan_redeployments(state):
    pending = {op.destination_id for op in state.operations.values()
               if op.kind in QRF_OPERATIONS | {"redeployment"} and op.phase not in TERMINAL_PHASES
               and op.phase not in {"returning", "stranded"}}
    for target in sorted(state.facilities.values(), key=lambda site: (-state.regions[site.region_id].importance, site.id)):
        if (not target.staffing_target or target.id in pending or target.damage == 100
                or not regional_command(state, target.region_id)):
            continue
        if sum(unit.soldiers for unit in state.units.values() if unit.node_id == target.node_id) >= target.staffing_target:
            continue
        candidates = []
        for origin in state.facilities.values():
            if origin is target or origin.damage == 100 or not regional_command(state, origin.region_id):
                continue
            if origin.region_id != target.region_id and not all(external_support_link(state, key)
                                                                for key in (origin.region_id, target.region_id)):
                continue
            local = [unit for unit in state.units.values() if unit.node_id == origin.node_id
                     and unit.status == "available" and unit.faction == "enemy"]
            spare = sum(unit.soldiers for unit in local) - origin.staffing_target
            if not any(unit.mobile and 0 < unit.soldiers <= spare for unit in local):
                continue
            route = find_route(state, origin.node_id, target.node_id)
            if route:
                distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
                candidates.extend((distance, unit.id, origin, unit, route) for unit in local
                                  if unit.mobile and 0 < unit.soldiers <= spare)
        for _, _, origin, unit, route in sorted(candidates, key=lambda row: row[:2]):
            if order_troop_transport(state, origin, target, unit, route, "redeployment", "주둔 병력 보강",
                                     state.rules["loading_s"]) is not None:
                break


def dispatch_response(state, facility_id, *, report_xy=None, sighting=False):
    target = state.facilities[facility_id]
    region = state.regions[target.region_id]
    command = regional_command(state, region.id)
    signal = external_support_link(state, region.id)
    pending = next((op for op in state.operations.values() if op.kind in QRF_OPERATIONS
                    and state.facilities[op.destination_id].region_id == region.id
                    and op.phase not in TERMINAL_PHASES and op.phase not in {"stranded", "returning"}), None)
    if pending is not None:
        if sighting and command and signal and pending.phase in {"loading", "outbound"}:
            pending.report_xy = report_xy
        return "already_dispatched", pending.id
    if not command:
        return "no_command", None
    if not signal:
        return "no_signal", None
    if sighting or target.damage == 100:
        # 도착할 수 있는 아군 시설을 보고 지점 근처의 집결점으로 정한다.
        location = state.nodes[target.node_id].position
        incident = report_xy if report_xy is not None else (location.x, location.y)

        def rally_distance(key):
            point = state.nodes[state.facilities[key].node_id].position
            return math.dist(incident, (point.x, point.y)), key

        usable = (key for key in region.facility_ids if state.facilities[key].damage < 100)
        target = state.facilities[min(usable, key=rally_distance)]

    candidates, motorized = [], []
    for origin in state.facilities.values():
        source_region = state.regions[origin.region_id]
        if (origin is target or source_region.owner != "enemy" or origin.damage == 100
                or source_region.withdrawal_started_at is not None
                or not _working(state, source_region, "qrf") or not external_support_link(state, source_region.id)):
            continue
        route = find_route(state, origin.node_id, target.node_id)
        if not route:
            continue
        distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
        nodes = {state.facilities[key].node_id for key in source_region.facility_ids}
        available = [unit for unit in state.units.values()
                     if unit.faction == "enemy" and unit.status == "available" and unit.node_id in nodes]
        surplus = sum(unit.soldiers for unit in available) - source_region.minimum_garrison
        for unit in available:
            if unit.node_id == origin.node_id and 0 < unit.soldiers <= surplus:
                row = (distance, unit.soldiers, origin.id, unit.id, route)
                if unit.mobile:
                    motorized.append(row)
                if source_region is not region:
                    candidates.append(row)

    reason = "순찰병의 확인된 접촉 보고" if sighting else "확인된 시설 공격 보고"
    for _, _, origin_id, unit_id, route in sorted(motorized):
        origin = state.facilities[origin_id]
        source_command = regional_command(state, origin.region_id)
        delay = 12 if command.commander_lost or source_command.commander_lost else 5
        operation = order_troop_transport(state, origin, target, state.units[unit_id], route,
                                         "motor_qrf", reason, delay, report_xy)
        if operation is not None:
            return "ordered", operation.id
    if not candidates:
        return "no_squad", None
    _, _, origin_id, unit_id, route = min(candidates)
    operation = Operation(state.new_id(), reason, "qrf", origin_id, target.id, route[0], route[-1],
                          [], [unit_id], state.world_time, route=route, report_xy=report_xy)
    reserve(state, operation, stock())
    source_command = regional_command(state, state.facilities[origin_id].region_id)
    delay = 12 if command.commander_lost or source_command.commander_lost else 5
    schedule(state, state.world_time + delay, "depart", priority=TRANSFER, operation_id=operation.id)
    return "ordered", operation.id
