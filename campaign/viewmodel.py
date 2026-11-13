"""게임 상태를 바꾸지 않는 전략 지도 좌표·선택·표시 모델."""

from dataclasses import dataclass
import math

from .models import CampaignState
from .resources import TERMINAL_PHASES
from .routes import position


FACILITY_RADIUS_M = 110  # 보기 전용 부지 크기. 시설 프리팹의 충돌 경계가 아니다.


@dataclass
class Camera:
    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.offset_x, y * self.scale + self.offset_y

    def world(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale

    def fit(self, state: CampaignState, width: int, height: int, *, padding: float = 60) -> None:
        xs = [node.position.x for node in state.nodes.values()]
        ys = [node.position.y for node in state.nodes.values()]
        self.scale = min(max(1, width - 2 * padding) / max(1, max(xs) - min(xs)),
                         max(1, height - 2 * padding) / max(1, max(ys) - min(ys)), 8.0)
        self.offset_x = width / 2 - (min(xs) + max(xs)) / 2 * self.scale
        self.offset_y = height / 2 - (min(ys) + max(ys)) / 2 * self.scale

    def zoom(self, factor: float, x: float, y: float) -> None:
        world_x, world_y = self.world(x, y)
        self.scale = min(8.0, max(0.001, self.scale * factor))
        self.offset_x = x - world_x * self.scale
        self.offset_y = y - world_y * self.scale


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


def segment_distance(x, y, ax, ay, bx, by) -> float:
    length_squared = (bx - ax) ** 2 + (by - ay) ** 2
    fraction = (max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length_squared))
                if length_squared else 0.0)
    return math.hypot(x - ax - fraction * (bx - ax), y - ay - fraction * (by - ay))


def pick(state: CampaignState, camera: Camera, markers: dict[str, ConvoyMarker],
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


def world_clock(seconds: int) -> str:
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"D{days + 1:02d}  {hours:02d}:{minutes:02d}:{seconds:02d}"
