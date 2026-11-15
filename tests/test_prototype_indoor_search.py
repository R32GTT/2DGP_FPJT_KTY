from pathlib import Path
from types import SimpleNamespace
import unittest

from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.indoor_search import IndoorSweep, build_search_areas
from prototype.navigation import Navigation
from prototype.simulation import PlaySimulation


FACILITY_SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_facility.json"


class IndoorSearchTests(unittest.TestCase):
    def test_walk_scan_respects_occlusion_and_near_only_samples(self):
        geometry = SpatialIndex()
        geometry.add(Collider("west", Rect(-0.2, 0, 0.2, 1.6)))
        geometry.add(Collider("north", Rect(0, -0.2, 1.6, 0.2)))
        geometry.add(Collider("pillar", Rect(3.8, 0.25, 1.2, 1.3)))
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 9, 3.6], [0, 3.6, 3.6, 3.6]]}},
                                facilities=[], geometry=geometry,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        origin, open_point = (0.9, 0.9), (2.7, 0.9)
        hidden = (6.3, 0.9)
        room = area.rooms[origin]
        sweep = IndoorSweep(area, origin, 0)

        self.assertEqual(area.rooms[hidden], room)
        self.assertIn(origin, area.details)
        sweep.scan_forward(origin, 0, room, geometry)
        self.assertIn(open_point, sweep.checked)
        self.assertNotIn(hidden, sweep.checked)
        self.assertNotIn(origin, sweep.checked)

        sweep.scan_forward(origin, 0, room, geometry, stationary=True)
        self.assertIn(origin, sweep.checked)
        sweep.scan_forward((5.5, 0.9), 0, room, geometry)
        self.assertIn(hidden, sweep.checked)

    def test_two_doorways_preserve_room_boundaries_when_a_door_opens(self):
        geometry = SpatialIndex()
        panels = ((0, 0.7, "wall"), (0.7, 1.8, "door"), (2.5, 4, "wall"),
                  (6.5, 2, "door"), (8.5, 0.5, "wall"))
        for y, height, kind in panels:
            geometry.add(Collider(f"panel-{y}", Rect(5.3, y, 0.2, height), kind))
        world = SimpleNamespace(data={"search_areas": {"suite": [[0, 0, 9, 9]]}},
                                facilities=[], geometry=geometry,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        left, right = (4.5, 0.9), (6.3, 0.9)
        left_room, right_room = area.rooms[left], area.rooms[right]
        sweep = IndoorSweep(area, left, 0)

        self.assertNotEqual(left_room, right_room)
        sweep.scan_forward(left, 0, right_room, geometry)
        self.assertNotIn(right, sweep.checked)

        geometry.objects["panel-0.7"].active = False
        sweep.scan_forward(left, 0, left_room, geometry)
        self.assertNotIn(right, sweep.checked)
        sweep.scan_forward(left, 0, right_room, geometry)
        self.assertIn(right, sweep.checked)

    def test_squad_finishes_search_in_facility(self):
        simulation = PlaySimulation(scene=FACILITY_SCENE)
        simulation.player.hp = 0
        area, = simulation.world.search_areas
        simulation.ai.start_search_drill(simulation.guards, area.points[len(area.points) // 2])

        while simulation.time < 90:
            simulation.step()
            sweep = simulation.ai.incidents["indoor_drill"].indoor
            if len(sweep.checked) == len(area.points):
                break

        self.assertEqual(sweep.checked, set(area.points))
