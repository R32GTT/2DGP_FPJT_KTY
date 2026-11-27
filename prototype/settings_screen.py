"""메인 화면 배경 위에서 여는 키·화면 설정 페이지."""

from time import perf_counter

import pico2d as pico
import sdl2

from .camera import CANVAS_SIZE
from .hud import HUD
from .hud_theme import THEME
from .settings import (ACTION_NAMES, ACTION_ROWS, Binding, DISPLAY_MODES, KEY_PAGE_SIZE,
                       MOD_ALT, MOD_CTRL, MOD_SHIFT, apply_display, available_sizes,
                       binding_name, input_mods)


PAGES = (("keys", "키 설정"), ("display", "화면 설정"))
MODE_NAMES = {"windowed": "창 모드", "borderless": "전체 화면"}
CONFIRM_SECONDS = 10.0


def _inside(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


class SettingsScreen:
    def __init__(self, backend, background, settings):
        self.backend = backend
        self.background = background
        self.settings = settings
        self.hud = HUD(backend)
        self.page = "hub"
        self.key_page = 0
        self.capturing = None
        self.status = ""
        self.mode = settings.window_mode
        self.sizes = available_sizes(backend)
        self.size = settings.window_size if settings.window_size in self.sizes else self.sizes[0]
        self.previous_display = None
        self.confirm_until = 0.0
        backend.SDL_ShowCursor(backend.SDL_ENABLE)

    def close(self):
        if self.previous_display is not None:
            self._confirm(False)
        self.hud.close()

    def _label(self, key, value, x, y, size=19, color=None):
        self.hud.label(key, value, size, color or THEME.white)
        self.hud.text(key, x, y)

    def _change_key(self, binding):
        try:
            other = self.settings.rebind(self.capturing, binding)
            self.settings.save()
        except (ValueError, OSError) as error:
            self.status = str(error)
            return
        self.status = (f"{ACTION_NAMES[other]}와 교환됨" if other else "키 설정 저장됨")
        self.capturing = None

    def _confirm(self, keep):
        previous = self.previous_display
        self.previous_display = None
        self.confirm_until = 0.0
        if keep:
            self.settings.window_mode = self.mode
            self.settings.window_size = self.size
            try:
                self.settings.save()
                self.status = "화면 설정 저장됨"
            except OSError as error:
                self.status = str(error)
        else:
            try:
                apply_display(self.backend, *previous)
            except RuntimeError as error:
                self.status = str(error)
            else:
                self.status = "이전 화면 설정으로 돌아감"
            self.mode, self.size = previous

    def _apply(self):
        previous = self.settings.window_mode, self.settings.window_size
        if (self.mode, self.size) == previous:
            self.status = "현재 화면 설정과 같습니다"
            return
        try:
            apply_display(self.backend, self.mode, self.size)
        except RuntimeError as error:
            self.status = str(error)
            return
        self.previous_display = previous
        self.confirm_until = perf_counter() + CONFIRM_SECONDS

    def _back(self):
        if self.capturing is not None:
            self.capturing = None
            return None
        if self.previous_display is not None:
            self._confirm(False)
            return None
        if self.page == "hub":
            return "back"
        self.page = "hub"
        self.status = ""
        self.mode, self.size = self.settings.window_mode, self.settings.window_size
        return None

    def handle(self, event):
        if event.type == sdl2.SDL_QUIT:
            return "quit"
        if event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
            key = event.key.keysym.sym
            if self.capturing is not None:
                if key == sdl2.SDLK_ESCAPE:
                    self.capturing = None
                else:
                    mods = input_mods(event.key.keysym.mod)
                    if key in (sdl2.SDLK_LSHIFT, sdl2.SDLK_RSHIFT):
                        mods &= ~MOD_SHIFT
                    elif key in (sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL):
                        mods &= ~MOD_CTRL
                    elif key in (sdl2.SDLK_LALT, sdl2.SDLK_RALT):
                        mods &= ~MOD_ALT
                    self._change_key(Binding("key", key, mods))
                return None
            if self.previous_display is not None:
                if key in (sdl2.SDLK_RETURN, sdl2.SDLK_SPACE):
                    self._confirm(True)
                elif key == sdl2.SDLK_ESCAPE:
                    self._confirm(False)
                return None
            if key == sdl2.SDLK_ESCAPE:
                return self._back()
        if event.type == sdl2.SDL_MOUSEWHEEL and self.page == "keys" and self.capturing is None:
            last = (len(ACTION_ROWS) - 1) // KEY_PAGE_SIZE
            self.key_page = max(0, min(last, self.key_page - event.wheel.y))
        if event.type != sdl2.SDL_MOUSEBUTTONUP:
            return None
        x, y = event.button.x, CANVAS_SIZE[1] - event.button.y
        if self.capturing is not None:
            self._change_key(Binding("mouse", event.button.button))
            return None
        if event.button.button != sdl2.SDL_BUTTON_LEFT:
            return None
        if self.previous_display is not None:
            if _inside(x, y, (438, 276, 590, 324)):
                self._confirm(True)
            elif _inside(x, y, (690, 276, 842, 324)):
                self._confirm(False)
            return None
        if _inside(x, y, (260, 91, 400, 140)):
            return self._back()
        if self.page == "hub":
            for index, (name, _) in enumerate(PAGES):
                top = 450 - index * 115
                if _inside(x, y, (295, top - 40, 985, top + 40)):
                    self.page = name
                    self.status = ""
                    return None
        elif self.page == "keys":
            if _inside(x, y, (690, 91, 750, 140)):
                self.key_page = max(0, self.key_page - 1)
            elif _inside(x, y, (826, 91, 886, 140)):
                self.key_page = min((len(ACTION_ROWS) - 1) // KEY_PAGE_SIZE, self.key_page + 1)
            elif _inside(x, y, (932, 91, 1050, 140)):
                self.settings.reset_bindings()
                try:
                    self.settings.save()
                    self.status = "기본 키로 복원됨"
                except OSError as error:
                    self.status = str(error)
            else:
                for row in range(KEY_PAGE_SIZE):
                    index = self.key_page * KEY_PAGE_SIZE + row
                    if index >= len(ACTION_ROWS):
                        break
                    center = 518 - row * 49
                    if _inside(x, y, (250, center - 21, 1030, center + 21)):
                        self.capturing = ACTION_ROWS[index][1]
                        self.status = ""
                        break
        elif self.page == "display":
            if _inside(x, y, (590, 442, 645, 490)) or _inside(x, y, (899, 442, 954, 490)):
                self.mode = DISPLAY_MODES[1 - DISPLAY_MODES.index(self.mode)]
            elif _inside(x, y, (590, 352, 645, 400)) or _inside(x, y, (899, 352, 954, 400)):
                step = -1 if x < 700 else 1
                self.size = self.sizes[(self.sizes.index(self.size) + step) % len(self.sizes)]
            elif _inside(x, y, (844, 91, 1020, 140)):
                self._apply()
        return None

    def draw(self, now):
        if self.previous_display is not None and now >= self.confirm_until:
            self._confirm(False)
        self.background.draw_scene(now, show_title=False)
        pico.draw_rectangle(0, 0, 1280, 720, 0, 0, 0, 139, filled=True)
        pico.draw_rectangle(220, 72, 1060, 650, 0, 0, 0, 182, filled=True)
        pico.draw_rectangle(220, 72, 1060, 650, *THEME.white, 134)
        pico.draw_rectangle(245, 578, 1035, 580, *THEME.red, 229, filled=True)
        title = "설정" if self.page == "hub" else "키 설정" if self.page == "keys" else "화면 설정"
        self._label("heading", title, 266, 608, 31)
        self._label("back", "뒤로", 279, 115, 20)
        if self.page == "hub":
            for index, (_, label) in enumerate(PAGES):
                center = 450 - index * 115
                pico.draw_rectangle(295, center - 40, 985, center + 40, 0, 0, 0, 120, filled=True)
                pico.draw_rectangle(295, center - 40, 985, center + 40, *THEME.white, 110)
                self._label(f"category/{index}", label, 337, center, 24)
                self._label(f"category_arrow/{index}", ">", 927, center, 24, THEME.red)
        elif self.page == "keys":
            self._label("key_hint", "항목을 클릭한 뒤 새 키를 누르세요", 274, 555, 15, THEME.muted)
            for row in range(KEY_PAGE_SIZE):
                index = self.key_page * KEY_PAGE_SIZE + row
                if index >= len(ACTION_ROWS):
                    break
                group, action, label = ACTION_ROWS[index]
                center = 518 - row * 49
                pico.draw_rectangle(250, center - 21, 1030, center + 21, 0, 0, 0, 133, filled=True)
                pico.draw_rectangle(250, center - 21, 1030, center + 21, *THEME.white, 73)
                self._label(f"group/{row}", group, 270, center, 14, THEME.muted)
                self._label(f"action/{row}", label, 355, center, 18)
                self._label(f"binding/{row}", binding_name(self.settings.bindings[action]), 745, center, 17,
                            THEME.red if action == self.capturing else THEME.white)
            self._label("key_prev", "<", 705, 115, 21)
            self._label("key_page", f"{self.key_page + 1} / {(len(ACTION_ROWS) - 1) // KEY_PAGE_SIZE + 1}",
                        759, 115, 17)
            self._label("key_next", ">", 842, 115, 21)
            self._label("reset", "기본값", 953, 115, 19)
        else:
            for center in (465, 375):
                pico.draw_rectangle(286, center - 38, 997, center + 38, 0, 0, 0, 135, filled=True)
                pico.draw_rectangle(286, center - 38, 997, center + 38, *THEME.white, 75)
            self._label("mode_label", "화면 모드", 315, 465, 20)
            self._label("mode_prev", "<", 607, 465, 22)
            self._label("mode_value", MODE_NAMES[self.mode], 690, 465, 19)
            self._label("mode_next", ">", 913, 465, 22)
            self._label("size_label", "창 크기", 315, 375, 20)
            self._label("size_prev", "<", 607, 375, 22)
            self._label("size_value", f"{self.size[0]} x {self.size[1]}", 686, 375, 19)
            self._label("size_next", ">", 913, 375, 22)
            self._label("display_hint", "전체 화면에서는 모니터 크기를 사용합니다", 316, 293, 15, THEME.muted)
            self._label("apply", "적용", 876, 115, 20)
            pico.draw_rectangle(844, 91, 1020, 93, *THEME.red, 255, filled=True)
        if self.status:
            self._label("status", self.status, 434, 115, 15, THEME.red)
        if self.capturing is not None:
            pico.draw_rectangle(270, 262, 1010, 445, 0, 0, 0, 234, filled=True)
            pico.draw_rectangle(270, 262, 1010, 445, *THEME.red, 230)
            self._label("capture_title", ACTION_NAMES[self.capturing], 335, 395, 25)
            self._label("capture_hint", "새 키를 누르세요 · Esc 취소", 335, 331, 19)
            if self.capturing in ("fire", "aim"):
                self._label("capture_mouse", "마우스 버튼도 사용할 수 있습니다", 335, 294, 15)
        if self.previous_display is not None:
            pico.draw_rectangle(306, 254, 974, 460, 0, 0, 0, 240, filled=True)
            pico.draw_rectangle(306, 254, 974, 460, *THEME.red, 230)
            self._label("confirm_title", "화면 설정을 유지할까요?", 370, 408, 25)
            self._label("confirm_timer", f"{max(0, int(self.confirm_until - now) + 1)}초 후 이전 설정으로 복귀",
                        370, 363, 17)
            self._label("confirm_keep", "유지", 465, 300, 20)
            self._label("confirm_revert", "되돌리기", 708, 300, 20)
        pico.update_canvas()
