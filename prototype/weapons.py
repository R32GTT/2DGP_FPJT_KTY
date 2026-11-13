"""총기 정의와 재개 가능한 재장전 상태. 입력·그리기·오디오에 의존하지 않는다."""

from dataclasses import dataclass
import json
import math
from pathlib import Path


STAGE_NAMES = {"remove_magazine": "탄창 빼기", "insert_magazine": "탄창 끼우기", "bolt_catch": "볼트캐치"}


@dataclass(frozen=True)
class ReloadStage:
    id: str
    duration: float
    noise_radius: float = 2.0


@dataclass(frozen=True)
class WeaponSpec:
    id: str
    name: str
    capacity: int
    reserve: int
    fire_interval: float
    damage: int
    range_m: float
    noise_radius: float
    spread_degrees: float
    penetration: int
    reload_move_multiplier: float
    image: str
    size_m: tuple
    mount: tuple
    grip: tuple
    muzzle: tuple
    reload_stages: tuple[ReloadStage, ...]


def load_weapons(path=None):
    path = Path(path) if path else Path(__file__).parent / "data" / "weapons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for row in rows:
        row = dict(row)
        row["reload_stages"] = tuple(ReloadStage(**stage) for stage in row["reload_stages"])
        for name in ("size_m", "mount", "grip", "muzzle"):
            row[name] = tuple(row[name])
        spec = WeaponSpec(**row)
        values = (spec.fire_interval, spec.range_m, spec.noise_radius, spec.reload_move_multiplier, *spec.size_m)
        integers = (spec.capacity, spec.reserve, spec.damage, spec.penetration)
        if (spec.id in result or any(type(value) is not int for value in integers)
                or spec.capacity <= 0 or spec.reserve < 0 or spec.fire_interval <= 0
                or spec.range_m <= 0 or spec.damage <= 0 or min(spec.size_m) <= 0
                or not all(math.isfinite(value) and value > 0 for value in values)
                or not math.isfinite(spec.spread_degrees) or spec.spread_degrees < 0 or spec.penetration < 0
                or any(len(getattr(spec, name)) != 2 for name in ("size_m", "mount", "grip", "muzzle"))
                or any(not math.isfinite(value) for value in (*spec.mount, *spec.grip, *spec.muzzle))
                or spec.reload_move_multiplier > 1
                or tuple(stage.id for stage in spec.reload_stages) != tuple(STAGE_NAMES)
                or any(not math.isfinite(stage.duration) or stage.duration <= 0
                       or not math.isfinite(stage.noise_radius) or stage.noise_radius < 0 for stage in spec.reload_stages)):
            raise ValueError(f"총기 데이터 오류: {spec.id}")
        result[spec.id] = spec
    return result


@dataclass
class WeaponState:
    spec: WeaponSpec
    magazine: int
    reserve: int
    chambered: bool = True
    magazine_attached: bool = True
    next_shot: float = 0.0
    stage: int = 0
    stage_elapsed: float = 0.0
    reloading: bool = False
    automatic: bool = True

    @classmethod
    def ready(cls, spec):
        return cls(spec, spec.capacity, spec.reserve)

    @property
    def ammo(self):
        return self.magazine + int(self.chambered)

    @property
    def remaining_reload(self):
        return sum(stage.duration for stage in self.spec.reload_stages[self.stage:]) - self.stage_elapsed

    def _stage_signal(self):
        definition = self.spec.reload_stages[self.stage]
        return {"kind": "reload_step_started", "stage": definition.id,
                "duration": definition.duration, "noise_radius": definition.noise_radius}

    def start_reload(self):
        if self.reloading or (self.stage == 0 and (self.reserve <= 0 or
                (self.magazine == self.spec.capacity and self.chambered))):
            return []
        if self.stage == 1 and self.reserve <= 0:
            return []
        self.reloading, self.stage_elapsed = True, 0.0
        return [self._stage_signal()]

    def cancel_reload(self):
        if not self.reloading:
            return []
        self.reloading, self.stage_elapsed = False, 0.0
        return [{"kind": "reload_cancelled", "stage": self.spec.reload_stages[self.stage].id}]

    def tick(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("재장전 경과 시간 오류")
        signals = []
        while self.reloading and dt > 1e-10:
            definition = self.spec.reload_stages[self.stage]
            consumed = min(dt, definition.duration - self.stage_elapsed)
            self.stage_elapsed += consumed
            dt -= consumed
            if self.stage_elapsed + 1e-9 < definition.duration:
                break
            # 완료 경계에서만 탄약·장착 상태를 이전한다. 취소가 이 결과를 되돌리지 않는다.
            if definition.id == "remove_magazine":
                self.reserve += self.magazine
                self.magazine, self.magazine_attached = 0, False
            elif definition.id == "insert_magazine":
                amount = min(self.spec.capacity, self.reserve)
                self.magazine, self.reserve = amount, self.reserve - amount
                self.magazine_attached = True
            elif not self.chambered and self.magazine:
                self.magazine -= 1
                self.chambered = True
            signals.append({"kind": "reload_step_completed", "stage": definition.id})
            self.stage += 1
            self.stage_elapsed = 0.0
            if self.stage == len(self.spec.reload_stages):
                self.stage, self.reloading = 0, False
                signals.append({"kind": "reload_completed"})
            else:
                signals.append(self._stage_signal())
        return signals

    def fire(self, time):
        if self.reloading or time + 1e-9 < self.next_shot or not self.chambered:
            return False
        self.next_shot = time + self.spec.fire_interval
        self.chambered = False
        if self.magazine_attached and self.magazine:
            self.magazine -= 1
            self.chambered = True
        return True


def rotated_offset(position, offset, facing):
    cosine, sine = math.cos(facing), math.sin(facing)
    return (position[0] + offset[0] * cosine - offset[1] * sine,
            position[1] + offset[0] * sine + offset[1] * cosine)


def weapon_pose(actor):
    weapon = actor.weapon
    if weapon is None:
        return actor.xy, actor.xy
    spec = weapon.spec
    center = (spec.mount[0] + (0.5 - spec.grip[0]) * spec.size_m[0],
              spec.mount[1] + (0.5 - spec.grip[1]) * spec.size_m[1])
    muzzle = (spec.mount[0] + (spec.muzzle[0] - spec.grip[0]) * spec.size_m[0],
              spec.mount[1] + (spec.muzzle[1] - spec.grip[1]) * spec.size_m[1])
    return rotated_offset(actor.xy, center, actor.facing), rotated_offset(actor.xy, muzzle, actor.facing)
