"""검증용 최소 실행기. 일반 테스트·플레이 평가는 사용자가 수행한다."""

import argparse
from pathlib import Path
from time import perf_counter, sleep

from .ai_scenarios import SCENARIOS
from .simulation import FIXED_DT, PlaySimulation
from .world import DEFAULT_SCENE


def run_window(simulation, *, frames=0):
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
        controls, camera = Controls(), PlayCamera(zoom=0.55 if test_mode else 1.0)
        controls.debug = controls.paused = test_mode
        camera.follow(simulation.player.xy)
        previous, accumulator, count = perf_counter(), 0.0, 0
        stack_previewed = False
        while controls.running and (not frames or count < frames):
            started = perf_counter()
            elapsed, previous = min(0.1, started - previous), started
            for action in controls.poll(camera):
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
                    if (controls.firing or controls.map_open) and action in {"door", "supplies"}:
                        simulation.message = "지도를 닫은 뒤 상호작용" if controls.map_open else "사격 중에는 상호작용할 수 없음"
                        continue
                    if action == "door":
                        simulation.use_door()
                    elif action == "supplies":
                        simulation.take_supplies()
                    elif action == "reload":
                        simulation.reload()
                    elif action == "cancel":
                        simulation.cancel_reload()
                    elif action.startswith("weapon_"):
                        simulation.switch_weapon(int(action[-1]) - 1)
                    elif action == "fire_mode" and simulation.player.weapon:
                        simulation.player.weapon.automatic = not simulation.player.weapon.automatic
                    elif action == "respawn":
                        simulation.request_respawn()
            camera.follow(simulation.player.xy, controls.mouse)
            if controls.paused:
                accumulator = 0.0
                controls.trigger_pending = False
            else:
                accumulator += elapsed
                steps = 0
                while accumulator >= FIXED_DT and steps < 6:
                    pressed = controls.consume_trigger()
                    simulation.step((0, 0) if controls.map_open else controls.direction,
                                    None if controls.map_open else camera.world(*controls.mouse),
                                    not controls.map_open and (controls.firing or pressed),
                                    controls.sprint, aiming=controls.aiming and not controls.map_open,
                                    trigger_pressed=pressed and not controls.map_open, walking=controls.walking)
                    accumulator -= FIXED_DT
                    steps += 1
                    if (simulation.stack_drill and not stack_previewed
                            and any(stack.ready_at is not None for stack in simulation.ai.door_traffic.stacks.values())):
                        stack_previewed = controls.paused = True
                        accumulator = 0.0
                        simulation.message = f"{simulation.stack_title} 완료 · P 통과 재개"
                        break
            camera.follow(simulation.player.xy, controls.mouse)
            update_ms = (perf_counter() - started) * 1000
            renderer.render(simulation, camera, debug=controls.debug, paused=controls.paused,
                            update_ms=update_ms, mouse=controls.mouse, map_open=controls.map_open)
            count += 1
            # VSync가 없는 환경에서도 무제한 바쁜 루프가 되지 않게 한다.
            sleep(max(0, FIXED_DT - (perf_counter() - started)))
    finally:
        if renderer is not None:
            renderer.close()
        pico2d.close_canvas()


def main(argv=None):
    parser = argparse.ArgumentParser(description="그림 맵·미터 좌표·기초 AI·발사·군수 연결 검증 목업")
    scene_options = parser.add_mutually_exclusive_group()
    scene_options.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    scene_options.add_argument("--generate", type=int, metavar="SEED", help="시드로 지형·도로·시설 예약 영역 생성")
    parser.add_argument("--region-size", choices=("small", "medium", "large", "xlarge"))
    parser.add_argument("--importance", choices=("normal", "important", "core"))
    parser.add_argument("--ai-scenario", choices=SCENARIOS, help="물류창고 AI 검증 장면")
    parser.add_argument("--headless", action="store_true", help="그래픽 없이 코어만 짧게 실행")
    parser.add_argument("--frames", type=int, default=0, help="지정 프레임 뒤 종료. 0이면 창을 닫을 때까지 실행")
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("프레임 수는 음수일 수 없음")
    if args.generate is None and (args.region_size or args.importance):
        parser.error("지역 크기·중요도는 --generate와 함께 사용하세요")
    if args.ai_scenario and (args.generate is not None or args.scene.resolve() != DEFAULT_SCENE.resolve()):
        parser.error("--ai-scenario는 기본 지역 장면에서 실행하세요")
    scene = args.scene
    if args.generate is not None:
        from .generation import generate_scene
        scene = generate_scene(args.generate, region_size=args.region_size or "small", importance=args.importance or "normal")
    simulation = PlaySimulation(scene, ai_scenario=args.ai_scenario)
    if args.generate is not None:
        simulation.message = f"야외 이동 시험 · 시드 {args.generate} · WASD 이동 / Shift 달리기 / M 지도 / Esc 종료"
    if args.headless:
        for _ in range(args.frames or 120):
            simulation.step()
        print(f"목업 코어 실행: {simulation.time:.2f}s / AI {len(simulation.guards)} / 창고 {simulation.campaign.supplies}")
    else:
        run_window(simulation, frames=args.frames)


if __name__ == "__main__":
    main()
