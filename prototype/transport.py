"""실제 군수 차량의 좌석, 하차 전투, 교대 운전을 현장에 연결한다."""

from dataclasses import dataclass, field
import math

from campaign.models import Position

from .actors import Actor
from .ai import Brain
from .bridge import ConvoyView
from .geometry import Rect


TRUCK_SIZE = (5.6, 2.4)
BODY = Rect(-2.6, -1.0, 5.2, 2.0)
WINDSHIELD = Rect(1.75, -0.46, 0.46, 0.92)
QUIET_SECONDS = 10.0


@dataclass
class Truck:
    id: str
    convoy: ConvoyView
    x: float
    y: float
    crew: list[Actor]
    stopped: bool

    @property
    def heading(self):
        return self.convoy.heading

    def world_point(self, x, y):
        c, s = math.cos(self.heading), math.sin(self.heading)
        return self.x + x * c - y * s, self.y + x * s + y * c

    def local_point(self, point):
        dx, dy = point[0] - self.x, point[1] - self.y
        c, s = math.cos(self.heading), math.sin(self.heading)
        return dx * c + dy * s, -dx * s + dy * c

    def raycast(self, start, end):
        a, b = self.local_point(start), self.local_point(end)
        hull = BODY.segment_entry(a, b)
        if hull is None:
            return None
        glass = WINDSHIELD.segment_entry(a, b)
        if (self.stopped or not self.crew or a[0] < BODY.right or glass is None
                or a[0] + (b[0] - a[0]) * hull < WINDSHIELD.x):
            return hull, None
        # 전면 유리를 두 좌석으로 나누고, 차체를 먼저 맞은 탄은 여기서 막는다.
        y = a[1] + (b[1] - a[1]) * glass
        seat = 0 if y < 0 else 1
        occupant = self.crew[seat] if seat < len(self.crew) else None
        return hull, occupant if occupant and occupant.hp > 0 else None


@dataclass
class Trip:
    seats: dict[str, list[Actor]]
    exits: dict[str, tuple[float, float]] = field(default_factory=dict)
    stopped_at: float | None = None


