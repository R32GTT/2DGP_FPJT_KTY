"""현장 휴대품과 고정 무기 슬롯의 이동을 처리한다."""

import math

from .interactions import nearby_facility
from .throwables import load_throwables


AMMUNITION = {
    "pistol": ("권총탄", 0.014, 24),
    "rifle": ("5.56 × 45mm", 0.018, 60),
    "shell": ("산탄", 0.04, 12),
    "precision": ("저격총탄", 0.027, 10),
    "machine_gun": ("기관총탄", 0.024, 80),
}
CARGO_ITEMS = {
    "supplies": ("보급품 상자", 1.0),
    "ammunition": ("탄약 상자", 2.0),
    "repair_parts": ("수리 부품 상자", 2.5),
    "night_gear": ("야간 장비 세트", 1.0),
}
SABOTAGE_ITEMS = {"tool:multitool": ("멀티툴", 1.2), "charge:c4": ("C4", 0.8)}
C4 = "charge:c4"
MULTITOOL = "tool:multitool"
NIGHT_KIT = "cargo:night_gear"
AMMO_CRATE = "cargo:ammunition"
BAG_LIMIT = 40.0
PICKUP_REACH = 1.5


def weapon_slots(loadout):
    long_guns = [key for key, weapon in loadout.items() if weapon.spec.category != "pistol"]
    sidearm = next((key for key, weapon in loadout.items() if weapon.spec.category == "pistol"), None)
    return (*((long_guns + [None, None])[:2]), sidearm)


def unique_weapon_key(weapons, weapon):
    key = weapon.spec.id
    if key not in weapons:
        return key
    number = 2
    while f"{key}#{number}" in weapons:
        number += 1
    return f"{key}#{number}"


