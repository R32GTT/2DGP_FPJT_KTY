"""기존 지역의 물류창고에서 전술 AI를 재현하는 초기 배치."""

import math

from campaign.models import Position


SCENARIOS = {
    "sound": ("발소리 조사", (0, 10), False, False,
              "Ctrl 걷기와 일반 이동을 비교하고, 문 밖 호위가 소리를 조사하는지 확인"),
    "search": ("목격 후 수색", (0, 16), True, False,
               "호위 정면에서 시작. 열린 문으로 들어간 뒤 옆으로 움직여 시야 차단"),
    "radio": ("무전 사용", (0, 16), False, False,
              "정면 호위가 발견한 뒤 창고 반대편 호위에 보고가 도달하는지 확인"),
    "radio-off": ("무전 차단", (0, 16), False, True,
                  "같은 배치에서 통신시설 무력화. 가까운 호위와 반대편 호위 반응 비교"),
}


def configure(simulation, name):
    title, spawn, door_open, radio_off, hint = SCENARIOS[name]
    world = simulation.world
    depot = next(site for site in world.facilities if site["id"] == "depot_a")
    x, y = depot["position"]
    simulation.player.position = Position(x + spawn[0], y + spawn[1])
    door = world.geometry.objects["depot_a/door"]
    if door.active == door_open:
        door.active = not door_open
        world.revision += 1
        world.navigation.invalidate()
    if radio_off:
        simulation.campaign.damage_facility("comms_a", 100)

    escorts = sorted((actor for actor in simulation.guard_records if actor.home_node == depot["id"]),
                     key=lambda actor: actor.id)
    placements = ((0, 21, -math.pi / 2), (6, 22, -math.pi / 2),
                  (17, -6, -math.pi / 2), (-17, -6, -math.pi / 2))
    if name == "sound":
        placements = ((0, 14.5, -math.pi / 2), *placements[1:])
    for actor, (dx, dy, facing) in zip(escorts, placements, strict=True):
        point = x + dx, y + dy
        actor.position = Position(*point)
        actor.facing = facing
        simulation.roster.spawns[actor.id] = point
        simulation.ai.brains[actor.id].patrol = [point]
    simulation.message = f"AI 검증 · {title} · F2 재시작 / F3 다음"
    simulation.ai_hint = hint
