"""병력·차량·물자의 실제 소유 위치를 보존하는 지속 상태."""

from __future__ import annotations

from dataclasses import dataclass, field
import random


RESOURCE_TYPES = ("supplies", "ammunition", "repair_parts")
RULES_VERSION = 1


def stock(**values: int) -> dict[str, int]:
    return {name: values.get(name, 0) for name in RESOURCE_TYPES}


@dataclass
class Position:
    x: float
    y: float
    floor: int = 0


@dataclass
class Node:
    id: str
    position: Position


@dataclass
class Road:
    id: str
    start: str
    end: str
    length_m: float
    blocked: bool = False


@dataclass
class Region:
    id: str
    facility_ids: list[str]
    minimum_garrison: int
    owner: str = "enemy"
    importance: int = 1


@dataclass
class Facility:
    id: str
    region_id: str
    node_id: str
    capabilities: list[str]
    inventory: dict[str, int]
    minimum_stock: dict[str, int]
    target_stock: dict[str, int]
    damage: int = 0


@dataclass
class Unit:
    id: str
    soldiers: int
    node_id: str | None
    faction: str = "enemy"
    operation_id: str | None = None
    status: str = "available"
    losses: int = 0
    position: Position | None = None


@dataclass
class Vehicle:
    id: str
    node_id: str | None
    capacity: int
    speed_mps: float
    operation_id: str | None = None
    status: str = "available"
    position: Position | None = None


@dataclass
class ExternalSource:
    id: str
    node_id: str
    inventory: dict[str, int]
    mode: str = "finite"
    next_departure: int = 0
    minimum_garrison: int = 0


@dataclass
class ExternalDemand:
    node_id: str
    cargo: dict[str, int]
    period_s: int
    collection_facility_id: str
    outstanding: dict[str, int] = field(default_factory=stock)
    delivered: dict[str, int] = field(default_factory=stock)


@dataclass
class Reservation:
    operation_id: str
    owner_id: str
    cargo: dict[str, int]
    provisions: dict[str, int]


@dataclass
class Operation:
    id: str
    reason: str
    kind: str
    origin_id: str
    destination_id: str
    home_node: str
    destination_node: str
    vehicle_ids: list[str]
    unit_ids: list[str]
    created_at: int
    phase: str = "loading"
    cargo: dict[str, int] = field(default_factory=stock)
    provisions: dict[str, int] = field(default_factory=stock)
    route: list[str] = field(default_factory=list)
    edge_index: int = 0
    progress_m: float = 0.0
    reversing: bool = False
    edge_paid: bool = False
    known_blocked: list[str] = field(default_factory=list)
    waiting_reason: str = ""
    departed_at: int | None = None
    delivered_at: int | None = None
    completed_at: int | None = None


@dataclass(order=True)
class Event:
    time: int
    priority: int
    sequence: int
    kind: str = field(compare=False)
    operation_id: str | None = field(default=None, compare=False)


@dataclass
class Ledger:
    initial: dict[str, int] = field(default_factory=stock)
    imported: dict[str, int] = field(default_factory=stock)
    consumed: dict[str, int] = field(default_factory=stock)
    destroyed: dict[str, int] = field(default_factory=stock)
    delivered: dict[str, int] = field(default_factory=stock)
    captured: dict[str, int] = field(default_factory=stock)
    returned_external: dict[str, int] = field(default_factory=stock)


@dataclass
class CampaignState:
    seed: int
    nodes: dict[str, Node]
    roads: dict[str, Road]
    regions: dict[str, Region]
    facilities: dict[str, Facility]
    units: dict[str, Unit]
    vehicles: dict[str, Vehicle]
    source: ExternalSource
    demand: ExternalDemand
    rules: dict[str, int]
    world_time: int = 0
    rules_version: int = RULES_VERSION
    next_id: int = 1
    next_sequence: int = 0
    rng_state: tuple = ()
    started: bool = False
    operations: dict[str, Operation] = field(default_factory=dict)
    reservations: dict[str, Reservation] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)
    player_stock: dict[str, int] = field(default_factory=stock)
    history: list[dict] = field(default_factory=list)
    initial_soldiers: int = 0
    initial_vehicle_ids: list[str] = field(default_factory=list)
    resource_priority: list[str] = field(default_factory=lambda: list(RESOURCE_TYPES))

    def new_id(self) -> str:
        result = f"operation_{self.next_id}"
        self.next_id += 1
        return result

    def choose(self, candidates: list[str]) -> str:
        # 화면·월드 생성의 난수 소비와 캠페인 난수를 분리한다.
        generator = random.Random()
        generator.setstate(self.rng_state)
        result = generator.choice(sorted(candidates))
        self.rng_state = generator.getstate()
        return result

    def record(self, kind: str, **details) -> None:
        self.history.append({"time": self.world_time, "kind": kind, **details})