class FieldInventory:
    def __init__(self, play, parts):
        self.play, self.parts = play, parts
        self.throwable_specs = load_throwables()
        self.selected_throwable = None
        self.selected_sabotage_mode = "hand"
        self.carried, self.carried_weapons, self.stored = {}, {}, {}
        throwable_stock = {f"throwable:{spec.id}": spec.warehouse_quantity for spec in self.throwable_specs.values()
                           if spec.warehouse_quantity}
        self.ground = [
            (tuple(site["terminal"]), {**dict.fromkeys(parts, 1), **throwable_stock,
                                      MULTITOOL: 1, C4: 3,
                                      **{f"ammo:{key}": row[2] for key, row in AMMUNITION.items()}})
            for site in play.world.facilities if site["kind"] == "warehouse"
        ]
        for weapon in play.player.loadout.values():
            self.stow_reserve(weapon)

    def stow_reserve(self, weapon):
        if not weapon or not weapon.reserve or weapon.spec.ammo_type not in AMMUNITION:
            return
        key = f"ammo:{weapon.spec.ammo_type}"
        weight = AMMUNITION[weapon.spec.ammo_type][1]
        capacity = max(0, math.floor((BAG_LIMIT - self.bag_weight + 1e-8) / weight))
        amount = min(weapon.reserve, capacity)
        overflow = weapon.reserve - amount
        weapon.reserve = 0
        if amount:
            self.carried[key] = self.carried.get(key, 0) + amount
        if overflow:
            self.ground.append((self.play.player.xy, {key: overflow}))

    def item(self, key):
        if key.startswith("ammo:"):
            return AMMUNITION[key[5:]][:2]
        if key.startswith("throwable:"):
            spec = self.throwable_specs[key.removeprefix("throwable:")]
            return spec.name, spec.weight
        if key.startswith("cargo:"):
            return CARGO_ITEMS[key.removeprefix("cargo:")]
        if key in SABOTAGE_ITEMS:
            return SABOTAGE_ITEMS[key]
        part = self.parts[key]
        return part.name, part.weight

    @property
    def has_night_gear(self):
        return not self.play.presence or NIGHT_KIT in self.carried

    def sync_night_gear(self):
        if not self.has_night_gear:
            player = self.play.player
            player.night_vision = player.ir_laser = False
            if player.lamp == "infrared":
                player.lamp = "off"

    @property
    def active_throwable(self):
        if self.selected_throwable in self.carried:
            return self.selected_throwable
        return next((key for key in self.carried if key.startswith("throwable:")), None)

    @property
    def sabotage_mode(self):
        key = MULTITOOL if self.selected_sabotage_mode == "multitool" else C4
        return self.selected_sabotage_mode if self.selected_sabotage_mode == "hand" or key in self.carried else "hand"

    def consume_throwable(self, key):
        self._remove(self.carried, key, 1)
        if self.selected_throwable == key and key not in self.carried:
            self.selected_throwable = None

    def validate_items(self, values):
        if not isinstance(values, dict):
            raise ValueError("잘못된 인벤토리 상태")
        for key, count in values.items():
            self.item(key)
            if type(count) is not int or count <= 0:
                raise ValueError("잘못된 인벤토리 수량")
        return values

    def validate_cargo(self):
        stock = self.play.campaign.simulation.state.player_stock
        inventories = [self.carried, *self.stored.values(), *(items for _, items in self.ground)]
        for resource in CARGO_ITEMS:
            key = f"cargo:{resource}"
            if sum(items.get(key, 0) for items in inventories) > stock[resource]:
                raise ValueError("캠페인 재고를 초과한 탈취품")

    @property
    def bag_weight(self):
        return (sum(self.item(key)[1] * amount for key, amount in self.carried.items())
                + sum(self.weapon_weight(weapon) for weapon in self.carried_weapons.values()))

    def weapon_weight(self, weapon):
        return (weapon.spec.weight + sum(self.parts[key].weight for key in weapon.attachments.values())
                + (weapon.ammo + weapon.reserve) * AMMUNITION.get(weapon.spec.ammo_type, ("", 0.0, 0))[1])

    @property
    def movement_multiplier(self):
        weapon_weight = sum(self.weapon_weight(weapon) for weapon in self.play.player.loadout.values())
        return max(0.78, 1.0 - 0.025 * max(0.0, weapon_weight + self.bag_weight - 9.0))

    def _reachable_piles(self, rows):
        origin = self.play.player.xy
        return (index for index, (point, _) in enumerate(rows)
                if math.dist(point, origin) <= PICKUP_REACH
                and self.play.world.geometry.raycast(origin, point) is None)

    def nearby(self, rows):
        return min(self._reachable_piles(rows), key=lambda index: math.dist(rows[index][0], self.play.player.xy),
                   default=None)

    def take_gun(self, index=None, key=None, slot=None):
        play = self.play
        if index is None:
            index = self.nearby(play.dropped)
        if index is None:
            return False
        _, guns = play.dropped[index]
        key = key or next(iter(guns))
        candidate = guns[key]
        if not self._valid_gun_slot(candidate, slot):
            return False
        incoming = guns.pop(key)
        self.stow_reserve(incoming)
        replaced = self._equip_gun(incoming, slot)
        if replaced:
            old_key, outgoing = replaced
            self.stow_reserve(outgoing)
            guns[old_key] = outgoing
        if not guns:
            play.dropped.pop(index)
        play.message = f"{incoming.spec.name} {'교환' if replaced else '회수'}"
        return True

    @staticmethod
    def _valid_gun_slot(weapon, slot):
        return slot is None or slot in (0, 1, 2) and (slot == 2) == (weapon.spec.category == "pistol")

    def _replacement(self, weapon, slot):
        first, second, sidearm = weapon_slots(self.play.player.loadout)
        if weapon.spec.category == "pistol":
            return sidearm
        if slot in (0, 1):
            return (first, second)[slot]
        if first and second:
            active = self.play.player.active_weapon
            return active if active in (first, second) else second
        return None

    def _equip_gun(self, incoming, slot):
        play, player = self.play, self.play.player
        replaced = self._replacement(incoming, slot)
        play.cancel_reload()
        remaining = {key: weapon for key, weapon in player.loadout.items() if key != replaced}
        new_key = unique_weapon_key(remaining, incoming)
        outgoing = player.loadout.get(replaced)
        if outgoing:
            player.loadout = {(new_key if key == replaced else key): (incoming if key == replaced else weapon)
                              for key, weapon in player.loadout.items()}
        else:
            player.loadout[new_key] = incoming
        player.active_weapon, player.equip_until = new_key, play.time + 0.25
        return (replaced, outgoing) if outgoing else None

    def store_gun(self, index, key):
        guns = self.play.dropped[index][1]
        weapon = guns[key]
        if self.bag_weight + self.weapon_weight(weapon) > BAG_LIMIT + 1e-8:
            self.play.message = "가방 용량 부족"
            return False
        guns.pop(key)
        self.stow_reserve(weapon)
        self.carried_weapons[unique_weapon_key(self.carried_weapons, weapon)] = weapon
        if not guns:
            self.play.dropped.pop(index)
        self.play.message = f"{weapon.spec.name} 가방에 보관"
        return True

    def stow_gun(self, slot):
        player = self.play.player
        key = weapon_slots(player.loadout)[slot]
        if key is None:
            return False
        weapon = player.loadout[key]
        if self.bag_weight + self.weapon_weight(weapon) > BAG_LIMIT + 1e-8:
            self.play.message = "가방 용량 부족"
            return False
        if key == player.active_weapon:
            self.play.cancel_reload()
        player.loadout.pop(key)
        self.stow_reserve(weapon)
        self.carried_weapons[unique_weapon_key(self.carried_weapons, weapon)] = weapon
        if key == player.active_weapon:
            player.active_weapon = next(iter(player.loadout), "")
            player.equip_until = self.play.time + 0.25
        self.play.message = f"{weapon.spec.name} 가방에 보관"
        return True

    def equip_carried_gun(self, key, slot=None):
        weapon = self.carried_weapons.get(key)
        if weapon is None or not self._valid_gun_slot(weapon, slot):
            return False
        replaced = self._replacement(weapon, slot)
        outgoing = self.play.player.loadout.get(replaced)
        if (self.bag_weight - self.weapon_weight(weapon)
                + (self.weapon_weight(outgoing) if outgoing else 0) > BAG_LIMIT + 1e-8):
            self.play.message = "가방 용량 부족"
            return False
        self.carried_weapons.pop(key)
        exchanged = self._equip_gun(weapon, slot)
        if exchanged:
            self.stow_reserve(exchanged[1])
            self.carried_weapons[unique_weapon_key(self.carried_weapons, exchanged[1])] = exchanged[1]
        self.play.message = f"{weapon.spec.name} 장착"
        return True

    def drop_carried_gun(self, key):
        weapon = self.carried_weapons.pop(key, None)
        if weapon is not None:
            self.play.dropped.append((self.play.player.xy, {key: weapon}))

    def feed_reserve(self, weapon):
        if weapon.reloading or weapon.stage >= 2 or (weapon.stage == 0 and weapon.magazine_attached and
                (weapon.chambered and weapon.magazine >= weapon.capacity or not weapon.chambered and weapon.magazine)):
            return
        key = f"ammo:{weapon.spec.ammo_type}"
        amount = min(self.carried.get(key, 0), max(0, weapon.capacity - weapon.reserve))
        if amount:
            self._remove(self.carried, key, amount)
            weapon.reserve += amount

    def drop_weapon(self, slot):
        player = self.play.player
        key = weapon_slots(player.loadout)[slot]
        if key is None:
            return
        if key == player.active_weapon:
            self.play.cancel_reload()
        weapon = player.loadout.pop(key)
        self.stow_reserve(weapon)
        self.play.dropped.append((player.xy, {key: weapon}))
        if key == player.active_weapon:
            player.active_weapon = next(iter(player.loadout), "")
            player.equip_until = self.play.time + 0.25

    def drop_carried(self):
        if self.carried_weapons:
            self.play.dropped.append((self.play.player.xy, self.carried_weapons))
            self.carried_weapons = {}
        if self.carried:
            self.ground.append((self.play.player.xy, self.carried))
            self.carried = {}
        self.selected_throwable = None
        self.selected_sabotage_mode = "hand"
        self.sync_night_gear()

    @staticmethod
    def _remove(items, key, amount):
        items[key] -= amount
        if not items[key]:
            del items[key]

    def _pickup_amount(self, key, available):
        capacity = math.floor((BAG_LIMIT - self.bag_weight + 1e-8) / self.item(key)[1])
        return min(available, max(0, capacity))

    def _take_items(self, source, key, limit=None):
        amount = min(self._pickup_amount(key, source[key]), limit or source[key])
        if amount:
            self._remove(source, key, amount)
            self.carried[key] = self.carried.get(key, 0) + amount
        else:
            self.play.message = "가방 용량 부족"

    def _take_cargo(self, operation_id, resource):
        play = self.play
        operation = play.transports.nearby_cargo(play.player, operation_id)
        if operation is None or not operation.cargo[resource]:
            play.message = "회수할 수 있는 화물이 없습니다"
            return
        key = f"cargo:{resource}"
        amount = self._pickup_amount(key, operation.cargo[resource])
        if not amount:
            play.message = "가방 용량 부족"
            return
        play.campaign.simulation.apply_action({"type": "capture", "operation_id": operation.id,
                                               "amount": {resource: amount}})
        self.carried[key] = self.carried.get(key, 0) + amount
        play.message = f"{self.item(key)[0]} ×{amount} 회수"

    def _unpack_ammunition(self, ammo_type):
        name, weight, rounds = AMMUNITION[ammo_type]
        if self.bag_weight - self.item(AMMO_CRATE)[1] + weight * rounds > BAG_LIMIT + 1e-8:
            self.play.message = "가방 용량 부족"
            return
        self.play.campaign.simulation.apply_action({"type": "unpack_player_ammunition"})
        self._remove(self.carried, AMMO_CRATE, 1)
        key = f"ammo:{ammo_type}"
        self.carried[key] = self.carried.get(key, 0) + rounds
        self.play.message = f"탄약 상자 개봉 · {name} {rounds}발"

    def _change_part(self, key, equip):
        weapon = self.play.player.weapon
        part = self.parts[key]
        if weapon is None or weapon.stage or weapon.reloading:
            self.play.message = "재장전을 끝낸 뒤 부착물을 변경하세요"
            return
        if not equip and self.bag_weight + part.weight > BAG_LIMIT + 1e-8:
            self.play.message = "분리한 부착물을 담을 공간이 없습니다"
            return
        previous = weapon.part(part.slot)
        if equip and previous and self.bag_weight - part.weight + previous.weight > BAG_LIMIT + 1e-8:
            self.play.message = "교체한 부착물을 담을 공간이 없습니다"
            return
        previous_id = weapon.change_attachment(part.slot, part if equip else None)
        if equip:
            self._remove(self.carried, key, 1)
        if previous_id:
            self.carried[previous_id] = self.carried.get(previous_id, 0) + 1
        self.stow_reserve(weapon)

    def rows(self):
        play, player = self.play, self.play.player
        rows = []
        for index, key in enumerate(weapon_slots(player.loadout)):
            title = ("주무장 1", "주무장 2", "권총")[index]
            if key is None:
                rows.append((f"{title}  ·  비어 있음", ()))
            else:
                weapon = player.loadout[key]
                active = "  ·  장착 중" if key == player.active_weapon else ""
                available = weapon.reserve + self.carried.get(f"ammo:{weapon.spec.ammo_type}", 0)
                rows.append((f"{title}  ·  {weapon.spec.name}  {weapon.ammo}/{available}{active}", ("equip", index)))
        if player.weapon:
            for part in player.weapon.spec.compatible:
                if player.weapon.attachments.get(part.slot) == part.id:
                    rows.append((f"{part.name} 분리", ("part", part.id, False)))
                elif self.carried.get(part.id):
                    rows.append((f"{part.name} 장착", ("part", part.id, True)))
            rows.append((f"{player.weapon.spec.name} 내려놓기", ("drop_gun",)))
        for drop in self._reachable_piles(play.dropped):
            for key, weapon in play.dropped[drop][1].items():
                rows.append((f"현장  ·  {weapon.spec.name} 회수·교환", ("gun", drop, key)))
        pile = self.nearby(self.ground)
        if pile is not None:
            for key, count in sorted(self.ground[pile][1].items()):
                rows.append((f"현장  ·  {self.item(key)[0]} ×{count} 회수", ("ground", pile, key)))
        cargo = play.transports.nearby_cargo(player)
        if cargo is not None:
            for resource, count in cargo.cargo.items():
                if count:
                    rows.append((f"수송대  ·  {CARGO_ITEMS[resource][0]} ×{count} 회수", ("cargo", cargo.id, resource)))
        site = nearby_facility(play.world, player.xy)
        safehouse = site["id"] if site and site["kind"] == "safehouse" else None
        if safehouse:
            stored_gun = play.safehouse_stash.get(safehouse)
            if player.weapon or stored_gun:
                rows.append(("안전가옥  ·  무기 보관·교환", ("stash_gun", site)))
            for key, count in sorted(self.stored.get(safehouse, {}).items()):
                rows.append((f"보관함  ·  {self.item(key)[0]} ×{count} 회수", ("take", safehouse, key)))
        active_throwable = self.active_throwable
        for key, weapon in self.carried_weapons.items():
            rows.append((f"가방  ·  {weapon.spec.name} 장착", ("equip_bag_gun", key)))
        for key, count in sorted(self.carried.items()):
            label = f"가방  ·  {self.item(key)[0]} ×{count}"
            if key in SABOTAGE_ITEMS:
                mode = "multitool" if key == MULTITOOL else "c4"
                status = "선택 중" if self.sabotage_mode == mode else "파괴 도구 선택"
                rows.append((f"{label}  ·  {status}", ("select_sabotage", mode)))
            if key.startswith("throwable:"):
                state = "선택 중 · G 투척" if key == active_throwable else "투척물 선택"
                rows.append((f"{label}  ·  {state}", ("select_throwable", key)))
            if key == AMMO_CRATE and player.weapon and player.weapon.spec.ammo_type in AMMUNITION:
                ammo_type = player.weapon.spec.ammo_type
                name, _, rounds = AMMUNITION[ammo_type]
                rows.append((f"{label}  ·  {name} {rounds}발 꺼내기", ("unpack_ammunition", ammo_type)))
            rows.append((f"{label} {'보관' if safehouse else '내려놓기'}", ("put", safehouse, key)))
        return rows

    def activate(self, index):
        if self.play.player.hp <= 0:
            return
        rows = self.rows()
        if not 0 <= index < len(rows) or not rows[index][1]:
            return
        self.activate_action(rows[index][1])

    def activate_action(self, action, *, single=False):
        if self.play.player.hp <= 0 or not action:
            return
        kind, *args = action
        if kind == "equip":
            self.play.switch_weapon(*args)
        elif kind == "part":
            self._change_part(*args)
        elif kind == "select_throwable":
            self.selected_throwable = args[0]
            self.play.message = f"{self.item(args[0])[0]} 선택 · G 투척"
        elif kind == "select_sabotage":
            self.selected_sabotage_mode = "hand" if self.sabotage_mode == args[0] else args[0]
            label = {"hand": "맨손", "multitool": "멀티툴", "c4": "C4"}[self.selected_sabotage_mode]
            self.play.message = f"시설 파괴 방식 · {label} · 시설에서 E 또는 Shift+E 길게 누르기"
        elif kind == "gun":
            self.take_gun(*args)
        elif kind == "equip_bag_gun":
            self.equip_carried_gun(*args)
        elif kind == "drop_gun":
            self.drop_weapon(weapon_slots(self.play.player.loadout).index(self.play.player.active_weapon))
        elif kind == "ground":
            pile, key = args
            self._take_items(self.ground[pile][1], key, 1 if single else None)
            if not self.ground[pile][1]:
                self.ground.pop(pile)
        elif kind == "cargo":
            self._take_cargo(*args)
        elif kind == "unpack_ammunition":
            self._unpack_ammunition(*args)
        elif kind == "stash_gun":
            from .interactions import exchange_safehouse_weapon
            self.play.message = exchange_safehouse_weapon(self.play, args[0])
        elif kind == "take":
            site, key = args
            self._take_items(self.stored[site], key, 1 if single else None)
        elif kind == "put":
            site, key = args
            amount = 1 if single else self.carried[key]
            self._remove(self.carried, key, amount)
            if self.selected_throwable == key and key not in self.carried:
                self.selected_throwable = None
            if site:
                stored = self.stored.setdefault(site, {})
                stored[key] = stored.get(key, 0) + amount
            else:
                pile = self.nearby(self.ground)
                if pile is None:
                    self.ground.append((self.play.player.xy, {key: amount}))
                else:
                    stored = self.ground[pile][1]
                    stored[key] = stored.get(key, 0) + amount
            if key == NIGHT_KIT:
                self.sync_night_gear()

    def interact(self, source, target=None, *, shift=False):
        """클릭은 기본 동작, 드래그는 목적지로 이동, Shift는 한 개씩 이동."""
        if source is None or self.play.player.hp <= 0:
            return
        if source.zone == "attached":
            if target is None or target.zone == "bag":
                self.play.switch_weapon(source.key[0])
                self._change_part(source.key[1], False)
            return
        if source.zone == "slot":
            slot = source.key[0]
            if slot < 0 or not source.weapon_id:
                return
            if shift or target is not None and target.zone == "floor":
                self.drop_weapon(slot)
            elif target is not None and target.zone == "bag":
                self.stow_gun(slot)
            elif target is not None and target.zone == "slot" and target.key[0] in (0, 1) and slot in (0, 1):
                keys = list(self.play.player.loadout)
                first, second, _ = weapon_slots(self.play.player.loadout)
                if first and second and first != second:
                    a, b = keys.index(first), keys.index(second)
                    keys[a], keys[b] = keys[b], keys[a]
                    self.play.player.loadout = {key: self.play.player.loadout[key] for key in keys}
            else:
                self.activate_action(source.action)
            return
        if source.zone == "floor":
            if target is not None and target.zone not in {"bag", "slot"}:
                return
            if source.action and source.action[0] == "gun":
                slot = target.key[0] if target and target.zone == "slot" else None
                if slot is not None and slot not in (0, 1, 2):
                    return
                if target is not None and target.zone == "bag":
                    self.store_gun(source.action[1], source.action[2])
                else:
                    self.take_gun(source.action[1], source.action[2], slot)
            else:
                self.activate_action(source.action, single=shift)
            return
        if source.zone == "bag":
            key = source.key[1]
            if source.key[0] == "bag_gun":
                if key not in self.carried_weapons:
                    return
                if shift or target is not None and target.zone == "floor":
                    self.drop_carried_gun(key)
                elif target is not None and target.zone == "slot" and target.key[0] in (0, 1, 2):
                    self.equip_carried_gun(key, target.key[0])
                elif target is None:
                    self.equip_carried_gun(key)
                return
            if key not in self.carried:
                return
            if shift or target is not None and target.zone == "floor":
                self.activate_action(("put", None, key), single=shift)
            elif target is not None and target.zone == "slot" and target.key[0] in (0, 1, 2):
                if key in self.parts:
                    self.play.switch_weapon(target.key[0])
                    self._change_part(key, True)
            else:
                self.activate_action(source.action)