class TransportCrew:
    def __init__(self, world, bridge, roster, ai, weapon, events):
        self.world, self.bridge, self.roster = world, bridge, roster
        self.ai, self.weapon, self.events = ai, weapon, events
        self.trips = {}
        self.trucks = []
        self.dismounted = []

    @staticmethod
    def assign(vehicle_ids, crew):
        return {key: list(crew[index::len(vehicle_ids)]) for index, key in enumerate(vehicle_ids)}

    def _exit_points(self, truck, occupied):
        for dx in (0.6, -0.6, 1.5, -1.5, 2.3, -2.3, 3.4, -3.4, 4.5, -4.5, 5.5, -5.5):
            for dy in (-1.6, 1.6, -2.3, 2.3, 0):
                if abs(dx) < 3.1 and dy == 0:
                    continue
                point = truck.world_point(dx, dy)
                if (self.world.navigation.walkable(point)
                        and all(math.dist(point, other) >= 0.7 for other in occupied)):
                    yield point

    def _dismount(self, trip, trucks, seconds):
        if trip.stopped_at is None:
            trip.stopped_at = seconds
        occupied = [actor.xy for actor in self.roster.sync() if actor.hp > 0]
        occupied.extend(actor.xy for truck in trucks for actor in truck.crew
                        if actor.hp > 0 and actor.id in trip.exits)
        for truck in trucks:
            for actor in truck.crew:
                if actor.hp <= 0 or actor.id in trip.exits:
                    continue
                point = next(self._exit_points(truck, occupied), None)
                if point is None:
                    continue
                occupied.append(point)
                trip.exits[actor.id] = point
                actor.position = Position(*point)
                actor.facing = truck.heading
                actor.boarding_goal = None
                actor.transport_boarding = False
                actor.state = "alert"
                self.ai.brains[actor.id] = Brain([point])

    def sync(self, seconds):
        self.trucks, self.dismounted = [], []
        if not self.world.region_layout or not self.roster.materialized:
            return
        state = self.bridge.simulation.state
        active = set()
        for view in self.bridge.convoys(seconds):
            operation = state.operations[view.id]
            active.add(view.id)
            trip = self.trips.get(view.id)
            ids = [key for key in operation.vehicle_ids if state.vehicles[key].status != "destroyed"]
            if not ids:
                continue
            if trip is None:
                bounds = self.world.bounds.expanded(12)
                if not (bounds.x <= view.x <= bounds.right and bounds.y <= view.y <= bounds.bottom):
                    continue
                crew = self.roster.transport_members(operation, self.weapon)
                for actor in crew:
                    self.ai.brains.setdefault(actor.id, Brain([]))
                trip = self.trips[view.id] = Trip(self.assign(ids, crew))

            stopped = operation.waiting_reason in {"driver_down", "no_driver"}
            moving = view.phase in {"outbound", "returning"} and not stopped
            trucks = []
            for index, key in enumerate(ids):
                offset = -index * 6.4
                truck = Truck(key, view, view.x + offset * math.cos(view.heading),
                              view.y + offset * math.sin(view.heading),
                              trip.seats.get(key, []) if moving or stopped else [], stopped)
                trucks.append(truck)
                if moving:
                    for seat, actor in enumerate(truck.crew):
                        if seat < 2 and actor.hp > 0:
                            actor.position = Position(*truck.world_point(1.8, -0.28 if seat == 0 else 0.28))
                            actor.facing = truck.heading
                    for actor in truck.crew:
                        actor.last_node = ""
            self.trucks.extend(trucks)
            if stopped:
                if any(actor.hp > 0 and actor.id not in trip.exits for truck in trucks for actor in truck.crew):
                    self._dismount(trip, trucks, seconds)
                self.dismounted.extend(actor for truck in trucks for actor in truck.crew
                                       if actor.hp > 0 and actor.id in trip.exits)
        for key in self.trips.keys() - active:
            del self.trips[key]

    def driver_shot(self, actor, vehicle_id):
        return next((truck for truck in self.trucks if truck.id == vehicle_id and not truck.stopped
                     and truck.crew and truck.crew[0] is actor), None)

    def react_to_stop(self, truck, seconds, incoming):
        for actor in self.dismounted:
            if actor.id in self.trips[truck.convoy.id].exits:
                self.events.emit("pressure", seconds, actor, strength=0.55, caliber=18,
                                 incoming=incoming, hit=False, close=True)

    def try_resume(self, seconds):
        resumed = False
        for key, trip in self.trips.items():
            if not trip.exits:
                continue
            trucks = [truck for truck in self.trucks if truck.convoy.id == key and truck.stopped]
            if not trucks:
                continue
            crew = [actor for truck in trucks for actor in truck.crew if actor.hp > 0]
            quiet = seconds - trip.stopped_at >= QUIET_SECONDS
            # 장기 경계 상태에서도 최근 교전이 끝났으면 차량으로 복귀할 수 있다.
            for actor in crew:
                brain = self.ai.brains[actor.id]
                memory = brain.memory
                quiet &= (not memory.visible and seconds - max(memory.seen_at, memory.sound_heard_at,
                                                                brain.incoming_at, brain.fired_at) >= QUIET_SECONDS)
            can_board = quiet and len(crew) >= len(trucks) and all(actor.id in trip.exits for actor in crew)
            for actor in crew:
                actor.transport_boarding = can_board
                actor.boarding_goal = trip.exits.get(actor.id) if can_board else None
            if can_board and all(math.dist(actor.xy, actor.boarding_goal) <= 0.75 for actor in crew):
                for actor in crew:
                    actor.boarding_goal = None
                    actor.transport_boarding = False
                trip.seats = self.assign([truck.id for truck in trucks], crew)
                trip.exits.clear()
                trip.stopped_at = None
                self.bridge.resume_transport(key, seconds)
                resumed = True
        return resumed

    def raycast(self, start, end):
        result = None
        for truck in self.trucks:
            impact = truck.raycast(start, end)
            if impact is not None and (result is None or impact[0] < result[0]):
                result = impact[0], truck, impact[1]
        return result
