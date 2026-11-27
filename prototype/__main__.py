"""검증용 최소 실행기. 일반 테스트·플레이 평가는 사용자가 수행한다."""

import argparse
import math
import sys
from pathlib import Path
from time import perf_counter, sleep

from .ai_scenarios import SCENARIOS
from .intelligence import collect as collect_intel
from .simulation import FIXED_DT, PlaySimulation
from .savegame import CampaignSaveError, DEFAULT_SAVE, QUICK_SAVE, load_game, save_game
from .world import DEFAULT_SCENE
from .world_layout import MAX_REGIONS


SIMULATION_FRAME_BUDGET_S = 0.012
MAX_SIMULATION_BACKLOG_S = 0.1
EQUIPMENT_RANGE_SCENE = DEFAULT_SCENE.with_name("ai_facility.json")


def run_window(simulation, *, frames=0, zoom=None, paused=None, debug=None, map_open=False,
               manage_canvas=True, settings=None):
    import pico2d
    from .camera import CANVAS_SIZE, PlayCamera
    from .controls import Controls
    from .rendering import Renderer
    from .inventory_ui import build_inventory_layout
    from .respawn_ui import hit_respawn_map
    from .map_navigation import RESPAWN_SIDEBAR
    from .settings import apply_display, binding_name, load_settings

    if manage_canvas:
        pico2d.open_canvas(*CANVAS_SIZE, sync=True)
    settings = settings or load_settings()
    import importlib
    backend = importlib.import_module("pico2d.pico2d")
    renderer = None
    try:
        apply_display(backend, settings.window_mode, settings.window_size)
        renderer = Renderer(simulation.world, settings)
        if simulation.world.chunks:
            simulation.world.chunks.start_prefetch()
        facility_test = simulation.world.data.get("training_facility", False)
        test_mode = (facility_test or bool(simulation.ai_scenario)) and not simulation.equipment_range
        controls = Controls(settings)
        camera = PlayCamera(zoom=zoom if zoom is not None else 0.55 if facility_test or test_mode else 1.0)
        controls.debug = test_mode if debug is None else debug
        controls.paused = test_mode if paused is None else paused
        controls.map_open = map_open
        camera.follow(simulation.player.xy)
        previous, accumulator, count = perf_counter(), 0.0, 0
        frame_deadline = previous
        last_step_s = FIXED_DT
        stack_previewed = False
        inventory_cursor = 0
        bag_scroll = floor_scroll = 0
        inventory_press = None
        inventory_layout = None
        inventory_layout_until = 0.0
        intel_cursor = 0

        def focus_respawn_site(site):
            controls.map_navigation.focus(site["position"], simulation.world.bounds,
                                          (camera.width, camera.height), sidebar=RESPAWN_SIDEBAR)

        while controls.running and (not frames or count < frames):
            started = perf_counter()
            elapsed, previous = min(MAX_SIMULATION_BACKLOG_S, started - previous), started
            actions = controls.poll(camera, respawn_selecting=simulation.respawn_selecting)
            if simulation.respawn_selecting:
                for mouse in controls.map_clicks:
                    hit = hit_respawn_map(simulation, controls.map_navigation,
                                          (camera.width, camera.height), mouse)
                    if hit is not None:
                        kind, value = hit
                        if kind == "site":
                            simulation.select_respawn_site(value)
                            focus_respawn_site(simulation.respawn_sites[value])
                        else:
                            simulation.change_respawn_page(value)
            for action in actions:
                if action in {"save", "load", "quick_save", "quick_load"}:
                    try:
                        if not simulation.presence:
                            raise CampaignSaveError("저장·불러오기는 절차 생성 월드에서 사용하세요")
                        quick = action.startswith("quick_")
                        slot = QUICK_SAVE if quick else DEFAULT_SAVE
                        if action in {"save", "quick_save"}:
                            save_game(simulation, slot)
                            load_key = binding_name(settings.bindings["quick_load" if quick else "load"])
                            simulation.message = f"{'빠른' if quick else '월드'} 저장 완료 · {load_key} 불러오기"
                        else:
                            loaded = load_game(slot)
                            replacement = Renderer(loaded.world, settings)
                            renderer.close()
                            if simulation.world.chunks:
                                simulation.world.chunks.close()
                            renderer, simulation = replacement, loaded
                            if simulation.world.chunks:
                                simulation.world.chunks.start_prefetch()
                            controls.keys.clear()
                            controls.firing = controls.aiming = controls.trigger_pending = False
                            controls.inventory_open = False
                            controls.intel_open = False
                            intel_cursor = 0
                            if not quick:
                                controls.paused = True
                    except CampaignSaveError as error:
                        simulation.message = str(error)
                    previous, elapsed, accumulator = perf_counter(), 0.0, 0.0
                    continue
                if simulation.equipment_range:
                    if action == "next_ai":
                        simulation.range_night = not simulation.range_night
                        simulation.message = "야간" if simulation.range_night else "주간"
                        continue
                    if action == "restock_range":
                        simulation.restock_range()
                        continue
                if simulation.presence and not simulation.respawn_selecting:
                    if action == "intel":
                        controls.intel_open = not controls.intel_open
                        controls.map_open, controls.inventory_open = True, False
                        controls.firing = controls.aiming = controls.trigger_pending = False
                        continue
                    if controls.intel_open and action in {"intel_previous", "intel_next"}:
                        step = -1 if action == "intel_previous" else 1
                        intel_cursor = max(0, min(intel_cursor + step, len(simulation.operation_notes) - 1))
                        continue
                select_drill = action == "next_ai" and "search_drill" in simulation.world.data
                select_stack = action == "next_ai" and "stack_drill" in simulation.world.data
                next_scenario = action == "next_ai" and simulation.ai_scenario
                restart = action == "restart_ai" and (test_mode or simulation.equipment_range)
                if simulation.equipment_range and action == "respawn" and simulation.player.hp <= 0:
                    restart = True
                if restart or select_drill or select_stack or next_scenario:
                    scenario = simulation.ai_scenario
                    if next_scenario:
                        cases = tuple(SCENARIOS)
                        scenario = cases[(cases.index(scenario) + 1) % len(cases)]
                    stack_variant = simulation.stack_variant
                    if select_stack and simulation.stack_drill:
                        stack_variant = (stack_variant + 1) % len(simulation.world.data["stack_drill"]["layouts"])
                    range_night = simulation.range_night
                    simulation = PlaySimulation(simulation.world.source, ai_scenario=scenario,
                                                search_drill=select_drill or simulation.search_drill,
                                                stack_drill=select_stack or simulation.stack_drill,
                                                stack_variant=stack_variant, equipment_range=simulation.equipment_range)
                    simulation.range_night = range_night
                    stack_previewed = False
                    controls.keys.clear()
                    controls.firing = controls.aiming = controls.trigger_pending = controls.map_open = False
                    controls.inventory_open = False
                    if simulation.equipment_range:
                        camera.follow(simulation.player.xy)
                        previous, elapsed, accumulator = perf_counter(), 0.0, 0.0
                    else:
                        controls.debug = controls.paused = True
                    continue
                if not controls.paused and not simulation.campaign_complete:
                    if action == "inventory":
                        if simulation.player.hp > 0 and not simulation.respawn_selecting:
                            controls.inventory_open = not controls.inventory_open
                            controls.map_open = controls.intel_open = False
                            controls.firing = controls.aiming = controls.trigger_pending = False
                            inventory_cursor = 0
                            inventory_press = None
                            inventory_layout = None
                        continue
                    if controls.inventory_open:
                        inventory_layout = None
                        if action == "item_previous":
                            inventory_cursor = max(0, inventory_cursor - 1)
                        elif action == "item_next":
                            inventory_cursor = min(len(simulation.inventory.rows()) - 1, inventory_cursor + 1)
                        elif action == "item_use":
                            simulation.inventory.activate(inventory_cursor)
                        elif action in {"slot_0", "slot_1", "slot_2"}:
                            simulation.switch_weapon(int(action[-1]))
                        continue
                    if action in {"lamp", "night_vision", "ir_laser"}:
                        if not controls.map_open and not simulation.respawn_selecting:
                            simulation.toggle_optics(action)
                        continue
                    if simulation.respawn_selecting:
                        if action.startswith("slot_"):
                            simulation.choose_respawn_slot(int(action[-1]))
                            if simulation.respawn_selected_id is not None:
                                focus_respawn_site(simulation.respawn_sites[simulation.respawn_selected_id])
                        elif action in {"respawn_previous", "respawn_next"}:
                            simulation.change_respawn_page(1 if action == "respawn_next" else -1)
                        elif action == "respawn":
                            if simulation.confirm_respawn():
                                controls.map_open = False
                        elif action in {"cancel", "respawn_cancel"}:
                            simulation.cancel_respawn_selection()
                            controls.map_open = False
                        continue
                    if (controls.firing or controls.map_open) and action in {"door", "supplies", "facility_alternate"}:
                        simulation.message = "지도를 닫은 뒤 상호작용" if controls.map_open else "사격 중에는 상호작용할 수 없음"
                        continue
                    if action == "throw":
                        if not controls.map_open and not controls.firing:
                            simulation.throw_selected(camera.world(*controls.mouse))
                    elif action == "collect_intel" and not controls.map_open and not controls.firing:
                        collect_intel(simulation)
                    elif action == "door":
                        simulation.use_door()
                    elif action == "supplies":
                        simulation.take_supplies()
                    elif action == "facility_alternate":
                        simulation.use_facility_alternate()
                    elif action == "reload":
                        simulation.reload()
                    elif action == "cancel":
                        simulation.cancel_reload()
                    elif action in {"slot_0", "slot_1", "slot_2"}:
                        simulation.switch_weapon(int(action[-1]))
                    elif action == "fire_mode" and simulation.player.weapon and simulation.player.weapon.spec.automatic:
                        simulation.player.weapon.automatic = not simulation.player.weapon.automatic
                    elif action == "respawn" and simulation.player.hp <= 0:
                        controls.map_open = False
                        controls.map_navigation.reset()
                        simulation.request_respawn()
            map_open = controls.map_open or (simulation.respawn_selecting and not controls.paused)
            if simulation.player.hp <= 0:
                controls.inventory_open = False
            interface_open = map_open or controls.inventory_open
            aiming = controls.aiming and not (controls.paused or interface_open)
            weapon = simulation.player.weapon
            camera.follow(simulation.player.xy, None if interface_open else controls.mouse, aiming=aiming,
                          look_multiplier=weapon.look_multiplier if weapon and aiming else 1.0, dt=elapsed)
            aim_offset = None
            if not interface_open:
                aim_x, aim_y = camera.world(*controls.mouse)
                aim_offset = aim_x - simulation.player.position.x, aim_y - simulation.player.position.y
            if controls.paused or simulation.campaign_complete:
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
                    simulation.step((0, 0) if interface_open else controls.direction,
                                    aim_offset=aim_offset, firing=not interface_open and (controls.firing or pressed),
                                    sprint=controls.sprint, aiming=aiming,
                                    trigger_pressed=pressed and not interface_open, walking=controls.walking)
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
            inventory_view = None
            if controls.inventory_open and simulation.player.hp > 0:
                size = (camera.width, camera.height)
                if inventory_layout is None or started >= inventory_layout_until or controls.inventory_events:
                    inventory_layout = build_inventory_layout(simulation.inventory, size, bag_scroll, floor_scroll)
                    inventory_layout_until = started + 0.15
                layout = inventory_layout
                for event_kind, position, modifier in controls.inventory_events:
                    if event_kind == "wheel":
                        if layout.floor_box[0] <= position[0] < layout.floor_box[2]:
                            floor_scroll = max(0, floor_scroll - modifier)
                        elif layout.bag_box[0] <= position[0] < layout.bag_box[2]:
                            bag_scroll = max(0, bag_scroll - modifier)
                    elif event_kind == "down":
                        inventory_press = layout.hit(*position), position
                    elif inventory_press is not None:
                        source, start = inventory_press
                        target = layout.drop_target(*position)
                        dragged = math.dist(start, position) >= 6
                        if source is not None and (not dragged or target is not None):
                            simulation.inventory.interact(source, target if dragged else None, shift=modifier)
                        inventory_press = None
                    layout = build_inventory_layout(simulation.inventory, size, bag_scroll, floor_scroll)
                bag_scroll, floor_scroll = layout.bag_scroll, layout.floor_scroll
                inventory_layout = layout
                inventory_view = layout, controls.mouse, inventory_press
            else:
                inventory_layout = None
            renderer.render(simulation, camera, debug=controls.debug, paused=controls.paused,
                            update_ms=update_ms, mouse=None if inventory_view else controls.mouse,
                            map_open=map_open, inventory_view=inventory_view,
                            intel_index=intel_cursor if controls.intel_open else None,
                            minimap_open=controls.minimap_open, map_navigation=controls.map_navigation)
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
        if simulation.world.chunks:
            simulation.world.chunks.close()
        if renderer is not None:
            renderer.close()
        if manage_canvas:
            pico2d.close_canvas()


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from .main_screen import run_frontend

        run_frontend()
        return
    parser = argparse.ArgumentParser(description="그림 맵·미터 좌표·기초 AI·발사·군수 연결 검증 목업")
    scene_options = parser.add_mutually_exclusive_group()
    scene_options.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    scene_options.add_argument("--generate", type=int, metavar="SEED", help="시드로 지형·도로·프리팹 시설 생성")
    scene_options.add_argument("--load", type=Path, nargs="?", const=DEFAULT_SAVE, help="생성 월드 이어하기")
    scene_options.add_argument("--equipment-range", action="store_true", help="장비 시험장 실행")
    parser.add_argument("--region-size", choices=("small", "medium", "large", "xlarge"))
    parser.add_argument("--regions", type=int, choices=range(1, MAX_REGIONS + 1), help="생성할 지역 수")
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
    if (args.load or args.equipment_range) and (args.ai_scenario or args.search_drill or args.stack_variant is not None):
        parser.error("이어하기·장비 시험장과 AI 훈련을 함께 사용할 수 없습니다")
    if args.ai_scenario and (args.generate is not None or args.scene.resolve() != DEFAULT_SCENE.resolve()):
        parser.error("--ai-scenario는 기본 지역 장면에서 실행하세요")
    scene = EQUIPMENT_RANGE_SCENE if args.equipment_range else args.scene
    world_root = DEFAULT_SAVE.parent / "worlds" if args.generate is not None and not args.headless else None
    if args.generate is not None:
        from .generated_world import generate_world
        try:
            scene = generate_world(args.generate, region_count=args.regions or 1,
                                   region_size=args.region_size or "small", importance=args.importance or "normal",
                                   region_sizes=args.region_sizes.split(',') if args.region_sizes else None,
                                   region_importances=args.region_importances.split(',') if args.region_importances else None,
                                   world_root=world_root)
        except ValueError as error:
            parser.error(str(error))
    try:
        simulation = load_game(args.load) if args.load else PlaySimulation(
            scene, ai_scenario=args.ai_scenario, search_drill=args.search_drill,
            stack_drill=args.stack_variant is not None, stack_variant=args.stack_variant or 0,
            equipment_range=args.equipment_range, world_root=world_root)
    except CampaignSaveError as error:
        parser.error(str(error))
    if args.generate is not None:
        simulation.message = f"생성 맵 · 시드 {args.generate} · 저장과 불러오기 사용 가능"
    if args.headless:
        for _ in range(args.frames or 120):
            simulation.step()
        print(f"목업 코어 실행: {simulation.time:.2f}s / AI {len(simulation.guards)} / 창고 {simulation.campaign.supplies}")
    else:
        run_window(simulation, frames=args.frames, zoom=args.zoom, paused=args.paused,
                   debug=args.debug, map_open=args.map_open)


if __name__ == "__main__":
    main()
