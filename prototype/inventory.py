"""현장 휴대품과 고정 무기 슬롯의 이동을 처리한다."""

import math

from .interactions import nearby_facility
from .throwables import load_throwables


AMMUNITION = {
    "pistol": ("권총탄", 0.014, 24),
    "rifle": ("소총탄", 0.018, 60),
    "shell": ("산탄", 0.04, 12),
    "precision": ("저격총탄", 0.027, 10),
    "machine_gun": ("기관총탄", 0.024, 80),
}
BAG_LIMIT = 8.0
PICKUP_REACH = 1.5


def weapon_slots(loadout):
    long_guns = [key for key, weapon in loadout.items() if weapon.spec.category != "pistol"]
    sidearm = next((key for key, weapon in loadout.items() if weapon.spec.category == "pistol"), None)
    return (*((long_guns + [None, None])[:2]), sidearm)


class FieldInventory:
    def __init__(self, play, parts):
        self.play, self.parts = play, parts
        self.throwable_specs = load_throwables()
        self.carried, self.stored = {}, {}
        self.ground = [
            (tuple(site["terminal"]), {**dict.fromkeys(parts, 1),
                                      **{f"ammo:{key}": row[2] for key, row in AMMUNITION.items()}})
            for site in play.world.facilities if site["kind"] == "warehouse"
        ]

    def item(self, key):
        if key.startswith("ammo:"):
            return AMMUNITION[key[5:]][:2]
        if key.startswith("throwable:"):
            spec = self.throwable_specs[key.removeprefix("throwable:")]
            return spec.name, spec.weight
        part = self.parts[key]
        return part.name, part.weight

    def validate_items(self, values):
        if not isinstance(values, dict):
            raise ValueError("잘못된 인벤토리 상태")
        for key, count in values.items():
            self.item(key)
            if type(count) is not int or count <= 0:
                raise ValueError("잘못된 인벤토리 수량")
        return values

    @property
    def bag_weight(self):
        return sum(self.item(key)[1] * amount for key, amount in self.carried.items())

    @property
    def movement_multiplier(self):
        weapon_weight = sum(
            weapon.spec.weight + sum(self.parts[key].weight for key in weapon.attachments.values())
            + (weapon.ammo + weapon.reserve) * AMMUNITION.get(weapon.spec.ammo_type, ("", 0.0, 0))[1]
            for weapon in self.play.player.loadout.values())
        return max(0.78, 1.0 - 0.025 * max(0.0, weapon_weight + self.bag_weight - 9.0))

    def nearby(self, rows):
        player = self.play.player
        return min((index for index, (point, _) in enumerate(rows)
                    if math.dist(point, player.xy) <= PICKUP_REACH
                    and self.play.world.geometry.raycast(player.xy, point) is None),
                   key=lambda index: math.dist(rows[index][0], player.xy), default=None)

    def take_gun(self, index=None, key=None):
        play, player = self.play, self.play.player
        if index is None:
            index = self.nearby(play.dropped)
        if index is None:
            return False
        _, guns = play.dropped[index]
        key = key or next(iter(guns))
        incoming = guns.pop(key)
        first, second, sidearm = weapon_slots(player.loadout)
        replaced = None
        if key in player.loadout:
            replaced = key
        elif incoming.spec.category == "pistol":
            replaced = sidearm
        elif first and second:
            replaced = player.active_weapon if player.active_weapon in (first, second) else second
        play.cancel_reload()
        if replaced:
            outgoing = player.loadout[replaced]
            guns[replaced] = outgoing
            player.loadout = {(key if old == replaced else old): (incoming if old == replaced else weapon)
                              for old, weapon in player.loadout.items()}
        else:
            player.loadout[key] = incoming
        player.active_weapon, player.equip_until = key, play.time + 0.25
        if not guns:
            play.dropped.pop(index)
        play.message = f"{incoming.spec.name} {'교환' if replaced else '회수'}"
        return True

    def feed_reserve(self, weapon):
        if weapon.reloading or weapon.stage >= 2 or (weapon.stage == 0 and weapon.magazine_attached and
                (weapon.chambered and weapon.magazine >= weapon.capacity or not weapon.chambered and weapon.magazine)):
            return
        key = f"ammo:{weapon.spec.ammo_type}"
        amount = min(self.carried.get(key, 0), max(0, weapon.capacity - weapon.reserve))
        if amount:
            self._remove(self.carried, key, amount)
            weapon.reserve += amount

    def drop_carried(self):
        if self.carried:
            self.ground.append((self.play.player.xy, self.carried))
            self.carried = {}

    @staticmethod
    def _remove(items, key, amount):
        items[key] -= amount
        if not items[key]:
            del items[key]

    def _take_items(self, source, key):
        limit = math.floor((BAG_LIMIT - self.bag_weight + 1e-8) / self.item(key)[1])
        amount = min(source[key], max(0, limit))
        if amount:
            self._remove(source, key, amount)
            self.carried[key] = self.carried.get(key, 0) + amount
        else:
            self.play.message = "가방 용량 부족"

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
                rows.append((f"{title}  ·  {weapon.spec.name}  {weapon.ammo}/{weapon.reserve}{active}", ("equip", index)))
        if player.weapon:
            for part in player.weapon.spec.compatible:
                if player.weapon.attachments.get(part.slot) == part.id:
                    rows.append((f"{part.name} 분리", ("part", part.id, False)))
                elif self.carried.get(part.id):
                    rows.append((f"{part.name} 장착", ("part", part.id, True)))
            rows.append((f"{player.weapon.spec.name} 내려놓기", ("drop_gun",)))
        drop = self.nearby(play.dropped)
        if drop is not None:
            for key, weapon in play.dropped[drop][1].items():
                rows.append((f"현장  ·  {weapon.spec.name} 회수·교환", ("gun", drop, key)))
        pile = self.nearby(self.ground)
        if pile is not None:
            for key, count in sorted(self.ground[pile][1].items()):
                rows.append((f"현장  ·  {self.item(key)[0]} ×{count} 회수", ("ground", pile, key)))
        site = nearby_facility(play.world, player.xy)
        safehouse = site["id"] if site and site["kind"] == "safehouse" else None
        if safehouse:
            stored_gun = play.safehouse_stash.get(safehouse)
            if player.weapon or stored_gun:
                rows.append(("안전가옥  ·  무기 보관·교환", ("stash_gun", site)))
            for key, count in sorted(self.stored.get(safehouse, {}).items()):
                rows.append((f"보관함  ·  {self.item(key)[0]} ×{count} 회수", ("take", safehouse, key)))
        for key, count in sorted(self.carried.items()):
            label = f"가방  ·  {self.item(key)[0]} ×{count}"
            rows.append((f"{label} {'보관' if safehouse else '내려놓기'}", ("put", safehouse, key)))
        return rows

    def activate(self, index):
        if self.play.player.hp <= 0:
            return
        rows = self.rows()
        if not 0 <= index < len(rows) or not rows[index][1]:
            return
        kind, *args = rows[index][1]
        if kind == "equip":
            self.play.switch_weapon(*args)
        elif kind == "part":
            self._change_part(*args)
        elif kind == "gun":
            self.take_gun(*args)
        elif kind == "drop_gun":
            player = self.play.player
            self.play.cancel_reload()
            key = player.active_weapon
            self.play.dropped.append((player.xy, {key: player.loadout.pop(key)}))
            player.active_weapon = next(iter(player.loadout), "")
            player.equip_until = self.play.time + 0.25
        elif kind == "ground":
            pile, key = args
            self._take_items(self.ground[pile][1], key)
            if not self.ground[pile][1]:
                self.ground.pop(pile)
        elif kind == "stash_gun":
            from .interactions import exchange_safehouse_weapon
            self.play.message = exchange_safehouse_weapon(self.play, args[0])
        elif kind == "take":
            site, key = args
            self._take_items(self.stored[site], key)
        elif kind == "put":
            site, key = args
            amount = self.carried.pop(key)
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
