"""예약은 표시, 출발은 이전, 소비·탈취·파괴·인도는 서로 다른 회계다."""

import math

from .models import (FOOT_OPERATIONS, TROOP_TRANSPORTS, CampaignState, Operation, Reservation, RESOURCE_TYPES,
                     TERMINAL_PHASES, stock, validate_state)



def quantities(values: dict[str, int]) -> dict[str, int]:
    if not isinstance(values, dict) or not set(values) <= set(RESOURCE_TYPES):
        raise ValueError("잘못된 자원 종류")
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("자원량은 음수가 아닌 정수여야 함")
    return stock(**values)


def owner(state: CampaignState, owner_id: str):
    return state.source if owner_id == state.source.id else state.facilities[owner_id]


def minimum(state: CampaignState, owner_id: str, name: str) -> int:
    return 0 if owner_id == state.source.id else state.facilities[owner_id].minimum_stock[name]


def available(state: CampaignState, owner_id: str, name: str) -> int | float:
    if owner_id == state.source.id and state.source.mode == "infinite":
        return math.inf
    reserved = sum(r.cargo[name] + r.provisions[name]
                   for r in state.reservations.values() if r.owner_id == owner_id)
    return owner(state, owner_id).inventory[name] - reserved


def _assets(state, operation):
    return ([state.vehicles[vid] for vid in operation.vehicle_ids]
            + [state.units[uid] for uid in operation.unit_ids])


def reserve(state: CampaignState, operation: Operation, cargo: dict[str, int],
            provisions: dict[str, int] | None = None) -> None:
    cargo, provisions = quantities(cargo), quantities(provisions or {})
    origin = owner(state, operation.origin_id)
    if operation.id in state.operations or operation.phase != "loading":
        raise ValueError("이미 존재하거나 예약할 수 없는 작전")
    on_foot = operation.kind in FOOT_OPERATIONS
    troop_transport = operation.kind in TROOP_TRANSPORTS
    if (origin.node_id != operation.home_node
            or (on_foot and (operation.vehicle_ids or not operation.unit_ids))
            or (not on_foot and not troop_transport and not sum(cargo.values()))):
        raise ValueError("출발 위치 또는 화물 오류")
    if (len(set(operation.vehicle_ids)) != len(operation.vehicle_ids)
            or len(set(operation.unit_ids)) != len(operation.unit_ids)):
        raise ValueError("중복 자산 배정")
    assets = _assets(state, operation)
    if any(a.status != "available" or a.operation_id is not None
           or a.node_id != origin.node_id for a in assets):
        raise ValueError("가용하지 않은 병력·차량")
    capacity = sum(state.vehicles[vid].capacity for vid in operation.vehicle_ids)
    if sum(cargo.values()) + sum(provisions.values()) > capacity:
        raise ValueError("차량 적재량 초과")
    soldiers = sum(state.units[uid].soldiers for uid in operation.unit_ids)
    if troop_transport:
        if (len(operation.vehicle_ids) != 1 or len(operation.unit_ids) != 1
                or not state.units[operation.unit_ids[0]].mobile
                or not 0 < soldiers <= state.vehicles[operation.vehicle_ids[0]].troop_seats):
            raise ValueError("수송할 병력 또는 좌석 부족")
    elif soldiers < state.rules["escort_soldiers"] * len(operation.vehicle_ids):
        raise ValueError("호위 병력 부족")
    if not on_foot and not troop_transport and any(not state.units[uid].can_escort for uid in operation.unit_ids):
        raise ValueError("시설 경계 병력은 수송 호위에 배정할 수 없음")
    if operation.origin_id == state.source.id:
        nodes, required = {state.source.node_id}, state.source.minimum_garrison
    else:
        region = state.regions[origin.region_id]
        nodes = {state.facilities[fid].node_id for fid in region.facility_ids}
        required = region.minimum_garrison
        if (region.owner != "enemy" or (origin.damage == 100 and operation.kind != "withdrawal")
                or (region.withdrawal_started_at is not None and operation.kind not in {"withdrawal", "repair"})):
            raise ValueError("출발 시설 사용 불가")
    remaining = sum(u.soldiers for u in state.units.values()
                    if u.node_id in nodes and u.status == "available") - soldiers
    if operation.kind != "withdrawal" and remaining < required:
        raise ValueError("최소 주둔 병력 침해")
    if not on_foot:
        for name in RESOURCE_TYPES:
            if available(state, origin.id, name) - cargo[name] - provisions[name] < minimum(state, origin.id, name):
                raise ValueError("가용 재고 또는 최소 비축량 부족")
    state.add_operation(operation)
    state.reservations[operation.id] = Reservation(operation.id, origin.id, cargo, provisions)
    for asset in assets:
        asset.status, asset.operation_id = "reserved", operation.id
    state.record("reserved", operation_id=operation.id)


