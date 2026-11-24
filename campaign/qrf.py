"""확인된 위협 보고를 받아 인접 주둔지의 실병력을 출동시킨다."""

import math

from .clock import TRANSFER, schedule
from .models import Operation, stock
from .resources import TERMINAL_PHASES, reserve
from .routes import find_route, road_between


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


def dispatch_response(state, facility_id, *, report_xy=None, sighting=False):
    target = state.facilities[facility_id]
    region = state.regions[target.region_id]
    command = regional_command(state, region.id)
    signal = external_support_link(state, region.id)
    pending = next((op for op in state.operations.values() if op.kind == "qrf"
                    and state.facilities[op.destination_id].region_id == region.id
                    and op.phase not in TERMINAL_PHASES), None)
    if pending is not None:
        if sighting and command and signal and pending.phase in {"loading", "outbound"}:
            pending.report_xy = report_xy
        return "already_dispatched", pending.id
    if not command:
        return "no_command", None
    if not signal:
        return "no_signal", None
    if sighting:
        # 목격 위치에서 가까운 아군 시설을 도로 이동의 집결점으로 정한다.
        def rally_distance(key):
            point = state.nodes[state.facilities[key].node_id].position
            return math.dist(report_xy, (point.x, point.y)), key

        target = state.facilities[min(region.facility_ids, key=rally_distance)]

    candidates = []
    for origin in state.facilities.values():
        source_region = state.regions[origin.region_id]
        if (source_region is region or source_region.owner != "enemy" or origin.damage == 100
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
                candidates.append((distance, unit.soldiers, origin.id, unit.id, route))

    if not candidates:
        return "no_squad", None
    _, _, origin_id, unit_id, route = min(candidates)
    reason = "순찰병의 확인된 접촉 보고" if sighting else "확인된 시설 공격 보고"
    operation = Operation(state.new_id(), reason, "qrf", origin_id, target.id, route[0], route[-1],
                          [], [unit_id], state.world_time, route=route, report_xy=report_xy)
    reserve(state, operation, stock())
    source_command = regional_command(state, state.facilities[origin_id].region_id)
    delay = 12 if command.commander_lost or source_command.commander_lost else 5
    schedule(state, state.world_time + delay, "depart", priority=TRANSFER, operation_id=operation.id)
    return "ordered", operation.id
