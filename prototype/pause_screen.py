"""정지된 게임 장면 위의 메뉴와 저장 슬롯 선택 화면."""

import ctypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter, sleep

import pico2d as pico
import sdl2

from .camera import CANVAS_SIZE
from .hud import HUD
from .hud_theme import THEME
from .savegame import AUTO_SAVE, compatible_save, content_version, save_slots


MENU = (("resume", "이어하기"), ("save", "저장"), ("load", "로드"),
        ("settings", "설정"), ("save_exit", "저장 및 메인화면"))
ROW_TOP = 535
ROW_STEP = 44


class FrozenGame:
    def __init__(self, backend):
        width, height = ctypes.c_int(), ctypes.c_int()
        if backend.SDL_GetRendererOutputSize(backend.renderer, ctypes.byref(width), ctypes.byref(height)) != 0:
            raise RuntimeError("정지 화면 크기를 읽을 수 없습니다")
        pixels = ctypes.create_string_buffer(width.value * height.value * 4)
        if backend.SDL_RenderReadPixels(backend.renderer, None, backend.SDL_PIXELFORMAT_RGBA32,
                                        pixels, width.value * 4) != 0:
            raise RuntimeError("정지 화면을 캡처할 수 없습니다")
        surface = backend.SDL_CreateRGBSurfaceWithFormatFrom(
            pixels, width.value, height.value, 32, width.value * 4, backend.SDL_PIXELFORMAT_RGBA32)
        if not surface:
            raise RuntimeError("정지 화면 텍스처를 만들 수 없습니다")
        try:
            texture = backend.SDL_CreateTextureFromSurface(backend.renderer, surface)
        finally:
            backend.SDL_FreeSurface(surface)
        if not texture:
            raise RuntimeError("정지 화면 텍스처를 만들 수 없습니다")
        backend.SDL_SetTextureScaleMode(texture, backend.SDL_ScaleModeLinear)
        self.image = pico.Image(texture)

    def draw_scene(self, now, *, show_title=False):
        self.image.draw(CANVAS_SIZE[0] / 2, CANVAS_SIZE[1] / 2, *CANVAS_SIZE)


