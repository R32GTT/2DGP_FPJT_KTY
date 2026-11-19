import subprocess
import unittest

from prototype.launcher import ROOT, build_command, discover_targets


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.targets = discover_targets()
        self.values = {"seed": "42", "region": "소형", "importance": "일반", "mode": "기본", "frames": "2",
                       "headless": True, "zoom": "장면 기본값", "paused": "장면 기본값",
                       "debug": "장면 기본값", "map_open": False}

    def test_discovered_door_drill_runs_through_the_real_entry(self):
        target = self.targets["ai_door_queue"]
        self.values["mode"] = next(reversed(target["modes"]))
        result = subprocess.run(build_command(target, self.values), cwd=ROOT, capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("목업 코어 실행", result.stdout)

    def test_invalid_options_are_rejected_before_starting(self):
        self.values["headless"] = False
        for key, value in (("seed", "invalid"), ("frames", "-1"), ("zoom", "nan"), ("zoom", "4.1")):
            with self.subTest(option=key, value=value), self.assertRaises(ValueError):
                build_command(self.targets["generated"], {**self.values, key: value})

    def test_automatic_test_selection_runs_existing_clock_tests(self):
        result = subprocess.run(build_command(self.targets["test/test_campaign_clock"], {}), cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stderr)
