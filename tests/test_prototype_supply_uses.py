"""공용 자원의 탄종별 비용과 안전가옥 사용을 검증한다."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campaign.models import Position
from campaign.resources import assert_invariants, issue_ammunition
from prototype.generated_world import generate_world
from prototype.inventory import AMMO_CRATE
from prototype.inventory_ui import build_inventory_layout
from prototype.savegame import load_game, save_game
from prototype.simulation import PlaySimulation
from prototype.supply_uses import AMMO_USES, DATA_PATH, load_supply_uses


class SupplyUseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def setUp(self):
        self.play = PlaySimulation(self.scene)
        self.state = self.play.campaign.simulation.state
        self.warehouse = next(site for site in self.play.world.facilities if site["kind"] == "warehouse")
        self.safehouses = [site for site in self.play.world.facilities if site["kind"] == "safehouse"]

    def collect(self, resource, count):
        site_id, _, point = next(row for row in self.play.supply_stacks
                                 if row[:2] == (self.warehouse["id"], resource))
        self.play.player.position = Position(*point)
        for _ in range(count):
            self.play.inventory.take_facility_stock(site_id, resource, single=True)

    def deposit(self, site, resources):
        self.play.player.position = Position(*site["terminal"])
        for resource in resources:
            self.play.inventory.activate_action(("put", site["id"], f"cargo:{resource}"))

    def test_future_ammo_and_equipment_can_be_registered_in_data(self):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        data["ammo"].append({"id": "new_caliber", "name": "새 탄종", "round_weight": 0.02,
                             "rounds": 17, "ammunition_cost": 3})
        data["equipment"].append({"id": "gear:boots", "name": "장화", "weight": 1.0,
                                  "cost": {"supplies": 2}})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supply_uses.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            ammo, equipment, _ = load_supply_uses(path)
        self.assertEqual(ammo["new_caliber"].ammunition_cost, 3)
        self.assertEqual(equipment["gear:boots"].cost, {"supplies": 2})

    def test_ammo_uses_its_own_ratio_from_shared_stock(self):
        facility = self.state.facilities[self.warehouse["id"]]
        before = facility.inventory["ammunition"]
        granted = issue_ammunition(self.state, facility.id, 2,
                                   cost=AMMO_USES["precision"].ammunition_cost)
        self.assertEqual(granted, 2)
        self.assertEqual(facility.inventory["ammunition"], before - 4)
        assert_invariants(self.state)

    def test_safehouse_stock_is_local_and_survives_save(self):
        self.collect("supplies", 5)
        self.collect("ammunition", 1)
        self.collect("repair_parts", 1)
        first, second = self.safehouses[:2]
        self.deposit(first, ("supplies", "ammunition", "repair_parts"))
        layout = build_inventory_layout(self.play.inventory, (1280, 720))
        self.assertTrue(any(entry.action == ("prepare_equipment", first["id"], "gear:vest")
                            for entry in layout.floor))
        self.play.inventory.prepare_equipment(first["id"], "gear:vest")
        self.play.inventory.prepare_ammo(first["id"], "rifle")
        stored = self.play.inventory.stored[first["id"]]
        self.assertEqual(stored["gear:vest"], 1)
        self.assertEqual(stored["ammo:rifle"], AMMO_USES["rifle"].rounds)
        self.assertEqual(self.state.player_stock["supplies"], 0)
        self.assertEqual(self.state.player_stock["ammunition"], 0)
        self.play.player.position = Position(*second["terminal"])
        self.play.inventory.prepare_equipment(second["id"], "gear:vest")
        self.assertNotIn(second["id"], self.play.inventory.stored)
        self.play.player.position = Position(*first["terminal"])
        self.play.inventory.activate_action(("take", first["id"], "gear:vest"))
        self.play.inventory.equip_armor("gear:vest")
        self.assertEqual(self.play.player.armor_max, 65)
        self.play.player.armor = 30
        self.play.inventory.repair_armor(first["id"])
        self.assertEqual(self.play.player.armor, 55)
        self.assertEqual(self.state.player_stock["repair_parts"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safehouse.json.gz"
            save_game(self.play, path)
            restored = load_game(path)
        self.assertEqual(restored.player.armor_max, 65)
        self.assertEqual(restored.player.armor, 55)
        self.assertTrue(restored.player.armor_equipment["gear:vest"])
        self.assertEqual(restored.inventory.stored[first["id"]]["ammo:rifle"], AMMO_USES["rifle"].rounds)
        restored.inventory.validate_cargo()
        assert_invariants(restored.campaign.simulation.state)

    def test_bag_ammo_conversion_uses_configured_cost(self):
        self.collect("ammunition", AMMO_USES["precision"].ammunition_cost)
        self.assertEqual(self.play.inventory.carried[AMMO_CRATE], 2)
        self.play.inventory._unpack_ammunition("precision")
        self.assertNotIn(AMMO_CRATE, self.play.inventory.carried)
        self.assertEqual(self.play.inventory.carried["ammo:precision"], AMMO_USES["precision"].rounds)
        self.assertEqual(self.state.player_stock["ammunition"], 0)
        assert_invariants(self.state)

    def test_safehouse_makes_usable_c4_from_two_resources(self):
        self.collect("supplies", 2)
        self.collect("ammunition", 1)
        first = self.safehouses[0]
        self.deposit(first, ("supplies", "ammunition"))
        self.play.inventory.prepare_equipment(first["id"], "charge:c4")
        self.play.inventory.activate_action(("take", first["id"], "charge:c4"))
        self.assertEqual(self.play.inventory.carried["charge:c4"], 1)
        self.assertEqual(self.play.inventory.item("charge:c4")[0], "C4")
        self.assertEqual(self.state.player_stock["supplies"], 0)
        self.assertEqual(self.state.player_stock["ammunition"], 0)
        assert_invariants(self.state)

    def test_truck_cargo_uses_the_same_safehouse_stock(self):
        operation = next(op for op in self.state.operations.values() if op.kind == "supply"
                         and self.state.reservations[op.id].cargo["supplies"] >= 5)
        self.play.roster.materialized = False
        self.play.campaign.simulation.advance_to(31)
        self.assertEqual(operation.phase, "outbound")
        before = operation.cargo["supplies"]
        with patch.object(self.play.transports, "nearby_cargo", return_value=operation):
            self.play.inventory._take_cargo(operation.id, "supplies")
        captured = self.play.inventory.carried["cargo:supplies"]
        self.assertEqual(operation.cargo["supplies"], before - captured)
        first = self.safehouses[0]
        self.deposit(first, ("supplies",))
        self.play.inventory.prepare_equipment(first["id"], "gear:vest")
        self.assertEqual(self.play.inventory.stored[first["id"]]["gear:vest"], 1)
        assert_invariants(self.state)


if __name__ == "__main__":
    unittest.main()
