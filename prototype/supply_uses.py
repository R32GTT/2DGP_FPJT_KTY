"""새 탄종·장비의 보급 비용을 데이터에서 읽는다."""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from campaign.models import RESOURCE_TYPES


DATA_PATH = Path(__file__).parent / "data/supply_uses.json"


@dataclass(frozen=True)
class AmmoUse:
    id: str
    name: str
    round_weight: float
    rounds: int
    ammunition_cost: int


@dataclass(frozen=True)
class EquipmentUse:
    id: str
    name: str
    weight: float
    cost: dict[str, int]
    armor_bonus: int = 0


def load_supply_uses(path=DATA_PATH):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(data) != {"ammo", "equipment", "armor_repair_per_part"}:
        raise ValueError("보급 사용 데이터 필드 오류")
    ammo, equipment = {}, {}
    for row in data["ammo"]:
        item = AmmoUse(**row)
        if (not item.id or item.id in ammo or not item.name
                or not math.isfinite(item.round_weight) or item.round_weight <= 0
                or type(item.rounds) is not int or item.rounds <= 0
                or type(item.ammunition_cost) is not int or item.ammunition_cost <= 0):
            raise ValueError(f"잘못된 탄종 보급 규칙: {item.id}")
        ammo[item.id] = item
    for row in data["equipment"]:
        item = EquipmentUse(**row)
        if (not item.id or item.id in equipment or not item.name
                or not math.isfinite(item.weight) or item.weight <= 0
                or type(item.armor_bonus) is not int or item.armor_bonus < 0
                or not item.cost or not set(item.cost) <= set(RESOURCE_TYPES)
                or any(type(amount) is not int or amount <= 0 for amount in item.cost.values())):
            raise ValueError(f"잘못된 장비 보급 규칙: {item.id}")
        equipment[item.id] = item
    if not {"gear:vest", "gear:helmet", "charge:c4", "tool:multitool"} <= equipment.keys():
        raise ValueError("기본 장비 보급 규칙 누락")
    repair = data["armor_repair_per_part"]
    if type(repair) is not int or repair <= 0:
        raise ValueError("잘못된 방어구 수리량")
    return ammo, equipment, repair


AMMO_USES, EQUIPMENT_USES, ARMOR_REPAIR_PER_PART = load_supply_uses()
