"""총기 정의와 재개 가능한 재장전 상태. 입력·그리기·오디오에 의존하지 않는다."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path


STAGE_NAMES = {"remove_magazine": "탄창 빼기", "insert_magazine": "탄창 끼우기", "bolt_catch": "볼트캐치"}
DATA = Path(__file__).parent / "data"
SLOTS = ("muzzle", "optic", "magazine")
WEAPON_CATEGORIES = ("pistol", "rifle", "shotgun", "sniper", "machine_gun")


@dataclass(frozen=True)
class AttachmentSpec:
    id: str
    name: str
    slot: str
    weight: float
    noise_radius_multiplier: float = 1.0
    sound_strength_multiplier: float = 1.0
    aim_multiplier: float = 1.0
    look_multiplier: float = 1.0
    capacity_multiplier: float = 1.0
    reload_multiplier: float = 1.0

    def __post_init__(self):
        values = (self.weight, self.noise_radius_multiplier, self.sound_strength_multiplier,
                  self.aim_multiplier, self.look_multiplier, self.capacity_multiplier, self.reload_multiplier)
        if (not isinstance(self.id, str) or not self.id or not isinstance(self.name, str) or not self.name
                or self.slot not in SLOTS or any(not _finite_number(value) or value <= 0 for value in values)
                or min(self.look_multiplier, self.capacity_multiplier, self.reload_multiplier) < 1
                or max(self.aim_multiplier, self.sound_strength_multiplier, self.noise_radius_multiplier) > 1):
            raise ValueError(f"부착물 데이터 오류: {self.id}")


def load_attachments():
    rows = json.loads((DATA / "attachments.json").read_text(encoding="utf-8"))
    parts = [AttachmentSpec(**row) for row in rows]
    result = {part.id: part for part in parts}
    if len(result) != len(parts):
        raise ValueError("중복 부착물 ID")
    return result


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
    ammo_type: str = ""
    automatic: bool = True
    damage_curve: tuple[tuple[float, float], ...] = ()
    spread_per_shot_deg: float = 0.25
    spread_max_deg: float = 3.0
    spread_recovery_delay_s: float = 0.25
    spread_recovery_deg_per_s: float = 2.0
    aim_spread_multiplier: float = 0.6
    aim_settle_s: float = 0.25
    move_spread_deg_per_mps: float = 0.15
    sprint_spread_multiplier: float = 3.0
    enemy_spread_multiplier: float = 8.0
    category: str = "rifle"
    weight: float = 3.2
    pellets: int = 1
    pellet_spread_deg: float = 0.0
    suppression_multiplier: float = 1.0
    compatible: tuple[AttachmentSpec, ...] = ()
    issue_roles: tuple[str, ...] = ()

    def __post_init__(self):
        positive = (self.fire_interval, self.range_m, self.noise_radius, self.reload_move_multiplier,
                    self.spread_max_deg, self.spread_recovery_deg_per_s, self.aim_spread_multiplier,
                    self.aim_settle_s, self.sprint_spread_multiplier, self.enemy_spread_multiplier,
                    self.weight, self.suppression_multiplier)
        nonnegative = (self.spread_degrees, self.spread_per_shot_deg, self.spread_recovery_delay_s,
                       self.move_spread_deg_per_mps, self.pellet_spread_deg)
        integers = (self.capacity, self.reserve, self.damage, self.penetration)
        if (not isinstance(self.id, str) or not self.id.strip()
                or not isinstance(self.name, str) or not self.name.strip()
                or not isinstance(self.ammo_type, str) or not isinstance(self.image, str)
                or type(self.automatic) is not bool
                or self.category not in WEAPON_CATEGORIES or type(self.pellets) is not int
                or not 1 <= self.pellets <= 16 or self.pellet_spread_deg >= 90
                or any(type(value) is not int for value in integers)
                or self.capacity <= 0 or self.reserve < 0 or self.damage <= 0 or self.penetration < 0
                or any(not _finite_number(value) or value <= 0 for value in positive)
                or any(not _finite_number(value) or value < 0 for value in nonnegative)
                or self.reload_move_multiplier > 1 or self.aim_spread_multiplier > 1
                or self.sprint_spread_multiplier < 1
                or not self.spread_degrees <= self.spread_max_deg < 90):
            raise ValueError(f"총기 데이터 오류: {self.id}")
        coordinates = (self.size_m, self.mount, self.grip, self.muzzle)
        if (any(len(values) != 2 for values in coordinates)
                or any(not _finite_number(value) for values in coordinates for value in values)
                or min(self.size_m) <= 0
                or tuple(stage.id for stage in self.reload_stages) != tuple(STAGE_NAMES)
                or any(not _finite_number(stage.duration) or stage.duration <= 0
                       or not _finite_number(stage.noise_radius) or stage.noise_radius < 0
                       for stage in self.reload_stages)):
            raise ValueError(f"총기 배치 또는 재장전 데이터 오류: {self.id}")
        if self.damage_curve:
            previous = -1.0
            for point in self.damage_curve:
                if (len(point) != 2 or any(not _finite_number(value) or value < 0 for value in point)
                        or point[0] <= previous):
                    raise ValueError(f"거리별 피해 데이터 오류: {self.id}")
                previous = point[0]
            if (len(self.damage_curve) < 2 or self.damage_curve[0][0] != 0
                    or self.damage_curve[-1] != (self.range_m, 0)):
                raise ValueError(f"거리별 피해의 시작·종료 지점 오류: {self.id}")

    def damage_at(self, distance_m):
        distance_m = max(0.0, distance_m)
        if distance_m >= self.range_m:
            return 0.0
        if not self.damage_curve:
            return float(self.damage)
        for (start, damage), (end, next_damage) in zip(self.damage_curve, self.damage_curve[1:]):
            if distance_m <= end:
                fraction = (distance_m - start) / (end - start)
                return damage + (next_damage - damage) * fraction
        return 0.0


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_weapons(path=None, *, attachments=None):
    path = Path(path) if path else DATA / "weapons.json"
    try:
        parts = load_attachments() if attachments is None else attachments
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows:
            raise ValueError("총기 목록은 비어 있지 않은 배열이어야 함")
        result = {}
        roles = set()
        for row in rows:
            row = dict(row)
            row["reload_stages"] = tuple(ReloadStage(**stage) for stage in row["reload_stages"])
            for name in ("size_m", "mount", "grip", "muzzle"):
                row[name] = tuple(row[name])
            row["damage_curve"] = tuple(tuple(point) for point in row.get("damage_curve", ()))
            row["compatible"] = tuple(parts[key] for key in row.get("compatible", ()))
            row["issue_roles"] = tuple(row.get("issue_roles", ()))
            spec = WeaponSpec(**row)
            if spec.id in result:
                raise ValueError(f"중복 총기 ID: {spec.id}")
            if (len(set(spec.issue_roles)) != len(spec.issue_roles)
                    or any(not isinstance(role, str) or not role or role in roles for role in spec.issue_roles)):
                raise ValueError(f"중복 또는 잘못된 지급 역할: {spec.id}")
            roles.update(spec.issue_roles)
            result[spec.id] = spec
        return result
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"총기 설정을 읽을 수 없음: {path}: {exc}") from exc


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
    aim_progress: float = 0.0
    shot_spread_deg: float = 0.0
    bloom_deg: float = 0.0
    recovery_delay: float = 0.0
    movement_spread_deg: float = 0.0
    attachments: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.attachments, dict) or any(
                not any(part.id == key and part.slot == slot for part in self.spec.compatible)
                for slot, key in self.attachments.items()):
            raise ValueError("총기에 호환되지 않는 부착물")

    @classmethod
    def ready(cls, spec):
        return cls(spec, spec.capacity, spec.reserve, automatic=spec.automatic)

    @property
    def ammo(self):
        return self.magazine + int(self.chambered)

    def part(self, slot):
        key = self.attachments.get(slot)
        return next((part for part in self.spec.compatible if part.id == key), None) if key else None

    @property
    def capacity(self):
        magazine = self.part("magazine")
        return max(self.spec.capacity, round(self.spec.capacity * magazine.capacity_multiplier)) if magazine else self.spec.capacity

    @property
    def look_multiplier(self):
        optic = self.part("optic")
        return optic.look_multiplier if optic else 1.0

    def reload_duration(self, index):
        magazine = self.part("magazine")
        return self.spec.reload_stages[index].duration * (magazine.reload_multiplier if magazine else 1.0)

    def change_attachment(self, slot, part=None):
        if self.stage or self.reloading or slot not in SLOTS:
            raise ValueError("재장전 중이거나 유효하지 않은 부착물 슬롯")
        if part is not None and (part.slot != slot or part not in self.spec.compatible):
            raise ValueError("호환되지 않는 부착물")
        previous = self.attachments.pop(slot, None)
        if part is not None:
            self.attachments[slot] = part.id
        overflow = max(0, self.magazine - self.capacity)
        self.magazine -= overflow
        self.reserve += overflow
        return previous

    @property
    def remaining_reload(self):
        if not self.reloading and self.stage == 0:
            return 0.0
        end = 2 if self.chambered else 3
        return max(0.0, sum(self.reload_duration(index) for index in range(self.stage, end)) - self.stage_elapsed)

    @property
    def spread_deg(self):
        optic = self.part("optic")
        aim = self.spec.aim_spread_multiplier * (optic.aim_multiplier if optic else 1.0)
        aim_scale = 1.0 - self.aim_progress * (1.0 - aim)
        return min(self.spec.spread_max_deg,
                   (self.spec.spread_degrees + self.bloom_deg) * aim_scale + self.movement_spread_deg)

    def update_accuracy(self, dt, *, aiming=False, movement_speed_mps=0.0, sprinting=False):
        if (not _finite_number(dt) or dt < 0 or not _finite_number(movement_speed_mps)
                or movement_speed_mps < 0):
            raise ValueError("조준 경과 시간 또는 이동 속도 오류")
        self.aim_progress = min(1.0, self.aim_progress + dt / self.spec.aim_settle_s) if aiming else 0.0
        self.movement_spread_deg = (movement_speed_mps * self.spec.move_spread_deg_per_mps
                                   * (self.spec.sprint_spread_multiplier if sprinting else 1.0))

    def _stage_signal(self):
        definition = self.spec.reload_stages[self.stage]
        return {"kind": "reload_step_started", "stage": definition.id,
                "duration": self.reload_duration(self.stage), "noise_radius": definition.noise_radius}

    def start_reload(self):
        if self.reloading or (self.stage == 0 and self.magazine == self.capacity and self.chambered):
            return []
        if self.stage == 0:
            if not self.chambered and self.magazine_attached and self.magazine > 0:
                self.stage = 2
            elif self.reserve <= 0:
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
        recovery_dt = max(0.0, dt - self.recovery_delay)
        self.recovery_delay = max(0.0, self.recovery_delay - dt)
        self.bloom_deg = max(0.0, self.bloom_deg - self.spec.spread_recovery_deg_per_s * recovery_dt)
        signals = []
        while self.reloading and dt > 1e-10:
            definition = self.spec.reload_stages[self.stage]
            duration = self.reload_duration(self.stage)
            consumed = min(dt, duration - self.stage_elapsed)
            self.stage_elapsed += consumed
            dt -= consumed
            if self.stage_elapsed + 1e-9 < duration:
                break
            # 완료 경계에서만 탄약·장착 상태를 이전한다. 취소가 이 결과를 되돌리지 않는다.
            if definition.id == "remove_magazine":
                self.reserve += self.magazine
                self.magazine, self.magazine_attached = 0, False
            elif definition.id == "insert_magazine":
                amount = min(self.capacity, self.reserve)
                self.magazine, self.reserve = amount, self.reserve - amount
                self.magazine_attached = True
            elif not self.chambered and self.magazine:
                self.magazine -= 1
                self.chambered = True
            signals.append({"kind": "reload_step_completed", "stage": definition.id})
            self.stage += 1
            self.stage_elapsed = 0.0
            if self.stage == 2 and self.chambered:
                self.stage += 1
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
        self.shot_spread_deg = self.spread_deg
        self.bloom_deg = min(self.spec.spread_max_deg - self.spec.spread_degrees,
                            self.bloom_deg + self.spec.spread_per_shot_deg)
        self.recovery_delay = self.spec.spread_recovery_delay_s
        self.chambered = False
        if self.magazine_attached and self.magazine:
            self.magazine -= 1
            self.chambered = True
        return True


def rotated_offset(position, offset, facing):
    cosine, sine = math.cos(facing), math.sin(facing)
    return (position[0] + offset[0] * cosine - offset[1] * sine,
            position[1] + offset[0] * sine + offset[1] * cosine)


def weapon_pose(actor, *, origin=None, facing=None):
    origin = actor.xy if origin is None else origin
    facing = actor.facing if facing is None else facing
    weapon = actor.weapon
    if weapon is None:
        return origin, origin
    spec = weapon.spec
    center = (spec.mount[0] + (0.5 - spec.grip[0]) * spec.size_m[0],
              spec.mount[1] + (0.5 - spec.grip[1]) * spec.size_m[1])
    muzzle = (spec.mount[0] + (spec.muzzle[0] - spec.grip[0]) * spec.size_m[0],
              spec.mount[1] + (spec.muzzle[1] - spec.grip[1]) * spec.size_m[1])
    return rotated_offset(origin, center, facing), rotated_offset(origin, muzzle, facing)
