"""로컬 목업 개체. 군수 부대의 실제 병력과는 별도인 훈련용 상태다."""

from dataclasses import dataclass

from campaign.models import Position


@dataclass
class Actor:
    id: str
    position: Position
    radius: float = 0.22
    speed: float = 2.3
    facing: float = 0.0
    hp: int = 50
    state: str = "patrol"

    @property
    def xy(self):
        return self.position.x, self.position.y


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float
    ttl: float = 1.0
    damage: int = 25
