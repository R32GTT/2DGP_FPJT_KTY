"""설정 파일과 조작 충돌의 회귀 검증."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import sdl2

from prototype.settings import (AppSettings, Binding, MOD_SHIFT, binding_name, load_settings)


class SettingsTests(unittest.TestCase):
    def test_rebinding_survives_restart_and_swaps_mouse_actions(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = AppSettings(path=path)
            self.assertEqual(settings.rebind("fire", Binding("mouse", sdl2.SDL_BUTTON_RIGHT)), "aim")
            self.assertEqual(settings.rebind("move_up", Binding("key", sdl2.SDLK_k)), "ir_laser")
            settings.window_mode = "borderless"
            settings.save()

            restored = load_settings(path)
            self.assertEqual(restored.bindings, settings.bindings)
            self.assertEqual(restored.window_mode, "borderless")
            self.assertEqual(restored.action_for_mouse(sdl2.SDL_BUTTON_RIGHT), "fire")
            self.assertTrue(restored.held("move_up", {sdl2.SDLK_k}, set()))
            self.assertIn("마우스", binding_name(restored.bindings["fire"]))

    def test_modifier_action_precedes_plain_key_and_escape_is_reserved(self):
        settings = AppSettings()
        self.assertEqual(settings.action_for_key(sdl2.SDLK_i), "intel")
        self.assertEqual(settings.action_for_key(sdl2.SDLK_i, MOD_SHIFT), "collect_intel")
        with self.assertRaises(ValueError):
            settings.rebind("reload", Binding("key", sdl2.SDLK_ESCAPE))

    def test_later_setting_sections_survive_current_save(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"version": 1, "audio": {"master": 0.4}}), encoding="utf-8")
            settings = load_settings(path)
            settings.save()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["audio"], {"master": 0.4})


if __name__ == "__main__":
    unittest.main()
