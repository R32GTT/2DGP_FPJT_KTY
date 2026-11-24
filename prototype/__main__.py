"""검증용 최소 실행기. 일반 테스트·플레이 평가는 사용자가 수행한다."""

import argparse
import math
from pathlib import Path
from time import perf_counter, sleep

from .ai_scenarios import SCENARIOS
from .simulation import FIXED_DT, PlaySimulation
from .savegame import CampaignSaveError, DEFAULT_SAVE, load_game, save_game
from .world import DEFAULT_SCENE


SIMULATION_FRAME_BUDGET_S = 0.012
MAX_SIMULATION_BACKLOG_S = 0.1


def run_window(simulation, *, frames=0, zoom=None, paused=None, debug=None, map_open=False):
    import pico2d
    from .camera import CANVAS_SIZE, PlayCamera
    from .controls import Controls
    from .rendering import Renderer

    pico2d.open_canvas(*CANVAS_SIZE, sync=True)
    renderer = None
    try:
        renderer = Renderer(simulation.world)
        facility_test = simulation.world.data.get("training_facility", False)
        test_mode = facility_test or bool(simulation.ai_scenario)
        controls = Controls()
        camera = PlayCamera(zoom=zoom if zoom is not None else 0.55 if test_mode else 1.0)
        controls.debug = test_mode if debug is None else debug
        controls.paused = test_mode if paused is None else paused
        controls.map_open = map_open
        camera.follow(simulation.player.xy)
        previous, accumulator, count = perf_counter(), 0.0, 0
        frame_deadline = previous
        last_step_s = FIXED_DT
        stack_previewed = False
        while controls.running and (not frames or count < frames):
            started = perf_counter()
            elapsed, previous = min(MAX_SIMULATION_BACKLOG_S, started - previous), started
            for action in controls.poll(camera):
                if action in {"save", "load"}:
                    try:
                        if not simulation.presence:
                            raise CampaignSaveError("저장·불러오기는 절차 생성 월드에서 사용하세요")
                        if action == "save":
                            save_game(simulation)
                            simulation.message = "월드 저장 완료 · F9 불러오기"
                        else:
                            loaded = load_game()
                            replacement = Renderer(loaded.world)
                            renderer.close()
                            renderer, simulation = replacement, loaded
                            controls.keys.clear()
                            controls.firing = controls.aiming = controls.trigger_pending = False
                            controls.paused = True
                    except CampaignSaveError as error:
                        simulation.message = str(error)
                    previous, elapsed, accumulator = perf_counter(), 0.0, 0.0
                    continue
                select_drill = action == "next_ai" and "search_drill" in simulation.world.data
                select_stack = action == "next_ai" and "stack_drill" in simulation.world.data
                next_scenario = action == "next_ai" and simulation.ai_scenario
                if (test_mode and action == "restart_ai") or select_drill or select_stack or next_scenario:
                    scenario = simulation.ai_scenario
                    if next_scenario:
                        cases = tuple(SCENARIOS)
                        scenario = cases[(cases.index(scenario) + 1) % len(cases)]
                    stack_variant = simulation.stack_variant
                    if select_stack and simulation.stack_drill:
                        stack_variant = (stack_variant + 1) % len(simulation.world.data["stack_drill"]["layouts"])
                    simulation = PlaySimulation(simulation.world.source, ai_scenario=scenario,
                                                search_drill=select_drill or simulation.search_drill,
                                                stack_drill=select_stack or simulation.stack_drill,
                                                stack_variant=stack_variant)
                    stack_previewed = False
                    controls.keys.clear()
                    controls.firing = controls.aiming = controls.trigger_pending = controls.map_open = False
                    controls.debug = controls.paused = True
                    continue
                if not controls.paused:
                    if action in {"lamp", "night_vision", "ir_laser"}:
                        if not controls.map_open and not simulation.respawn_selecting:
                            simulation.toggle_optics(action)
                        continue
                    if simulation.respawn_selecting:
                        if action.startswith("slot_"):
                            simulation.request_respawn(int(action[-1]))
                            controls.map_open = False
                        elif action == "cancel":
                            simulation.respawn_selecting = False
                        continue
                    if (controls.firing or controls.map_open) and action in {"door", "supplies", "facility_alternate"}:
                        simulation.message = "지도를 닫은 뒤 상호작용" if controls.map_open else "사격 중에는 상호작용할 수 없음"
                        continue
                    if action == "door":
                        simulation.use_door()
                    elif action == "supplies":
                        simulation.take_supplies()
                    elif action == "facility_alternate":
                        simulation.use_facility_alternate()
                    elif action == "reload":
                        simulation.reload()
                    elif action == "cancel":
                        simulation.cancel_reload()
                    elif action in {"slot_0", "slot_1"}:
                        simulation.switch_weapon(int(action[-1]))
                    elif action == "fire_mode" and simulation.player.weapon:
                        simulation.player.weapon.automatic = not simulation.player.weapon.automatic
                    elif action == "respawn" and simulation.player.hp <= 0:
                        controls.map_open = False
                        simulation.request_respawn()
            map_open = controls.map_open or (simulation.respawn_selecting and not controls.paused)
            aiming = controls.aiming and not (controls.paused or map_open)
            camera.follow(simulation.player.xy, None if map_open else controls.mouse, aiming=aiming, dt=elapsed)
            aim_offset = None
            if not map_open:
                aim_x, aim_y = camera.world(*controls.mouse)
                aim_offset = aim_x - simulation.player.position.x, aim_y - simulation.player.position.y
            if controls.paused:
                accumulator = 0.0
                controls.trigger_pending = False
            else:
                accumulator = min(MAX_SIMULATION_BACKLOG_S, accumulator + elapsed)
                steps = 0
                while accumulator >= FIXED_DT and steps < 6:
                    step_started = perf_counter()
                    if steps and step_started - started + last_step_s > SIMULATION_FRAME_BUDGET_S:
                        break
                    pressed = controls.consume_trigger()
                    simulation.step((0, 0) if map_open else controls.direction,
                                    aim_offset=aim_offset, firing=not map_open and (controls.firing or pressed),
                                    sprint=controls.sprint, aiming=aiming,
                                    trigger_pressed=pressed and not map_open, walking=controls.walking)
                    last_step_s = perf_counter() - step_started
                    accumulator -= FIXED_DT
                    steps += 1
                    if (simulation.stack_drill and not stack_previewed
                            and any(stack.ready_at is not None for stack in simulation.ai.door_traffic.stacks.values())):
                        stack_previewed = controls.paused = True
                        accumulator = 0.0
                        simulation.message = f"{simulation.stack_title} 완료 · P 통과 재개"
                        break
            camera.recenter(simulation.player.xy)
            update_ms = (perf_counter() - started) * 1000
            renderer.render(simulation, camera, debug=controls.debug, paused=controls.paused,
                            update_ms=update_ms, mouse=controls.mouse, map_open=map_open)
            count += 1
            # 대기가 길어진 오차를 다음 프레임에 누적하지 않는다.
            frame_deadline += FIXED_DT
            now = perf_counter()
            remaining = frame_deadline - now
            if remaining > 0:
                sleep(remaining)
            elif remaining < -FIXED_DT:
                frame_deadline = now
    finally:
        if renderer is not None:
            renderer.close()
        pico2d.close_canvas()


