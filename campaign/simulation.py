"""고정 전략 틱·사건·외부 개입을 묶는 독립 실행 코어."""

from copy import deepcopy
import math

from . import clock
from .logistics import plan_exports, plan_supplies
from .models import CampaignState, Event
from .qrf import dispatch_response
from .resources import (TERMINAL_PHASES, assert_invariants, cancel, consume, depart,
                        finish_response, finish_return, handover, owner, remove)
from .routes import move_one_second, position, request_return, road_between, set_route


class Simulation:
    def __init__(self, state: CampaignState, *, departure_ready=None):
        self.state = state
        self.departure_ready = departure_ready
        self._moving = {op.id for op in state.operations.values()
                        if op.phase in {"outbound", "returning"}}

    def advance_to(self, world_time: int) -> None:
        state = self.state
        if type(world_time) is not int or world_time < state.world_time:
            raise ValueError("목표 시각은 현재 이후의 정수 초여야 함")
        if not state.started:
            state.started = True
            clock.schedule(state, state.world_time, "planning")
            clock.schedule(state, state.world_time + state.rules["consumption_period_s"],
                           "consumption", priority=clock.CONSUMPTION)
            clock.schedule(state, state.world_time + state.demand.period_s,
                           "demand", priority=clock.DEMAND)
        clock.advance_to(state, world_time, self._handle_event, tick=self._tick)
        assert_invariants(state)

    def _tick(self) -> None:
        state = self.state
        for operation_id in sorted(self._moving):
            operation = state.operations[operation_id]
            if move_one_second(state, operation):
                if operation.phase == "returning":
                    origin = owner(state, operation.origin_id)
                    if (operation.origin_id != state.source.id and
                            (origin.damage == 100 or state.regions[origin.region_id].owner != "enemy")):
                        operation.waiting_reason = "origin_unavailable"
                    else:
                        finish_return(state, operation_id)
                elif operation.kind == "qrf":
                    if state.regions[state.facilities[operation.destination_id].region_id].owner == "enemy":
                        finish_response(state, operation)
                    else:
                        request_return(state, operation)
                else:
                    operation.phase = "unloading"
                    state.record("arrived", operation_id=operation_id)
                    clock.schedule(state, state.world_time + state.rules["unloading_s"],
                                   "unload", priority=clock.TRANSFER, operation_id=operation_id)
            if operation.phase not in {"outbound", "returning"}:
                self._moving.discard(operation_id)

    def _handle_event(self, event: Event) -> None:
        state = self.state
        operation = state.operations.get(event.operation_id)
        if event.kind == "planning":
            plan_supplies(state)
            plan_exports(state)
            clock.schedule(state, state.world_time + state.rules["planning_period_s"], "planning")
        elif event.kind == "demand":
            for name, amount in state.demand.cargo.items():
                state.demand.outstanding[name] += amount
            state.record("export_demand", amount=dict(state.demand.cargo))
            clock.schedule(state, state.world_time + state.demand.period_s,
                           "demand", priority=clock.DEMAND)
        elif event.kind == "consumption":
            self._consume_garrisons()
            clock.schedule(state, state.world_time + state.rules["consumption_period_s"],
                           "consumption", priority=clock.CONSUMPTION)
        elif operation is not None:
            if event.kind == "depart" and operation.phase == "loading":
                origin = owner(state, operation.origin_id)
                invalid = False
                if operation.kind != "export":
                    destination = state.facilities[operation.destination_id]
                    invalid = (state.regions[destination.region_id].owner != "enemy"
                               or (operation.kind != "qrf" and destination.damage == 100))
                if operation.origin_id != state.source.id:
                    invalid |= origin.damage == 100 or state.regions[origin.region_id].owner != "enemy"
                if invalid:
                    cancel(state, operation.id)
                elif self.departure_ready is not None and not self.departure_ready(operation):
                    if operation.waiting_reason != "crew_gathering":
                        state.record("departure_wait", operation_id=operation.id, reason="crew_gathering")
                    operation.waiting_reason = "crew_gathering"
                    clock.schedule(state, state.world_time + 5, "depart",
                                   priority=clock.TRANSFER, operation_id=operation.id)
                elif operation.origin_id == state.source.id and state.world_time < state.source.next_departure:
                    clock.schedule(state, state.source.next_departure, "depart",
                                   priority=clock.TRANSFER, operation_id=operation.id)
                elif depart(state, operation.id):
                    operation.waiting_reason = ""
                    if operation.origin_id == state.source.id:
                        state.source.next_departure = state.world_time + state.rules["external_departure_interval_s"]
                    set_route(state, operation)
            elif event.kind == "unload" and operation.phase == "unloading":
                if handover(state, operation.id):
                    clock.schedule(state, state.world_time + state.rules["turnaround_s"],
                                   "begin_return", priority=clock.TRANSFER, operation_id=operation.id)
                else:
                    request_return(state, operation)
            elif event.kind == "begin_return" and operation.phase == "turnaround":
                request_return(state, operation)
            if operation.phase in {"outbound", "returning"}:
                self._moving.add(operation.id)

    def _consume_garrisons(self) -> None:
        state = self.state
        rate = state.rules["supplies_per_soldier"]
        for facility in sorted(state.facilities.values(), key=lambda item: item.id):
            soldiers = sum(u.soldiers for u in state.units.values() if u.node_id == facility.node_id)
            consumed = consume(state, facility.inventory, {"supplies": soldiers * rate},
                               reason="garrison_upkeep", owner_id=facility.id)
            if consumed["supplies"] < soldiers * rate:
                state.record("shortage", facility_id=facility.id,
                             missing=soldiers * rate - consumed["supplies"])
        for operation in state.operations.values():
            if operation.kind == "qrf" or operation.phase in TERMINAL_PHASES or operation.phase == "loading":
                continue
            soldiers = sum(state.units[uid].soldiers for uid in operation.unit_ids)
            consumed = consume(state, operation.provisions, {"supplies": soldiers * rate},
                               reason="transport_escort_upkeep")
            if consumed["supplies"] < soldiers * rate:
                state.record("shortage", operation_id=operation.id,
                             missing=soldiers * rate - consumed["supplies"])

    def apply_action(self, action: dict) -> None:
        state = self.state
        kind = action["type"]
        if kind == "block_road":
            if type(action["blocked"]) is not bool:
                raise ValueError("도로 차단 값은 bool이어야 함")
            state.roads[action["road_id"]].blocked = action["blocked"]
        elif kind in {"capture", "destroy_stock"}:
            owner_id = action.get("owner_id")
            if owner_id is not None and "operation_id" in action:
                raise ValueError("시설과 작전 중 하나만 지정해야 함")
            if "operation_id" in action:
                operation = state.operations[action["operation_id"]]
                if operation.phase in TERMINAL_PHASES:
                    raise ValueError("종료된 작전의 화물을 처리할 수 없음")
                if operation.phase == "loading":
                    owner_id = operation.origin_id
                    if owner_id == state.source.id and state.source.mode == "infinite":
                        raise ValueError("무한 외부 예비 물자는 출발 후 실제 화물에서 처리해야 함")
                    inventory = owner(state, owner_id).inventory
                else:
                    inventory = operation.cargo
            else:
                inventory = state.facilities[owner_id].inventory
            remove(state, inventory, action["amount"],
                   outcome="captured" if kind == "capture" else "destroyed", owner_id=owner_id)
        elif kind in {"destroy_vehicle", "damage_unit"}:
            self._damage_asset(action)
        elif kind == "resume_transport":
            operation = state.operations[action["operation_id"]]
            vehicles = [vid for vid in operation.vehicle_ids if state.vehicles[vid].status != "destroyed"]
            if (operation.phase not in {"outbound", "returning"}
                    or operation.waiting_reason != "driver_down" or not vehicles
                    or sum(state.units[uid].soldiers for uid in operation.unit_ids) < len(vehicles)):
                raise ValueError("모든 운행 차량에 대체 운전자가 필요함")
            operation.waiting_reason = ""
        elif kind == "report_facility_attack":
            facility_id = action["facility_id"]
            status, operation_id = dispatch_response(state, facility_id)
            state.record("qrf_report", facility_id=facility_id, status=status, operation_id=operation_id)
        elif kind == "damage_facility":
            damage = action["damage"]
            if type(damage) is not int or damage < 0:
                raise ValueError("시설 피해는 음수가 아닌 정수여야 함")
            facility = state.facilities[action["facility_id"]]
            facility.damage = min(100, facility.damage + damage)
            if facility.damage == 100:
                for operation in list(state.operations.values()):
                    if (operation.origin_id == facility.id
                            or (operation.destination_id == facility.id and operation.kind != "qrf")):
                        cancel(state, operation.id)
        elif kind == "return_operation":
            operation = state.operations[action["operation_id"]]
            if operation.waiting_reason == "driver_down":
                raise ValueError("운전자 교대 후 귀환할 수 있음")
            if not cancel(state, operation.id):
                request_return(state, operation)
                self._moving.add(operation.id)
        else:
            raise ValueError(f"지원하지 않는 행동: {kind}")
        state.record("action", action=deepcopy(action))
        assert_invariants(state)

    def _damage_asset(self, action: dict) -> None:
        state = self.state
        vehicle_loss = action["type"] == "destroy_vehicle"
        asset = (state.vehicles[action["vehicle_id"]] if vehicle_loss
                 else state.units[action["unit_id"]])
        if asset.status == "destroyed":
            raise ValueError("이미 격파된 자산")
        losses = 0 if vehicle_loss else action["soldiers"]
        if not vehicle_loss and (type(losses) is not int or not 0 < losses <= asset.soldiers):
            raise ValueError("잘못된 병력 손실량")
        operation = state.operations.get(asset.operation_id)
        driver_vehicle = action.get("driver_vehicle_id") if not vehicle_loss else None
        if driver_vehicle is not None:
            if (operation is None or operation.phase not in {"outbound", "returning"}
                    or operation.waiting_reason == "driver_down" or losses != 1
                    or driver_vehicle not in operation.vehicle_ids
                    or state.vehicles[driver_vehicle].status == "destroyed"):
                raise ValueError("운행 중인 차량의 운전자 손실만 처리할 수 있음")
            progress = action.get("progress_m", operation.progress_m)
            if (type(progress) not in {int, float} or not math.isfinite(progress)
                    or progress < 0 or (progress != operation.progress_m and not operation.edge_paid)):
                raise ValueError("잘못된 정차 위치")
            if progress != operation.progress_m:
                start, end = operation.route[operation.edge_index:operation.edge_index + 2]
                if (progress >= road_between(state, start, end).length_m
                        or (progress < operation.progress_m) != operation.reversing):
                    raise ValueError("현재 도로 구간 안에서 정차해야 함")
                operation.progress_m = progress
            # 기존 이동 단계와 경로를 유지해야 교대 후 같은 지점에서 이어서 운행할 수 있다.
            operation.waiting_reason = "driver_down"
        location = deepcopy(position(state, operation) if asset.node_id is None
                            else state.nodes[asset.node_id].position)
        if vehicle_loss:
            if operation is not None and operation.phase != "loading":
                capacity = sum(state.vehicles[vid].capacity for vid in operation.vehicle_ids
                               if state.vehicles[vid].status != "destroyed")
                # 원거리 화물은 적재 용량 비율로 분산된 것으로 취급한다.
                for inventory in (operation.cargo, operation.provisions):
                    amount = {name: (value * asset.capacity + capacity - 1) // capacity
                              for name, value in inventory.items()}
                    remove(state, inventory, amount, outcome="destroyed")
            asset.status = "destroyed"
        else:
            asset.soldiers -= losses
            asset.losses += losses
            if asset.soldiers == 0:
                asset.status = "destroyed"
        if asset.status == "destroyed":
            asset.position, asset.operation_id = location, None
        if operation is not None:
            if operation.phase == "loading":
                cancel(state, operation.id)
            else:
                vehicles = [state.vehicles[vid] for vid in operation.vehicle_ids
                            if state.vehicles[vid].status != "destroyed"]
                soldiers = sum(state.units[uid].soldiers for uid in operation.unit_ids)
                if operation.kind == "qrf":
                    if not soldiers:
                        operation.phase = "destroyed"
                        operation.completed_at = state.world_time
                        self._moving.discard(operation.id)
                elif not vehicles:
                    operation.phase = "stranded" if soldiers else "destroyed"
                    operation.waiting_reason = "no_vehicle"
                    if not soldiers:
                        operation.completed_at = state.world_time
                    self._moving.discard(operation.id)
                elif operation.waiting_reason == "driver_down" and soldiers < len(vehicles):
                    operation.phase = "stranded"
                    operation.waiting_reason = "no_driver"
                    self._moving.discard(operation.id)
                elif (soldiers < state.rules["escort_soldiers"] * len(vehicles)
                      and operation.waiting_reason != "driver_down"
                      and operation.phase in {"outbound", "unloading"}):
                    request_return(state, operation)
                    self._moving.add(operation.id)
        if asset.node_id is not None:
            # 주둔 손실 이후 미출발 동원이 최소 주둔량을 침해하지 않게 한다.
            for candidate in reversed(list(state.operations.values())):
                if candidate.phase != "loading" or candidate.origin_id == state.source.id:
                    continue
                origin = state.facilities[candidate.origin_id]
                region = state.regions[origin.region_id]
                nodes = {state.facilities[fid].node_id for fid in region.facility_ids}
                if asset.node_id in nodes:
                    remaining = sum(u.soldiers for u in state.units.values()
                                    if u.node_id in nodes and u.status == "available")
                    if remaining < region.minimum_garrison:
                        cancel(state, candidate.id)