class PauseScreen:
    def __init__(self, backend, current_slot=None, *, background=None, load_only=False):
        self.backend = backend
        self.background = background if background is not None else FrozenGame(backend)
        self.external_background = background is not None
        self.load_only = load_only
        self.hud = HUD(backend)
        self.current_slot = current_slot
        self.page = "load" if load_only else "menu"
        self.selected = 0
        self.pending_choice = None
        self.return_page = "menu"
        self.status = ""
        self.slots = ()
        self.refresh_slots()
        self.cursor_before = backend.SDL_ShowCursor(backend.SDL_QUERY)
        backend.SDL_ShowCursor(backend.SDL_ENABLE)

    def close(self):
        self.hud.close()
        if not self.external_background:
            self.background.image = None
        self.backend.SDL_ShowCursor(self.cursor_before)

    def refresh_slots(self):
        rows = []
        self.loadable = set()
        expected = content_version()
        for name, path in save_slots(expected_content=expected):
            usable = compatible_save(path, expected_content=expected)
            if usable:
                self.loadable.add(path)
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m/%d %H:%M") if usable else "비어 있음"
            rows.append((name, path, stamp))
        self.slots = tuple(rows)

    def _label(self, key, value, x, y, size=19, color=None):
        self.hud.label(key, value, size, color or THEME.white)
        self.hud.text(key, x, y)

    def _choose(self, action, path=None):
        if action in ("save", "load") and path is None:
            self.page = action
            self.selected = 0
            self.status = ""
            self.refresh_slots()
            return None
        if action == "save_exit":
            path = self.current_slot or AUTO_SAVE
        if action in ("save", "save_exit") and path in self.loadable:
            self.pending_choice = action, path
            self.return_page = self.page
            self.page = "confirm"
            return None
        return action, path

    def _activate(self):
        if self.page == "menu":
            return self._choose(MENU[self.selected][0])
        if self.page in ("save", "load"):
            if not 0 <= self.selected < len(self.slots):
                return None
            _, path, _ = self.slots[self.selected]
            if self.page == "load" and path not in self.loadable:
                self.status = "비어 있는 슬롯입니다"
                return None
            return self._choose(self.page, path)
        return None

    def handle(self, event):
        if event.type == sdl2.SDL_QUIT:
            return "quit", None
        if event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
            key = event.key.keysym.sym
            if self.page == "confirm":
                if key in (sdl2.SDLK_RETURN, sdl2.SDLK_SPACE):
                    choice = self.pending_choice
                    self.pending_choice = None
                    self.page = self.return_page
                    return choice
                if key == sdl2.SDLK_ESCAPE:
                    self.pending_choice = None
                    self.page = self.return_page
                return None
            if key == sdl2.SDLK_ESCAPE:
                if self.load_only:
                    return "back", None
                if self.page == "menu":
                    return "resume", None
                self.page = "menu"
                self.selected = 0
                return None
            if key in (sdl2.SDLK_UP, sdl2.SDLK_DOWN):
                count = len(MENU) if self.page == "menu" else len(self.slots)
                self.selected = (self.selected + (-1 if key == sdl2.SDLK_UP else 1)) % count
            elif key == sdl2.SDLK_RETURN:
                return self._activate()
        if event.type == sdl2.SDL_MOUSEMOTION and self.page != "confirm":
            index = self._hit(event.motion.x, CANVAS_SIZE[1] - event.motion.y)
            if index is not None:
                self.selected = index
        if event.type == sdl2.SDL_MOUSEBUTTONUP and event.button.button == sdl2.SDL_BUTTON_LEFT:
            x, y = event.button.x, CANVAS_SIZE[1] - event.button.y
            if self.page == "confirm":
                if 435 <= x <= 570 and 273 <= y <= 330:
                    choice = self.pending_choice
                    self.pending_choice = None
                    self.page = self.return_page
                    return choice
                if 695 <= x <= 850 and 273 <= y <= 330:
                    self.pending_choice = None
                    self.page = self.return_page
                return None
            if self.page != "menu" and 875 <= x <= 1015 and 70 <= y <= 122:
                if self.load_only:
                    return "back", None
                self.page = "menu"
                self.selected = 0
                return None
            index = self._hit(x, y)
            if index is not None:
                self.selected = index
                return self._activate()
        return None

    def _hit(self, x, y):
        if not 874 <= x <= 1240:
            return None
        if self.page == "menu":
            return next((index for index in range(len(MENU))
                         if abs(y - (506 - index * 78)) <= 30), None)
        return next((index for index in range(len(self.slots))
                     if abs(y - (ROW_TOP - index * ROW_STEP)) <= 21), None)

    def draw(self):
        self.background.draw_scene(perf_counter(), show_title=self.external_background)
        pico.draw_rectangle(0, 0, *CANVAS_SIZE, 0, 0, 0, 156, filled=True)
        pico.draw_rectangle(840, 0, 1280, 720, 0, 0, 0, 196, filled=True)
        pico.draw_rectangle(840, 0, 842, 720, *THEME.white, 205, filled=True)
        heading = ("일시정지" if self.page == "menu" else "저장 슬롯" if self.page == "save"
                   else "불러오기" if self.page == "load" else "덮어쓰기")
        self._label("heading", heading, 882, 621, 30)
        pico.draw_rectangle(880, 583, 1240, 585, *THEME.red, 240, filled=True)
        if self.page == "menu":
            for index, (_, label) in enumerate(MENU):
                y = 506 - index * 78
                if index == self.selected:
                    pico.draw_rectangle(873, y - 31, 1240, y + 31, *THEME.red_dim, 86, filled=True)
                pico.draw_rectangle(873, y - 31, 1240, y + 31, *THEME.white, 92)
                self._label(f"menu/{index}", label, 900, y, 21)
        elif self.page in ("save", "load"):
            for index, (name, path, stamp) in enumerate(self.slots):
                y = ROW_TOP - index * ROW_STEP
                color = THEME.red if path == self.current_slot else THEME.white
                if index == self.selected:
                    pico.draw_rectangle(873, y - 21, 1240, y + 21, *THEME.red_dim, 78, filled=True)
                pico.draw_rectangle(873, y - 21, 1240, y + 21, *THEME.white, 83)
                self._label(f"slot/{index}", name, 888, y, 18, color)
                self._label(f"stamp/{index}", stamp, 1074, y, 14, THEME.muted)
            self._label("back", "뒤로", 900, 96, 19)
        if self.status:
            self._label("status", self.status, 884, 142, 15, THEME.red)
        if self.page == "confirm":
            pico.draw_rectangle(306, 215, 974, 475, 0, 0, 0, 235, filled=True)
            pico.draw_rectangle(306, 215, 974, 475, *THEME.red, 240)
            self._label("confirm_title", "이 슬롯을 덮어쓸까요?", 365, 417, 25)
            self._label("confirm_slot", self.pending_choice[1].name, 365, 361, 18)
            self._label("confirm_yes", "덮어쓰기", 449, 301, 20)
            self._label("confirm_no", "취소", 723, 301, 20)
        pico.update_canvas()


