"""시설별 사용 기능. 키 입력·그림 표시와 캠페인 상태 변경을 분리한다."""

import math


def _reachable_terminal(world, point, terminal, radius):
    return (math.dist(point, terminal) <= radius
            and world.geometry.raycast(point, tuple(terminal), category="movement") is None)


def nearby_facility(world, point, radius=1.8):
    return min((facility for facility in world.facilities
                if _reachable_terminal(world, point, facility["terminal"], radius)),
               key=lambda facility: math.dist(point, facility["terminal"]), default=None)


def nearby_road_control(world, point):
    control = min((control for control in world.road_controls
                   if _reachable_terminal(world, point, control["position"], 1.8)),
                  key=lambda item: math.dist(point, item["position"]), default=None)
    if control is None:
        return None
    site = nearby_facility(world, point)
    return control if site is None or math.dist(point, control["position"]) <= math.dist(point, site["terminal"]) else None


def nearby_watch_post(world, point):
    for site in world.facilities:
        post = site.get("watch_post")
        if post and math.dist(point, post[0]) <= 1.5 and world.geometry.raycast(point, post[0]) is None:
            return site
    return None


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
    if stored:
        pistol = stored.spec.category == "pistol"
        count = sum((weapon.spec.category == "pistol") == pistol
                    for weapon in player.loadout.values() if weapon is not equipped)
        if count >= (1 if pistol else 2):
            return "해당 무기 슬롯을 비우거나 같은 슬롯의 무기를 장착하세요"

    if equipped:
        simulation.cancel_reload()
        simulation.inventory.stow_reserve(equipped)
        stash[key] = player.loadout.pop(player.active_weapon)
    else:
        stash.pop(key)
    if stored:
        from .inventory import unique_weapon_key

        simulation.inventory.stow_reserve(stored)
        new_key = unique_weapon_key(player.loadout, stored)
        player.loadout[new_key] = stored
        player.active_weapon = new_key
    else:
        player.active_weapon = next(iter(player.loadout), "")
    if player.weapon:
        player.equip_until = simulation.time + 0.25
    if equipped and stored:
        return f"{equipped.spec.name} 보관 · {stored.spec.name} 회수"
    if stored:
        return f"{stored.spec.name} 회수"
    return f"{equipped.spec.name} 보관"


def use_facility(simulation, facility, *, sabotage=False, method=None):
    player, bridge = simulation.player, simulation.campaign
    kind, key = facility["kind"], facility["campaign_id"]
    if kind == "safehouse":
        if not player.loadout and simulation.kit_available:
            from .weapons import WeaponState
            player.loadout = {"ar_a": WeaponState.ready(simulation.specs["ar_a"])}
            player.active_weapon, simulation.kit_available = "ar_a", False
            simulation.inventory.stow_reserve(player.weapon)
            return "안전가옥 예비 무장 확보"
        return "안전가옥 · 보조 동작으로 무기 보관함 열기"
    if key:
        current = bridge.simulation.state.facilities[key].damage
        if current >= 100:
            if method == "c4":
                _emit_sabotage(simulation, key, facility["terminal"], method=method)
            return f"{facility['name']} 기능이 이미 무력화됨"
        if kind == "warehouse" and not sabotage:
            return "창고 안의 보급 상자에서 E를 길게 누르거나 가방에서 회수하세요"
        if kind == "hq" and not sabotage:
            if not bridge.disable_component(key, "relay_disabled"):
                return "HQ 통신 중계기가 이미 무력화됨"
            _emit_sabotage(simulation, key, facility["terminal"], method=method)
            return "HQ 통신 중계기 무력화 · 외부 증원 차단"
        damage = bridge.damage_facility(key, damage=100 if method else 25,
                                        destroy_inventory=kind == "warehouse")
        _emit_sabotage(simulation, key, facility["terminal"], method=method)
        if kind == "warehouse" and damage == 100:
            return f"{facility['name']} 무력화 · 재고 파괴"
        if damage == 100:
            state = bridge.simulation.state
            region = state.facilities[key].region_id
            if kind in {"sam", "comms"} and bridge.air_support_available(region):
                return f"{facility['name']} 무력화 · 항공 지원 제한 해제"
            return f"{facility['name']} 무력화"
        return f"{facility['name']} 피해 {damage}% — 실제 캠페인 상태 반영"
    return facility["name"]


def disable_watch_post(simulation, site, *, method=None):
    key = site["campaign_id"]
    state = simulation.campaign.simulation.state
    if state.facilities[key].damage == 100 or not simulation.campaign.disable_component(key, "watch_disabled"):
        return "감시초소가 이미 무력화됨"
    _emit_sabotage(simulation, key, site["watch_post"][0], method=method)
    return "감시초소 무력화"


def _emit_sabotage(simulation, key, point, *, method=None):
    if method == "multitool":
        return
    events, time, player = simulation.events, simulation.time, simulation.player
    point = tuple(point)
    events.emit_at("noise", time, player.id, point,
                   {"team": player.team, "radius": 36 if method == "c4" else 14,
                    "strength": 1.0, "reason": "explosion" if method == "c4" else "sabotage"})
    events.emit_at("facility_damaged", time, player.id, point,
                   {"team": player.team, "facility": key, "point": point,
                    "damage": simulation.campaign.simulation.state.facilities[key].damage})
    if method in ("hand", "c4"):
        simulation.raise_facility_alarm(key, point)
