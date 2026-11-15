"""시설별 사용 기능. 키 입력·그림 표시와 캠페인 상태 변경을 분리한다."""

import math


def nearby_facility(world, point, radius=1.8):
    return min((facility for facility in world.facilities if math.dist(point, facility["terminal"]) <= radius),
               key=lambda facility: math.dist(point, facility["terminal"]), default=None)


def use_facility(simulation, facility):
    player, bridge = simulation.player, simulation.campaign
    kind, key = facility["kind"], facility["campaign_id"]
    if kind == "safehouse":
        if not player.loadout and simulation.kit_available:
            from .weapons import WeaponState
            player.loadout = {"ar_a": WeaponState.ready(simulation.specs["ar_a"])}
            player.active_weapon, simulation.kit_available = "ar_a", False
            return "안전가옥 예비 무장 확보"
        return "안전가옥 · 복귀 거점 / 예비 키트는 무장 없이 사용"
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
