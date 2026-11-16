import unittest

from campaign.models import Position
from prototype.actors import Actor
from prototype.door_layout import DoorLayout
from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.navigation import Navigation


class DoorLayoutTests(unittest.TestCase):
    def test_horizontal_door_uses_both_flanks_when_clear(self):
        layout = DoorLayout.from_rect(Rect(5.5, 7, 1, 0.2))
        actors = [Actor("first", Position(5.6, 6.1)), Actor("second", Position(6.4, 6.1))]
        navigation = Navigation({}, [], SpatialIndex())

        first, second = layout.waiting_slots(-1, actors, actors, navigation).values()

        self.assertLess(first[0], 5.5)
        self.assertGreater(second[0], 6.5)
        self.assertAlmostEqual(first[1], second[1])
        self.assertLess(first[1], 7)

    def test_vertical_door_uses_the_clear_flank_when_the_other_is_blocked(self):
        layout = DoorLayout.from_rect(Rect(7, 5.5, 0.2, 1))
        actors = [Actor("first", Position(6.16, 5.72)), Actor("second", Position(5.78, 6.33))]
        geometry = SpatialIndex()
        geometry.add(Collider("cabinet", Rect(5.6, 3.5, 1.38, 1.58)))
        navigation = Navigation({}, [], geometry)

        first, second = layout.waiting_slots(-1, actors, actors, navigation).values()

        self.assertLess(first[0], 7)
        self.assertAlmostEqual(first[0], second[0])
        self.assertGreater(first[1], 6.5)
        self.assertGreater(second[1] - first[1], actors[0].radius + actors[1].radius)
