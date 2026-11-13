"""검증용 고정 월드 로드. 수치는 최종 밸런스가 아닌 검증 가설이다."""

import json
from pathlib import Path
import random

from .models import (CampaignState, ExternalDemand, ExternalSource, Facility,
                     Node, Position, Region, Road, Unit, Vehicle, stock, validate_state)


DEFAULT_SCENARIO = Path(__file__).parent / "scenarios" / "rear_test.json"


def load_scenario(path=DEFAULT_SCENARIO, *, seed: int = 42,
                  support: str = "finite") -> CampaignState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    def indexed(cls, rows):
        items = [cls(**row) for row in rows]
        result = {item.id: item for item in items}
        if len(result) != len(items):
            raise ValueError("중복 ID")
        return result

    nodes = {row["id"]: Node(row["id"], Position(**row["position"]))
             for row in data["nodes"]}
    if len(nodes) != len(data["nodes"]):
        raise ValueError("중복 노드 ID")
    facilities = indexed(Facility, data["facilities"])
    source = ExternalSource(**data["source"])
    source.mode = support
    state = CampaignState(
        seed=seed, nodes=nodes, roads=indexed(Road, data["roads"]),
        regions=indexed(Region, data["regions"]), facilities=facilities,
        units=indexed(Unit, data["units"]), vehicles=indexed(Vehicle, data["vehicles"]),
        source=source, demand=ExternalDemand(**data["demand"]), rules=data["rules"],
        rng_state=random.Random(seed).getstate(),
    )
    for name in stock():
        state.ledger.initial[name] = sum(f.inventory[name] for f in facilities.values())
        if support == "finite":
            state.ledger.initial[name] += source.inventory[name]
    state.initial_soldiers = sum(unit.soldiers for unit in state.units.values())
    state.initial_vehicle_ids = sorted(state.vehicles)
    validate_state(state)
    return state
