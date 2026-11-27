"""수송기 내부 메인 화면. 장면과 입력 상태는 게임 시뮬레이션과 분리한다."""

import ctypes
from concurrent.futures import ThreadPoolExecutor
from math import cos, pi
from time import perf_counter, sleep

import pico2d as pico
import sdl2

from .camera import CANVAS_SIZE
from .hud import HUD
from .hud_theme import THEME
from .savegame import DEFAULT_SAVE, QUICK_SAVE
from .world import ROOT


ASSETS = ROOT / "assets" / "mainscreen"
ITEMS = (("new_game", "새 게임"), ("continue", "이어하기"),
         ("settings", "설정"), ("quit", "종료"))
ITEM_CENTERS = (450, 600, 750, 900)
MENU_IDLE_SECONDS = 8.0
FRAME_SECONDS = 1 / 60
TITLE_FADE_END_SECONDS = 2.65
RED_PULSE_SECONDS = 5.2
RED_MIN_OPACITY = 0.08
RED_MAX_OPACITY = 0.55


def _smooth(start, end, value):
    fraction = max(0.0, min(1.0, (value - start) / (end - start)))
    return fraction * fraction * (3 - 2 * fraction)


class MainScreen:
    def __init__(self, backend):
        self.backend = backend
        backend.SDL_SetRenderDrawBlendMode(backend.renderer, backend.SDL_BLENDMODE_BLEND)
        backend.SDL_RenderSetLogicalSize(backend.renderer, *CANVAS_SIZE)
        backend.SDL_SetWindowResizable(backend.window, backend.SDL_TRUE)
        backend.SDL_SetWindowTitle(backend.window, b"REVENANT : L.O.S.T")
        self.base = pico.load_image(str(ASSETS / "aircraft_base.png"))
        self.red_light = pico.load_image(str(ASSETS / "aircraft_red_light.png"))
        self.title = pico.load_image(str(ASSETS / "title_lost_red.png"))
        self.hud = HUD(backend)
        self.started = perf_counter()
        self.previous = self.started
        self.last_activity = self.started
        self.menu_opacity = 0.0
        self.menu_requested = False
        self.selection = 0
        self.mouse = self._mouse_position()
        self.cursor_visible = False
        self.continue_available = DEFAULT_SAVE.is_file() or QUICK_SAVE.is_file()
        self.notice = ""
        self.notice_until = 0.0
        backend.SDL_ShowCursor(backend.SDL_DISABLE)
        for index, (_, name) in enumerate(ITEMS):
            self.hud.label(f"menu/{index}", name, 25, THEME.white)

    def reset(self):
        self.started = self.previous = self.last_activity = perf_counter()
        self.menu_opacity = 0.0
        self.menu_requested = False
        self.selection = 0
        self.mouse = self._mouse_position()
        self.continue_available = DEFAULT_SAVE.is_file() or QUICK_SAVE.is_file()
        self.notice = ""
        self._cursor(False)
        self.backend.SDL_SetWindowTitle(self.backend.window, b"REVENANT : L.O.S.T")

    def _cursor(self, visible):
        if visible != self.cursor_visible:
            self.backend.SDL_ShowCursor(self.backend.SDL_ENABLE if visible else self.backend.SDL_DISABLE)
            self.cursor_visible = visible

    def _mouse_position(self):
        x, y = ctypes.c_int(), ctypes.c_int()
        self.backend.SDL_GetMouseState(ctypes.byref(x), ctypes.byref(y))
        return x.value, y.value

    def _wake(self, now, *, mouse=False):
        self.last_activity = now
        self.menu_requested = True
        self._cursor(mouse)

    def _hit(self, x, y):
        if not 590 <= y <= 660:
            return None
        return next((index for index, center in enumerate(ITEM_CENTERS)
                     if abs(x - center) <= 62), None)

    def handle(self, event, now):
        if event.type == sdl2.SDL_QUIT:
            return "quit"
        if now - self.started < TITLE_FADE_END_SECONDS:
            if event.type == sdl2.SDL_MOUSEMOTION:
                self.mouse = event.motion.x, event.motion.y
            return None
        if event.type == sdl2.SDL_MOUSEMOTION:
            point = event.motion.x, event.motion.y
            if point != self.mouse:
                self.mouse = point
                self._wake(now, mouse=True)
            hit = self._hit(*point)
            if hit is not None and (hit != 1 or self.continue_available):
                self.selection = hit
            return None
        if event.type == sdl2.SDL_MOUSEBUTTONUP and event.button.button == sdl2.SDL_BUTTON_LEFT:
            was_visible = self.menu_requested and self.menu_opacity >= 0.5
            self._wake(now, mouse=True)
            if was_visible:
                hit = self._hit(event.button.x, event.button.y)
                if hit is not None and (hit != 1 or self.continue_available):
                    return ITEMS[hit][0]
            return None
        if event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
            was_visible = self.menu_requested and self.menu_opacity >= 0.5
            self._wake(now)
            key = event.key.keysym.sym
            if not was_visible:
                return None
            if key in (sdl2.SDLK_LEFT, sdl2.SDLK_a, sdl2.SDLK_UP):
                self.selection = (self.selection - 1) % len(ITEMS)
            elif key in (sdl2.SDLK_RIGHT, sdl2.SDLK_d, sdl2.SDLK_DOWN):
                self.selection = (self.selection + 1) % len(ITEMS)
            elif key in (sdl2.SDLK_RETURN, sdl2.SDLK_SPACE):
                action = ITEMS[self.selection][0]
                if action != "continue" or self.continue_available:
                    return action
            elif key == sdl2.SDLK_ESCAPE:
                self.menu_requested = False
        return None

    def draw(self, now):
        delta = min(0.1, now - self.previous)
        self.previous = now
        if self.menu_requested and now - self.last_activity >= MENU_IDLE_SECONDS:
            self.menu_requested = False
            self._cursor(False)
        target = 1.0 if self.menu_requested else 0.0
        self.menu_opacity += max(-delta / 0.38, min(delta / 0.38, target - self.menu_opacity))

        self.draw_scene(now)
        width, _ = CANVAS_SIZE
        if self.menu_opacity > 0.01:
            alpha = round(255 * self.menu_opacity)
            for index, (action, _) in enumerate(ITEMS):
                key = f"menu/{index}"
                x = ITEM_CENTERS[index] - self.hud.labels[key].width / 2
                self.hud.text(key, x, 95, opacity=round(alpha * (0.43 if action == "continue" and
                                                                 not self.continue_available else 1)))
            if self.selection != 1 or self.continue_available:
                center = ITEM_CENTERS[self.selection]
                pico.draw_rectangle(center - 38, 73, center + 38, 75,
                                    *THEME.red, alpha, filled=True)
        if self.notice and now < self.notice_until:
            self.hud.label("notice", self.notice, 16, THEME.white)
            self.hud.text("notice", width / 2 - self.hud.labels["notice"].width / 2, 145)
        pico.update_canvas()

    def draw_scene(self, now, *, show_title=True):
        width, height = CANVAS_SIZE
        elapsed = now - self.started

        self.backend.SDL_SetRenderDrawColor(self.backend.renderer, 0, 0, 0, 255)
        self.backend.SDL_RenderClear(self.backend.renderer)
        self.base.draw(width / 2, height / 2, width, height)
        darkness = round(255 - 103 * _smooth(0.0, 1.3, elapsed))
        pico.draw_rectangle(0, 0, width, height, *THEME.black, darkness, filled=True)
        first_flash = max(0.0, 1.0 - abs(elapsed - 0.62) / 0.27) ** 2
        phase = max(0.0, elapsed - 1.3) / RED_PULSE_SECONDS
        slow_pulse = (1.0 - cos(2 * pi * phase)) / 2
        steady = RED_MIN_OPACITY + (RED_MAX_OPACITY - RED_MIN_OPACITY) * slow_pulse
        self.red_light.opacify(min(1.0, steady * _smooth(0.25, 1.3, elapsed) + 0.92 * first_flash))
        self.red_light.draw(width / 2, height / 2, width, height)
        if show_title:
            self.title.opacify(_smooth(0.95, TITLE_FADE_END_SECONDS, elapsed))
            self.title.draw(width / 2, height / 2, width, height)

    def show_notice(self, message):
        self.notice = message
        self.notice_until = perf_counter() + 3.5

    def close(self):
        self._cursor(True)
        self.hud.close()
        self.base = self.red_light = self.title = None


