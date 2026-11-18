"""이전 전략 뷰어의 선택·표시 모델."""

from dataclasses import dataclass
import math

from .models import CampaignState
from .resources import TERMINAL_PHASES
from .routes import position
from prototype.geometry import segment_distance


FACILITY_RADIUS_M = 110  # 보기 전용 부지 크기. 시설 프리팹의 충돌 경계가 아니다.


@dataclass(frozen=True)
class ConvoyMarker:
    id: str
    x: float
    y: float
    heading: float
    phase: str
    kind: str
    vehicles: int
    soldiers: int
    cargo: int
    waiting: str
    edge: tuple | None


def convoy_markers(state: CampaignState) -> dict[str, ConvoyMarker]:
    result = {}
    for operation in state.operations.values():
        if operation.phase in TERMINAL_PHASES or operation.phase == "loading":
            continue
        point = position(state, operation)
        heading, edge = 0.0, None
        if operation.route and operation.edge_index < len(operation.route) - 1:
            start_id, end_id = operation.route[operation.edge_index:operation.edge_index + 2]
            start, end = state.nodes[start_id].position, state.nodes[end_id].position
            heading = math.atan2(end.y - start.y, end.x - start.x)
            if operation.reversing:
                heading += math.pi
            edge = (start_id, end_id, operation.reversing)
        result[operation.id] = ConvoyMarker(
            operation.id, point.x, point.y, heading, operation.phase, operation.kind,
            sum(state.vehicles[vid].status != "destroyed" for vid in operation.vehicle_ids),
            sum(state.units[uid].soldiers for uid in operation.unit_ids),
            sum(operation.cargo.values()), operation.waiting_reason, edge)
    return result


def pick(state: CampaignState, camera, markers: dict[str, ConvoyMarker],
         x: float, y: float) -> tuple[str, str] | None:
    candidates = []
    for marker in markers.values():
        sx, sy = camera.screen(marker.x, marker.y)
        distance = math.hypot(sx - x, sy - y)
        if distance <= 16:
            candidates.append((distance, marker.id))
    if candidates:
        return "operation", min(candidates)[1]
    for facility in state.facilities.values():
        point = state.nodes[facility.node_id].position
        sx, sy = camera.screen(point.x, point.y)
        radius = max(17, FACILITY_RADIUS_M * camera.scale)
        if abs(sx - x) <= radius and abs(sy - y) <= radius:
            return "facility", facility.id
    closest = (float("inf"), "")
    for road in state.roads.values():
        start, end = state.nodes[road.start].position, state.nodes[road.end].position
        ax, ay = camera.screen(start.x, start.y)
        bx, by = camera.screen(end.x, end.y)
        closest = min(closest, (segment_distance(x, y, ax, ay, bx, by), road.id))
    return ("road", closest[1]) if closest[0] <= 8 else None
