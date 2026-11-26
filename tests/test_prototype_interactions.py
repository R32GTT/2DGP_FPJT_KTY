from pathlib import Path
from types import SimpleNamespace
import unittest

from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.interactions import nearby_facility, nearby_road_control
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState


SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_facility.json"


class InteractionTests(unittest.TestCase):
    def test_terminal_needs_clear_access_from_any_direction(self):
        geometry = SpatialIndex()
        facility = {"terminal": (0, 0)}
        control = {"position": (0, 0)}
        world = SimpleNamespace(geometry=geometry, facilities=[facility], road_controls=[control])
        geometry.add(Collider("wall", Rect(-0.6, -0.3, 0.2, 0.6)))
        door = Collider("door", Rect(0.4, -0.3, 0.2, 0.6), kind="door")
        geometry.add(door)

        self.assertIsNone(nearby_facility(world, (-1, 0)))
        self.assertIsNone(nearby_facility(world, (1, 0)))
        self.assertIsNone(nearby_road_control(world, (1, 0)))
        door.active = False
        for point in ((1, 0), (0, -1), (0, 1)):
            self.assertIs(nearby_facility(world, point), facility)
            self.assertIs(nearby_road_control(world, point), control)
        self.assertIsNone(nearby_facility(world, (0, 2)))

    def test_inventory_lists_and_recovers_weapon_from_second_pile(self):
        play = PlaySimulation(SCENE, equipment_range=True)
        play.player.loadout.clear()
        play.player.active_weapon = ""
        point = play.player.xy
        first, second = (WeaponState.ready(play.specs[key]) for key in ("ar_a", "ar_b"))
        play.dropped = [(point, {"ar_a": first}), (point, {"ar_b": second})]
        guns = [(index, action) for index, (_, action) in enumerate(play.inventory.rows())
                if action and action[0] == "gun"]
        self.assertEqual([action[2] for _, action in guns], ["ar_a", "ar_b"])
        play.inventory.activate(guns[1][0])
        self.assertIs(play.player.weapon, second)
        self.assertEqual(play.dropped, [(point, {"ar_a": first})])