def run_pause(backend, settings, simulation, current_slot=None, initial_page="menu"):
    """게임 틱을 멈춘 채 메뉴 입력을 처리한다."""
    from .savegame import CampaignSaveError, load_game, save_game
    from .settings_screen import SettingsScreen

    pause = PauseScreen(backend, current_slot)
    pause.page = initial_page
    options = None
    pending = None
    pending_action = pending_path = None
    quit_after_pending = False
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pause-save")
    try:
        while True:
            started = perf_counter()
            event = sdl2.SDL_Event()
            while sdl2.SDL_PollEvent(ctypes.byref(event)):
                if pending is not None:
                    quit_after_pending |= event.type == sdl2.SDL_QUIT
                    continue
                if options is not None:
                    result = options.handle(event)
                    if result == "back":
                        options.close()
                        options = None
                    elif result == "quit":
                        return "quit", None, pause.current_slot
                    continue
                result = pause.handle(event)
                if result is None:
                    continue
                action, path = result
                if action in ("resume", "quit"):
                    return action, None, pause.current_slot
                if action == "settings":
                    options = SettingsScreen(backend, pause.background, settings)
                elif action in ("save", "save_exit", "load"):
                    if not simulation.presence:
                        pause.status = "이 장면은 저장·불러오기를 지원하지 않습니다"
                    else:
                        pending_action, pending_path = action, path
                        pending = executor.submit(load_game, path) if action == "load" else executor.submit(save_game, simulation, path)
                        pause.status = "불러오는 중" if action == "load" else "저장 중"
            if pending is not None and pending.done():
                try:
                    result = pending.result()
                except (CampaignSaveError, OSError, ValueError) as error:
                    pause.status = str(error)
                else:
                    if pending_action == "load":
                        return "load", result, pending_path
                    pause.current_slot = pending_path
                    pause.refresh_slots()
                    if pending_action == "save_exit":
                        return "menu", None, pending_path
                    pause.page = "menu"
                    pause.status = "저장 완료"
                pending = None
                if quit_after_pending:
                    return "quit", None, pause.current_slot
            if options is not None:
                options.draw(perf_counter())
            else:
                pause.draw()
            remaining = 1 / 60 - (perf_counter() - started)
            if remaining > 0:
                sleep(remaining)
    finally:
        executor.shutdown(wait=True)
        if options is not None:
            options.close()
        pause.close()
