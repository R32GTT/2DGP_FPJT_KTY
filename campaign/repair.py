"""보고된 시설 피해를 실제 인력과 수리 자재로 복구한다."""

from . import clock
from .logistics import plan_transport
from .resources import consume
from .routes import find_route, road_between


REPAIR_PER_PART = 25
WORK_SECONDS = 25
RETRY_SECONDS = 15


def queue_repairs(state, *, delay=RETRY_SECONDS):
    if state.repair_requests and not any(event.kind == "repair_retry" for event in state.events):
        clock.schedule(state, state.world_time + delay, "repair_retry")


def plan_repairs(state):
    active = {op.destination_id for op in state.active_operations()
              if op.kind == "repair" and op.phase in {"loading", "outbound", "working"}}
    for target_id in sorted(state.repair_requests):
        destination = state.facilities[target_id]
        region = state.regions[destination.region_id]
        if target_id in active or region.owner != "enemy":
            continue
        facilities = [state.facilities[key] for key in region.facility_ids]
        if not any("command" in item.capabilities and item.damage < 100 for item in facilities):
            continue
        candidates = []
        for origin in facilities:
            if (origin.id == target_id or origin.damage == 100
                    or not {"logistics", "command"}.intersection(origin.capabilities)):
                continue
            route = find_route(state, origin.node_id, destination.node_id)
            if route:
                distance = sum(road_between(state, a, b).length_m for a, b in zip(route, route[1:]))
                candidates.append((distance, origin.id))
        parts = (state.repair_requests[target_id] + REPAIR_PER_PART - 1) // REPAIR_PER_PART
        for _, origin_id in sorted(candidates):
            if plan_transport(state, origin_id, target_id, {"repair_parts": parts}, kind="repair",
                              reason="HQ에 보고된 시설 복구 요청") is not None:
                break


def finish_repair(state, operation):
    facility = state.facilities[operation.destination_id]
    known = state.repair_requests.get(facility.id, 0)
    damage = min(facility.damage, known)
    if state.regions[facility.region_id].owner != "enemy" or not damage:
        return
    parts = min(operation.cargo["repair_parts"], (damage + REPAIR_PER_PART - 1) // REPAIR_PER_PART)
    if not parts:
        return
    consume(state, operation.cargo, {"repair_parts": parts}, reason="facility_repair")
    restored = min(damage, parts * REPAIR_PER_PART)
    facility.damage -= restored
    if known == restored:
        del state.repair_requests[facility.id]
    else:
        state.repair_requests[facility.id] = known - restored
    operation.delivered_at = state.world_time
    state.record("repaired", operation_id=operation.id, facility_id=facility.id, damage=facility.damage,
                 restored=restored, parts=parts)
