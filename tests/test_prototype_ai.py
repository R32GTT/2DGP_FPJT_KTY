import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from campaign.models import Position
from prototype.actors import Actor
from prototype.ai import EnemyAI, SearchCue
from prototype.combat import Combat
from prototype.events import EventBus
from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.navigation import Navigation
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState, load_weapons


DOOR_SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_door_queue.json"


class PrototypeAITests(unittest.TestCase):
    def test_gunshot_stays_in_focus_while_route_bends_away(self):
        simulation = PlaySimulation(DOOR_SCENE)
        guard = simulation.guards[0]
        simulation.player.hp = 0
        cue = (9, 9)
        brain = simulation.ai.brains[guard.id]
        brain.combat_alert_until = 60
        brain.priority_cue = SearchCue("sound", cue, 0)

        for tick in range(1, 31):
            intent = simulation.ai.update([guard], simulation.player, tick / 60)[guard.id]

        self.assertGreater(intent.direction[0], 0)
        self.assertNotEqual(brain.path[0], cue)
        self.assertEqual(brain.look_point, cue)
        self.assertAlmostEqual(guard.facing, math.atan2(cue[1] - guard.xy[1], cue[0] - guard.xy[0]))

    def test_searchers_can_pass_an_occupied_door_in_opposite_directions(self):
        simulation = PlaySimulation(DOOR_SCENE)
        simulation.player.hp = 0
        front, following = simulation.guards
        front.position, following.position = Position(6.2, 6), Position(7.88, 6)
        for actor, goal in ((front, (10, 6)), (following, (4, 6))):
            brain = simulation.ai.brains[actor.id]
            brain.incident = "door_test"
            brain.combat_alert_until = 60
            brain.priority_cue = SearchCue("seen", goal, 0)

        while simulation.time < 5 and (front.xy[0] < 9 or following.xy[0] > 5):
            simulation.step()

        self.assertGreaterEqual(front.xy[0], 9)
        self.assertLessEqual(following.xy[0], 5)

    def test_investigators_stack_along_the_wall_then_pass(self):
        simulation = PlaySimulation(DOOR_SCENE, stack_drill=True)
        front, following = simulation.guards
        door = simulation.world.geometry.objects["shared_door"]
        while simulation.time < 1 and not simulation.ai.door_traffic.stacks:
            simulation.step()

        stack = simulation.ai.door_traffic.stacks["shared_door"]
        first, second = stack.slots[front.id], stack.slots[following.id]
        self.assertLess(first[0], door.rect.x)
        self.assertAlmostEqual(first[0], second[0])
        self.assertLess(first[1], door.rect.y)
        self.assertGreater(second[1], door.rect.bottom)
        while simulation.time < 1.5 and stack.ready_at is None:
            simulation.step()

        self.assertIsNotNone(stack.ready_at)
        self.assertTrue(door.active)
        self.assertLess(math.dist(front.xy, first), 0.06)
        self.assertLess(math.dist(following.xy, second), 0.06)
        center_y = door.rect.y + door.rect.h / 2
        for actor in simulation.guards:
            look_x, look_y = simulation.ai.brains[actor.id].look_point
            self.assertGreater(look_x, door.rect.right)
            self.assertLess((look_y - center_y) * (actor.xy[1] - center_y), 0)
            expected = math.atan2(look_y - actor.xy[1], look_x - actor.xy[0])
            self.assertLessEqual(abs(math.remainder(actor.facing - expected, math.tau)), math.radians(5))
        self.assertGreater(front.facing, 0)
        self.assertLess(following.facing, 0)

        while simulation.time < 3 and not all(actor.xy[0] > 8 for actor in simulation.guards):
            simulation.step()

        self.assertTrue(all(actor.xy[0] > 8 for actor in simulation.guards))
        self.assertFalse(simulation.ai.door_traffic.stacks)
        self.assertFalse(door.active)
        self.assertEqual(simulation.player.hp, 100)

    def test_new_gunshot_releases_stack(self):
        simulation = PlaySimulation(DOOR_SCENE, stack_drill=True)
        while simulation.time < 1 and not simulation.ai.door_traffic.stacks:
            simulation.step()
        self.assertIn("shared_door", simulation.ai.door_traffic.stacks)

        source = Actor("new_sound", Position(3, 6), team="player")
        simulation.events.emit("noise", simulation.time, source,
                               radius=30, strength=0.8, reason="gunshot")
        simulation.step()

        self.assertFalse(simulation.ai.door_traffic.stacks)
        self.assertTrue(simulation.world.geometry.objects["shared_door"].active)
        for actor in simulation.guards:
            brain = simulation.ai.brains[actor.id]
            self.assertIsNone(brain.door_stack)
            self.assertEqual(brain.priority_cue.point, (3, 6))

    def test_gunshot_replaces_a_patrol_route_while_waiting_at_a_door(self):
        simulation = PlaySimulation(DOOR_SCENE)
        simulation.player.hp = 0
        waiter = None
        while waiter is None and simulation.time < 2:
            simulation.step()
            waiter = next((actor for actor in simulation.guards if simulation.ai.brains[actor.id].door_wait), None)
        self.assertIsNotNone(waiter)
        brain = simulation.ai.brains[waiter.id]
        self.assertEqual(waiter.state, "patrol")
        old_goal = brain.goal

        simulation.player.position = Position(4, 4)
        simulation.events.emit("noise", simulation.time, simulation.player,
                               radius=30, strength=0.8, reason="gunshot")
        simulation.step()

        self.assertEqual(brain.priority_cue.kind, "sound")
        self.assertNotEqual(brain.goal, old_goal)
        self.assertFalse(brain.door_wait)
        self.assertEqual(waiter.state, "search")

    def test_peek_keeps_its_direction_and_can_fire_after_reacquiring_sight(self):
        geometry = SpatialIndex()
        geometry.add(Collider("wall", Rect(4, 2.5, 0.2, 1)))
        world = SimpleNamespace(geometry=geometry, revision=0, door_on_ai_path=lambda actor, point: None,
                                navigation=Navigation({}, [], geometry))
        guard = Actor("guard", Position(2, 3), facing=math.pi / 2, state="retreat")
        guard.loadout["ar_a"] = WeaponState.ready(load_weapons()["ar_a"])
        guard.active_weapon = "ar_a"
        player = Actor("player", Position(6, 3), team="player")
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        brain.combat_alert_until = 60
        brain.memory.last_seen, brain.memory.seen_at = player.xy, 0.1
        brain.peek_home = guard.xy
        brain.action, brain.action_goal, brain.action_until = "peek", (2, 4.8), 1.01
        brain.fire_at = 1.2

        initial = ai.update([guard], player, 0.35)[guard.id]
        self.assertEqual(initial.direction, (0, 0))
        self.assertLess(guard.facing, math.pi / 2)
        for tick in range(22, 55):
            move = ai.update([guard], player, tick / 60)[guard.id]
        self.assertEqual(brain.action, "peek")
        self.assertGreater(move.direction[1], 0)
        self.assertEqual(guard.facing, 0)

        guard.position = Position(2, 4.8)
        brain.sense_at = 0
        ai.update([guard], player, 1.02)
        self.assertTrue(brain.visible)
        self.assertEqual(brain.action, "peek")
        self.assertTrue(ai.update([guard], player, 1.2)[guard.id].fire)

        combat = Combat(EventBus())
        combat.prepare([guard, player], 1 / 60)
        self.assertTrue(combat.clear_fire_line(guard, player, world))
        self.assertTrue(combat.fire(guard, world, 1.2))
