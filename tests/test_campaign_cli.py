from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campaign.cli import interactive, main, run_schedule, summary
from campaign.persistence import load_campaign, save_campaign
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class ConsoleTests(unittest.TestCase):
    def test_batch_cli_runs_full_day(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["--hours", "24", "--support", "finite", "--seed", "42"])
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["world_time"], 86400)
        self.assertGreater(report["delivered"]["supplies"], 0)

    def test_load_without_duration_does_not_start_or_advance_world(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            path = Path(directory) / "campaign.json"
            state = load_scenario()
            save_campaign(state, path)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--load", str(path), "--save", str(path)]), 0)
            self.assertEqual(asdict(load_campaign(path)), asdict(state))

    def test_invalid_script_times_are_rejected_before_world_changes(self):
        simulation = Simulation(load_scenario())
        before = asdict(simulation.state)
        with self.assertRaises(ValueError):
            run_schedule(simulation, 100, [{"time": 101, "action": {"type": "block_road"}}])
        self.assertEqual(asdict(simulation.state), before)

    def test_scripted_actions_and_console_use_real_campaign_rules(self):
        simulation = Simulation(load_scenario())
        run_schedule(simulation, 100, [
            {"time": 50, "action": {"type": "capture", "owner_id": "hq_a", "amount": {"supplies": 5}}},
            {"time": 100, "action": {"type": "block_road", "road_id": "a_b", "blocked": True}},
        ])
        self.assertEqual(simulation.state.player_stock["supplies"], 5)
        commands = ["action {\"type\":\"block_road\",\"road_id\":\"a_b\",\"blocked\":false}",
                    "advance 10", "status", "operations", "assets", "quit"]
        with patch("builtins.input", side_effect=commands), redirect_stdout(io.StringIO()):
            interactive(simulation)
        self.assertEqual(summary(simulation.state)["world_time"], 110)
        self.assertFalse(simulation.state.roads["a_b"].blocked)

    def test_missing_save_returns_error_without_replacement_campaign(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            output = io.StringIO()
            with redirect_stderr(io.StringIO()), redirect_stdout(output):
                result = main(["--load", str(Path(directory) / "missing.json")])
            self.assertEqual(result, 2)
            self.assertEqual(output.getvalue(), "")

    def test_action_file_save_and_resume_match_direct_simulation(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            actions_path = Path(directory) / "actions.json"
            save_path = Path(directory) / "save.json"
            actions = [{"time": 100, "action": {"type": "block_road", "road_id": "a_b", "blocked": True}}]
            actions_path.write_text(json.dumps(actions), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--seconds", "100", "--actions", str(actions_path),
                                       "--save", str(save_path)]), 0)
                self.assertEqual(main(["--load", str(save_path), "--seconds", "100",
                                       "--save", str(save_path)]), 0)
            reference = Simulation(load_scenario())
            run_schedule(reference, 100, actions)
            reference.advance_to(200)
            self.assertEqual(asdict(load_campaign(save_path)), asdict(reference.state))
