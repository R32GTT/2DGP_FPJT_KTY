import math
import unittest

from campaign.resources import assert_invariants
from campaign.routes import FOOT_SPEED_MPS, position, road_between
from campaign.models import Position
from prototype.actors import Actor
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
        play.player.position = Position(*self.scene['generated_regions'][1]['origin'])
        play._sync_guards()
        self.assertNotIn(casualty.home_node, play.roster.active_nodes)
        play.player.position = Position(*play.world.safehouse)
        play._sync_guards()
        self.assertIs(play.roster.by_id[casualty.id], casualty)
        self.assertEqual(casualty.hp, 0)
        self.assertEqual(unit.soldiers, remaining)
        self.assertEqual(len(play.guard_records), len({actor.id for actor in play.guard_records}))

    def sighting(self, play):
        witness = next(actor for actor in play.guards if '/hq/' in actor.id)
        point = witness.xy[0] + math.cos(witness.facing) * 4, witness.xy[1] + math.sin(witness.facing) * 4
        self.assertTrue(play.world.navigation.walkable(point))
        play.player.position = Position(*point)
        for index in range(1, 49):
            play.time = index / 60
            play.ai.update(play.guards, play.player, play.time)
        self.assertTrue(play.ai.brains[witness.id].memory.visible)
        return witness, tuple(round(axis / 4) * 4 for axis in point)

    def test_real_sighting_dispatches_same_members_along_roads_and_delivers_last_report(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        witness, reported = self.sighting(play)
        operation, = [op for op in state.operations.values() if op.kind == 'qrf']
        self.assertEqual(tuple(operation.report_xy), reported)
        self.assertNotEqual(state.facilities[operation.origin_id].region_id, state.facilities[operation.destination_id].region_id)
        self.assertEqual(operation.route[-1], operation.destination_node)
        self.assertTrue(all(state.units[key].operation_id == operation.id for key in operation.unit_ids))
        play.player.position = Position(0, 0)
        seen = {}
        held = False
        distance = sum(road_between(state, a, b).length_m for a, b in zip(operation.route, operation.route[1:]))
        deadline = math.ceil(distance / FOOT_SPEED_MPS) + 60
        while state.world_time < deadline:
            second = state.world_time + 1
            play.time = float(second)
            play.campaign.advance_to(second)
            play._sync_guards()
            members = play.presence.marches.get(operation.id, [])
            if members:
                for actor in members:
                    if actor.id in seen:
                        self.assertIs(seen[actor.id], actor)
                    seen[actor.id] = actor
                    self.assertTrue(play.world.navigation.walkable(actor.xy))
                if not held:
                    before = position(state, operation)
                    play.ai.brains[members[0].id].memory.visible = True
                    play.campaign.advance_to(second + 1)
                    self.assertEqual(position(state, operation), before)
                    self.assertEqual(operation.waiting_reason, 'local_engagement')
                    play.roster.materialized = False
                    play._sync_guards()
                    play.campaign.advance_to(second + 2)
                    self.assertNotEqual(position(state, operation), before)
                    play.ai.brains[members[0].id].memory.visible = False
                    play.roster.materialized = True
                    play.time = float(second + 2)
                    play._sync_guards()
                    held = True
            if operation.phase == 'completed':
                break
        self.assertTrue(seen)
        self.assertEqual(operation.phase, 'completed')
        self.assertEqual(sum(unit.soldiers for unit in state.units.values()), state.initial_soldiers)
        for key in operation.unit_ids:
            self.assertEqual(state.units[key].node_id, operation.destination_node)
        for key, actor in seen.items():
            self.assertIs(play.roster.by_id[key], actor)
            self.assertTrue(actor.present)
            self.assertEqual(play.ai.brains[key].memory.reported, reported)
            self.assertNotEqual(play.ai.brains[key].memory.reported, play.player.xy)
        play.ai.update(play.guards, play.player, play.time)
        self.assertTrue(all(actor.state in {'search', 'combat_guard'} for actor in seen.values()))
        assert_invariants(state)

    def test_communication_failure_or_no_surplus_blocks_support(self):
        for mode in ('no_signal', 'no_squad'):
            with self.subTest(mode=mode):
                play = PlaySimulation(self.scene)
                state = play.campaign.simulation.state
                if mode == 'no_signal':
                    comms = next(f for f in state.facilities.values() if f.region_id == 'region/1' and 'communication' in f.capabilities)
                    play.campaign.damage_facility(comms.id, 100)
                else:
                    region = state.regions['region/2']
                    nodes = {state.facilities[key].node_id for key in region.facility_ids}
                    region.minimum_garrison = sum(unit.soldiers for unit in state.units.values() if unit.node_id in nodes)
                self.sighting(play)
                self.assertFalse(any(op.kind == 'qrf' for op in state.operations.values()))
                self.assertTrue(any(row['kind'] == 'qrf_report' and row['status'] == mode for row in state.history))

    def test_noise_without_sighting_does_not_request_support(self):
        play = PlaySimulation(self.scene)
        guard = play.guards[0]
        play.player.position = Position(160, 0)
        noise = Actor('noise', Position(guard.position.x + 2, guard.position.y), team='player')
        play.events.emit('noise', 0, noise, radius=20, strength=1, reason='gunshot')
        play._dispatch()
        for index in range(1, 49):
            play.ai.update(play.guards, play.player, index / 60)
        self.assertFalse(any(op.kind == 'qrf' for op in play.campaign.simulation.state.operations.values()))
