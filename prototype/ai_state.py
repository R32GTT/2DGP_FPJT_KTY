"""개인 행동 상태와 프레임 실행 요청."""

from dataclasses import dataclass, field
from enum import Enum
import math

from .actors import Actor
from .indoor_search import CoverageScan, IndoorSweep
from .perception import Knowledge


@dataclass(frozen=True)
class SearchCue:
    kind: str
    point: tuple[float, float]
    at: float


class MoveStatus(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    MOVING = "moving"
    WAITING = "waiting"
    ARRIVED = "arrived"
    UNREACHABLE = "unreachable"


class ScanPhase(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    FINISHED = "finished"


@dataclass(frozen=True)
class MoveRequest:
    goal: tuple | None
    face: bool = True
    approximate: bool = False
    precise: bool = False


@dataclass
class MotionState:
    plan_at: float = 0.0
    path: list = field(default_factory=list)
    goal: tuple | None = None
    path_revision: int = -1
    route_failed: bool = False
    route_origin: tuple | None = None
    progress_point: tuple | None = None
    progress_at: float = 0.0
    pending_goal: tuple | None = None
    blocked_by: str = ""
    door_wait: str = ""
    door_stack: tuple[float, float] | None = None
    speed: float = 0.0
    depart_at: float | None = None
    heading: float | None = None
    yield_point: tuple | None = None
    yield_retry_at: float = 0.0

    @property
    def status(self):
        if self.pending_goal is not None:
            return MoveStatus.PLANNING
        if self.goal is None:
            return MoveStatus.IDLE
        if self.path_revision < 0:
            return MoveStatus.PLANNING
        if self.route_failed:
            return MoveStatus.UNREACHABLE
        if not self.path:
            return MoveStatus.ARRIVED
        if self.blocked_by or self.door_wait:
            return MoveStatus.WAITING
        return MoveStatus.MOVING

    def arrived(self, goal, revision):
        return self.goal == goal and self.path_revision == revision and self.status is MoveStatus.ARRIVED

    def failed(self, goal, revision):
        return self.goal == goal and self.path_revision == revision and self.status is MoveStatus.UNREACHABLE

    def cancel(self):
        self.path.clear()
        self.goal = self.pending_goal = self.progress_point = None
        self.route_failed = False

    def invalidate(self):
        self.path.clear()
        self.path_revision, self.plan_at = -1, 0.0


@dataclass
class CombatState:
    fire_at: float = 0.0
    fired_at: float = -1000.0
    burst: int = 0
    action: str = ""
    action_goal: tuple | None = None
    action_until: float = 0.0
    cover_threat: tuple | None = None
    cover_revision: int = -1
    peek_home: tuple | None = None
    peek_at: float = 0.0
    peek_contact: bool = False
    peek_departed: bool = False
    suppression: float = 0.0
    pressure_at: float = 0.0
    incoming: tuple | None = None
    incoming_at: float = -1000.0
    combat_alert_until: float = 0.0


@dataclass
class LookState:
    watch_point: tuple | None = None
    watch_cue: tuple | None = None
    watch_at: float = 0.0
    point: tuple | None = None
    reason: str = ""
    turn_rate: float = 0.0


@dataclass
class ScanState:
    walk_look: tuple | None = None
    walk_look_at: float = 0.0
    walk_look_revision: int = -1
    walk_look_started: bool = False
    exposure: dict[tuple[float, float], float] = field(default_factory=dict)
    coverage: CoverageScan = field(default_factory=CoverageScan)
    probe_key: tuple | None = None
    probes: tuple = ()
    probe_at: float = 0.0
    probe_offset: int = 0
    coverage_goal_cursor: int = 0
    coverage_retry_at: float = 0.0
    occluded: set[tuple[float, float]] = field(default_factory=set)
    revealed: set[tuple[float, float]] = field(default_factory=set)
    sample_at: float = -math.inf


@dataclass
class IndoorState:
    sweep: IndoorSweep | None = None
    staging: bool = False
    target: tuple | None = None
    sound_cue: SearchCue | None = None
    blocked: dict = field(default_factory=dict)
    phase: ScanPhase = ScanPhase.IDLE
    scan_origin: tuple | None = None
    sample_stationary: bool = False
    observation: IndoorSweep | None = None

    scan: ScanState = field(default_factory=ScanState)

    @property
    def scanning(self):
        return self.phase is ScanPhase.SCANNING


@dataclass
class FailedClue:
    cue: SearchCue
    origin: tuple
    revision: int


@dataclass
class SearchState:
    next_sweep_at: float = 0.0
    sweep_pass: int = 0
    cover: tuple | None = None
    cue: tuple | None = None
    priority_cue: SearchCue | None = None
    priority_checked: bool = False
    failed_clue: FailedClue | None = None
    points: list = field(default_factory=list)
    target: tuple | None = None
    checked: set = field(default_factory=set)
    scan_until: float = 0.0
    scan_angle: float = 0.0
    look_until: float = 0.0
    checked_sound_at: float = -1000.0
    sound_response: str = ""
    sound_response_at: float = -1000.0
    indoor: IndoorState = field(default_factory=IndoorState)

    @property
    def investigating_priority(self):
        return (self.priority_cue is not None and not self.priority_checked
                and (self.failed_clue is None or self.failed_clue.cue != self.priority_cue))


@dataclass
class Brain:
    patrol: list
    memory: Knowledge = field(default_factory=Knowledge)
    waypoint: int = 0
    sense_at: float = 0.0
    radio_at: float = 0.0
    contact_at: float = 0.0
    role: str = ""
    incident: str = ""
    role_changed: bool = False
    far_at: float = 0.0
    far_updated: float = 0.0
    motion: MotionState = field(default_factory=MotionState)
    combat: CombatState = field(default_factory=CombatState)
    search: SearchState = field(default_factory=SearchState)
    look: LookState = field(default_factory=LookState)

    @property
    def visible(self):
        return self.memory.visible


@dataclass
class Intent:
    direction: tuple = (0, 0)
    fire: bool = False
    retreat_fire: bool = False
    reload: bool = False
    open_door: bool = False
    step_dt: float = 1 / 60
    move_multiplier: float = 1.0
    move_straight: bool = False
    face_path: bool = False


@dataclass(frozen=True)
class Situation:
    confirmed: bool
    investigating: bool
    incoming: tuple | None
    tracking: bool
    reported: bool
    fresh_sound: bool
    looking_for_sound: bool
    urgent: bool
    old_state: str
    priority_search: bool
    distant: bool


@dataclass(frozen=True)
class ActionPlan:
    goal: tuple | None
    threat: tuple | None = None
    sound_sweep: IndoorSweep | None = None

@dataclass
class ApproachMove:
    actor: Actor
    brain: Brain
    intent: Intent
    goal: tuple
    approximate: bool = False
    priority: bool = False

@dataclass
class Incident:
    updated_at: float
    heading: float
    point: tuple
    indoor: IndoorSweep | None = None
    checked: list = field(default_factory=list)
    claims: dict = field(default_factory=dict)
    sighting_at: float = -math.inf
    reporter: str = ""


def set_role(brain, role):
    if brain.role != role:
        brain.role = role
        brain.role_changed = True
        brain.search.cue = None
        brain.search.target = None
