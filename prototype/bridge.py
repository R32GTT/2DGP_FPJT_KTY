"""전술 지역과 기존 군수 코어의 좌표·병력·재고·수송을 연결한다."""

from dataclasses import dataclass
import math

from campaign.models import FOOT_OPERATIONS, Node, Position, Road
from campaign.qrf import air_support_available, regional_command, regional_signal
from campaign.routes import FOOT_SPEED_MPS, movement_supply_cost
from campaign.scenario import load_scenario
from campaign.simulation import Simulation

from .geometry import Rect
from .spatial import BoundsIndex


VISIBLE_PHASES = {"outbound", "returning", "unloading", "turnaround", "working", "stranded"}
CONVOY_SPACING_M = 6.4


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
    edge_index: int = 0
    reversing: bool = False


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
        self.route_roads = {frozenset((road.start, road.end)): road for road in state.roads.values()}
        self._transport_stamp = None
        self._convoy_ids, self._parked_nodes = [], {}
        self._convoy_index = BoundsIndex()
        self._road_scale = max((math.dist((state.nodes[road.start].position.x, state.nodes[road.start].position.y),
                                          (state.nodes[road.end].position.x, state.nodes[road.end].position.y))
                                / road.length_m for road in state.roads.values()), default=1)
        self._road_scale = max(1, self._road_scale)
        self._resumed_at = {}
        self._reports = {}

    @property
    def supplies(self):
        return self.inventory(self.facility_id)["supplies"]

    def inventory(self, facility_id):
        return self.simulation.state.facilities[facility_id].inventory

    def actor_region(self, actor):
        facility = self.node_facilities.get(actor.last_node or actor.home_node)
        return facility.region_id if facility else self.simulation.state.facilities[self.facility_id].region_id

    def has_signal(self, region_id):
        return regional_signal(self.simulation.state, region_id)

    def local_command(self, region_id):
        return regional_command(self.simulation.state, region_id)

    def can_radio_locally(self, region_id):
        return self.local_command(region_id) is not None or self.has_signal(region_id)

    def air_support_available(self, region_id):
        return air_support_available(self.simulation.state, region_id)

    def advance_to(self, seconds):
        target = int(seconds + 1e-8)
        if target > self.simulation.state.world_time:
            self.simulation.advance_to(target)

    def report_contact(self, actor, region_id, point, seconds):
        state = self.simulation.state
        unit = state.units.get(actor.source_unit)
        facility = self.node_facilities.get(unit.node_id) if unit else None
        if unit and unit.node_id is None and unit.operation_id:
            operation = state.operations[unit.operation_id]
            endpoints = (state.facilities.get(key) for key in (operation.origin_id, operation.destination_id))
            facility = next((site for site in endpoints if site and site.region_id == region_id), None)
        if actor.hp <= 0 or not actor.present or facility is None:
            return None
        if facility.region_id != region_id or not self._allow_report(region_id, seconds):
            return None
        return self.simulation.apply_action({"type": "report_contact", "facility_id": facility.id,
                                             "unit_id": unit.id, "position": list(point)})

    def report_facility_attack(self, facility_id, point, seconds):
        facility = self.simulation.state.facilities[facility_id]
        if not self._allow_report(facility.region_id, seconds):
            return None
        return self.simulation.apply_action({"type": "report_facility_attack", "facility_id": facility_id,
                                             "position": list(point)})

    def _allow_report(self, region_id, seconds):
        if seconds < self._reports.get(region_id, -math.inf) + 4:
            return False
        self._reports[region_id] = seconds
        return True

    def disable_component(self, facility_id, component):
        return self.simulation.apply_action({"type": "disable_facility_component", "facility_id": facility_id,
                                             "component": component})

    def take_supplies(self, facility_id=None):
        facility_id = facility_id or self.facility_id
        amount = min(10, self.inventory(facility_id)["supplies"])
        if amount:
            self.simulation.apply_action({"type": "capture", "owner_id": facility_id,
                                          "amount": {"supplies": amount}})
        return amount

    def damage_facility(self, facility_id, damage=25, *, destroy_inventory=False):
        action = {"type": "damage_facility", "facility_id": facility_id, "damage": damage}
        if destroy_inventory:
            action["destroy_inventory"] = True
        self.simulation.apply_action(action)
        return self.simulation.state.facilities[facility_id].damage

    def lose_soldier(self, unit_id, *, driver_vehicle_id=None, progress_m=None, night_kit=None):
        action = {"type": "damage_unit", "unit_id": unit_id, "soldiers": 1}
        if night_kit:
            action["night_kit"] = True
        if driver_vehicle_id is not None:
            action.update(driver_vehicle_id=driver_vehicle_id, progress_m=progress_m)
        self.simulation.apply_action(action)

    def resume_transport(self, operation_id, seconds):
        self.simulation.apply_action({"type": "resume_transport", "operation_id": operation_id})
        self._resumed_at[operation_id] = seconds

    def parked(self, nodes):
        state = self.simulation.state
        self._index_transports()
        return [(state.nodes[node].position.x, state.nodes[node].position.y, self._parked_nodes[node])
                for node in sorted(self._parked_nodes.keys() & nodes)]

    def _route_pose(self, route, edge, progress, reversing):
        state = self.simulation.state
        if len(route) == 1:
            point = state.nodes[route[0]].position
            return point.x, point.y, 0.0
        if edge == len(route) - 1:
            edge -= 1
            progress = self.route_roads[frozenset(route[edge:edge + 2])].length_m
        start, end = route[edge:edge + 2]
        a, b = state.nodes[start].position, state.nodes[end].position
        fraction = progress / self.route_roads[frozenset((start, end))].length_m
        heading = math.atan2(b.y - a.y, b.x - a.x)
        return a.x + (b.x - a.x) * fraction, a.y + (b.y - a.y) * fraction, heading + (math.pi if reversing else 0)

    def _preview_motion(self, operation, speed, vehicle_count, fraction):
        """다음 전략 틱이 실제로 지나갈 도로를 따라 현재 프레임 위치를 구한다."""
        state = self.simulation.state
        edge, progress = operation.edge_index, operation.progress_m
        paid, reversing = operation.edge_paid, operation.reversing
        supplies = operation.provisions["supplies"]
        odometer = operation.operating_distance_m
        budget, travel = speed, speed * fraction
        quantum_cost = "road_cost_distance_m" in state.rules
        while travel > 0 and edge < len(operation.route) - 1:
            road = self.route_roads[frozenset(operation.route[edge:edge + 2])]
            if road.blocked and not reversing:
                break
            if not paid and not reversing and not quantum_cost:
                supplies -= vehicle_count * state.rules["road_cost_per_vehicle"]
            segment = min(budget, progress if reversing else road.length_m - progress)
            cost, odometer = movement_supply_cost(state, odometer, segment, vehicle_count)
            if cost > supplies:
                break
            supplies -= cost
            distance = min(segment, travel)
            progress += -distance if reversing else distance
            if reversing or distance < segment or progress < road.length_m:
                break
            edge, progress, paid = edge + 1, 0.0, False
            budget, travel = budget - segment, travel - segment
        return edge, progress

    def following_pose(self, view, distance):
        """후속 대열도 선두의 접선이 아닌 실제 도로를 따라간다."""
        if not distance:
            return view.x, view.y, view.heading
        route = self.simulation.state.operations[view.id].route
        if len(route) < 2:
            return view.x - distance * math.cos(view.heading), view.y - distance * math.sin(view.heading), view.heading
        edge, progress = view.edge_index, view.progress_m
        if edge == len(route) - 1:
            edge -= 1
            progress = self.route_roads[frozenset(route[edge:edge + 2])].length_m
        if view.reversing:
            progress += distance
            while edge < len(route) - 2:
                length = self.route_roads[frozenset(route[edge:edge + 2])].length_m
                if progress <= length:
                    break
                edge, progress = edge + 1, progress - length
        else:
            progress -= distance
            while progress < 0 and edge > 0:
                edge -= 1
                progress += self.route_roads[frozenset(route[edge:edge + 2])].length_m
        return self._route_pose(route, edge, progress, view.reversing)

    def _index_transports(self):
        state = self.simulation.state
        stamp = self.simulation.revision, state.world_time, len(state.operations), len(state.vehicles)
        if stamp == self._transport_stamp:
            return
        self._convoy_ids, self._parked_nodes = [], {}
        self._convoy_index = BoundsIndex()
        for vehicle in state.vehicles.values():
            if vehicle.node_id is not None and vehicle.status in {"available", "reserved"}:
                self._parked_nodes[vehicle.node_id] = self._parked_nodes.get(vehicle.node_id, 0) + 1
        for op in state.active_operations():
            if op.phase not in VISIBLE_PHASES:
                continue
            x, y, _ = self._route_pose(op.route or [op.home_node], op.edge_index, op.progress_m, op.reversing)
            speed = (FOOT_SPEED_MPS if op.kind in FOOT_OPERATIONS
                     else max((state.vehicles[key].speed_mps for key in op.vehicle_ids), default=0))
            # 다음 전략 틱까지의 이동과 후속 차량의 차체를 모두 포함한다.
            reach = (speed + CONVOY_SPACING_M * max(0, len(op.vehicle_ids) - 1)) * self._road_scale + 4
            self._convoy_index.add(len(self._convoy_ids), Rect(x, y, 0, 0).expanded(reach), op.id)
            self._convoy_ids.append(op.id)
        self._transport_stamp = stamp

    def convoys(self, seconds, *, bounds=None):
        state = self.simulation.state
        self._index_transports()
        result = []
        ids = self._convoy_ids if bounds is None else self._convoy_index.query(bounds)
        for key in ids:
            op = state.operations[key]
            if op.phase not in VISIBLE_PHASES:
                continue
            route = op.route or [op.home_node]
            edge, progress = op.edge_index, op.progress_m
            vehicles = [state.vehicles[vid] for vid in op.vehicle_ids if state.vehicles[vid].status != "destroyed"]
            if (edge < len(route) - 1 and (vehicles or op.kind in FOOT_OPERATIONS)
                    and not op.waiting_reason and op.phase in {"outbound", "returning"}):
                since = max(state.world_time, self._resumed_at.get(key, 0.0))
                fraction = max(0, min(1, seconds - since))
                speed = FOOT_SPEED_MPS if op.kind in FOOT_OPERATIONS else min(vehicle.speed_mps for vehicle in vehicles)
                edge, progress = self._preview_motion(op, speed, len(vehicles), fraction)
            x, y, heading = self._route_pose(route, edge, progress, op.reversing)
            result.append(ConvoyView(key, x, y, heading, len(vehicles),
                                     sum(state.units[uid].soldiers for uid in op.unit_ids),
                                     sum(op.cargo.values()), op.phase, progress, edge, op.reversing))
        return result
