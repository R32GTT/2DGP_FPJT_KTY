"""고정 전략 틱·사건·외부 개입을 묶는 독립 실행 코어."""

from . import clock
from .logistics import plan_exports, plan_supplies
from .models import CampaignState, Event
from .resources import (TERMINAL_PHASES, assert_invariants, cancel, consume, depart,
                        finish_return, handover, owner, remove)
from .routes import move_one_second, request_return, set_route


class Simulation:
    def __init__(self, state: CampaignState):
        self.state = state
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
                    invalid = destination.damage == 100 or state.regions[destination.region_id].owner != "enemy"
                if operation.origin_id != state.source.id:
                    invalid |= origin.damage == 100 or state.regions[origin.region_id].owner != "enemy"
                if invalid:
                    cancel(state, operation.id)
                elif operation.origin_id == state.source.id and state.world_time < state.source.next_departure:
                    clock.schedule(state, state.source.next_departure, "depart",
                                   priority=clock.TRANSFER, operation_id=operation.id)
                elif depart(state, operation.id):
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
            if operation.phase in TERMINAL_PHASES or operation.phase == "loading":
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
            if "operation_id" in action:
                operation = state.operations[action["operation_id"]]
                if operation.phase in TERMINAL_PHASES:
                    raise ValueError("종료된 작전의 화물을 처리할 수 없음")
                if operation.phase == "loading":
                    owner_id = operation.origin_id
                    inventory = owner(state, owner_id).inventory
                else:
                    inventory = operation.cargo
            else:
                inventory = state.facilities[owner_id].inventory
            remove(state, inventory, action["amount"],
                   outcome="captured" if kind == "capture" else "destroyed", owner_id=owner_id)
        elif kind == "damage_facility":
            damage = action["damage"]
            if type(damage) is not int or damage < 0:
                raise ValueError("시설 피해는 음수가 아닌 정수여야 함")
            facility = state.facilities[action["facility_id"]]
            facility.damage = min(100, facility.damage + damage)
            if facility.damage == 100:
                for operation in list(state.operations.values()):
                    if operation.origin_id == facility.id or operation.destination_id == facility.id:
                        cancel(state, operation.id)
        elif kind == "return_operation":
            operation = state.operations[action["operation_id"]]
            if not cancel(state, operation.id):
                request_return(state, operation)
                self._moving.add(operation.id)
        else:
            raise ValueError(f"지원하지 않는 행동: {kind}")
        state.record("action", action=dict(action))
        assert_invariants(state)
