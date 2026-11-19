import math
import unittest

from prototype.generated_world import generate_world
from prototype.simulation import PlaySimulation


class GeneratedQRFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def test_local_garrison_uses_real_members_and_losses_survive_reentry(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        actors = list(play.guards)
        self.assertTrue(actors)
        self.assertEqual(len(actors), sum(unit.soldiers for unit in state.units.values() if unit.node_id in play.roster.active_nodes))
        self.assertTrue(all(play.world.navigation.walkable(actor.xy) for actor in actors))
        for index, actor in enumerate(actors):
            self.assertTrue(actor.weapon)
            self.assertIn(actor.id, play.ai.brains)
            self.assertTrue(all(math.dist(actor.xy, other.xy) >= actor.radius + other.radius for other in actors[index + 1:]))
        casualty = next(actor for actor in actors if '/hq/' in actor.id)
        unit = state.units[casualty.source_unit]
        casualty.hp = 0
        play.events.emit('killed', play.time, casualty)
        play._dispatch()
        remaining = unit.soldiers
        play.player.position.x = 128
        play._sync_guards()
        self.assertNotIn(casualty.home_node, play.roster.active_nodes)
        play.player.position.x = 0
        play._sync_guards()
        self.assertIs(play.roster.by_id[casualty.id], casualty)
        self.assertEqual(casualty.hp, 0)
        self.assertEqual(unit.soldiers, remaining)
        self.assertEqual(len(play.guard_records), len({actor.id for actor in play.guard_records}))
