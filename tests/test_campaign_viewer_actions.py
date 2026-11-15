from dataclasses import asdict
import os
from pathlib import Path
import tempfile
import unittest

from campaign.logistics import plan_transport
from campaign.resources import assert_invariants
from campaign.scenario import load_scenario
from campaign.simulation import Simulation
from campaign.viewer_actions import allowed_commands, build_action


class ViewerActionTests(unittest.TestCase):
    def test_road_and_warehouse_controls_use_actual_actions_and_accounting(self):
        state = load_scenario()
        simulation = Simulation(state)
        action = build_action(state, ("road", "a_b"), "toggle_road")
        simulation.apply_action(action)
        self.assertTrue(state.roads["a_b"].blocked)
        before = state.facilities["depot_a"].inventory["supplies"]
        simulation.apply_action(build_action(state, ("facility", "depot_a"), "capture", amount=10))
        self.assertEqual(state.facilities["depot_a"].inventory["supplies"], before - 10)
        self.assertEqual(state.player_stock["supplies"], 10)
        assert_invariants(state)

    def test_infinite_unmaterialized_reserve_is_not_capturable_in_viewer(self):
        state = load_scenario(support="infinite")
        operation = plan_transport(state, state.source.id, "depot_a", {"supplies": 100})
        selection = ("operation", operation.id)
        self.assertNotIn("capture", allowed_commands(state, selection))
        before = asdict(state)
        with self.assertRaises(ValueError):
            build_action(state, selection, "capture")
        self.assertEqual(asdict(state), before)

    def test_convoy_vehicle_loss_and_return_commands_track_same_ids(self):
        state = load_scenario()
        state.started = True
        operation = plan_transport(state, "depot_a", "hq_b", {"supplies": 50})
        simulation = Simulation(state)
        simulation.advance_to(100)
        selection = ("operation", operation.id)
        action = build_action(state, selection, "destroy_vehicle")
        self.assertEqual(action["vehicle_id"], operation.vehicle_ids[0])
        simulation.apply_action(action)
        self.assertEqual(operation.phase, "stranded")
        self.assertNotIn("destroy_vehicle", allowed_commands(state, selection))
        self.assertNotIn("return_operation", allowed_commands(state, selection))
        assert_invariants(state)

    def test_invalid_quantity_does_not_change_selected_inventory(self):
        state = load_scenario()
        before = asdict(state)
        for amount in (-1, 0, 100000):
            with self.assertRaises(ValueError):
                build_action(state, ("facility", "hq_a"), "capture", amount=amount)
        self.assertEqual(asdict(state), before)


@unittest.skipUnless(os.environ.get("CAMPAIGN_GUI_TEST") == "1", "GUI 검증은 CAMPAIGN_GUI_TEST=1로 실행")
class ViewerActionGuiTests(unittest.TestCase):
    def setUp(self):
        from campaign.visualizer import Visualizer
        simulation = Simulation(load_scenario())
        simulation.advance_to(100)
        self.app = Visualizer(simulation, paused=True, autostart=False)
        self.addCleanup(self.app.close)
        self.app.update()

    def test_selection_buttons_change_core_and_visible_details(self):
        app = self.app
        app.select(("road", "a_b"))
        self.assertFalse(app.action_buttons["toggle_road"].instate(["disabled"]))
        app.action_buttons["toggle_road"].invoke()
        self.assertTrue(app.state.roads["a_b"].blocked)
        self.assertIn("차단", app.details.get("1.0", "end"))
        app.select(("facility", "depot_a"))
        before = app.state.facilities["depot_a"].inventory["supplies"]
        app.action_buttons["capture"].invoke()
        self.assertEqual(app.state.player_stock["supplies"], 10)
        self.assertEqual(app.state.facilities["depot_a"].inventory["supplies"], before - 10)
        app.action_buttons["damage_facility"].invoke()
        self.assertEqual(app.state.facilities["depot_a"].damage, 25)
        self.assertIn("25%", app.details.get("1.0", "end"))
        assert_invariants(app.state)

    def test_load_rebinds_map_and_playback_without_advancing_saved_world(self):
        app = self.app
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            path = Path(directory) / "viewer.json"
            app.select(("road", "a_b"))
            app.intervene("toggle_road")
            app.save_to(path)
            saved = asdict(app.state)
            app.playback.request_skip(100)
            while app.playback.skip_target is not None:
                app.playback.update(0)
            app.load_from(path)
            self.assertEqual(asdict(app.state), saved)
            self.assertIs(app.map.state, app.state)
            self.assertTrue(app.playback.paused)
            self.assertIsNone(app.selection)
            self.assertTrue(app.state.roads["a_b"].blocked)
            app.step()
            app.playback.update(0)
            self.assertEqual(app.state.world_time, saved["world_time"] + 1)

    def test_failed_load_keeps_current_world_and_map_binding(self):
        app = self.app
        before = asdict(app.state)
        playback = app.playback
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            with self.assertRaises(ValueError):
                app.load_from(Path(directory) / "missing.json")
        self.assertIs(app.playback, playback)
        self.assertIs(app.map.state, app.state)
        self.assertEqual(asdict(app.state), before)
