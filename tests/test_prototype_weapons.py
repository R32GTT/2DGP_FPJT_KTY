import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from campaign.models import Position
from prototype.actors import Actor
from prototype.ai_state import Intent
from prototype.combat import Combat
from prototype.events import EventBus
from prototype.geometry import SpatialIndex
from prototype.simulation import FIXED_DT, PlaySimulation
from prototype.weapons import WeaponState, load_weapons, weapon_pose


ROOT = Path(__file__).resolve().parents[1]
FIELD_SCENE = ROOT / "prototype/scenarios/field_proving_ground.json"


class WeaponTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = load_weapons()

    def test_loaded_chamber_skips_bolt_and_its_events(self):
        weapon = WeaponState.ready(self.specs["ar_a"])
        weapon.fire(0)
        total = weapon.ammo + weapon.reserve
        weapon.start_reload()
        self.assertAlmostEqual(weapon.remaining_reload, 1.5)
        events = weapon.tick(1.5)
        self.assertFalse(weapon.reloading)
        self.assertEqual(weapon.stage, 0)
        self.assertEqual(weapon.remaining_reload, 0)
        self.assertEqual(weapon.ammo, 31)
        self.assertEqual(weapon.ammo + weapon.reserve, total)
        self.assertFalse(any(event.get("stage") == "bolt_catch" for event in events))
        self.assertEqual(events[-1]["kind"], "reload_completed")

    def test_empty_chamber_runs_bolt_and_transfers_one_round(self):
        weapon = WeaponState(self.specs["ar_a"], 0, 90, chambered=False)
        weapon.start_reload()
        events = weapon.tick(1.5)
        self.assertEqual(weapon.stage, 2)
        self.assertFalse(weapon.chambered)
        self.assertAlmostEqual(weapon.remaining_reload, 0.5)
        self.assertEqual(events[-1]["stage"], "bolt_catch")
        weapon.tick(0.5)
        self.assertFalse(weapon.reloading)
        self.assertTrue(weapon.chambered)
        self.assertEqual((weapon.magazine, weapon.reserve, weapon.ammo), (29, 60, 30))

    def test_cancelled_insertion_restarts_without_undoing_completed_removal(self):
        weapon = WeaponState.ready(self.specs["ar_a"])
        weapon.fire(0)
        total = weapon.ammo + weapon.reserve
        weapon.start_reload()
        weapon.tick(0.9)
        weapon.cancel_reload()
        self.assertEqual(weapon.stage, 1)
        self.assertEqual(weapon.stage_elapsed, 0)
        self.assertFalse(weapon.magazine_attached)
        self.assertEqual(weapon.ammo + weapon.reserve, total)
        weapon.start_reload()
        self.assertEqual(weapon.remaining_reload, 1.0)
        weapon.tick(0.99)
        self.assertTrue(weapon.reloading)
        weapon.tick(0.01)
        self.assertFalse(weapon.reloading)
        self.assertEqual(weapon.ammo + weapon.reserve, total)

    def test_chamber_round_can_be_fired_with_magazine_removed(self):
        weapon = WeaponState.ready(self.specs["ar_a"])
        weapon.fire(0)
        weapon.start_reload()
        weapon.tick(0.5)
        self.assertFalse(weapon.fire(1))
        self.assertTrue(weapon.reloading)
        weapon.cancel_reload()
        self.assertTrue(weapon.fire(1))
        self.assertFalse(weapon.chambered)
        self.assertEqual(weapon.magazine, 0)
        self.assertFalse(weapon.fire(2))
        weapon.start_reload()
        self.assertEqual(weapon.remaining_reload, 1.5)
        weapon.tick(1.5)
        self.assertEqual((weapon.magazine, weapon.chambered), (29, True))

    def test_cancelled_bolt_keeps_inserted_magazine(self):
        weapon = WeaponState(self.specs["ar_a"], 0, 30, chambered=False)
        weapon.start_reload()
        weapon.tick(1.7)
        weapon.cancel_reload()
        self.assertEqual(weapon.stage, 2)
        self.assertEqual((weapon.magazine, weapon.reserve), (30, 0))
        weapon.start_reload()
        self.assertEqual(weapon.remaining_reload, 0.5)
        weapon.tick(0.5)
        self.assertEqual((weapon.ammo, weapon.reserve), (30, 0))

    def test_inserted_ammo_can_be_chambered_without_reserve_rounds(self):
        weapon = WeaponState(self.specs["ar_a"], 30, 0, chambered=False)
        events = weapon.start_reload()
        self.assertEqual(events[0]["stage"], "bolt_catch")
        self.assertEqual(weapon.remaining_reload, 0.5)
        weapon.tick(0.5)
        self.assertEqual((weapon.magazine, weapon.chambered, weapon.reserve), (29, True, 0))

    def test_large_and_partitioned_ticks_preserve_reload_and_ammo(self):
        for chambered in (False, True):
            with self.subTest(chambered=chambered):
                first = WeaponState(self.specs["ar_a"], 0, 90, chambered=chambered)
                second = WeaponState(self.specs["ar_a"], 0, 90, chambered=chambered)
                first.start_reload()
                second.start_reload()
                first.tick(3)
                for _ in range(180):
                    second.tick(FIXED_DT)
                self.assertEqual(first, second)

    def test_shot_uses_spread_before_adding_bloom_and_failures_leave_it_alone(self):
        weapon = WeaponState.ready(self.specs["ar_a"])
        self.assertTrue(weapon.fire(0))
        self.assertEqual(weapon.shot_spread_deg, 0.7)
        before = (weapon.bloom_deg, weapon.recovery_delay, weapon.ammo, weapon.shot_spread_deg)
        self.assertFalse(weapon.fire(0.1))
        self.assertEqual((weapon.bloom_deg, weapon.recovery_delay, weapon.ammo, weapon.shot_spread_deg), before)
        weapon.tick(0.2)
        self.assertTrue(weapon.fire(0.2))
        self.assertAlmostEqual(weapon.shot_spread_deg, 0.95)
        weapon.chambered = False
        before = (weapon.bloom_deg, weapon.recovery_delay)
        self.assertFalse(weapon.fire(1))
        self.assertEqual((weapon.bloom_deg, weapon.recovery_delay), before)

    def test_bloom_recovery_waits_and_is_independent_of_tick_partition(self):
        first = WeaponState.ready(self.specs["ar_a"])
        second = WeaponState.ready(self.specs["ar_a"])
        first.fire(0)
        second.fire(0)
        first.tick(0.35)
        second.tick(0.1)
        second.tick(0.25)
        self.assertAlmostEqual(first.bloom_deg, 0.05)
        self.assertAlmostEqual(first.bloom_deg, second.bloom_deg)
        first.tick(1)
        self.assertEqual(first.bloom_deg, 0)

    def test_aim_settles_gradually_and_release_removes_aim_bonus(self):
        weapon = WeaponState.ready(self.specs["pistol_45"])
        weapon.update_accuracy(0.125, aiming=True)
        self.assertAlmostEqual(weapon.spread_deg, 0.8)
        weapon.update_accuracy(0.125, aiming=True)
        self.assertAlmostEqual(weapon.spread_deg, 0.6)
        weapon.update_accuracy(0)
        self.assertEqual(weapon.spread_deg, 1)

    def test_actual_speed_and_sprinting_modify_spread_with_a_cap(self):
        weapon = WeaponState.ready(self.specs["pistol_45"])
        weapon.update_accuracy(0.25, aiming=True, movement_speed_mps=5, sprinting=True)
        self.assertAlmostEqual(weapon.spread_deg, 2.85)
        weapon.update_accuracy(0, movement_speed_mps=100, sprinting=True)
        self.assertEqual(weapon.spread_deg, 3)
        weapon.update_accuracy(0, movement_speed_mps=0, sprinting=True)
        self.assertEqual(weapon.spread_deg, 1)

    def test_distance_damage_matches_the_transferred_pistol_curve(self):
        spec = self.specs["pistol_45"]
        for distance, expected in ((0, 70), (25, 65), (37.5, 57.5), (50, 50), (62.5, 25), (75, 0), (100, 0)):
            with self.subTest(distance=distance):
                self.assertEqual(spec.damage_at(distance), expected)
        self.assertFalse(WeaponState.ready(spec).automatic)

    def test_loaded_definitions_are_independent_per_weapon(self):
        first = WeaponState.ready(self.specs["ar_a"])
        second = WeaponState.ready(self.specs["ar_a"])
        first.fire(0)
        first.update_accuracy(0.25, aiming=True)
        self.assertEqual(second.ammo, 31)
        self.assertEqual((second.bloom_deg, second.aim_progress), (0, 0))


