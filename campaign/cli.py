"""배치 실행·상태 조회·동일한 규칙을 사용하는 디버그 콘솔."""

import argparse
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

from .persistence import load_campaign, save_campaign
from .resources import TERMINAL_PHASES, assert_invariants, material_totals
from .routes import position
from .scenario import DEFAULT_SCENARIO, load_scenario
from .simulation import Simulation


def hours_to_seconds(value: str) -> int:
    try:
        seconds = Decimal(value) * 3600
        if not seconds.is_finite() or seconds < 0 or seconds != seconds.to_integral_value():
            raise ValueError
        return int(seconds)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("시간은 음수가 아니며 정수 초로 환산되어야 함") from exc


def summary(state) -> dict:
    return {
        "world_time": state.world_time, "support": state.source.mode, "seed": state.seed,
        "operations": dict(sorted(Counter(op.phase for op in state.operations.values()).items())),
        "external_reserve": state.source.inventory if state.source.mode == "finite" else "infinite",
        "delivered": state.demand.delivered, "outstanding": state.demand.outstanding,
        "physical_stock": material_totals(state), "player_stock": state.player_stock,
        "ledger": asdict(state.ledger),
        "vehicles": dict(sorted(Counter(v.status for v in state.vehicles.values()).items())),
        "soldiers": sum(unit.soldiers for unit in state.units.values()),
        "soldier_losses": sum(unit.losses for unit in state.units.values()),
        "shortages": sum(row["kind"] == "shortage" for row in state.history),
    }


def operation_details(state, operation) -> dict:
    return {**asdict(operation), "position": asdict(position(state, operation)),
            "reservation": asdict(state.reservations[operation.id])
            if operation.id in state.reservations else None}


def asset_details(state) -> dict:
    result = {}
    for name in ("units", "vehicles"):
        rows = {}
        for key, asset in getattr(state, name).items():
            if asset.status == "destroyed" and asset.position is not None:
                point = asset.position
            elif asset.node_id is not None:
                point = state.nodes[asset.node_id].position
            else:
                point = position(state, state.operations[asset.operation_id])
            rows[key] = {**asdict(asset), "position": asdict(point)}
        result[name] = rows
    return result


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def run_schedule(simulation: Simulation, seconds: int, actions: list) -> None:
    start = simulation.state.world_time
    if type(seconds) is not int or seconds < 0 or not isinstance(actions, list):
        raise ValueError("실행 시간·행동 목록 오류")
    target, previous = start + seconds, start
    # 먼저 전체 시각을 검사해 잘못된 스크립트가 일부만 실행되는 일을 막는다.
    for row in actions:
        if (not isinstance(row, dict) or set(row) != {"time", "action"}
                or type(row["time"]) is not int or not previous <= row["time"] <= target
                or not isinstance(row["action"], dict)):
            raise ValueError("행동은 실행 구간 내 절대 시각의 오름차순 목록이어야 함")
        previous = row["time"]
    for row in actions:
        simulation.advance_to(row["time"])
        simulation.apply_action(row["action"])
    simulation.advance_to(target)


def interactive(simulation: Simulation) -> None:
    print("명령: status, facilities, operations, operation ID, assets, history [N], "
          "advance SECONDS, action JSON, save PATH, quit")
    while True:
        try:
            line = input("campaign> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        command, _, argument = line.partition(" ")
        state = simulation.state
        try:
            if command in {"quit", "exit"}:
                break
            if command == "status":
                emit(summary(state))
            elif command == "facilities":
                emit({key: asdict(value) for key, value in state.facilities.items()})
            elif command == "operations":
                emit([operation_details(state, op) for op in state.operations.values()
                      if op.phase not in TERMINAL_PHASES])
            elif command == "operation":
                emit(operation_details(state, state.operations[argument.strip()]))
            elif command == "assets":
                emit(asset_details(state))
            elif command == "history":
                count = int(argument) if argument else 10
                if count <= 0:
                    raise ValueError("조회 개수는 양수여야 함")
                emit(state.history[-count:])
            elif command == "advance":
                seconds = int(argument)
                if seconds < 0:
                    raise ValueError("진행 시간은 음수일 수 없음")
                simulation.advance_to(state.world_time + seconds)
                emit(summary(state))
            elif command == "action":
                simulation.apply_action(json.loads(argument))
                emit(summary(state))
            elif command == "save":
                if not argument.strip():
                    raise ValueError("저장 경로 필요")
                save_campaign(state, argument.strip().strip('"'))
                print("저장 완료")
            elif command:
                print("명령: status, facilities, operations, operation ID, assets, history [N], "
                      "advance SECONDS, action JSON, save PATH, quit")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            print(f"오류: {exc}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="자율 후방 군수 v0.1 검증 실행기")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--support", choices=("finite", "infinite"))
    parser.add_argument("--seed", type=int)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--hours", type=hours_to_seconds, dest="duration")
    duration.add_argument("--seconds", type=int, dest="duration")
    parser.add_argument("--load", type=Path, dest="load_path")
    parser.add_argument("--save", type=Path, dest="save_path")
    parser.add_argument("--actions", type=Path, help="절대 시각과 행동을 담은 JSON 목록")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args(argv)
    if args.load_path and any(value is not None for value in (args.scenario, args.support, args.seed)):
        parser.error("--load는 --scenario·--support·--seed와 함께 사용할 수 없음")
    if args.duration is not None and args.duration < 0:
        parser.error("진행 시간은 음수일 수 없음")
    try:
        state = (load_campaign(args.load_path) if args.load_path else
                 load_scenario(args.scenario or DEFAULT_SCENARIO,
                               support=args.support or "finite", seed=args.seed if args.seed is not None else 42))
        simulation = Simulation(state)
        seconds = args.duration if args.duration is not None else (0 if args.load_path or args.interactive else 86400)
        actions = json.loads(args.actions.read_text(encoding="utf-8")) if args.actions else []
        if not isinstance(actions, list):
            raise ValueError("행동 스크립트는 JSON 목록이어야 함")
        if not args.load_path or seconds or actions:
            run_schedule(simulation, seconds, actions)
        assert_invariants(state)
        emit(summary(state))
        if args.interactive:
            interactive(simulation)
        if args.save_path:
            save_campaign(state, args.save_path)
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"캠페인 실행 실패: {exc}", file=sys.stderr)
        return 2
