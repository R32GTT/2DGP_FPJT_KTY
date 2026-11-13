"""예약은 표시, 출발은 이전, 소비·탈취·파괴·인도는 서로 다른 회계다."""

import math

from .models import CampaignState, Operation, Reservation, RESOURCE_TYPES, stock, validate_state


TERMINAL_PHASES = {"completed", "cancelled"}


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
    if origin.node_id != operation.home_node or not sum(cargo.values()):
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
    if soldiers < state.rules["escort_soldiers"] * len(operation.vehicle_ids):
        raise ValueError("호위 병력 부족")
    if operation.origin_id == state.source.id:
        nodes, required = {state.source.node_id}, state.source.minimum_garrison
    else:
        region = state.regions[origin.region_id]
        nodes = {state.facilities[fid].node_id for fid in region.facility_ids}
        required = region.minimum_garrison
        if origin.damage == 100 or region.owner != "enemy":
            raise ValueError("출발 시설 사용 불가")
    remaining = sum(u.soldiers for u in state.units.values()
                    if u.node_id in nodes and u.status == "available") - soldiers
    if remaining < required:
        raise ValueError("최소 주둔 병력 침해")
    for name in RESOURCE_TYPES:
        if available(state, origin.id, name) - cargo[name] - provisions[name] < minimum(state, origin.id, name):
            raise ValueError("가용 재고 또는 최소 비축량 부족")
    state.operations[operation.id] = operation
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
    operation.phase, operation.completed_at = "cancelled", state.world_time
    state.record("cancelled", operation_id=operation_id)
    return True


def reconcile_reservations(state: CampaignState, owner_id: str) -> None:
    # 소비·약탈로 부족해지면 가장 늦은 미출발 예약부터 해제한다.
    for operation_id in reversed(list(state.reservations)):
        reservation = state.reservations[operation_id]
        if reservation.owner_id != owner_id:
            continue
        if any(available(state, owner_id, name) < minimum(state, owner_id, name)
               for name in RESOURCE_TYPES):
            cancel(state, operation_id)


def depart(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if operation.phase != "loading" or operation_id not in state.reservations:
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
    destination = state.facilities[operation.destination_id]
    if destination.damage == 100 or state.regions[destination.region_id].owner != "enemy":
        operation.waiting_reason = "destination_unavailable"
        return False
    for name in RESOURCE_TYPES:
        destination.inventory[name] += operation.cargo[name]
        operation.cargo[name] = 0
    operation.delivered_at, operation.phase = state.world_time, "turnaround"
    state.record("handed_over", operation_id=operation_id)
    return True


def finish_return(state: CampaignState, operation_id: str) -> bool:
    operation = state.operations[operation_id]
    if (operation.phase != "returning" or not operation.route
            or operation.route[operation.edge_index] != operation.home_node
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
    operation.phase, operation.completed_at = "completed", state.world_time
    operation.waiting_reason = ""
    state.record("returned", operation_id=operation_id)
    return True


def material_totals(state: CampaignState) -> dict[str, int]:
    inventories = [f.inventory for f in state.facilities.values()] + [state.player_stock]
    if state.source.mode == "finite":
        inventories.append(state.source.inventory)
    for operation in state.operations.values():
        inventories.extend((operation.cargo, operation.provisions))
    return {name: sum(inventory[name] for inventory in inventories) for name in RESOURCE_TYPES}


def assert_invariants(state: CampaignState) -> None:
    validate_state(state)
    totals = material_totals(state)
    for name in RESOURCE_TYPES:
        accounted = (totals[name] + state.ledger.consumed[name] + state.ledger.destroyed[name]
                     + state.ledger.delivered[name] + state.ledger.returned_external[name])
        if state.ledger.initial[name] + state.ledger.imported[name] != accounted:
            raise ValueError(f"자원 보존 위반: {name}")
    if sum(u.soldiers + u.losses for u in state.units.values()) != state.initial_soldiers:
        raise ValueError("병력 보존 위반")
    if sorted(state.vehicles) != state.initial_vehicle_ids:
        raise ValueError("차량 ID 보존 위반")
    for reservation in state.reservations.values():
        if any(available(state, reservation.owner_id, name) < minimum(state, reservation.owner_id, name)
               for name in RESOURCE_TYPES):
            raise ValueError("예약 재고 초과")
    assigned = set()
    for operation in state.operations.values():
        if operation.phase in TERMINAL_PHASES:
            continue
        for asset in _assets(state, operation):
            if asset.status == "destroyed":
                continue
            key = (type(asset).__name__, asset.id)
            if key in assigned or asset.operation_id != operation.id:
                raise ValueError("자산 중복 배정 또는 작전 불일치")
            assigned.add(key)
        capacity = sum(state.vehicles[vid].capacity for vid in operation.vehicle_ids
                       if state.vehicles[vid].status != "destroyed")
        if sum(operation.cargo.values()) + sum(operation.provisions.values()) > capacity:
            raise ValueError("운송 적재량 초과")
