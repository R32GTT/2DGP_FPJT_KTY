"""시설 상호작용 시간, 파괴 도구, 원격 기폭을 검증한다."""

from pathlib import Path
import json
import tempfile
import unittest

import sdl2

from campaign.models import Position
from prototype.generated_world import generate_world
from prototype.inventory import C4, MULTITOOL
from prototype.savegame import load_game, save_game
from prototype.settings import AppSettings, key, load_settings
from prototype.simulation import FIXED_DT, PlaySimulation


class FacilitySabotageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def setup_comms(self):
        play = PlaySimulation(self.scene)
        site = next(site for site in play.world.facilities if site["kind"] == "comms")
        play.player.position = Position(*site["terminal"])
        return play, site

    @staticmethod
    def finish_hold(play):
        duration = play.facility_interaction["duration"]
        for _ in range(round(duration / FIXED_DT)):
            play.update_facility_interaction(True)

    def test_hand_requires_continuous_hold_and_always_alarms(self):
        play, site = self.setup_comms()
        facility = play.campaign.simulation.state.facilities[site["campaign_id"]]
        play.update_facility_interaction(True)
        self.assertEqual(play.facility_interaction["duration"], 8.0)
        self.assertEqual(facility.damage, 0)
        play.update_facility_interaction(False)
        self.assertIsNone(play.facility_interaction)
        play.update_facility_interaction(True)
        self.assertEqual(play.facility_interaction["elapsed"], FIXED_DT)
        self.finish_hold(play)
        self.assertEqual(facility.damage, 100)
        self.assertTrue(play.alarms)
        count = play.events.counts["facility_damaged"]
        play.update_facility_interaction(True)
        self.assertEqual(play.events.counts["facility_damaged"], count)

    def test_game_step_clears_progress_when_key_is_released(self):
        play, _ = self.setup_comms()
        play.step(interact_held=True)
        self.assertIsNotNone(play.facility_interaction)
        play.step(interact_held=False)
        self.assertIsNone(play.facility_interaction)

    def test_multitool_is_faster_and_silent(self):
        play, site = self.setup_comms()
        play.inventory.carried[MULTITOOL] = 1
        play.inventory.activate_action(("select_sabotage", "multitool"))
        play.update_facility_interaction(True)
        self.assertEqual(play.facility_interaction["duration"], 4.0)
        self.finish_hold(play)
        self.assertEqual(play.campaign.simulation.state.facilities[site["campaign_id"]].damage, 100)
        self.assertFalse(play.alarms)
        self.assertEqual(play.events.counts["noise"], 0)
        self.assertEqual(play.inventory.carried[MULTITOOL], 1)

    def test_c4_is_placed_then_detonated_after_loading(self):
        play, site = self.setup_comms()
        play.inventory.carried[C4] = 2
        play.inventory.activate_action(("select_sabotage", "c4"))
        play.update_facility_interaction(True)
        self.assertEqual(play.facility_interaction["duration"], 2.0)
        self.finish_hold(play)
        self.assertEqual(play.campaign.simulation.state.facilities[site["campaign_id"]].damage, 0)
        self.assertEqual(play.inventory.carried[C4], 1)
        self.assertIn(site["id"], play.planted_charges)
        self.assertFalse(play.alarms)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "c4.json.gz"
            save_game(play, path)
            restored = load_game(path)
        self.assertIn(site["id"], restored.planted_charges)
        restored.detonate_charges()
        self.assertEqual(restored.campaign.simulation.state.facilities[site["campaign_id"]].damage, 100)
        self.assertFalse(restored.planted_charges)
        self.assertTrue(restored.alarms)
        self.assertEqual(len(restored.throwable_bursts), 1)

    def test_watch_post_requires_hold_and_hand_raises_alarm(self):
        play = PlaySimulation(self.scene)
        site = next(site for site in play.world.facilities if site.get("watch_post"))
        play.player.position = Position(*site["watch_post"][0])
        play.update_facility_interaction(True)
        self.assertEqual(play.facility_interaction["identity"][1], "watch")
        self.finish_hold(play)
        self.assertTrue(play.campaign.simulation.state.facilities[site["campaign_id"]].watch_disabled)
        self.assertTrue(play.alarms)

    def test_detonation_binding_can_be_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = AppSettings(path=path)
            self.assertEqual(settings.action_for_key(sdl2.SDLK_b), "detonate")
            settings.rebind("detonate", key(sdl2.SDLK_j))
            settings.save()
            self.assertEqual(load_settings(path).action_for_key(sdl2.SDLK_j), "detonate")

    def test_existing_binding_to_b_survives_new_detonation_action(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"version": 1, "bindings": {
                "move_up": key(sdl2.SDLK_b).as_dict()}}), encoding="utf-8")
            restored = load_settings(path)
            self.assertEqual(restored.action_for_key(sdl2.SDLK_b), "move_up")
            self.assertNotEqual(restored.bindings["detonate"], restored.bindings["move_up"])


if __name__ == "__main__":
    unittest.main()
