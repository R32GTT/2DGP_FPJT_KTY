"""전술 지역과 기존 군수 코어의 좌표·병력·재고·수송을 연결한다."""

from dataclasses import dataclass
import math

from campaign.models import FOOT_OPERATIONS, Node, Position, Road
from campaign.routes import FOOT_SPEED_MPS, position, road_between
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


@dataclass(frozen=True)
class ConvoyView:
    id: str
    x: float
    y: float
    heading: float
    vehicles: int
    crew: int
    cargo: int
    phase: str
    progress_m: float


def create_campaign(world):
    if world.data.get("generation"):
        from .generated_campaign import build_generated_state
        state = build_generated_state(world.data)
    else:
        state = load_scenario()
        if world.region_layout:
            layout = world.region_layout
            for key, xy in layout["road_nodes"].items():
                state.nodes[key] = Node(key, Position(*xy))
            for key in layout.get("campaign_remove_roads", []):
                state.roads.pop(key, None)
            for road in layout["roads"]:
                a, b = layout["road_nodes"][road["start"]], layout["road_nodes"][road["end"]]
                state.roads[road["id"]] = Road(road["id"], road["start"], road["end"], math.dist(a, b))
    simulation = Simulation(state)
    simulation.advance_to(state.world_time)
    return simulation


class CampaignBridge:
    def __init__(self, simulation, facility_id):
        self.simulation = simulation
        self.facility_id = facility_id
        state = simulation.state
        self.node_facilities = {facility.node_id: facility for facility in state.facilities.values()}
        self._convoy_time = -1
        self._convoy_ids = []
        self._resumed_at = {}
        self._reports = {}

    @property
    def supplies(self):
        return self.inventory(self.facility_id)["supplies"]

    def inventory(self, facility_id):
        return self.simulation.state.facilities[facility_id].inventory

    def can_radio(self, sender, receiver):
        state = self.simulation.state
        sender_home = self.node_facilities.get(sender.last_node or sender.home_node)
        receiver_home = self.node_facilities.get(receiver.last_node or receiver.home_node)
        if sender_home is None or receiver_home is None or sender_home.region_id != receiver_home.region_id:
            return False
        return any("communication" in state.facilities[key].capabilities and state.facilities[key].damage < 100
                   for key in state.regions[sender_home.region_id].facility_ids)

    def advance_to(self, seconds):
        target = int(seconds + 1e-8)
        if target > self.simulation.state.world_time:
            self.simulation.advance_to(target)

    def report_contact(self, actor, point, seconds):
        state = self.simulation.state
        unit = state.units.get(actor.source_unit)
        facility = self.node_facilities.get(unit.node_id) if unit else None
        if actor.hp <= 0 or not actor.present or facility is None:
            return None
        if seconds < self._reports.get(facility.region_id, -math.inf) + 4:
            return None
        self._reports[facility.region_id] = seconds
        return self.simulation.apply_action({"type": "report_contact", "facility_id": facility.id,
                                             "unit_id": unit.id, "position": list(point)})

    def take_supplies(self, facility_id=None):
        facility_id = facility_id or self.facility_id
        amount = min(10, self.inventory(facility_id)["supplies"])
        if amount:
            self.simulation.apply_action({"type": "capture", "owner_id": facility_id,
                                          "amount": {"supplies": amount}})
        return amount

    def damage_facility(self, facility_id, damage=25):
        self.simulation.apply_action({"type": "damage_facility", "facility_id": facility_id, "damage": damage})
        return self.simulation.state.facilities[facility_id].damage

    def lose_soldier(self, unit_id, *, driver_vehicle_id=None, progress_m=None):
        action = {"type": "damage_unit", "unit_id": unit_id, "soldiers": 1}
        if driver_vehicle_id is not None:
            action.update(driver_vehicle_id=driver_vehicle_id, progress_m=progress_m)
        self.simulation.apply_action(action)

    def resume_transport(self, operation_id, seconds):
        self.simulation.apply_action({"type": "resume_transport", "operation_id": operation_id})
        self._resumed_at[operation_id] = seconds

    def parked(self, nodes):
        state = self.simulation.state
        result = []
        for node in sorted(nodes):
            count = sum(vehicle.node_id == node and vehicle.status in {"available", "reserved"}
                        for vehicle in state.vehicles.values())
            if count:
                point = state.nodes[node].position
                result.append((point.x, point.y, count))
        return result

    def convoys(self, seconds):
        state = self.simulation.state
        if state.world_time != self._convoy_time:
            self._convoy_time = state.world_time
            self._convoy_ids = [op.id for op in state.operations.values()
                                if op.phase in {"outbound", "returning", "unloading", "turnaround", "stranded"}]
        result = []
        for key in self._convoy_ids:
            op = state.operations[key]
            if op.phase not in {"outbound", "returning", "unloading", "turnaround", "stranded"}:
                continue
            point = position(state, op)
            heading = 0.0
            progress = op.progress_m
            if len(op.route) > 1 and op.edge_index == len(op.route) - 1:
                a, b = state.nodes[op.route[-2]].position, state.nodes[op.route[-1]].position
                heading = math.atan2(b.y - a.y, b.x - a.x)
            if op.route and op.edge_index < len(op.route) - 1:
                start, end = op.route[op.edge_index:op.edge_index + 2]
                a, b = state.nodes[start].position, state.nodes[end].position
                heading = math.atan2(b.y - a.y, b.x - a.x)
                if op.reversing:
                    heading += math.pi
                road = road_between(state, start, end)
                vehicles = [state.vehicles[vid] for vid in op.vehicle_ids if state.vehicles[vid].status != "destroyed"]
                if ((vehicles or op.kind in FOOT_OPERATIONS) and op.edge_paid and not op.waiting_reason
                        and op.phase in {"outbound", "returning"} and (not road.blocked or op.reversing)):
                    since = max(state.world_time, self._resumed_at.get(key, 0.0))
                    fraction = max(0, min(1, seconds - since))
                    speed = FOOT_SPEED_MPS if op.kind in FOOT_OPERATIONS else min(vehicle.speed_mps for vehicle in vehicles)
                    progress = (max(0, op.progress_m - speed * fraction) if op.reversing
                                else min(math.nextafter(road.length_m, 0), op.progress_m + speed * fraction))
                    point = Position(a.x + (b.x - a.x) * progress / road.length_m,
                                     a.y + (b.y - a.y) * progress / road.length_m)
            result.append(ConvoyView(key, point.x, point.y, heading,
                                     sum(state.vehicles[vid].status != "destroyed" for vid in op.vehicle_ids),
                                     sum(state.units[uid].soldiers for uid in op.unit_ids),
                                     sum(op.cargo.values()), op.phase, progress))
        return result
