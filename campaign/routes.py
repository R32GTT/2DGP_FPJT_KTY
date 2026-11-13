"""도로 거리 기반 이동. 차단은 현장에서 발견하며 진행 위치를 보존한다."""

import heapq

from .models import CampaignState, Operation, Position, Road
from .resources import consume


def road_between(state: CampaignState, start: str, end: str) -> Road:
    for road in state.roads.values():
        if {road.start, road.end} == {start, end}:
            return road
    raise ValueError(f"연결 도로 없음: {start} -> {end}")


def find_route(state: CampaignState, start: str, end: str,
               known_blocked=()) -> list[str]:
    if start not in state.nodes or end not in state.nodes:
        raise ValueError("경로 노드 누락")
    blocked = set(known_blocked)
    graph = {node: [] for node in state.nodes}
    for road in sorted(state.roads.values(), key=lambda item: item.id):
        # 실제 차단 여부는 관측 전까지 경로 계획에 유출하지 않는다.
        if road.id not in blocked:
            graph[road.start].append((road.end, road.length_m))
            graph[road.end].append((road.start, road.length_m))
    pending = [(0.0, start, [start])]
    distances = {start: 0.0}
    while pending:
        distance, node, path = heapq.heappop(pending)
        if distance != distances[node]:
            continue
        if node == end:
            return path
        for neighbor, length in graph[node]:
            candidate = distance + length
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                heapq.heappush(pending, (candidate, neighbor, path + [neighbor]))
    return []


def position(state: CampaignState, operation: Operation) -> Position:
    if not operation.route:
        return state.nodes[operation.home_node].position
    start = state.nodes[operation.route[operation.edge_index]].position
    if operation.progress_m == 0 or operation.edge_index == len(operation.route) - 1:
        return Position(start.x, start.y, start.floor)
    end_id = operation.route[operation.edge_index + 1]
    end = state.nodes[end_id].position
    road = road_between(state, operation.route[operation.edge_index], end_id)
    fraction = operation.progress_m / road.length_m
    return Position(start.x + (end.x - start.x) * fraction,
                    start.y + (end.y - start.y) * fraction, start.floor)


def set_route(state: CampaignState, operation: Operation, *, returning: bool = False) -> bool:
    if operation.progress_m:
        raise ValueError("도로 중간에서는 경로를 교체할 수 없음")
    current = operation.route[operation.edge_index] if operation.route else operation.home_node
    target = operation.home_node if returning else operation.destination_node
    operation.phase = "returning" if returning else "outbound"
    operation.route = find_route(state, current, target, operation.known_blocked) or [current]
    operation.edge_index, operation.progress_m = 0, 0.0
    operation.reversing = operation.edge_paid = False
    found = operation.route[-1] == target
    operation.waiting_reason = "" if found else "route_unavailable"
    return found


def request_return(state: CampaignState, operation: Operation) -> None:
    if operation.phase not in {"outbound", "unloading", "turnaround", "returning"}:
        raise ValueError("귀환할 수 없는 작전 상태")
    operation.phase = "returning"
    operation.waiting_reason = ""
    if operation.progress_m:
        operation.reversing = True
    else:
        set_route(state, operation, returning=True)


def _replan(state: CampaignState, operation: Operation) -> bool:
    current = operation.route[operation.edge_index]
    operation.known_blocked = [rid for rid in operation.known_blocked
                              if state.roads[rid].blocked
                              or current not in {state.roads[rid].start, state.roads[rid].end}]
    returning = operation.phase == "returning"
    if set_route(state, operation, returning=returning):
        return True
    if not returning:
        # 목적지 단절 시 남은 화물과 자산을 실제 경로로 회수한다.
        state.record("transport_aborted", operation_id=operation.id, reason="route_unavailable")
        return set_route(state, operation, returning=True)
    return False


def move_one_second(state: CampaignState, operation: Operation) -> bool:
    """현재 이동 구간의 끝에 도달하면 True. 작업·인계는 호출자가 처리한다."""
    if operation.phase not in {"outbound", "returning"}:
        return False
    vehicles = [state.vehicles[vid] for vid in operation.vehicle_ids
                if state.vehicles[vid].status != "destroyed"]
    if not vehicles:
        operation.waiting_reason = "no_vehicle"
        return False
    if not operation.route:
        set_route(state, operation, returning=operation.phase == "returning")
    budget = min(vehicle.speed_mps for vehicle in vehicles)
    while budget > 0:
        target = operation.home_node if operation.phase == "returning" else operation.destination_node
        if operation.edge_index == len(operation.route) - 1:
            if operation.route[-1] == target:
                operation.waiting_reason = ""
                return True
            if not _replan(state, operation):
                return False
            continue
        road = road_between(state, operation.route[operation.edge_index],
                            operation.route[operation.edge_index + 1])
        if road.blocked and not operation.reversing:
            if road.id not in operation.known_blocked:
                operation.known_blocked.append(road.id)
                state.record("road_discovered", operation_id=operation.id, road_id=road.id)
            if operation.progress_m:
                operation.reversing = True
            else:
                if not _replan(state, operation):
                    return False
                continue
        if operation.reversing:
            distance = min(budget, operation.progress_m)
            operation.progress_m -= distance
            budget -= distance
            if operation.progress_m == 0:
                operation.reversing = operation.edge_paid = False
                if not _replan(state, operation):
                    return False
                target = operation.home_node if operation.phase == "returning" else operation.destination_node
                if operation.route == [target]:
                    return True
            continue
        if not operation.edge_paid:
            cost = len(vehicles) * state.rules["road_cost_per_vehicle"]
            if operation.provisions["supplies"] < cost:
                operation.waiting_reason = "no_provisions"
                return False
            consume(state, operation.provisions, {"supplies": cost}, reason="vehicle_operation")
            operation.edge_paid = True
        operation.waiting_reason = ""
        distance = min(budget, road.length_m - operation.progress_m)
        operation.progress_m += distance
        budget -= distance
        if operation.progress_m >= road.length_m:
            operation.edge_index += 1
            operation.progress_m = 0.0
            operation.edge_paid = False
            if operation.edge_index == len(operation.route) - 1:
                return True
    return False