def main(argv=None):
    parser = argparse.ArgumentParser(description="그림 맵·미터 좌표·기초 AI·발사·군수 연결 검증 목업")
    scene_options = parser.add_mutually_exclusive_group()
    scene_options.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    scene_options.add_argument("--generate", type=int, metavar="SEED", help="시드로 지형·도로·프리팹 시설 생성")
    scene_options.add_argument("--load", type=Path, nargs="?", const=DEFAULT_SAVE, help="생성 월드 이어하기")
    parser.add_argument("--region-size", choices=("small", "medium", "large", "xlarge"))
    parser.add_argument("--regions", type=int, choices=(1, 2, 3, 4), help="생성할 지역 수")
    parser.add_argument("--importance", choices=("normal", "important", "core"))
    parser.add_argument("--region-sizes", help="지역 순서별 크기, 쉼표로 구분 (예: small,medium,small,small)")
    parser.add_argument("--region-importances", help="지역 순서별 중요도, 쉼표로 구분")
    drills = parser.add_mutually_exclusive_group()
    drills.add_argument("--ai-scenario", choices=SCENARIOS, help="물류창고 AI 검증 장면")
    drills.add_argument("--search-drill", action="store_true", help="선택한 시설에서 실내 수색 훈련")
    drills.add_argument("--stack-variant", type=int, help="문 집결 훈련 배치 번호 (0부터)")
    parser.add_argument("--zoom", type=float, help="시작 줌 (0.5~4.0)")
    parser.add_argument("--paused", action=argparse.BooleanOptionalAction, default=None, help="일시정지 상태로 시작")
    parser.add_argument("--debug", action=argparse.BooleanOptionalAction, default=None, help="개발 정보 표시")
    parser.add_argument("--map-open", action="store_true", help="지도를 연 상태로 시작")
    parser.add_argument("--headless", action="store_true", help="그래픽 없이 코어만 짧게 실행")
    parser.add_argument("--frames", type=int, default=0, help="지정 프레임 뒤 종료. 0이면 창을 닫을 때까지 실행")
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("프레임 수는 음수일 수 없음")
    if args.zoom is not None and (not math.isfinite(args.zoom) or not 0.5 <= args.zoom <= 4):
        parser.error("시작 줌은 0.5~4.0 사이여야 합니다")
    if args.stack_variant is not None and args.stack_variant < 0:
        parser.error("문 집결 배치 번호는 음수일 수 없습니다")
    if args.generate is None and (args.region_size or args.importance or args.regions or args.region_sizes or args.region_importances):
        parser.error("지역 수·크기·중요도는 --generate와 함께 사용하세요")
    if args.load and (args.ai_scenario or args.search_drill or args.stack_variant is not None):
        parser.error("이어하기와 AI 훈련을 함께 사용할 수 없습니다")
    if args.ai_scenario and (args.generate is not None or args.scene.resolve() != DEFAULT_SCENE.resolve()):
        parser.error("--ai-scenario는 기본 지역 장면에서 실행하세요")
    scene = args.scene
    if args.generate is not None:
        from .generated_world import generate_world
        try:
            scene = generate_world(args.generate, region_count=args.regions or 1,
                                   region_size=args.region_size or "small", importance=args.importance or "normal",
                                   region_sizes=args.region_sizes.split(',') if args.region_sizes else None,
                                   region_importances=args.region_importances.split(',') if args.region_importances else None)
        except ValueError as error:
            parser.error(str(error))
    try:
        simulation = load_game(args.load) if args.load else PlaySimulation(
            scene, ai_scenario=args.ai_scenario, search_drill=args.search_drill,
            stack_drill=args.stack_variant is not None, stack_variant=args.stack_variant or 0)
    except CampaignSaveError as error:
        parser.error(str(error))
    if args.generate is not None:
        simulation.message = f"생성 맵 · 시드 {args.generate} · WASD 이동 / M 지도 / F5 저장 / F9 불러오기"
    if args.headless:
        for _ in range(args.frames or 120):
            simulation.step()
        print(f"목업 코어 실행: {simulation.time:.2f}s / AI {len(simulation.guards)} / 창고 {simulation.campaign.supplies}")
    else:
        run_window(simulation, frames=args.frames, zoom=args.zoom, paused=args.paused,
                   debug=args.debug, map_open=args.map_open)


if __name__ == "__main__":
    main()
