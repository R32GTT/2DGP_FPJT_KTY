"""Data-driven, magazine-fed firearms; no rendering or world dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random


DEFAULT_WEAPONS_PATH = Path(__file__).resolve().parent / "data" / "weapons.json"
_EPSILON = 1e-9


@dataclass(frozen=True)
class WeaponSpec:
    id: str
    name: str
    ammo_type: str
    magazine_capacity: int
    initial_reserve: int
    fire_interval_s: float
    damage_curve: tuple[tuple[float, float], ...]
    reload_remove_s: float
    reload_insert_s: float
    reload_chamber_s: float
    reload_move_multiplier: float
    noise_radius_m: float
    spread_base_deg: float
    spread_per_shot_deg: float
    spread_max_deg: float
    spread_recovery_delay_s: float
    spread_recovery_deg_per_s: float
    aim_spread_multiplier: float
    aim_settle_s: float
    move_spread_deg_per_mps: float
    sprint_spread_multiplier: float

    def __post_init__(self) -> None:
        for name in ("id", "name", "ammo_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name, minimum in (("magazine_capacity", 1), ("initial_reserve", 0)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        for name in (
            "fire_interval_s", "reload_remove_s", "reload_insert_s",
            "reload_chamber_s", "reload_move_multiplier", "noise_radius_m",
            "spread_base_deg", "spread_max_deg", "spread_recovery_deg_per_s",
            "aim_spread_multiplier", "aim_settle_s", "sprint_spread_multiplier",
        ):
            value = getattr(self, name)
            if not _is_number(value) or value <= 0:
                raise ValueError(f"{name} must be a finite number > 0")
        if self.reload_move_multiplier > 1:
            raise ValueError("reload_move_multiplier must be <= 1")
        for name in (
            "spread_per_shot_deg", "spread_recovery_delay_s", "move_spread_deg_per_mps",
        ):
            value = getattr(self, name)
            if not _is_number(value) or value < 0:
                raise ValueError(f"{name} must be a finite number >= 0")
        if not self.spread_base_deg <= self.spread_max_deg < 90:
            raise ValueError("spread angles must satisfy 0 < base <= max < 90 degrees")
        if self.aim_spread_multiplier > 1:
            raise ValueError("aim_spread_multiplier must be <= 1")
        if self.sprint_spread_multiplier < 1:
            raise ValueError("sprint_spread_multiplier must be >= 1")
        if len(self.damage_curve) < 2:
            raise ValueError("damage_curve needs at least two [meters, damage] points")
        previous = -1.0
        for point in self.damage_curve:
            if len(point) != 2:
                raise ValueError("damage_curve points must be [meters, damage]")
            distance, damage = point
            if (
                not _is_number(distance) or not _is_number(damage)
                or distance < 0 or distance <= previous or damage < 0
            ):
                raise ValueError("damage_curve needs increasing distances and nonnegative damage")
            previous = distance
        if self.damage_curve[0][0] != 0 or self.damage_curve[-1][1] != 0:
            raise ValueError("damage_curve must start at 0m and end with 0 damage")

    @property
    def range_m(self) -> float:
        return self.damage_curve[-1][0]

    @property
    def reload_durations(self) -> tuple[float, float, float]:
        return self.reload_remove_s, self.reload_insert_s, self.reload_chamber_s

    def damage_at(self, distance_m: float) -> float:
        distance_m = max(0.0, distance_m)
        if distance_m >= self.range_m:
            return 0.0
        for (start, damage), (end, next_damage) in zip(
            self.damage_curve, self.damage_curve[1:]
        ):
            if distance_m <= end:
                fraction = (distance_m - start) / (end - start)
                return damage + (next_damage - damage) * fraction
        return 0.0


def _is_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def load_weapon_specs(path: str | Path = DEFAULT_WEAPONS_PATH) -> dict[str, WeaponSpec]:
    """Load all definitions once. Invalid settings fail with a readable error."""
    try:
        with Path(path).open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict) or not data:
            raise ValueError("expected a non-empty object keyed by weapon ID")
        specs = {}
        for weapon_id, settings in data.items():
            try:
                settings = dict(settings)
                settings["damage_curve"] = tuple(
                    tuple(point) for point in settings["damage_curve"]
                )
                specs[weapon_id] = WeaponSpec(id=weapon_id, **settings)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{weapon_id}: {exc}") from exc
        return specs
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid weapon config {path}: {exc}") from exc


@dataclass
class Weapon:
    spec: WeaponSpec
    magazine_rounds: int = field(init=False)
    chambered: bool = field(default=True, init=False)
    reserve_rounds: int = field(init=False)
    cooldown: float = field(default=0.0, init=False)
    reloading: bool = field(default=False, init=False)
    aim_progress: float = field(default=0.0, init=False)
    shot_offset_deg: float = field(default=0.0, init=False)
    _bloom_deg: float = field(default=0.0, init=False, repr=False)
    _recovery_delay: float = field(default=0.0, init=False, repr=False)
    _movement_spread_deg: float = field(default=0.0, init=False, repr=False)
    _rng: random.Random = field(default_factory=random.Random, init=False, repr=False)
    _reload_stage: int = field(default=0, init=False, repr=False)
    _reload_elapsed: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.magazine_rounds = self.spec.magazine_capacity
        self.reserve_rounds = self.spec.initial_reserve

    @property
    def loaded_rounds(self) -> int:
        return self.magazine_rounds + int(self.chambered)

    @property
    def spread_deg(self) -> float:
        """Current half-angle, including aim and actual movement; never zero."""
        aim_scale = 1.0 - self.aim_progress * (1.0 - self.spec.aim_spread_multiplier)
        return min(
            self.spec.spread_max_deg,
            (self.spec.spread_base_deg + self._bloom_deg) * aim_scale
            + self._movement_spread_deg,
        )

    @property
    def reload_stage(self) -> str:
        return ("remove", "insert", "chamber")[self._reload_stage]

    @property
    def reload_remaining_s(self) -> float:
        if not self.reloading:
            return 0.0
        end = 2 if self.chambered else 3
        return max(
            0.0,
            sum(self.spec.reload_durations[self._reload_stage:end]) - self._reload_elapsed,
        )

    def fire(self) -> bool:
        # Even a dry trigger or a click during cooldown cancels reloading.
        self.cancel_reload()
        if not self.chambered or self.cooldown > _EPSILON:
            return False
        # Resolve this shot before adding bloom for the next shot.
        spread = self.spread_deg
        self.shot_offset_deg = self._rng.uniform(-spread, spread)
        self._bloom_deg = min(
            self.spec.spread_max_deg - self.spec.spread_base_deg,
            self._bloom_deg + self.spec.spread_per_shot_deg,
        )
        self._recovery_delay = self.spec.spread_recovery_delay_s
        self.chambered = False
        if self.magazine_rounds:
            self.magazine_rounds -= 1
            self.chambered = True
        self.cooldown = self.spec.fire_interval_s
        return True

    def start_reload(self) -> bool:
        if self.reloading:
            return False
        if self._reload_stage == 0:
            if self.loaded_rounds == self.spec.magazine_capacity + 1:
                return False
            if not self.chambered and self.magazine_rounds:
                # A magazine is already inserted; only chambering is needed.
                self._reload_stage = 2
            elif self.reserve_rounds <= 0:
                return False
        elif self._reload_stage == 1 and self.reserve_rounds <= 0:
            return False
        self.reloading = True
        self._reload_elapsed = 0.0
        return True

    def cancel_reload(self) -> None:
        self.reloading = False
        # Completed stages and their ammo transfers survive cancellation.
        self._reload_elapsed = 0.0

    def update(
        self, dt: float, *, aiming: bool = False,
        movement_speed_mps: float = 0.0, sprinting: bool = False,
    ) -> None:
        dt = max(0.0, dt)
        self.cooldown = max(0.0, self.cooldown - dt)
        recovery_dt = max(0.0, dt - self._recovery_delay)
        self._recovery_delay = max(0.0, self._recovery_delay - dt)
        self._bloom_deg = max(
            0.0, self._bloom_deg - self.spec.spread_recovery_deg_per_s * recovery_dt,
        )
        self.aim_progress = (
            min(1.0, self.aim_progress + dt / self.spec.aim_settle_s) if aiming else 0.0
        )
        self._movement_spread_deg = (
            max(0.0, movement_speed_mps) * self.spec.move_spread_deg_per_mps
            * (self.spec.sprint_spread_multiplier if sprinting else 1.0)
        )
        if not self.reloading:
            return
        self._reload_elapsed += dt
        while self.reloading:
            duration = self.spec.reload_durations[self._reload_stage]
            if self._reload_elapsed + _EPSILON < duration:
                break
            self._reload_elapsed = max(0.0, self._reload_elapsed - duration)
            if self._reload_stage == 0:
                # Retain old rounds in the shared reserve, not separate magazines.
                self.reserve_rounds += self.magazine_rounds
                self.magazine_rounds = 0
                self._reload_stage = 1
            elif self._reload_stage == 1:
                self.magazine_rounds = min(self.spec.magazine_capacity, self.reserve_rounds)
                self.reserve_rounds -= self.magazine_rounds
                if self.chambered:
                    self._finish_reload()
                else:
                    self._reload_stage = 2
            else:
                if self.magazine_rounds:
                    self.magazine_rounds -= 1
                    self.chambered = True
                self._finish_reload()

    def _finish_reload(self) -> None:
        self.reloading = False
        self._reload_stage = 0
        self._reload_elapsed = 0.0
