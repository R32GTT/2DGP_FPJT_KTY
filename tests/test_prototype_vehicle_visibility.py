"""차량이 화면 안에 있더라도 시야와 밝기 없이는 표시되지 않는다."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prototype.rendering import Renderer


class Sight:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def sight_flags(self, origin, targets, *, floor):
        self.calls.append((origin, tuple(targets), floor))
        return self.answers.pop(0)


class VehicleVisibilityTests(unittest.TestCase):
    @staticmethod
    def _inputs(sight, *, floor=0, sky=None):
        world = SimpleNamespace(geometry=sight)
        player = SimpleNamespace(xy=(0.0, 0.0), hp=100,
                                 position=SimpleNamespace(floor=floor))
        sky = sky or SimpleNamespace(ambient=(255, 255, 255), daylight=1.0)
        return world, player, [(10.0, 0.0, 0.0)], sky, (), False

    def test_wall_hides_vehicle_after_batched_center_and_end_checks(self):
        sight = Sight([False], [False, False])
        self.assertEqual(Renderer._vehicle_visibility(*self._inputs(sight)), (0.0,))
        self.assertEqual(len(sight.calls), 2)
        self.assertEqual(len(sight.calls[1][1]), 2)

    def test_visible_center_needs_no_extra_rays(self):
        sight = Sight([True])
        self.assertEqual(Renderer._vehicle_visibility(*self._inputs(sight)), (1.0,))
        self.assertEqual(len(sight.calls), 1)

    def test_visible_vehicle_end_is_not_hidden(self):
        sight = Sight([False], [True, False])
        self.assertEqual(Renderer._vehicle_visibility(*self._inputs(sight)), (1.0,))

    def test_upper_floor_hides_ground_vehicle_without_rays(self):
        sight = Sight()
        self.assertEqual(Renderer._vehicle_visibility(*self._inputs(sight, floor=1)), (0.0,))
        self.assertEqual(sight.calls, [])

    def test_dark_vehicle_fades_out_without_a_light_source(self):
        sight = Sight([True])
        sky = SimpleNamespace(ambient=(0, 0, 0), daylight=0.0)
        world, player, _, sky, lights, night_vision = self._inputs(sight, sky=sky)
        with patch("prototype.rendering.incident_light", return_value=(0.0, 0.0)):
            result = Renderer._vehicle_visibility(world, player, [(20.0, 0.0, 0.0)],
                                                  sky, lights, night_vision)
        self.assertEqual(result, (0.0,))


if __name__ == "__main__":
    unittest.main()