def run_main_screen(*, frames=0):
    """메인 화면의 첫 진입점을 실행한다. frames는 화면 검증용 제한이다."""
    pico.open_canvas(*CANVAS_SIZE, sync=True)
    import importlib
    backend = importlib.import_module("pico2d.pico2d")
    screen = MainScreen(backend)
    count = 0
    try:
        while not frames or count < frames:
            now = perf_counter()
            event = sdl2.SDL_Event()
            while sdl2.SDL_PollEvent(ctypes.byref(event)):
                action = screen.handle(event, now)
                if action is not None:
                    return action
            screen.draw(now)
            count += 1
            remaining = FRAME_SECONDS - (perf_counter() - now)
            if remaining > 0:
                sleep(remaining)
    finally:
        screen.close()
        pico.close_canvas()
    return None


def run_frontend(*, frames=0):
    """일반 실행: 메인 화면 → 새 게임 설정/이어하기 → 실제 게임."""
    from .__main__ import run_window
    from .generated_world import generate_world
    from .new_game_screen import NewGameScreen
    from .savegame import CampaignSaveError, load_game
    from .settings import apply_display, load_settings
    from .settings_screen import SettingsScreen
    from .simulation import PlaySimulation

    settings = load_settings()
    pico.open_canvas(*CANVAS_SIZE, sync=True)
    import importlib
    backend = importlib.import_module("pico2d.pico2d")
    screen = MainScreen(backend)
    try:
        apply_display(backend, settings.window_mode, settings.window_size)
    except RuntimeError as error:
        screen.show_notice(str(error))
    setup = None
    options_screen = None
    pending = None
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="world-load")
    count = 0
    try:
        while not frames or count < frames:
            frame_start = perf_counter()
            event = sdl2.SDL_Event()
            action = None
            while sdl2.SDL_PollEvent(ctypes.byref(event)):
                if pending is not None:
                    if event.type == sdl2.SDL_QUIT:
                        action = "quit"
                        break
                    continue
                if setup is not None:
                    result = setup.handle(event)
                elif options_screen is not None:
                    result = options_screen.handle(event)
                else:
                    result = screen.handle(event, frame_start)
                if result is not None:
                    action = result
                    break
            if action == "quit":
                break
            if action == "new_game":
                setup = NewGameScreen(backend, screen.base)
            elif action == "back" and (setup is not None or options_screen is not None):
                if setup is not None:
                    setup.close()
                    setup = None
                else:
                    options_screen.close()
                    options_screen = None
                screen.reset()
                screen.started -= TITLE_FADE_END_SECONDS
                screen.menu_requested = True
                screen._cursor(True)
            elif action == "settings":
                options_screen = SettingsScreen(backend, screen, settings)
            elif action in {"start", "continue"}:
                try:
                    if action == "start":
                        options = setup.config.generation_options()
                        setup.loading = True
                        world_root = DEFAULT_SAVE.parent / "worlds"

                        def create_game():
                            scene = generate_world(**options, world_root=world_root)
                            return PlaySimulation(scene, world_root=world_root)

                        pending = executor.submit(create_game)
                    else:
                        save = DEFAULT_SAVE if DEFAULT_SAVE.is_file() else QUICK_SAVE
                        pending = executor.submit(load_game, save)
                        screen.show_notice("저장 불러오는 중")
                except (ValueError, OSError, CampaignSaveError) as error:
                    if setup is not None:
                        setup.error = str(error)
                        setup.loading = False
                    else:
                        screen.show_notice(str(error))
            if pending is not None and pending.done():
                try:
                    simulation = pending.result()
                except (ValueError, OSError, CampaignSaveError) as error:
                    if setup is not None:
                        setup.error = str(error)
                        setup.loading = False
                    else:
                        screen.show_notice(str(error))
                    pending = None
                else:
                    pending = None
                    if setup is not None:
                        setup.close()
                        setup = None
                    screen.close()
                    run_window(simulation, manage_canvas=False, settings=settings)
                    screen = MainScreen(backend)
            if options_screen is not None:
                options_screen.draw(perf_counter())
            elif setup is not None:
                setup.draw()
            else:
                screen.draw(perf_counter())
            count += 1
            remaining = FRAME_SECONDS - (perf_counter() - frame_start)
            if remaining > 0:
                sleep(remaining)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        if setup is not None:
            setup.close()
        if options_screen is not None:
            options_screen.close()
        screen.close()
        pico.close_canvas()
