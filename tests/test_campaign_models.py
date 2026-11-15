import copy
import unittest

from campaign.models import validate_state
from campaign.scenario import load_scenario


class ScenarioTests(unittest.TestCase):
    def test_fixed_world_has_real_assets_and_asymmetric_stock(self):
        state = load_scenario()
        self.assertEqual(len(state.regions), 3)
        self.assertEqual(len(state.facilities), 9)
        self.assertEqual(len(state.vehicles), 8)
        self.assertNotEqual(state.facilities["depot_a"].inventory,
                            state.facilities["depot_b"].inventory)
        for region in state.regions.values():
            capabilities = {cap for fid in region.facility_ids
                            for cap in state.facilities[fid].capabilities}
            self.assertTrue({"command", "communication", "logistics"} <= capabilities)

    def test_rejects_negative_inventory_and_broken_references(self):
        original = load_scenario()
        for mutation in (lambda s: s.facilities["depot_a"].inventory.update(supplies=-1),
                         lambda s: setattr(s.roads["a_b"], "end", "missing"),
                         lambda s: setattr(s.vehicles["a_truck_1"], "capacity", 0)):
            state = copy.deepcopy(original)
            mutation(state)
            with self.assertRaises(ValueError):
                validate_state(state)

    def test_campaign_random_stream_is_reproducible(self):
        left, right = load_scenario(seed=7), load_scenario(seed=7)
        self.assertEqual([left.choose(["a", "b", "c"]) for _ in range(20)],
                         [right.choose(["c", "a", "b"]) for _ in range(20)])
