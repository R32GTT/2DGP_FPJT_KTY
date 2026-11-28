"""가방 화면과 입력이 공유하는 고정 영역 및 항목 배치."""

from dataclasses import dataclass
import math

from .inventory import AMMO_CRATE, C4, MULTITOOL, weapon_slots
from .interactions import nearby_facility


@dataclass(frozen=True)
class InventoryEntry:
    zone: str
    key: tuple
    rect: tuple[int, int, int, int]
    name: str = ""
    details: tuple[str, ...] = ()
    action: tuple = ()
    weapon_id: str = ""
    active: bool = False

    def contains(self, x, y):
        left, top, right, bottom = self.rect
        return left <= x < right and top <= y < bottom


@dataclass
class InventoryLayout:
    panel: tuple[int, int, int, int]
    bag_box: tuple[int, int, int, int]
    floor_box: tuple[int, int, int, int]
    slots: tuple[InventoryEntry, ...]
    bag: tuple[InventoryEntry, ...]
    floor: tuple[InventoryEntry, ...]
    weight: float
    bag_scroll: int
    floor_scroll: int

    def hit(self, x, y):
        for column in (self.slots, self.bag, self.floor):
            for entry in column:
                if entry.contains(x, y):
                    return entry
        return None

    def drop_target(self, x, y):
        hit = self.hit(x, y)
        if hit is not None:
            return hit
        if self.bag_box[0] <= x < self.bag_box[2] and self.bag_box[1] <= y < self.bag_box[3]:
            return InventoryEntry("bag", (), self.bag_box)
        if self.floor_box[0] <= x < self.floor_box[2] and self.floor_box[1] <= y < self.floor_box[3]:
            return InventoryEntry("floor", (), self.floor_box)
        return None


def build_inventory_layout(inventory, size, bag_scroll=0, floor_scroll=0):
    width, height = size
    left = (width - 960) // 2
    top = (height - 520) // 2
    panel = (left, top, left + 960, top + 520)
    bag_box = (left + 370, top + 46, left + 655, top + 488)
    floor_box = (left + 665, top + 46, left + 938, top + 488)
    player, play = inventory.play.player, inventory.play
    slots = []
    weapon_keys = weapon_slots(player.loadout)
    for index, rect in enumerate(((left + 68, top + 72, left + 122, top + 126),
                                  (left + 54, top + 144, left + 144, top + 286),
                                  (left + 32, top + 330, left + 172, top + 383),
                                  (left + 32, top + 397, left + 172, top + 450),
                                  (left + 205, top + 360, left + 300, top + 411))):
        weapon_slot = index - 2
        key = weapon_keys[weapon_slot] if index >= 2 else None
        weapon = player.loadout.get(key) if key else None
        slots.append(InventoryEntry("slot", (weapon_slot,), rect,
                                    action=("equip", weapon_slot) if weapon else (),
                                    weapon_id=weapon.spec.id if weapon else "",
                                    active=key == player.active_weapon if key else False))
        if weapon:
            for part_index, part_id in enumerate(weapon.attachments.values()):
                part = inventory.parts[part_id]
                slots.append(InventoryEntry("attached", (weapon_slot, part_id),
                                            (rect[2] + 5, rect[1] + part_index * 20,
                                             left + 360, rect[1] + (part_index + 1) * 20),
                                            "-" + part.name, action=("part", part_id, False)))

    bag_rows = []
    for key, weapon in inventory.carried_weapons.items():
        details = tuple("-" + inventory.parts[part].name for part in weapon.attachments.values())
        bag_rows.append((("bag_gun", key), weapon.spec.name, details,
                         ("equip_bag_gun", key), weapon.spec.id))
    compatible = {part.id for part in player.weapon.spec.compatible} if player.weapon else set()
    for key, count in sorted(inventory.carried.items()):
        name = inventory.item(key)[0]
        action = ()
        if key.startswith("throwable:"):
            action = ("select_throwable", key)
        elif key in (C4, MULTITOOL):
            action = ("select_sabotage", "c4" if key == C4 else "multitool")
            if inventory.sabotage_mode == action[1]:
                name += "  ·  선택 중"
        elif key == AMMO_CRATE and player.weapon:
            action = ("unpack_ammunition", player.weapon.spec.ammo_type)
        elif key in compatible:
            action = ("part", key, True)
        bag_rows.append((("bag", key), f"{name}  {count}", (), action, ""))

    floor_rows = []
    for drop in inventory._reachable_piles(play.dropped):
        for key, weapon in play.dropped[drop][1].items():
            details = tuple("-" + inventory.parts[part].name for part in weapon.attachments.values())
            floor_rows.append((('gun', drop, key), weapon.spec.name, details, ("gun", drop, key), key))
    for pile in inventory._reachable_piles(inventory.ground):
        for key, count in sorted(inventory.ground[pile][1].items()):
            floor_rows.append((('ground', pile, key), f"{inventory.item(key)[0]}  {count}", (),
                               ("ground", pile, key), ""))
    cargo = play.transports.nearby_cargo(player)
    if cargo:
        for resource, count in cargo.cargo.items():
            if count:
                key = f"cargo:{resource}"
                floor_rows.append((('cargo', cargo.id, resource), f"{inventory.item(key)[0]}  {count}", (),
                                   ("cargo", cargo.id, resource), ""))
    for site_id, resource, point in play.supply_stacks:
        if (math.dist(player.xy, point) <= 1.8
                and play.world.geometry.raycast(player.xy, point, category="movement") is None):
            count = play.campaign.simulation.state.facilities[site_id].inventory[resource]
            if count:
                key = f"cargo:{resource}"
                floor_rows.append((('facility_stock', site_id, resource),
                                   f"{inventory.item(key)[0]}  {count}", (),
                                   ("facility_stock", site_id, resource), ""))
    site = nearby_facility(play.world, player.xy)
    safehouse = site["id"] if site and site["kind"] == "safehouse" else None
    if safehouse:
        weapon = play.safehouse_stash.get(safehouse)
        if weapon:
            floor_rows.append((('stash_gun', safehouse), weapon.spec.name,
                               tuple("-" + inventory.parts[part].name for part in weapon.attachments.values()),
                               ("stash_gun", site), weapon.spec.id))
        for key, count in sorted(inventory.stored.get(safehouse, {}).items()):
            floor_rows.append((('take', safehouse, key), f"{inventory.item(key)[0]}  {count}", (),
                               ("take", safehouse, key), ""))

    def place(rows, box, zone, scroll):
        result = []
        y = box[1] + 7
        for key, name, details, action, weapon_id in rows[scroll:]:
            row_height = 29 + len(details) * 20
            if y + row_height > box[3] - 5:
                break
            result.append(InventoryEntry(zone, key, (box[0] + 7, y, box[2] - 7, y + row_height),
                                         name, details, action, weapon_id))
            y += row_height + 3
        return tuple(result)

    bag_scroll = max(0, min(bag_scroll, max(0, len(bag_rows) - 1)))
    floor_scroll = max(0, min(floor_scroll, max(0, len(floor_rows) - 1)))
    return InventoryLayout(panel, bag_box, floor_box, tuple(slots),
                           place(bag_rows, bag_box, "bag", bag_scroll),
                           place(floor_rows, floor_box, "floor", floor_scroll),
                           inventory.bag_weight, bag_scroll, floor_scroll)
