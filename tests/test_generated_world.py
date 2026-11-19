from dataclasses import asdict
import json
import math
import unittest

from campaign.persistence import deserialize_campaign, serialize_campaign
from campaign.resources import assert_invariants
from campaign.routes import find_route
from campaign.simulation import Simulation
from prototype.generated_world import generate_region, generate_world
from prototype.movement import move_actor
from prototype.simulation import PlaySimulation


class GeneratedWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def test_region_ids_seeds_and_layout_are_independent_of_generation_order(self):
        scene = self.scene
        self.assertEqual(generate_world(**json.loads(json.dumps(scene["generation"]))), scene)
        recreated = []
        for row in reversed(scene["generated_regions"]):
            region = generate_region(42, row["id"], origin=row["origin"], entry_offsets=row["entry_offsets"])
            self.assertEqual(region["planned_facilities"], [site for site in scene["planned_facilities"] if site["region_id"] == row["id"]])
            recreated.append(region)
        self.assertFalse(set(recreated[0]["navigation"]) & set(recreated[1]["navigation"]))
        self.assertNotEqual(*[row["seed"] for row in scene["generated_regions"]])
        single = generate_world(42)
        self.assertEqual(single["planned_facilities"], [site for site in scene["planned_facilities"] if site["region_id"] == "region/1"])

    def test_player_starts_in_the_first_regions_safehouse(self):
        play = PlaySimulation(self.scene)
        safehouses = [site for site in play.world.facilities if site['kind'] == 'safehouse']
        self.assertEqual(len(safehouses), 2)
        self.assertEqual(play.player.xy, tuple(safehouses[0]['position']))
        self.assertNotEqual(play.player.xy, tuple(safehouses[1]['position']))
        self.assertEqual(play.world.territory_at(play.player.xy), 'region/1')

    def test_player_crosses_shared_portal_and_returns_without_resetting_campaign(self):
        play = PlaySimulation(self.scene)
        campaign, actor = play.campaign.simulation, play.player
        state = campaign.state
        hq = next(f for f in state.facilities.values() if f.region_id == "region/2" and "command" in f.capabilities)
        unit = next(unit for unit in state.units.values() if unit.node_id == hq.node_id)
        campaign.apply_action({"type": "damage_unit", "unit_id": unit.id, "soldiers": 1})
        spawn = next(site['service_node'] for site in play.world.facilities if site['kind'] == 'safehouse')
        path = find_route(state, spawn, hq.node_id)
        self.assertIn("portal/1", path)

        def walk(keys):
            for key in keys:
                node = state.nodes[key].position
                goal = node.x, node.y
                for _ in range(math.ceil(math.dist(actor.xy, goal) / 0.3) + 5):
                    distance = math.dist(actor.xy, goal)
                    if distance < 0.02:
                        break
                    move_actor(actor, (goal[0] - actor.position.x, goal[1] - actor.position.y), play.world,
                               min(distance, 0.3) / 3, speed=3)
                self.assertLess(math.dist(actor.xy, goal), 0.02)

        walk(path)
        play._sync_guards()
        self.assertEqual(play.world.territory_at(actor.xy), "region/2")
        walk(reversed(path))
        play._sync_guards()
        self.assertEqual(play.world.territory_at(actor.xy), "region/1")
        self.assertIs(play.campaign.simulation, campaign)
        self.assertEqual(unit.soldiers, 9)
        self.assertEqual(state.world_time, 0)

    def test_autonomous_cross_region_delivery_and_save_resume_preserve_resources(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        initial_player = play.player.xy
        depots = [f for f in state.facilities.values() if "logistics" in f.capabilities]
        self.assertIsNot(depots[0].inventory, depots[1].inventory)
        play.campaign.advance_to(600)
        delivered = [op for op in state.operations.values() if op.kind == "supply" and op.delivered_at is not None
                     and op.origin_id in state.facilities
                     and state.facilities[op.origin_id].region_id != state.facilities[op.destination_id].region_id]
        self.assertTrue(delivered)
        self.assertTrue(all("portal/1" in operation.route for operation in delivered))
        self.assertEqual(play.player.xy, initial_player)
        restored = Simulation(deserialize_campaign(serialize_campaign(state)))
        play.campaign.advance_to(720)
        restored.advance_to(720)
        self.assertEqual(asdict(state), asdict(restored.state))
        assert_invariants(state)
