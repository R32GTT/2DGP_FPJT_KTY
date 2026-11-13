"""검증용 최소 실행기. 일반 테스트·플레이 평가는 사용자가 수행한다."""

import argparse
from pathlib import Path
from time import perf_counter, sleep

from .simulation import FIXED_DT, PlaySimulation
from .world import DEFAULT_SCENE


def run_window(simulation, *, frames=0):
    import pico2d
    from .camera import PlayCamera
    from .controls import Controls
    from .rendering import Renderer

    pico2d.open_canvas(1280, 720, sync=True)
    renderer = None
    try:
        renderer = Renderer(simulation.world)
        controls, camera = Controls(), PlayCamera()
        camera.follow(simulation.player.xy)
        previous, accumulator, count = perf_counter(), 0.0, 0
        while controls.running and (not frames or count < frames):
            started = perf_counter()
            elapsed, previous = min(0.1, started - previous), started
            for action in controls.poll(camera):
                if not controls.paused:
                    if controls.firing and action in {"door", "supplies"}:
                        simulation.message = "사격 중에는 상호작용할 수 없음"
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
                    simulation.step(controls.direction, camera.world(*controls.mouse), controls.firing or pressed,
                                    controls.sprint, aiming=controls.aiming, trigger_pressed=pressed, walking=controls.walking)
                    accumulator -= FIXED_DT
                    steps += 1
            camera.follow(simulation.player.xy, controls.mouse)
            update_ms = (perf_counter() - started) * 1000
            renderer.render(simulation, camera, debug=controls.debug, paused=controls.paused,
                            update_ms=update_ms, mouse=controls.mouse)
            count += 1
            # VSync가 없는 환경에서도 무제한 바쁜 루프가 되지 않게 한다.
            sleep(max(0, FIXED_DT - (perf_counter() - started)))
    finally:
        if renderer is not None:
            renderer.close()
        pico2d.close_canvas()


def main(argv=None):
    parser = argparse.ArgumentParser(description="그림 맵·미터 좌표·기초 AI·발사·군수 연결 검증 목업")
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--headless", action="store_true", help="그래픽 없이 코어만 짧게 실행")
    parser.add_argument("--frames", type=int, default=0, help="지정 프레임 뒤 종료. 0이면 창을 닫을 때까지 실행")
    args = parser.parse_args(argv)
    if args.frames < 0:
        parser.error("프레임 수는 음수일 수 없음")
    simulation = PlaySimulation(args.scene)
    if args.headless:
        for _ in range(args.frames or 120):
            simulation.step()
        print(f"목업 코어 실행: {simulation.time:.2f}s / AI {len(simulation.guards)} / 창고 {simulation.campaign.supplies}")
    else:
        run_window(simulation, frames=args.frames)


if __name__ == "__main__":
    main()
