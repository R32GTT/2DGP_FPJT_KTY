"""선택 항목과 UI 입력을 기존 코어의 행동으로 변환한다."""

from .models import RESOURCE_TYPES
from .resources import TERMINAL_PHASES, owner


def selection_stock(state, selection):
    if selection is None:
        return None
    kind, key = selection
    if kind == "facility":
        return state.facilities[key].inventory
    if kind != "operation":
        return None
    operation = state.operations[key]
    if operation.phase in TERMINAL_PHASES:
        return None
    if operation.phase == "loading":
        if operation.origin_id == state.source.id and state.source.mode == "infinite":
            return None
        return owner(state, operation.origin_id).inventory
    return operation.cargo


def allowed_commands(state, selection) -> set[str]:
    if selection is None:
        return set()
    kind, key = selection
    if kind == "road":
        return {"toggle_road"}
    result = set()
    inventory = selection_stock(state, selection)
    if inventory is not None and any(inventory.values()):
        result.update(("capture", "destroy_stock"))
    if kind == "facility":
        if state.facilities[key].damage < 100:
            result.add("damage_facility")
        return result
    operation = state.operations[key]
    if operation.phase in TERMINAL_PHASES:
        return result
    if operation.phase != "stranded":
        result.add("return_operation")
    if any(state.vehicles[vid].status != "destroyed" for vid in operation.vehicle_ids):
        result.add("destroy_vehicle")
    if any(state.units[uid].soldiers for uid in operation.unit_ids):
        result.add("damage_unit")
    return result


def build_action(state, selection, command: str, *, resource="supplies", amount=10) -> dict:
    if command not in allowed_commands(state, selection):
        raise ValueError("현재 선택·상태에서 사용할 수 없는 행동")
    kind, key = selection
    if command == "toggle_road":
        return {"type": "block_road", "road_id": key, "blocked": not state.roads[key].blocked}
    if command in {"capture", "destroy_stock"}:
        if resource not in RESOURCE_TYPES or type(amount) is not int or amount <= 0:
            raise ValueError("물자 종류와 양의 정수 수량 필요")
        if selection_stock(state, selection)[resource] < amount:
            raise ValueError("선택 대상의 실제 물자가 입력 수량보다 적음")
        return {"type": command, "owner_id" if kind == "facility" else "operation_id": key,
                "amount": {resource: amount}}
    if command == "damage_facility":
        return {"type": command, "facility_id": key, "damage": 25}
    if command == "return_operation":
        return {"type": command, "operation_id": key}
    operation = state.operations[key]
    if command == "destroy_vehicle":
        vehicle_id = next(vid for vid in operation.vehicle_ids if state.vehicles[vid].status != "destroyed")
        return {"type": command, "vehicle_id": vehicle_id}
    unit_id = next(uid for uid in operation.unit_ids if state.units[uid].soldiers)
    return {"type": "damage_unit", "unit_id": unit_id, "soldiers": 1}
