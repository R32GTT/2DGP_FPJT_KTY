"""마우스 인벤토리와 플레이어·적군 예비탄 분리를 확인한다."""

from pathlib import Path
import ctypes
import math
import unittest

import sdl2

from prototype.camera import PlayCamera
from prototype.controls import Controls
from prototype.inventory_ui import build_inventory_layout
from prototype.inventory import weapon_slots
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState


SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_facility.json"


class InventoryUITests(unittest.TestCase):
    def test_identical_guns_can_fill_both_primary_slots(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        play.player.loadout.clear()
        play.player.active_weapon = ""
        first = WeaponState.ready(play.specs["ar_a"])
        second = WeaponState.ready(play.specs["ar_a"])
        second.magazine = 7
        point = play.player.xy
        play.dropped = [(point, {"ar_a": first}), (point, {"ar_a": second})]

        layout = build_inventory_layout(play.inventory, (1280, 720))
        play.inventory.interact(layout.floor[0], next(row for row in layout.slots if row.key == (0,)))
        layout = build_inventory_layout(play.inventory, (1280, 720))
        play.inventory.interact(layout.floor[0], next(row for row in layout.slots if row.key == (1,)))

        keys = weapon_slots(play.player.loadout)
        self.assertNotEqual(keys[0], keys[1])
        self.assertIs(play.player.loadout[keys[0]], first)
        self.assertIs(play.player.loadout[keys[1]], second)
        play.switch_weapon(0)
        self.assertIs(play.player.weapon, first)
        play.switch_weapon(1)
        self.assertIs(play.player.weapon, second)

    def test_guns_move_between_floor_bag_and_slots(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        bag = play.inventory
        gun = WeaponState.ready(play.specs["ar_b"])
        part = next(part for part in gun.spec.compatible if part.slot == "optic")
        gun.change_attachment("optic", part)
        play.dropped = [(play.player.xy, {"ar_b": gun})]
        layout = build_inventory_layout(bag, (1280, 720))
        target = layout.drop_target(layout.bag_box[0] + 20, layout.bag_box[3] - 20)
        bag.interact(next(row for row in layout.floor if row.key[0] == "gun"), target)
        self.assertIn(gun, bag.carried_weapons.values())
        self.assertFalse(play.dropped)
        self.assertLessEqual(bag.bag_weight, 40)

        layout = build_inventory_layout(bag, (1280, 720))
        bag.interact(next(row for row in layout.slots if row.key == (0,)), target)
        self.assertEqual(len(bag.carried_weapons), 2)
        layout = build_inventory_layout(bag, (1280, 720))
        source = next(row for row in layout.bag if row.weapon_id == "ar_b")
        bag.interact(source, next(row for row in layout.slots if row.key == (1,)))
        self.assertIs(play.player.weapon, gun)
        self.assertEqual(gun.part("optic"), part)
        layout = build_inventory_layout(bag, (1280, 720))
        source = next(row for row in layout.bag if row.key[0] == "bag_gun")
        bag.interact(source, layout.drop_target(layout.floor_box[0] + 20, layout.floor_box[3] - 20))
        self.assertTrue(play.dropped)

    def test_bag_capacity_preserves_weapon_when_pickup_fails(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        bag = play.inventory
        gun = WeaponState.ready(play.specs["ar_b"])
        play.dropped = [(play.player.xy, {"ar_b": gun})]
        bag.carried["cargo:supplies"] = math.floor(40 - bag.bag_weight)
        self.assertFalse(bag.store_gun(0, "ar_b"))
        self.assertIs(play.dropped[0][1]["ar_b"], gun)
        self.assertFalse(bag.carried_weapons)
        equipped = play.player.weapon
        self.assertFalse(bag.stow_gun(0))
        self.assertIs(play.player.weapon, equipped)

    def test_bag_gun_swaps_with_equipped_gun(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        bag = play.inventory
        outgoing = play.player.weapon
        incoming = WeaponState.ready(play.specs["ar_a"])
        bag.stow_reserve(incoming)
        bag.carried_weapons["ar_a"] = incoming
        equipped_count = len(play.player.loadout)
        self.assertTrue(bag.equip_carried_gun("ar_a", 0))
        self.assertIs(play.player.weapon, incoming)
        self.assertIn(outgoing, bag.carried_weapons.values())
        self.assertEqual(len(play.player.loadout), equipped_count)

    def test_player_and_enemy_keep_spare_rounds_in_their_inventories(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        player, enemy = play.player, play.guards[0]
        player_key = f"ammo:{player.weapon.spec.ammo_type}"
        enemy_key = f"ammo:{enemy.weapon.spec.ammo_type}"
        self.assertEqual(player.weapon.reserve, 0)
        self.assertGreater(play.inventory.carried[player_key], 0)
        self.assertEqual(enemy.weapon.reserve, 0)
        self.assertGreater(enemy.inventory[enemy_key], 0)
        enemy.weapon.fire(0)
        play.reload(enemy)
        self.assertGreater(enemy.weapon.reserve, 0)
        self.assertLess(enemy.inventory[enemy_key], enemy.weapon.spec.reserve)
        remaining = enemy.inventory[enemy_key] + enemy.weapon.reserve
        enemy.hp = 0
        play.events.emit("killed", play.time, enemy)
        play._dispatch()
        self.assertEqual(play.inventory.ground[-1][1][enemy_key], remaining)
        self.assertEqual(next(iter(play.dropped[-1][1].values())).reserve, 0)

    def test_floor_gun_shows_parts_and_pickup_stows_spare_ammo(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        point = play.player.xy
        gun = WeaponState.ready(play.specs["ar_b"])
        part = next(part for part in gun.spec.compatible if part.slot == "optic")
        gun.change_attachment("optic", part)
        play.dropped = [(point, {gun.spec.id: gun})]
        layout = build_inventory_layout(play.inventory, (1280, 720))
        entry = next(row for row in layout.floor if row.key[0] == "gun")
        self.assertIn("-" + part.name, entry.details)
        before = play.inventory.carried.get(f"ammo:{gun.spec.ammo_type}", 0)
        play.inventory.interact(entry)
        self.assertIs(play.player.weapon, gun)
        self.assertEqual(gun.reserve, 0)
        self.assertEqual(play.inventory.carried[f"ammo:{gun.spec.ammo_type}"], before + gun.spec.reserve)

    def test_shift_moves_one_item_and_drag_uses_empty_floor_space(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        bag = play.inventory
        layout = build_inventory_layout(bag, (1280, 720))
        source = next(row for row in layout.bag if row.key[1].startswith("ammo:"))
        key = source.key[1]
        before = bag.carried[key]
        bag.interact(source, shift=True)
        self.assertEqual(bag.carried[key], before - 1)
        layout = build_inventory_layout(bag, (1280, 720))
        target = layout.drop_target(layout.floor_box[0] + 20, layout.floor_box[3] - 20)
        self.assertEqual(target.zone, "floor")
        bag.interact(next(row for row in layout.bag if row.key[1] == key), target, shift=True)
        self.assertEqual(bag.carried[key], before - 2)
        floor = next(row for row in build_inventory_layout(bag, (1280, 720)).floor
                     if row.key[0] == "ground" and row.key[2] == key)
        bag.interact(floor, shift=True)
        self.assertEqual(bag.carried[key], before - 1)

    def test_attached_part_can_be_dragged_back_to_bag(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        weapon = play.player.weapon
        part = next(part for part in weapon.spec.compatible if part.slot == "optic")
        weapon.change_attachment(part.slot, part)
        layout = build_inventory_layout(play.inventory, (1280, 720))
        source = next(row for row in layout.slots if row.zone == "attached")
        target = layout.drop_target(layout.bag_box[0] + 20, layout.bag_box[3] - 20)
        play.inventory.interact(source, target)
        self.assertIsNone(weapon.part("optic"))
        self.assertEqual(play.inventory.carried[part.id], 1)

    def test_excess_loose_ammo_remains_on_ground_when_bag_is_full(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        bag = play.inventory
        bag.carried["cargo:supplies"] = 37
        weapon = WeaponState.ready(play.specs["ar_b"])
        key = f"ammo:{weapon.spec.ammo_type}"
        before = bag.carried[key]
        bag.stow_reserve(weapon)
        self.assertLessEqual(bag.bag_weight, 40 + 1e-8)
        self.assertEqual(weapon.reserve, 0)
        self.assertEqual(bag.carried[key] - before + bag.ground[-1][1][key], weapon.spec.reserve)

    def test_mouse_events_are_collected_without_firing(self):
        self.assertEqual(sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS), 0)
        try:
            controls = Controls()
            controls.inventory_open = True
            down = sdl2.SDL_Event()
            down.type = sdl2.SDL_MOUSEBUTTONDOWN
            down.button.button = sdl2.SDL_BUTTON_LEFT
            down.button.x, down.button.y = 550, 200
            sdl2.SDL_PushEvent(ctypes.byref(down))
            up = sdl2.SDL_Event()
            up.type = sdl2.SDL_MOUSEBUTTONUP
            up.button.button = sdl2.SDL_BUTTON_LEFT
            up.button.x, up.button.y = 820, 200
            sdl2.SDL_PushEvent(ctypes.byref(up))
            controls.poll(PlayCamera())
            self.assertEqual([event[0] for event in controls.inventory_events], ["down", "up"])
            self.assertFalse(controls.firing)
        finally:
            sdl2.SDL_Quit()


if __name__ == "__main__":
    unittest.main()
