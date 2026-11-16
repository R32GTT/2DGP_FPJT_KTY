from pathlib import Path
import unittest

from prototype.simulation import PlaySimulation


SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/two_region_campaign.json"


def travel(simulation, direction, seconds):
    for _ in range(round(seconds * 60)):
        simulation.step(direction, sprint=True)


def eastbound_delivery(state):
    return next(operation for operation in state.operations.values()
                if operation.kind == "supply" and operation.destination_id == "depot_b")


class TwoRegionCampaignTests(unittest.TestCase):
    def test_crossing_preserves_losses_and_delays_eastbound_supply(self):
        simulation = PlaySimulation(SCENE)
        state = simulation.campaign.simulation.state
        delivery = eastbound_delivery(state)
        unit = state.units["a_garrison"]
        casualty = simulation.roster.by_id["a_garrison:0"]
        self.assertIn(casualty, simulation.guards)
        self.assertNotIn(simulation.roster.by_id["b_garrison:0"], simulation.guards)

        casualty.hp = 0
        simulation.events.emit("killed", simulation.time, casualty)
        travel(simulation, (1, 0), 22)
        travel(simulation, (0, 1), 6.2)
        self.assertIsNone(simulation.world.territory_at(simulation.player.xy))
        simulation.take_supplies()
        self.assertTrue(state.roads["a_b"].blocked)
        self.assertEqual(unit.soldiers, 9)

        travel(simulation, (0, -1), 6.2)
        travel(simulation, (1, 0), 22)
        self.assertEqual(simulation.world.territory_at(simulation.player.xy), "b")
        self.assertIs(state, simulation.campaign.simulation.state)
        self.assertGreater(simulation.player.hp, 0)
        self.assertIn("a_b", delivery.known_blocked)
        self.assertNotIn("hq_a", simulation.roster.active_nodes)
        self.assertIn(simulation.roster.by_id["b_garrison:0"], simulation.guards)

        travel(simulation, (0, 0), 44)
        comparison = PlaySimulation(SCENE)
        comparison.campaign.advance_to(state.world_time)
        self.assertIsNotNone(eastbound_delivery(comparison.campaign.simulation.state).delivered_at)
        self.assertIsNone(delivery.delivered_at)

        travel(simulation, (-1, 0), 22)
        travel(simulation, (0, 1), 6.2)
        simulation.take_supplies()
        self.assertFalse(state.roads["a_b"].blocked)
        travel(simulation, (0, -1), 6.2)
        travel(simulation, (-1, 0), 22)
        self.assertEqual(simulation.world.territory_at(simulation.player.xy), "a")
        self.assertEqual(unit.soldiers, 9)
        self.assertEqual(casualty.hp, 0)


if __name__ == "__main__":
    unittest.main()
