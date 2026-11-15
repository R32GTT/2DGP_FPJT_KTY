"""병력·차량·물자의 실제 소유 위치를 보존하는 지속 상태."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random


RESOURCE_TYPES = ("supplies", "ammunition", "repair_parts")
RULES_VERSION = 1
RULE_KEYS = {"planning_period_s", "consumption_period_s", "supplies_per_soldier",
             "loading_s", "unloading_s", "turnaround_s", "external_departure_interval_s",
             "external_cargo_limit", "escort_soldiers", "provisions_per_vehicle",
             "road_cost_per_vehicle", "minimum_export_batch", "export_provision_buffer"}


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
    require(type(state.world_time) is int and state.world_time >= 0, "잘못된 세계 시각")
    require(type(state.seed) is int and type(state.started) is bool, "잘못된 시드·시작 상태")
    require(type(state.rules_version) is int and state.rules_version == RULES_VERSION,
            "지원하지 않는 규칙 버전")
    require(type(state.next_id) is int and state.next_id > 0, "잘못된 ID 발급 상태")
    require(type(state.next_sequence) is int and state.next_sequence >= 0, "잘못된 사건 순번")
    require(type(state.initial_soldiers) is int and state.initial_soldiers >= 0, "잘못된 초기 병력")
    require(type(state.source.next_departure) is int and state.source.next_departure >= 0,
            "잘못된 외부 출발 시각")
    require(type(state.source.minimum_garrison) is int and state.source.minimum_garrison >= 0,
            "잘못된 외부 최소 주둔량")
    random.Random().setstate(state.rng_state)
    require(len(state.resource_priority) == len(RESOURCE_TYPES)
            and set(state.resource_priority) == set(RESOURCE_TYPES), "잘못된 자원 우선순위")
    require(state.source.node_id in state.nodes, "외부 공급원 노드 누락")
    require(state.demand.node_id in state.nodes, "외부 출하 노드 누락")
    require(state.demand.collection_facility_id in state.facilities, "집적 창고 누락")
    require(type(state.demand.period_s) is int and state.demand.period_s > 0, "잘못된 출하 주기")
    for mapping in (state.nodes, state.roads, state.regions, state.facilities,
                    state.units, state.vehicles, state.operations):
        require(all(key == item.id for key, item in mapping.items()), "ID와 키 불일치")
    positions = [node.position for node in state.nodes.values()]
    positions.extend(asset.position for asset in (*state.units.values(), *state.vehicles.values())
                     if asset.position is not None)
    for point in positions:
        require(isinstance(point.x, (int, float)) and math.isfinite(point.x)
                and isinstance(point.y, (int, float)) and math.isfinite(point.y)
                and type(point.floor) is int, "잘못된 좌표")
    for road in state.roads.values():
        require(road.start in state.nodes and road.end in state.nodes, "도로 노드 누락")
        require(math.isfinite(road.length_m) and road.length_m > 0
                and road.start != road.end and type(road.blocked) is bool, "잘못된 도로 상태")
    pairs = [frozenset((road.start, road.end)) for road in state.roads.values()]
    require(len(set(pairs)) == len(pairs), "중복 도로 연결")
    for region in state.regions.values():
        require(type(region.minimum_garrison) is int and region.minimum_garrison >= 0,
                "잘못된 최소 주둔량")
        require(region.owner in {"enemy", "player"}, "잘못된 지역 소유 진영")
        require(len(set(region.facility_ids)) == len(region.facility_ids), "시설 중복 참조")
        require(all(fid in state.facilities and state.facilities[fid].region_id == region.id
                    for fid in region.facility_ids), "지역 시설 참조 오류")
    inventories = [state.source.inventory, state.player_stock, state.demand.cargo,
                   state.demand.outstanding, state.demand.delivered]
    for facility in state.facilities.values():
        require(facility.region_id in state.regions, "시설 지역 누락")
        require(facility.id in state.regions[facility.region_id].facility_ids, "시설 역참조 누락")
        require(facility.node_id in state.nodes, "시설 노드 누락")
        require(type(facility.damage) is int and 0 <= facility.damage <= 100, "잘못된 시설 피해")
        require(all(facility.target_stock[name] >= facility.minimum_stock[name]
                    for name in RESOURCE_TYPES), "목표 비축량이 최소 비축량보다 적음")
        inventories.extend((facility.inventory, facility.minimum_stock, facility.target_stock))
    require(len({f.node_id for f in state.facilities.values()}) == len(state.facilities),
            "시설 노드 중복")
    for unit in state.units.values():
        require(type(unit.soldiers) is int and type(unit.losses) is int
                and unit.soldiers >= 0 and unit.losses >= 0, "잘못된 병력")
        require((unit.soldiers == 0) == (unit.status == "destroyed"), "병력 격파 상태 불일치")
    for vehicle in state.vehicles.values():
        require(type(vehicle.capacity) is int and vehicle.capacity > 0
                and math.isfinite(vehicle.speed_mps) and vehicle.speed_mps > 0, "잘못된 차량 제원")
    for asset in (*state.units.values(), *state.vehicles.values()):
        require(asset.status in {"available", "reserved", "in_transit", "destroyed"}, "잘못된 자산 상태")
        require(asset.node_id is None or asset.node_id in state.nodes, "자산 노드 누락")
        require(asset.operation_id is None or asset.operation_id in state.operations,
                "자산 작전 누락")
        require(asset.node_id is not None or asset.operation_id is not None
                or asset.position is not None, "자산 위치 누락")
    for operation in state.operations.values():
        require(operation.kind in {"supply", "export"}, "잘못된 작전 유형")
        require(operation.phase in {"loading", "outbound", "unloading", "turnaround", "returning",
                                    "completed", "cancelled", "stranded", "destroyed"}, "잘못된 작전 상태")
        require(operation.origin_id in state.facilities or operation.origin_id == state.source.id,
                "작전 출발지 누락")
        origin_node = (state.source.node_id if operation.origin_id == state.source.id
                       else state.facilities[operation.origin_id].node_id)
        require(operation.home_node == origin_node, "작전 귀환 위치 불일치")
        if operation.kind == "export":
            require(operation.destination_node == state.demand.node_id, "작전 출하 위치 불일치")
        else:
            require(operation.destination_id in state.facilities, "작전 목적지 누락")
            require(operation.destination_node == state.facilities[operation.destination_id].node_id,
                    "작전 목적지 위치 불일치")
        require(len(set(operation.vehicle_ids)) == len(operation.vehicle_ids)
                and all(vid in state.vehicles for vid in operation.vehicle_ids), "작전 차량 참조 오류")
        require(len(set(operation.unit_ids)) == len(operation.unit_ids)
                and all(uid in state.units for uid in operation.unit_ids), "작전 병력 참조 오류")
        require(all(node in state.nodes for node in operation.route), "작전 경로 노드 누락")
        require(type(operation.edge_index) is int and operation.edge_index >= 0, "잘못된 경로 순번")
        require(math.isfinite(operation.progress_m) and operation.progress_m >= 0, "잘못된 이동 진행도")
        if operation.route:
            require(operation.edge_index < len(operation.route), "경로 순번 초과")
            require(all(frozenset((a, b)) in pairs for a, b in zip(operation.route, operation.route[1:])),
                    "경로 도로 누락")
            if operation.edge_index == len(operation.route) - 1:
                require(operation.progress_m == 0, "도착 후 잔여 이동 거리")
            else:
                pair = frozenset(operation.route[operation.edge_index:operation.edge_index + 2])
                length = next(road.length_m for road in state.roads.values()
                              if frozenset((road.start, road.end)) == pair)
                require(operation.progress_m < length, "도로 길이를 초과한 이동")
        else:
            require(operation.phase in {"loading", "cancelled"} and operation.progress_m == 0,
                    "이동 경로 누락")
        require(all(rid in state.roads for rid in operation.known_blocked), "관측 도로 누락")
        for time in (operation.created_at, operation.departed_at,
                     operation.delivered_at, operation.completed_at):
            require(time is None or (type(time) is int and 0 <= time <= state.world_time),
                    "잘못된 작전 시각")
        require(operation.id.startswith("operation_") and operation.id[10:].isdigit()
                and 0 < int(operation.id[10:]) < state.next_id, "잘못된 작전 ID 발급 상태")
        inventories.extend((operation.cargo, operation.provisions))
    for key, reservation in state.reservations.items():
        require(key == reservation.operation_id and key in state.operations, "예약 작전 참조 오류")
        require(state.operations[key].phase == "loading"
                and state.operations[key].origin_id == reservation.owner_id, "예약 상태 불일치")
        inventories.extend((reservation.cargo, reservation.provisions))
    inventories.extend(vars(state.ledger).values())
    for inventory in inventories:
        require(set(inventory) == set(RESOURCE_TYPES), "잘못된 자원 종류")
        require(all(type(value) is int and value >= 0 for value in inventory.values()),
                "재고는 음수가 아닌 정수여야 함")
    require(RULE_KEYS <= set(state.rules)
            and all(type(value) is int and value > 0 for value in state.rules.values()),
            "운영 수치는 양의 정수여야 함")
    sequences = set()
    for index, event in enumerate(state.events):
        require(type(event.time) is int and event.time >= state.world_time, "과거 예정 사건")
        require(type(event.priority) is int and type(event.sequence) is int
                and 0 <= event.sequence < state.next_sequence
                and event.sequence not in sequences, "잘못된 사건 순번")
        sequences.add(event.sequence)
        require(index == 0 or state.events[(index - 1) // 2] <= event, "사건 큐 순서 오류")
        require(event.kind in {"planning", "consumption", "demand", "depart", "unload", "begin_return"},
                "지원하지 않는 예정 사건")
        if event.kind in {"depart", "unload", "begin_return"}:
            require(event.operation_id in state.operations, "예정 사건 작전 누락")
