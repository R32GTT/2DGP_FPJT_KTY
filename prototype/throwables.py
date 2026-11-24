"""투척물 데이터와 공통 이동·발동 흐름. 개별 효과는 호출자가 처리한다."""

from dataclasses import dataclass
import json
import math
from pathlib import Path


DATA_PATH = Path(__file__).with_name("data") / "throwables.json"
BURST_SECONDS = 0.24


def _number(value, *, minimum=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


@dataclass(frozen=True)
class ThrowableEffect:
    kind: str
    radius: float
    duration: float = 0
    noise_radius: float = 0

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind.strip() or not all(
                _number(value) for value in (self.radius, self.duration, self.noise_radius)):
            raise ValueError("잘못된 투척 효과 정의")


@dataclass(frozen=True)
class ThrowableSpec:
    id: str
    name: str
    weight: float
    throw_range: float
    speed: float
    fuse: float
    effect: ThrowableEffect
    warehouse_quantity: int = 0

    def __post_init__(self):
        if (not all(isinstance(value, str) and value.strip() for value in (self.id, self.name))
                or not all(_number(value) and value > 0 for value in (self.weight, self.throw_range, self.speed))
                or not _number(self.fuse) or not isinstance(self.effect, ThrowableEffect)
                or type(self.warehouse_quantity) is not int or self.warehouse_quantity < 0):
            raise ValueError(f"잘못된 투척물 정의: {self.id}")


def load_throwables(path=None):
    source = DATA_PATH if path is None else Path(path)
    if path is None and not source.exists():
        return {}
    rows = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("투척물 정의는 배열이어야 함")
    result = {}
    for row in rows:
        spec = ThrowableSpec(**{**row, "effect": ThrowableEffect(**row["effect"])})
        if spec.id in result:
            raise ValueError(f"중복 투척물 ID: {spec.id}")
        result[spec.id] = spec
    return result


@dataclass
class Flight:
    spec: ThrowableSpec
    source: str
    floor: int
    position: tuple[float, float]
    target: tuple[float, float]
    updated_at: float
    trigger_at: float
    team: str


@dataclass(frozen=True)
class Activation:
    spec: ThrowableSpec
    source: str
    floor: int
    position: tuple[float, float]
    time: float
    team: str


@dataclass(frozen=True)
class SmokeCloud:
    floor: int
    position: tuple[float, float]
    radius: float
    expires_at: float


class Throwables:
    def __init__(self, specs):
        self.specs = specs
        self.flights = []
        self.smoke = []

    def launch(self, key, *, source, floor, origin, target, time, team=""):
        spec = self.specs[key]
        if (len(origin) != 2 or len(target) != 2 or not _number(time)
                or not all(_number(value, minimum=-math.inf) for value in (*origin, *target))):
            raise ValueError("잘못된 투척 위치 또는 시각")
        distance = math.dist(origin, target)
        if distance > spec.throw_range:
            fraction = spec.throw_range / distance
            target = tuple(start + (end - start) * fraction for start, end in zip(origin, target))
        self.flights.append(Flight(spec, source, floor, tuple(origin), tuple(target), time, time + spec.fuse, team))

    def update(self, time, trace):
        """trace(floor, start, end)는 첫 장애물의 선분 비율이나 None을 반환한다."""
        if not _number(time):
            raise ValueError("잘못된 투척 시각")
        if self.smoke:
            self.smoke[:] = [cloud for cloud in self.smoke if time < cloud.expires_at]
        active, activations = [], []
        for flight in self.flights:
            end_time = min(time, flight.trigger_at)
            elapsed = max(0.0, end_time - flight.updated_at)
            distance = math.dist(flight.position, flight.target)
            if distance and elapsed:
                start = flight.position
                step = min(1.0, elapsed * flight.spec.speed / distance)
                end = tuple(a + (b - a) * step for a, b in zip(start, flight.target))
                hit = trace(flight.floor, start, end)
                if hit is not None:
                    # 장애물 표면 바로 앞에서 멈추고 남은 신관 시간은 계속 흐른다.
                    fraction = max(0.0, hit - 0.001 / (distance * step))
                    end = tuple(a + (b - a) * fraction for a, b in zip(start, end))
                    flight.target = end
                flight.position = end
                if step == 1.0:
                    flight.target = end
            flight.updated_at = max(flight.updated_at, end_time)
            if time >= flight.trigger_at:
                activations.append(Activation(flight.spec, flight.source, flight.floor,
                                              flight.position, flight.trigger_at, flight.team))
            else:
                active.append(flight)
        self.flights = active
        return sorted(activations, key=lambda item: item.time)

    def snapshot(self):
        return {"flights": [{**vars(flight), "spec": flight.spec.id} for flight in self.flights],
                "smoke": [vars(cloud) for cloud in self.smoke]}

    def restore(self, saved, time):
        def point(value):
            if len(value) != 2 or not all(_number(coord, minimum=-math.inf) for coord in value):
                raise ValueError("잘못된 투척물 저장 위치")
            return tuple(value)

        flights = [Flight(**{**row, "spec": self.specs[row["spec"]],
                             "position": point(row["position"]), "target": point(row["target"])})
                   for row in saved["flights"]]
        smoke = [SmokeCloud(**{**row, "position": point(row["position"])}) for row in saved["smoke"]]
        if any(type(flight.floor) is not int or not isinstance(flight.source, str)
               or not isinstance(flight.team, str) or not _number(flight.updated_at)
               or not _number(flight.trigger_at) or not flight.updated_at <= time < flight.trigger_at for flight in flights):
            raise ValueError("잘못된 투척물 비행 상태")
        if any(type(cloud.floor) is not int or not _number(cloud.radius) or cloud.radius <= 0
               or not _number(cloud.expires_at) or cloud.expires_at <= time for cloud in smoke):
            raise ValueError("잘못된 연막 상태")
        self.flights = flights
        self.smoke[:] = smoke