def cancel(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if operation.phase != "loading":
        return False
    state.reservations.pop(operation_id, None)
    for asset in _assets(state, operation):
        if asset.status != "destroyed":
            asset.status, asset.operation_id = "available", None
    state.end_operation(operation, "cancelled")
    state.record("cancelled", operation_id=operation_id)
    return True


def reconcile_reservations(state: CampaignState, owner_id: str) -> None:
    # 소비·약탈로 부족해지면 가장 늦은 미출발 예약부터 해제한다.
    for operation_id in reversed(list(state.reservations)):
        reservation = state.reservations[operation_id]
        if reservation.owner_id != owner_id or state.operations[operation_id].kind in FOOT_OPERATIONS:
            continue
        if any(available(state, owner_id, name) < minimum(state, owner_id, name)
               for name in RESOURCE_TYPES):
            cancel(state, operation_id)


def depart(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if operation.phase != "loading" or operation_id not in state.reservations:
        return False
    origin = owner(state, operation.origin_id)
    if operation.origin_id == state.source.id:
        nodes, required = {origin.node_id}, origin.minimum_garrison
    else:
        region = state.regions[origin.region_id]
        nodes = {state.facilities[fid].node_id for fid in region.facility_ids}
        required = region.minimum_garrison
    escorts = sum(state.units[uid].soldiers for uid in operation.unit_ids)
    remaining = sum(u.soldiers for u in state.units.values()
                    if u.node_id in nodes and u.status == "available")
    required_crew = 1 if operation.kind in TROOP_TRANSPORTS else state.rules["escort_soldiers"] * len(operation.vehicle_ids)
    if ((operation.kind != "withdrawal" and remaining < required)
            or escorts < required_crew
            or (operation.kind in FOOT_OPERATIONS and not escorts)
            or any(a.status != "reserved" or a.operation_id != operation.id
                   for a in _assets(state, operation))):
        cancel(state, operation_id)
        return False
    reconcile_reservations(state, operation.origin_id)
    if operation.phase != "loading":
        return False
    reservation = state.reservations.pop(operation_id)
    for name in RESOURCE_TYPES:
        amount = reservation.cargo[name] + reservation.provisions[name]
        if operation.origin_id == state.source.id and state.source.mode == "infinite":
            state.ledger.imported[name] += amount
        else:
            owner(state, operation.origin_id).inventory[name] -= amount
    operation.cargo, operation.provisions = dict(reservation.cargo), dict(reservation.provisions)
    operation.phase, operation.departed_at = "outbound", state.world_time
    for asset in _assets(state, operation):
        asset.node_id, asset.status = None, "in_transit"
    state.record("departed", operation_id=operation_id, cargo=dict(operation.cargo))
    return True


def consume(state: CampaignState, inventory: dict[str, int], requested: dict[str, int],
            *, reason: str, owner_id: str | None = None) -> dict[str, int]:
    requested = quantities(requested)
    used = stock()
    for name in RESOURCE_TYPES:
        used[name] = min(requested[name], inventory[name])
        inventory[name] -= used[name]
        state.ledger.consumed[name] += used[name]
    if owner_id is not None:
        reconcile_reservations(state, owner_id)
    if any(used.values()):
        state.record("consumed", reason=reason, owner_id=owner_id, amount=used)
    return used


def issue_ammunition(state: CampaignState, facility_id: str, requested: int) -> int:
    """수송에 예약된 물량은 보존하고 지급한 탄약만 전술 재고로 넘긴다."""
    if type(requested) is not int or requested < 0:
        raise ValueError("지급 탄약은 음수가 아닌 정수여야 함")
    facility = state.facilities[facility_id]
    if facility.damage == 100 or state.regions[facility.region_id].owner != "enemy":
        return 0
    amount = min(requested, max(0, available(state, facility_id, "ammunition")
                                   - minimum(state, facility_id, "ammunition")))
    if not amount:
        return 0
    return consume(state, facility.inventory, {"ammunition": amount},
                   reason="garrison_ammunition", owner_id=facility_id)["ammunition"]


def remove(state: CampaignState, inventory: dict[str, int], amount: dict[str, int],
           *, outcome: str, owner_id: str | None = None) -> None:
    amount = quantities(amount)
    if outcome not in {"captured", "destroyed"}:
        raise ValueError("잘못된 물자 처리 결과")
    if any(amount[name] > inventory[name] for name in RESOURCE_TYPES):
        raise ValueError("실제 보유량을 초과하는 물자 처리")
    for name in RESOURCE_TYPES:
        inventory[name] -= amount[name]
        getattr(state.ledger, outcome)[name] += amount[name]
        if outcome == "captured":
            state.player_stock[name] += amount[name]
    if owner_id is not None:
        reconcile_reservations(state, owner_id)
    state.record(outcome, owner_id=owner_id, amount=amount)


def handover(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if operation.phase != "unloading" or operation.delivered_at is not None:
        return False
    if (not operation.route or operation.edge_index != len(operation.route) - 1
            or operation.route[-1] != operation.destination_node or operation.progress_m):
        return False
    cargo = dict(operation.cargo)
    if operation.kind == "export":
        if operation.destination_node != state.demand.node_id:
            return False
        for name in RESOURCE_TYPES:
            state.demand.outstanding[name] = max(0, state.demand.outstanding[name] - cargo[name])
            state.demand.delivered[name] += cargo[name]
            state.ledger.delivered[name] += cargo[name]
            operation.cargo[name] = 0
    else:
        destination = state.facilities[operation.destination_id]
        if destination.damage == 100 or state.regions[destination.region_id].owner != "enemy":
            operation.waiting_reason = "destination_unavailable"
            return False
        for name in RESOURCE_TYPES:
            destination.inventory[name] += cargo[name]
            operation.cargo[name] = 0
    operation.delivered_at, operation.phase = state.world_time, "turnaround"
    state.record("exported" if operation.kind == "export" else "handed_over",
                 operation_id=operation_id, cargo=cargo)
    return True


def finish_return(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if (operation.phase != "returning" or not operation.route
            or operation.edge_index != len(operation.route) - 1
            or operation.route[-1] != operation.home_node
            or operation.progress_m != 0):
        return False
    for name in RESOURCE_TYPES:
        amount = operation.cargo[name] + operation.provisions[name]
        if operation.origin_id == state.source.id and state.source.mode == "infinite":
            state.ledger.returned_external[name] += amount
        else:
            owner(state, operation.origin_id).inventory[name] += amount
        operation.cargo[name] = operation.provisions[name] = 0
    for asset in _assets(state, operation):
        if asset.status != "destroyed":
            asset.node_id, asset.operation_id, asset.status = operation.home_node, None, "available"
    state.end_operation(operation, "completed")
    operation.waiting_reason = ""
    state.record("returned", operation_id=operation_id)
    return True


def finish_relocation(state: CampaignState, operation: Operation) -> None:
    """도착한 부대를 목적지의 실제 주둔 병력으로 편입한다."""
    soldiers = sum(state.units[key].soldiers for key in operation.unit_ids)
    for asset in _assets(state, operation):
        if asset.status != "destroyed":
            asset.node_id, asset.operation_id, asset.status = operation.destination_node, None, "available"
    destination = owner(state, operation.destination_id)
    for name in RESOURCE_TYPES:
        destination.inventory[name] += operation.cargo[name] + operation.provisions[name]
        operation.cargo[name] = operation.provisions[name] = 0
    state.end_operation(operation, "completed")
    operation.waiting_reason = ""
    operation.delivered_at = state.world_time
    state.record(f"{operation.kind}_arrived", operation_id=operation.id, facility_id=operation.destination_id,
                 soldiers=soldiers)


def material_totals(state: CampaignState, *, include_history=True) -> dict[str, int]:
    inventories = [f.inventory for f in state.facilities.values()] + [state.player_stock]
    if state.source.mode == "finite":
        inventories.append(state.source.inventory)
    operations = state.operations.values() if include_history else state.active_operations()
    for operation in operations:
        inventories.extend((operation.cargo, operation.provisions))
    return {name: sum(inventory[name] for inventory in inventories) for name in RESOURCE_TYPES}


def assert_invariants(state: CampaignState, *, include_history=True) -> None:
    validate_state(state, include_history=include_history)
    if state.demand.delivered != state.ledger.delivered:
        raise ValueError("외부 인도 실적과 자원 회계 불일치")
    totals = material_totals(state, include_history=include_history)
    for name in RESOURCE_TYPES:
        accounted = (totals[name] + state.ledger.consumed[name] + state.ledger.destroyed[name]
                     + state.ledger.delivered[name] + state.ledger.returned_external[name])
        if state.ledger.initial[name] + state.ledger.imported[name] != accounted:
            raise ValueError(f"자원 보존 위반: {name}")
    if sum(u.soldiers + u.losses for u in state.units.values()) != state.initial_soldiers:
        raise ValueError("병력 보존 위반")
    if sum(unit.night_kits for unit in state.units.values()) > state.ledger.consumed["night_gear"]:
        raise ValueError("지급 야간 장비 보존 위반")
    if sorted(state.vehicles) != state.initial_vehicle_ids:
        raise ValueError("차량 ID 보존 위반")
    for reservation in state.reservations.values():
        if state.operations[reservation.operation_id].kind in FOOT_OPERATIONS:
            continue
        if any(available(state, reservation.owner_id, name) < minimum(state, reservation.owner_id, name)
               for name in RESOURCE_TYPES):
            raise ValueError("예약 재고 초과")
    assigned = set()
    operations = state.operations.values() if include_history else state.active_operations()
    for operation in operations:
        if operation.phase in TERMINAL_PHASES:
            if any(operation.cargo.values()) or any(operation.provisions.values()):
                raise ValueError("종료 작전에 남은 화물")
            continue
        if (operation.phase == "loading") != (operation.id in state.reservations):
            raise ValueError("작전 예약 누락 또는 중복")
        for asset in _assets(state, operation):
            if asset.status == "destroyed":
                continue
            key = (type(asset).__name__, asset.id)
            if key in assigned or asset.operation_id != operation.id:
                raise ValueError("자산 중복 배정 또는 작전 불일치")
            assigned.add(key)
        capacity = sum(state.vehicles[vid].capacity for vid in operation.vehicle_ids
                       if state.vehicles[vid].status != "destroyed")
        reservation = state.reservations.get(operation.id)
        load = (sum(reservation.cargo.values()) + sum(reservation.provisions.values())
                if reservation else sum(operation.cargo.values()) + sum(operation.provisions.values()))
        if load > capacity:
            raise ValueError("운송 적재량 초과")
    for asset in (*state.units.values(), *state.vehicles.values()):
        if asset.status == "destroyed":
            continue
        if asset.operation_id is not None:
            if (type(asset).__name__, asset.id) not in assigned:
                raise ValueError("작전에 속하지 않는 배정 자산")
            operation = state.operations[asset.operation_id]
            if operation.phase == "loading":
                if asset.status != "reserved" or asset.node_id != operation.home_node:
                    raise ValueError("예약 자산의 위치·상태 불일치")
            elif asset.status != "in_transit" or asset.node_id is not None:
                raise ValueError("이동 자산의 위치·상태 불일치")
        elif asset.status != "available" or asset.node_id is None:
            raise ValueError("가용 자산의 위치·상태 불일치")
