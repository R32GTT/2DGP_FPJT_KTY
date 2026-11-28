"""실물 재고, 새 병력의 지급, C4 파괴와 복구 출동을 검증한다."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from campaign.models import Position
from campaign.logistics import plan_supplies
from campaign.resources import assert_invariants
from prototype.generated_world import generate_world
from prototype.inventory import C4
from prototype.savegame import load_game, save_game
from prototype.simulation import FIXED_DT, PlaySimulation


class SupplyStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def setUp(self):
        self.play = PlaySimulation(self.scene)
        self.state = self.play.campaign.simulation.state
        self.site = next(site for site in self.play.world.facilities if site["kind"] == "warehouse")

    def stack(self, resource):
        return next(row for row in self.play.supply_stacks
                    if row[:2] == (self.site["id"], resource))

    def test_collection_moves_physical_stock_into_bag(self):
        site_id, resource, point = self.stack("supplies")
        self.play.player.position = Position(*point)
        before = self.state.facilities[site_id].inventory[resource]
        self.play.inventory.take_facility_stock(site_id, resource, single=True)
        self.assertEqual(self.state.facilities[site_id].inventory[resource], before - 1)
        self.assertEqual(self.state.player_stock[resource], 1)
        self.assertEqual(self.play.inventory.carried["cargo:supplies"], 1)
        self.play.update_facility_interaction(True)
        self.assertEqual(self.play.facility_interaction["duration"], 1.5)
        for _ in range(round(1.5 / FIXED_DT)):
            self.play.update_facility_interaction(True)
        self.assertGreater(self.play.inventory.carried["cargo:supplies"], 1)

    def test_c4_destroys_only_target_stack_and_calls_repair_crew(self):
        site_id, resource, point = self.stack("ammunition")
        facility = self.state.facilities[site_id]
        before = facility.inventory[resource]
        supplies = facility.inventory["supplies"]
        self.play.player.position = Position(*point)
        self.play.inventory.carried[C4] = 1
        self.play.inventory.selected_sabotage_mode = "c4"
        for _ in range(round(2 / FIXED_DT) + 1):
            self.play.update_facility_interaction(True)
        charge_id = f"{site_id}#stock:{resource}"
        self.assertIn(charge_id, self.play.planted_charges)
        self.assertEqual(facility.inventory[resource], before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supply.json.gz"
            save_game(self.play, path)
            self.play = load_game(path)
        self.state = self.play.campaign.simulation.state
        facility = self.state.facilities[site_id]
        self.play.detonate_charges()
        self.assertEqual(facility.inventory[resource], 0)
        self.assertEqual(facility.inventory["supplies"], supplies)
        self.assertEqual(self.state.ledger.destroyed[resource], before)
        assert_invariants(self.state)

    def test_only_new_soldiers_lose_equipment_after_stock_is_destroyed(self):
        site_id = self.site["id"]
        facility = self.state.facilities[site_id]
        source = next(unit for unit in self.state.units.values() if unit.node_id == facility.node_id)
        old = self.play.roster.by_unit[source.id][0]
        original_ammo = old.weapon.ammo + old.inventory.get(f"ammo:{old.weapon.spec.ammo_type}", 0)
        original_parts = dict(old.weapon.attachments)
        self.play.campaign.simulation.apply_action({"type": "damage_facility", "facility_id": site_id,
                                                    "damage": 100, "destroy_inventory": True})
        recruit = deepcopy(source)
        recruit.id = site_id + "/reinforcement"
        recruit.soldiers = 1
        recruit.night_kits = 0
        self.state.units[recruit.id] = recruit
        self.state.initial_soldiers += 1
        new = self.play.roster.members(recruit.id, old.weapon.spec)[0]
        self.assertEqual(new.weapon.ammo, 0)
        self.assertFalse(new.inventory)
        self.assertFalse(new.weapon.attachments)
        self.assertEqual(old.weapon.ammo + old.inventory.get(f"ammo:{old.weapon.spec.ammo_type}", 0),
                         original_ammo)
        self.assertEqual(old.weapon.attachments, original_parts)
        assert_invariants(self.state)


if __name__ == "__main__":
    unittest.main()