class WeaponConfigTests(unittest.TestCase):
    def load_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weapons.json"
            path.write_text(json.dumps(rows), encoding="utf-8")
            return load_weapons(path)

    def test_legacy_json_retains_constant_damage_without_a_curve(self):
        row = json.loads((ROOT / "prototype/data/weapons.json").read_text(encoding="utf-8"))[0]
        for key in ("ammo_type", "automatic", "damage_curve", "spread_per_shot_deg", "spread_max_deg",
                    "spread_recovery_delay_s", "spread_recovery_deg_per_s", "aim_spread_multiplier",
                    "aim_settle_s", "move_spread_deg_per_mps", "sprint_spread_multiplier", "enemy_spread_multiplier"):
            row.pop(key)
        spec = self.load_rows([row])["ar_a"]
        self.assertEqual(spec.damage_at(59), 18)
        self.assertEqual(spec.damage_at(60), 0)

    def test_malformed_definitions_fail_during_load(self):
        base = json.loads((ROOT / "prototype/data/weapons.json").read_text(encoding="utf-8"))[0]
        invalid = (
            {"damage_curve": [[0, 18], [0, 10], [60, 0]]},
            {"damage_curve": [[0, 18], [50, 0]]},
            {"damage_curve": [[0, 18], [60, 1]]},
            {"damage_curve": [[0, 18], [60, float("nan")]]},
            {"spread_max_deg": 0.1},
            {"aim_settle_s": 0},
            {"spread_per_shot_deg": -1},
            {"move_spread_deg_per_mps": float("inf")},
            {"automatic": "auto"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load_rows([dict(base, **changes)])
        with self.assertRaises(ValueError):
            self.load_rows([base, copy.deepcopy(base)])
        with self.assertRaises(ValueError):
            self.load_rows({})


class WeaponIntegrationTests(unittest.TestCase):
    def test_holding_fire_does_not_cancel_reload_but_a_new_click_does(self):
        simulation = PlaySimulation(FIELD_SCENE)
        weapon = simulation.player.weapon
        weapon.fire(0)
        simulation.reload()
        simulation.step(firing=True)
        self.assertTrue(weapon.reloading)
        ammo = weapon.ammo
        simulation.step(firing=True, trigger_pressed=True)
        self.assertFalse(weapon.reloading)
        self.assertEqual(weapon.ammo, ammo)  # 발사 대기 중인 클릭도 재장전을 취소한다.
        for _ in range(11):
            simulation.step(firing=True)
        self.assertLess(weapon.ammo, ammo)

    def test_semi_automatic_requires_a_new_trigger(self):
        simulation = PlaySimulation(FIELD_SCENE)
        weapon = WeaponState.ready(simulation.specs["pistol_45"])
        simulation.player.loadout = {"pistol_45": weapon}
        simulation.player.active_weapon = "pistol_45"
        simulation.step(firing=True)
        self.assertEqual(weapon.ammo, 13)
        simulation.step(firing=True, trigger_pressed=True)
        self.assertEqual(weapon.ammo, 12)
        for _ in range(20):
            simulation.step(firing=True)
        self.assertEqual(weapon.ammo, 12)

    def test_blocked_player_has_no_movement_spread(self):
        simulation = PlaySimulation(FIELD_SCENE)
        simulation.player.position.x = simulation.world.bounds.x + simulation.player.radius
        simulation.step((-1, 0), sprint=True)
        self.assertEqual(simulation.player.weapon.movement_spread_deg, 0)

    def test_arrived_guard_with_zero_movement_time_has_no_movement_spread(self):
        simulation = PlaySimulation(FIELD_SCENE)
        guard = simulation.guards[0]
        with patch.object(simulation.ai, "update", return_value={guard.id: Intent(step_dt=0)}):
            simulation.step()
        self.assertEqual(guard.weapon.movement_spread_deg, 0)

    def test_switching_preserves_reload_ammo_bloom_and_absolute_fire_deadline(self):
        simulation = PlaySimulation(FIELD_SCENE)
        weapon = simulation.player.weapon
        weapon.fire(0)
        weapon.start_reload()
        weapon.tick(0.5)
        before = (weapon.magazine, weapon.reserve, weapon.chambered, weapon.bloom_deg, weapon.next_shot)
        simulation.switch_weapon(1)
        self.assertFalse(weapon.reloading)
        self.assertEqual(weapon.stage, 1)
        simulation.switch_weapon(0)
        self.assertIs(simulation.player.weapon, weapon)
        self.assertEqual((weapon.magazine, weapon.reserve, weapon.chambered, weapon.bloom_deg, weapon.next_shot), before)

    def test_hitscan_applies_distance_damage_to_each_penetrated_actor(self):
        shooter = Actor("player", Position(0, 0), team="player", active_weapon="ar_a")
        shooter.loadout["ar_a"] = WeaponState.ready(load_weapons()["ar_a"])
        muzzle = weapon_pose(shooter)[1]
        near = Actor("near", Position(20, muzzle[1]))
        far = Actor("far", Position(50, muzzle[1]))
        combat = Combat(EventBus())
        combat.prepare([shooter, near, far], FIXED_DT)
        with patch.object(combat.random, "uniform", return_value=0):
            self.assertTrue(combat.fire(shooter, SimpleNamespace(geometry=SpatialIndex()), 0))
        expected = shooter.weapon.spec.damage_at(50 - far.radius - muzzle[0])
        self.assertEqual(near.hp, 32)
        self.assertAlmostEqual(far.hp, 50 - expected)
        self.assertEqual(combat.hits, 2)

    def test_vehicle_driver_hit_retains_kill_context(self):
        shooter = Actor("player", Position(0, 0), team="player", active_weapon="ar_a")
        shooter.loadout["ar_a"] = WeaponState.ready(load_weapons()["ar_a"])
        driver = Actor("driver", Position(30, 0), hp=1)
        truck = SimpleNamespace(id="truck")
        transports = SimpleNamespace(raycast=lambda start, end: (0.5, truck, driver)
                                     if math.dist(start, end) > 2 else None)
        events = EventBus()
        combat = Combat(events, transports)
        combat.prepare([shooter], FIXED_DT)
        with patch.object(combat.random, "uniform", return_value=0):
            combat.fire(shooter, SimpleNamespace(geometry=SpatialIndex()), 0)
        killed = next(event for event in events.pending if event.kind == "killed")
        self.assertEqual(killed.source, driver.id)
        self.assertEqual(killed.data["vehicle_id"], truck.id)
        self.assertIn("incoming", killed.data)


if __name__ == "__main__":
    unittest.main()
