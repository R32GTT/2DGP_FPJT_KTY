"""로컬 목업 개체. 군수 부대의 실제 병력과는 별도인 훈련용 상태다."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from campaign.models import Position

if TYPE_CHECKING:
    from .weapons import WeaponState


@dataclass
class Actor:
    id: str
    position: Position
    radius: float = 0.22
    speed: float = 2.3
    facing: float = 0.0
    hp: int = 50
    state: str = "patrol"
    team: str = "enemy"
    armor: int = 0
    loadout: dict[str, "WeaponState"] = field(default_factory=dict)
    active_weapon: str = ""
    equip_until: float = 0.0
    hurt_until: float = 0.0

    @property
    def xy(self):
        return self.position.x, self.position.y

    @property
    def weapon(self):
        return self.loadout.get(self.active_weapon)
