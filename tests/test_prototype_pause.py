"""일시정지 슬롯 선택과 기존 저장 보호를 검증한다."""

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

import sdl2

from prototype.pause_screen import PauseScreen
from prototype.savegame import AUTO_SAVE, MANUAL_SAVES, latest_save, save_slots
from prototype.settings import AppSettings


class PauseSlotTests(unittest.TestCase):
    def test_slots_are_distinct_and_old_saves_stay_visible(self):
        slots = save_slots()
        self.assertEqual(slots[0], ("자동 저장", AUTO_SAVE))
        self.assertEqual([path for _, path in slots[1:7]], list(MANUAL_SAVES))
        self.assertEqual(len({path for _, path in slots}), len(slots))

    def test_continue_chooses_most_recent_occupied_slot(self):
        with TemporaryDirectory() as directory:
            older = Path(directory) / "older.json.gz"
            newer = Path(directory) / "newer.json.gz"
            older.write_bytes(b"old")
            newer.write_bytes(b"new")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            with patch("prototype.savegame.save_slots", return_value=(("예전", older), ("최근", newer))):
                self.assertEqual(latest_save(), newer)

    def test_save_exit_reuses_slot_and_warns_before_overwrite(self):
        with TemporaryDirectory() as directory:
            occupied = Path(directory) / "slot_1.json.gz"
            occupied.write_bytes(b"existing")
            screen = object.__new__(PauseScreen)
            screen.current_slot = occupied
            screen.page = "menu"
            screen.pending_choice = None
            self.assertIsNone(screen._choose("save_exit"))
            self.assertEqual(screen.page, "confirm")
            self.assertEqual(screen.pending_choice, ("save_exit", occupied))

    def test_save_exit_uses_autosave_for_new_game(self):
        with TemporaryDirectory() as directory:
            autosave = Path(directory) / "autosave.json.gz"
            screen = object.__new__(PauseScreen)
            screen.current_slot = None
            screen.page = "menu"
            with patch("prototype.pause_screen.AUTO_SAVE", autosave):
                self.assertEqual(screen._choose("save_exit"), ("save_exit", autosave))

    def test_p_is_no_longer_pause_binding(self):
        settings = AppSettings()
        self.assertIsNone(settings.action_for_key(sdl2.SDLK_p))
        self.assertNotIn("pause", settings.bindings)

    def test_main_menu_load_screen_returns_selected_slot(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "slot_2.json.gz"
            path.write_bytes(b"existing")
            screen = object.__new__(PauseScreen)
            screen.page = "load"
            screen.load_only = True
            screen.selected = 0
            screen.slots = (("슬롯 2", path, "저장됨"),)
            self.assertEqual(screen._activate(), ("load", path))
            event = sdl2.SDL_Event()
            event.type = sdl2.SDL_KEYDOWN
            event.key.keysym.sym = sdl2.SDLK_ESCAPE
            self.assertEqual(screen.handle(event), ("back", None))


if __name__ == "__main__":
    unittest.main()
