from dataclasses import asdict
import os
import unittest

from campaign.playback import Playback
from campaign.scenario import load_scenario
from campaign.simulation import Simulation
from campaign.viewmodel import Camera, convoy_markers, pick, world_clock


class ViewerModelTests(unittest.TestCase):
    def test_camera_fit_inverse_and_cursor_anchored_zoom(self):
        state = load_scenario()
        camera = Camera()
        camera.fit(state, 1000, 700)
        for node in state.nodes.values():
            sx, sy = camera.screen(node.position.x, node.position.y)
            self.assertTrue(0 <= sx <= 1000 and 0 <= sy <= 700)
            x, y = camera.world(sx, sy)
            self.assertAlmostEqual(x, node.position.x)
            self.assertAlmostEqual(y, node.position.y)
        before = camera.world(222, 333)
        camera.zoom(1.2, 222, 333)
        self.assertAlmostEqual(camera.world(222, 333)[0], before[0])
        self.assertAlmostEqual(camera.world(222, 333)[1], before[1])

    def test_markers_and_picking_use_real_positions_without_mutating_state(self):
        simulation = Simulation(load_scenario())
        simulation.advance_to(100)
        state = simulation.state
        before = asdict(state)
        camera = Camera()
        camera.fit(state, 1000, 700)
        markers = convoy_markers(state)
        marker = next(iter(markers.values()))
        self.assertEqual(pick(state, camera, markers, *camera.screen(marker.x, marker.y))[0], "operation")
        self.assertEqual(pick(state, camera, {}, *camera.screen(1000, 0)), ("road", "a_b"))
        self.assertEqual(pick(state, camera, {}, *camera.screen(0, 500)), ("facility", "hq_a"))
        self.assertEqual(asdict(state), before)

    def test_world_clock_keeps_days(self):
        self.assertEqual(world_clock(86400 + 3661), "D02  01:01:01")


class PlaybackTests(unittest.TestCase):
    def test_frame_partitioning_matches_direct_simulation(self):
        playback = Playback(Simulation(load_scenario()), speed=30, timer=lambda: 0)
        for _ in range(100):
            playback.update(0.05)
        direct = Simulation(load_scenario())
        direct.advance_to(150)
        self.assertEqual(asdict(playback.simulation.state), asdict(direct.state))

    def test_large_skip_is_chunked_and_matches_same_core_rules(self):
        playback = Playback(Simulation(load_scenario()), timer=lambda: 0)
        playback.set_paused(True)
        playback.request_skip(21600)
        playback.update(1, max_chunks=1)
        self.assertEqual(playback.simulation.state.world_time, 30)
        self.assertEqual(playback.skip_target, 21600)
        while playback.skip_target is not None:
            playback.update(0, max_chunks=4)
        direct = Simulation(load_scenario())
        direct.advance_to(21600)
        self.assertEqual(asdict(playback.simulation.state), asdict(direct.state))
        self.assertTrue(playback.paused)

    def test_pause_cancels_backlog_and_rendering_does_not_advance_time(self):
        playback = Playback(Simulation(load_scenario()), timer=lambda: 0)
        playback.update(100, max_chunks=1)
        playback.set_paused(True)
        before = asdict(playback.simulation.state)
        playback.update(100)
        playback.markers()
        self.assertEqual(asdict(playback.simulation.state), before)
        self.assertEqual(playback.desired_time, playback.simulation.state.world_time)

    def test_frame_budget_preserves_pending_work(self):
        times = iter([0.0, 0.010])
        playback = Playback(Simulation(load_scenario()), speed=1, timer=lambda: next(times))
        playback.update(100)
        self.assertEqual(playback.simulation.state.world_time, 0)
        self.assertEqual(playback.desired_time, 100)
        playback.timer = lambda: 0
        playback.update(0)
        self.assertEqual(playback.simulation.state.world_time, 100)


@unittest.skipUnless(os.environ.get("CAMPAIGN_GUI_TEST") == "1", "GUI 검증은 CAMPAIGN_GUI_TEST=1로 실행")
class ViewerGuiTests(unittest.TestCase):
    def setUp(self):
        from campaign.visualizer import Visualizer
        simulation = Simulation(load_scenario())
        simulation.advance_to(100)
        self.app = Visualizer(simulation, paused=True, autostart=False)
        self.addCleanup(self.app.close)
        self.app.update()
        self.app._initial_render()

    def test_map_selection_and_rendering_share_real_campaign_state(self):
        app = self.app
        before = asdict(app.state)
        marker = next(iter(app.playback.current.values()))
        x, y = app.map.camera.screen(marker.x, marker.y)
        app.map.event_generate("<Button-1>", x=int(x), y=int(y))
        app.update()
        self.assertEqual(app.selection[0], "operation")
        self.assertIn(app.selection[1], app.details.get("1.0", "end"))
        self.assertGreater(len(app.map.find_withtag("terrain")), len(app.state.roads))
        self.assertGreater(len(app.map.find_withtag("dynamic")), len(app.playback.current))
        self.assertEqual(asdict(app.state), before)

    def test_pause_and_single_step_use_the_core_and_refresh_visible_clock(self):
        app = self.app
        app.playback.update(10)
        self.assertEqual(app.state.world_time, 100)
        app.step()
        app.playback.update(0)
        app.refresh_panel(force=True)
        self.assertEqual(app.state.world_time, 101)
        self.assertIn("00:01:41", app.clock_text.get())
        app.facilities.selection_set("depot_a")
        app.update()
        self.assertEqual(app.selection, ("facility", "depot_a"))
        self.assertIn(str(app.state.facilities["depot_a"].inventory["supplies"]), app.details.get("1.0", "end"))
