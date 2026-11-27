"""새 게임의 시드와 지역별 생성 설정을 편집하는 화면."""

from dataclasses import dataclass, field
from secrets import randbelow

import pico2d as pico
import sdl2

from .camera import CANVAS_SIZE
from .hud import HUD
from .hud_theme import THEME
from .world_layout import MAX_REGIONS


SIZES = ("small", "medium", "large", "xlarge")
SIZE_NAMES = ("소형", "중형", "대형", "초대형")
IMPORTANCES = ("normal", "important", "core")
IMPORTANCE_NAMES = ("일반", "중요", "핵심")
PAGE_SIZE = 6


@dataclass
class NewGameConfig:
    seed_text: str = "42"
    region_count: int = 1
    sizes: list[str] = field(default_factory=lambda: ["small"] * MAX_REGIONS)
    importances: list[str] = field(default_factory=lambda: ["normal"] * MAX_REGIONS)

    @property
    def seed(self):
        if not self.seed_text or self.seed_text == "-":
            raise ValueError("시드를 입력하세요")
        return int(self.seed_text)

    def generation_options(self):
        return dict(seed=self.seed, region_count=self.region_count,
                    region_sizes=self.sizes[:self.region_count],
                    region_importances=self.importances[:self.region_count])


def _inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


class NewGameScreen:
    def __init__(self, backend, base, config=None):
        self.backend = backend
        self.base = base
        self.hud = HUD(backend)
        self.config = config or NewGameConfig()
        self.page = 0
        self.seed_focused = False
        self.error = ""
        self.loading = False
        backend.SDL_ShowCursor(backend.SDL_ENABLE)
        backend.SDL_StartTextInput()

    def close(self):
        self.backend.SDL_StopTextInput()
        self.hud.close()

    def _label(self, key, value, x, y, size=20, color=None):
        self.hud.label(key, value, size, color or THEME.white)
        self.hud.text(key, x, y)

    def handle(self, event):
        if event.type == sdl2.SDL_QUIT:
            return "quit"
        if event.type == sdl2.SDL_TEXTINPUT and self.seed_focused:
            entered = event.text.text.decode("utf-8", errors="ignore")
            if entered.isascii() and entered.isdecimal() and len(self.config.seed_text) + len(entered) <= 18:
                self.config.seed_text += entered
                self.error = ""
        if event.type == sdl2.SDL_KEYDOWN and not event.key.repeat:
            key = event.key.keysym.sym
            if key == sdl2.SDLK_ESCAPE:
                return "back"
            if self.seed_focused and key == sdl2.SDLK_BACKSPACE:
                self.config.seed_text = self.config.seed_text[:-1]
            elif key == sdl2.SDLK_RETURN:
                return self._start()
        if event.type == sdl2.SDL_MOUSEWHEEL:
            self.page = max(0, min((self.config.region_count - 1) // PAGE_SIZE,
                                   self.page - event.wheel.y))
        if event.type != sdl2.SDL_MOUSEBUTTONUP or event.button.button != sdl2.SDL_BUTTON_LEFT:
            return None
        x, y = event.button.x, CANVAS_SIZE[1] - event.button.y
        point = x, y
        self.seed_focused = _inside(point, (423, 503, 630, 543))
        if _inside(point, (690, 503, 810, 543)):
            self.config.seed_text = str(randbelow(10**9))
        if _inside(point, (424, 453, 467, 493)):
            self.config.region_count = max(1, self.config.region_count - 1)
        if _inside(point, (557, 453, 600, 493)):
            self.config.region_count = min(MAX_REGIONS, self.config.region_count + 1)
        self.page = min(self.page, (self.config.region_count - 1) // PAGE_SIZE)
        if _inside(point, (903, 454, 947, 486)):
            self.page = max(0, self.page - 1)
        if _inside(point, (962, 454, 1006, 486)):
            self.page = min((self.config.region_count - 1) // PAGE_SIZE, self.page + 1)
        for row in range(PAGE_SIZE):
            index = self.page * PAGE_SIZE + row
            if index >= self.config.region_count:
                break
            row_y = 421 - row * 47
            if row_y - 17 <= y <= row_y + 17:
                if 606 <= x <= 776:
                    current = SIZES.index(self.config.sizes[index])
                    self.config.sizes[index] = SIZES[(current + 1) % len(SIZES)]
                elif 818 <= x <= 988:
                    current = IMPORTANCES.index(self.config.importances[index])
                    self.config.importances[index] = IMPORTANCES[(current + 1) % len(IMPORTANCES)]
        if _inside(point, (286, 107, 420, 147)):
            return "back"
        if _inside(point, (830, 107, 995, 147)):
            return self._start()
        return None

    def _start(self):
        try:
            self.config.seed
        except ValueError as error:
            self.error = str(error)
            return None
        return "start"

    def draw(self):
        width, height = CANVAS_SIZE
        self.backend.SDL_SetRenderDrawColor(self.backend.renderer, 0, 0, 0, 255)
        self.backend.SDL_RenderClear(self.backend.renderer)
        self.base.draw(width / 2, height / 2, width, height)
        pico.draw_rectangle(0, 0, width, height, 0, 0, 0, 205, filled=True)
        pico.draw_rectangle(220, 80, 1060, 635, 0, 0, 0, 192, filled=True)
        pico.draw_rectangle(220, 80, 1060, 635, *THEME.white, 153)
        pico.draw_rectangle(246, 574, 1034, 576, *THEME.red, 230, filled=True)
        self._label("title", "새 게임", 270, 600, 31)
        self._label("seed", "시드", 290, 523)
        self._label("seed_value", self.config.seed_text + ("|" if self.seed_focused else "") or " ", 435, 523)
        self._label("random", "무작위", 704, 523, 18)
        self._label("count", "지역 수", 290, 474)
        self._label("minus", "−", 437, 474, 24)
        self._label("count_value", str(self.config.region_count), 501, 474)
        self._label("plus", "+", 568, 474, 24)
        self._label("page", f"{self.page + 1} / {(self.config.region_count - 1) // PAGE_SIZE + 1}", 837, 474, 17)
        self._label("prev", "<", 910, 474, 22)
        self._label("next", ">", 970, 474, 22)
        self._label("size_heading", "크기", 656, 449, 15, THEME.muted)
        self._label("importance_heading", "중요도", 865, 449, 15, THEME.muted)
        for row in range(PAGE_SIZE):
            index = self.page * PAGE_SIZE + row
            if index >= self.config.region_count:
                break
            row_y = 421 - row * 47
            pico.draw_rectangle(275, row_y - 20, 1000, row_y + 20, 0, 0, 0, 132, filled=True)
            pico.draw_rectangle(275, row_y - 20, 1000, row_y + 20, *THEME.white, 73)
            self._label(f"row/{row}", f"지역 {index + 1:02d}", 294, row_y, 18)
            size = SIZE_NAMES[SIZES.index(self.config.sizes[index])]
            importance = IMPORTANCE_NAMES[IMPORTANCES.index(self.config.importances[index])]
            self._label(f"size/{row}", size + "  ›", 640, row_y, 18)
            self._label(f"importance/{row}", importance + "  ›", 850, row_y, 18)
        self._label("back", "뒤로", 305, 126, 20)
        self._label("start", "게임 시작", 853, 126, 21)
        pico.draw_rectangle(830, 104, 995, 106, *THEME.red, 255, filled=True)
        if self.error:
            self._label("error", self.error, 469, 126, 16, THEME.red)
        if self.loading:
            pico.draw_rectangle(220, 80, 1060, 635, 0, 0, 0, 169, filled=True)
            self._label("loading", "월드 생성 중", 558, 359, 25)
        pico.update_canvas()
