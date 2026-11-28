"""지도에 드러나는 차량 정보는 확보한 관측 기록만 사용한다."""

from types import SimpleNamespace
import unittest

from prototype.intelligence import OperationNote, observed_vehicles, refresh_intelligence_view, sorted_notes


class ObservedVehicleTests(unittest.TestCase):
    def test_unobserved_operation_has_no_map_position(self):
        play = self._play({"trip": OperationNote("trip", source="facility", recorded_at=90)})
        self.assertEqual(list(observed_vehicles(play)), [])

    def test_last_observation_is_used_without_live_tracking(self):
        note = OperationNote("trip", source="facility", recorded_at=90,
                             observed_at=95, observed_position=(12.0, 8.0))
        play = self._play({"trip": note})
        self.assertEqual(list(observed_vehicles(play)), [((12.0, 8.0), "supply")])
        self.assertEqual(list(observed_vehicles(play, max_age=4)), [])
        self.assertEqual(list(observed_vehicles(play, max_age=5)), [((12.0, 8.0), "supply")])

    def test_visual_observation_does_not_reveal_operation_type(self):
        play = self._play({"trip": OperationNote("trip", observed_at=95,
                                                  observed_position=(12.0, 8.0))})
        self.assertEqual(list(observed_vehicles(play)), [((12.0, 8.0), None)])

    def test_markers_and_note_order_rebuild_only_after_record_change(self):
        first = OperationNote("trip", observed_at=95, observed_position=(12.0, 8.0))
        play = self._play({"trip": first})
        positions = observed_vehicles(play)
        recent = observed_vehicles(play, max_age=45)
        self.assertIs(observed_vehicles(play), positions)
        self.assertIs(observed_vehicles(play, max_age=45), recent)
        self.assertIs(sorted_notes(play), sorted_notes(play))
        play.campaign.simulation.state.world_time = 141
        self.assertEqual(observed_vehicles(play, max_age=45), ())
        play.operation_notes["trip"] = OperationNote("trip", observed_at=141,
                                                      observed_position=(13.0, 9.0))
        refresh_intelligence_view(play)
        self.assertEqual(observed_vehicles(play), (((13.0, 9.0), None),))

    @staticmethod
    def _play(notes):
        state = SimpleNamespace(world_time=100, operations={"trip": SimpleNamespace(kind="supply")})
        return SimpleNamespace(operation_notes=notes,
                               campaign=SimpleNamespace(simulation=SimpleNamespace(state=state)))


if __name__ == "__main__":
    unittest.main()
