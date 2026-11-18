"""개인 행동 상태와 프레임 실행 요청."""

from dataclasses import dataclass, field
from enum import Enum
from py_trees.common import Status
import math

from .actors import Actor
from .indoor_search import IndoorSweep, Reservations
from .perception import Knowledge, SOUND_MEMORY_SECONDS


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
    blocked_fire_at: float | None = None
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
    formation: tuple | None = None


@dataclass
class LookState:
    sample: tuple | None = None
    watch_point: tuple | None = None
    watch_cue: tuple | None = None
    watch_at: float = 0.0
    point: tuple | None = None
    reason: str = ""
    turn_rate: float = 0.0


@dataclass
class ScanState:
    walk_look: tuple | None = None
    walk_look_started: bool = False
    exposure: dict[tuple[float, float], float] = field(default_factory=dict)
    occluded: set[tuple[float, float]] = field(default_factory=set)
    urgent_focus: bool = False
    sample_at: float = -math.inf


@dataclass
class InvestigationState:
    sweep: IndoorSweep | None = None
    staging: bool = False
    target: tuple | None = None
    sound_cue: SearchCue | None = None
    blocked: dict = field(default_factory=dict)
    execution: object = None
    cue: tuple | None = None
    points: list = field(default_factory=list)
    scan_origin: tuple | None = None
    sample_stationary: bool = False
    observation: IndoorSweep | None = None

    scan: ScanState = field(default_factory=ScanState)

    @property
    def scanning(self):
        return self.execution is not None and self.execution.children[1].status is Status.RUNNING


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
    priority_cue: SearchCue | None = None
    priority_checked: bool = False
    failed_clue: FailedClue | None = None
    arrival: tuple | None = None
    reported_check: SearchCue | None = None
    checked: set = field(default_factory=set)
    look_until: float = 0.0
    checked_sound_at: float = -1000.0
    sound_response: str = ""
    sound_response_at: float = -1000.0
    task: InvestigationState = field(default_factory=InvestigationState)

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
    incident: str = ""
    far_at: float = 0.0
    far_updated: float = 0.0
    motion: MotionState = field(default_factory=MotionState)
    combat: CombatState = field(default_factory=CombatState)
    search: SearchState = field(default_factory=SearchState)
    look: LookState = field(default_factory=LookState)

    @property
    def visible(self):
        return self.memory.visible

    def clear_contact(self):
        if self.search.task.execution is not None:
            self.search.task.execution.stop(Status.INVALID)
        self.memory.last_seen = self.memory.reported = None
        self.search.task.cue = self.search.task.target = self.search.cover = self.search.arrival = None
        self.search.priority_cue = None
        self.combat.peek_home = self.combat.incoming = self.combat.formation = self.search.reported_check = None


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


class Situation:
    """한 번의 판단에 사용하는 관측·위험 스냅샷. 개인의 지속 상태는 Brain에 둔다."""

    def __init__(self, actor, brain, player, time):
        memory, search, combat = brain.memory, brain.search, brain.combat
        self.investigating = (memory.last_sound is not None and memory.sound_at > search.checked_sound_at
            and (search.failed_clue is None or search.failed_clue.cue != SearchCue("sound", memory.last_sound, memory.sound_at))
            and (search.task.sound_cue is not None or time - memory.sound_heard_at < SOUND_MEMORY_SECONDS)
            and (memory.suspicion >= 0.3 or actor.state in {"alert", "search", "regroup"}))
        self.confirmed = time < combat.combat_alert_until
        self.incoming = combat.incoming if time - combat.incoming_at < (12 if self.confirmed else 4) else None
        self.tracking = self.confirmed and memory.last_seen is not None
        self.reported = self.confirmed and memory.reported is not None
        self.fresh_sound = time < search.look_until
        self.looking_for_sound = not (self.tracking or self.investigating or self.reported) and self.fresh_sound
        self.old_state = actor.state
        self.urgent = actor.hp < 20 or actor.hurt_until > time or combat.suppression >= 0.55
        self.distant = math.dist(actor.xy, player.xy) > 60 and not (self.investigating or self.confirmed or self.incoming is not None)
        self.priority_search = search.investigating_priority


@dataclass(frozen=True)
class ActionPlan:
    goal: tuple | None
    threat: tuple | None = None
    sound_sweep: IndoorSweep | None = None
    mode: str = ""
    face: bool = False
    approximate: bool = False
    room_sweep: bool = False
    priority: bool = False
    approach: str | tuple | None = None

@dataclass
class ApproachMove:
    actor: Actor
    brain: Brain
    intent: Intent
    goal: tuple
    approximate: bool = False
    priority: bool = False

@dataclass
class Incident(Reservations):
    updated_at: float
    point: tuple
    indoor: IndoorSweep | None = None
    checked: list = field(default_factory=list)
    claims: dict = field(default_factory=dict)
    sighting_at: float = -math.inf
    reporter: str = ""