def validate_state(state: CampaignState) -> None:
    """입력·저장 파일의 잘못된 참조와 음수 자원을 즉시 거부한다."""
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    require(state.source.mode in {"finite", "infinite"}, "잘못된 외부 지원 모드")
    require(state.world_time >= 0, "음수 세계 시각")
    require(len(state.resource_priority) == len(RESOURCE_TYPES)
            and set(state.resource_priority) == set(RESOURCE_TYPES), "잘못된 자원 우선순위")
    require(state.source.node_id in state.nodes, "외부 공급원 노드 누락")
    require(state.demand.node_id in state.nodes, "외부 출하 노드 누락")
    require(state.demand.collection_facility_id in state.facilities, "집적 창고 누락")
    require(type(state.demand.period_s) is int and state.demand.period_s > 0, "잘못된 출하 주기")
    for mapping in (state.nodes, state.roads, state.regions, state.facilities,
                    state.units, state.vehicles, state.operations):
        require(all(key == item.id for key, item in mapping.items()), "ID와 키 불일치")
    for road in state.roads.values():
        require(road.start in state.nodes and road.end in state.nodes, "도로 노드 누락")
        require(road.length_m > 0 and road.start != road.end, "잘못된 도로 거리")
    pairs = [frozenset((road.start, road.end)) for road in state.roads.values()]
    require(len(set(pairs)) == len(pairs), "중복 도로 연결")
    for region in state.regions.values():
        require(region.minimum_garrison >= 0, "음수 최소 주둔량")
        require(len(set(region.facility_ids)) == len(region.facility_ids), "시설 중복 참조")
        require(all(fid in state.facilities and state.facilities[fid].region_id == region.id
                    for fid in region.facility_ids), "지역 시설 참조 오류")
    inventories = [state.source.inventory, state.player_stock, state.demand.cargo,
                   state.demand.outstanding, state.demand.delivered]
    for facility in state.facilities.values():
        require(facility.region_id in state.regions, "시설 지역 누락")
        require(facility.id in state.regions[facility.region_id].facility_ids, "시설 역참조 누락")
        require(facility.node_id in state.nodes, "시설 노드 누락")
        require(0 <= facility.damage <= 100, "잘못된 시설 피해")
        inventories.extend((facility.inventory, facility.minimum_stock, facility.target_stock))
    for unit in state.units.values():
        require(unit.soldiers >= 0 and unit.losses >= 0, "음수 병력")
    for vehicle in state.vehicles.values():
        require(vehicle.capacity > 0 and vehicle.speed_mps > 0, "잘못된 차량 제원")
    for asset in (*state.units.values(), *state.vehicles.values()):
        require(asset.node_id is None or asset.node_id in state.nodes, "자산 노드 누락")
        require(asset.operation_id is None or asset.operation_id in state.operations,
                "자산 작전 누락")
        require(asset.node_id is not None or asset.operation_id is not None
                or asset.position is not None, "자산 위치 누락")
    for operation in state.operations.values():
        inventories.extend((operation.cargo, operation.provisions))
    for reservation in state.reservations.values():
        inventories.extend((reservation.cargo, reservation.provisions))
    inventories.extend(vars(state.ledger).values())
    for inventory in inventories:
        require(set(inventory) == set(RESOURCE_TYPES), "잘못된 자원 종류")
        require(all(type(value) is int and value >= 0 for value in inventory.values()),
                "재고는 음수가 아닌 정수여야 함")
    require(all(type(value) is int and value > 0 for value in state.rules.values()),
            "운영 수치는 양의 정수여야 함")
