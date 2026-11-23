"""시설별 사용 기능. 키 입력·그림 표시와 캠페인 상태 변경을 분리한다."""

import math


def nearby_facility(world, point, radius=1.8):
    return min((facility for facility in world.facilities if math.dist(point, facility["terminal"]) <= radius),
               key=lambda facility: math.dist(point, facility["terminal"]), default=None)


def nearby_road_control(world, point):
    return next((control for control in world.road_controls
                 if math.dist(point, control["position"]) <= 1.8), None)


def use_road_control(bridge, control):
    road = bridge.simulation.state.roads[control["road"]]
    blocked = not road.blocked
    bridge.simulation.apply_action({"type": "block_road", "road_id": road.id, "blocked": blocked})
    return f"{control['name']} · {'수송 차단' if blocked else '수송 재개'}"


def exchange_safehouse_weapon(simulation, facility):
    player, stash = simulation.player, simulation.safehouse_stash
    key = facility["id"]
    equipped, stored = player.weapon, stash.get(key)
    if equipped is None and stored is None:
        return "안전가옥 보관함이 비어 있음"
    if stored and stored.spec.id in player.loadout and stored.spec.id != player.active_weapon:
        return "같은 종류의 무기를 이미 휴대 중입니다. 해당 무기를 장착한 뒤 교체하세요"

    if equipped:
        simulation.cancel_reload()
        stash[key] = player.loadout.pop(player.active_weapon)
    else:
        stash.pop(key)
    if stored:
        player.loadout[stored.spec.id] = stored
        player.active_weapon = stored.spec.id
    else:
        player.active_weapon = next(iter(player.loadout), "")
    if player.weapon:
        player.equip_until = simulation.time + 0.25
    if equipped and stored:
        return f"{equipped.spec.name} 보관 · {stored.spec.name} 회수"
    if stored:
        return f"{stored.spec.name} 회수"
    return f"{equipped.spec.name} 보관"


def use_facility(simulation, facility):
    player, bridge = simulation.player, simulation.campaign
    kind, key = facility["kind"], facility["campaign_id"]
    if kind == "safehouse":
        if not player.loadout and simulation.kit_available:
            from .weapons import WeaponState
            player.loadout = {"ar_a": WeaponState.ready(simulation.specs["ar_a"])}
            player.active_weapon, simulation.kit_available = "ar_a", False
            return "안전가옥 예비 무장 확보"
        return "안전가옥 · Shift+E: 무기 보관함"
    if kind == "warehouse":
        amount = bridge.take_supplies(key)
        return f"{facility['name']}에서 실제 보급품 {amount}개 확보"
    if key:
        current = bridge.simulation.state.facilities[key].damage
        if current >= 100:
            return f"{facility['name']} 기능이 이미 무력화됨"
        damage = bridge.damage_facility(key)
        simulation.events.emit("noise", simulation.time, player, radius=14, strength=1.0, reason="sabotage")
        simulation.events.emit("facility_damaged", simulation.time, player, facility=key, damage=damage)
        return f"{facility['name']} 피해 {damage}% — 실제 캠페인 상태 반영"
    return facility["name"]
