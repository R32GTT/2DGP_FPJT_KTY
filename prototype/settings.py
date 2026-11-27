"""사용자 설정 저장과 조작 바인딩. 오디오 설정은 이후 같은 파일에 추가한다."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

import sdl2


MOD_SHIFT, MOD_CTRL, MOD_ALT = 1, 2, 4
DISPLAY_MODES = ("windowed", "borderless")
WINDOW_SIZES = ((1280, 720), (1600, 900), (1920, 1080))
KEY_PAGE_SIZE = 8
ACTION_GROUPS = (
    ("이동", (("move_up", "위로 이동"), ("move_down", "아래로 이동"),
             ("move_left", "왼쪽 이동"), ("move_right", "오른쪽 이동"),
             ("sprint", "달리기"), ("walk", "걷기"))),
    ("전투", (("fire", "사격"), ("aim", "조준"), ("reload", "재장전"),
             ("fire_mode", "사격 방식"), ("throw", "투척"),
             ("weapon_1", "무기 1"), ("weapon_2", "무기 2"),
             ("weapon_3", "무기 3"), ("weapon_4", "무기 4"))),
    ("상호작용", (("door", "문"), ("supplies", "보급"),
                  ("facility_alternate", "시설 보조 동작"), ("inventory", "가방"),
                  ("map", "지도"), ("minimap", "미니맵"),
                  ("intel", "작전 정보"), ("collect_intel", "정보 수집"),
                  ("lamp", "손전등"), ("night_vision", "야간 투시"),
                  ("ir_laser", "적외선 레이저"))),
    ("기타", (("save", "저장"), ("quick_save", "빠른 저장"),
             ("quick_load", "빠른 불러오기"), ("load", "불러오기"),
             ("zoom_in", "확대"), ("zoom_out", "축소"),
             ("respawn", "복귀 확정"), ("cancel", "취소"))),
)
ACTION_ROWS = tuple((group, action, label) for group, rows in ACTION_GROUPS
                    for action, label in rows)
ACTION_NAMES = {action: label for _, action, label in ACTION_ROWS}
MOUSE_ACTIONS = frozenset(("fire", "aim"))


@dataclass(frozen=True)
class Binding:
    device: str
    code: int
    mods: int = 0

    def as_dict(self):
        return {"device": self.device, "code": self.code, "mods": self.mods}


def key(code, mods=0):
    return Binding("key", code, mods)


DEFAULT_BINDINGS = {
    "move_up": key(sdl2.SDLK_w), "move_down": key(sdl2.SDLK_s),
    "move_left": key(sdl2.SDLK_a), "move_right": key(sdl2.SDLK_d),
    "sprint": key(sdl2.SDLK_LSHIFT), "walk": key(sdl2.SDLK_LCTRL),
    "fire": Binding("mouse", sdl2.SDL_BUTTON_LEFT),
    "aim": Binding("mouse", sdl2.SDL_BUTTON_RIGHT),
    "reload": key(sdl2.SDLK_r), "fire_mode": key(sdl2.SDLK_v),
    "throw": key(sdl2.SDLK_g),
    **{f"weapon_{n}": key(getattr(sdl2, f"SDLK_{n}")) for n in range(1, 5)},
    "door": key(sdl2.SDLK_f), "supplies": key(sdl2.SDLK_e),
    "facility_alternate": key(sdl2.SDLK_e, MOD_SHIFT),
    "inventory": key(sdl2.SDLK_TAB), "map": key(sdl2.SDLK_m),
    "minimap": key(sdl2.SDLK_h), "intel": key(sdl2.SDLK_i),
    "collect_intel": key(sdl2.SDLK_i, MOD_SHIFT),
    "lamp": key(sdl2.SDLK_l), "night_vision": key(sdl2.SDLK_n),
    "ir_laser": key(sdl2.SDLK_k),
    "save": key(sdl2.SDLK_F5), "quick_save": key(sdl2.SDLK_F6),
    "quick_load": key(sdl2.SDLK_F8), "load": key(sdl2.SDLK_F9),
    "zoom_in": key(sdl2.SDLK_EQUALS), "zoom_out": key(sdl2.SDLK_MINUS),
    "respawn": key(sdl2.SDLK_RETURN), "cancel": key(sdl2.SDLK_c),
}


def settings_path():
    override = os.environ.get("REVENANT_SETTINGS_PATH")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RevenantLost" / "settings.json"


def input_mods(sdl_mod):
    return (MOD_SHIFT if sdl_mod & sdl2.KMOD_SHIFT else 0) | (
        MOD_CTRL if sdl_mod & sdl2.KMOD_CTRL else 0) | (
        MOD_ALT if sdl_mod & sdl2.KMOD_ALT else 0)


def binding_name(binding):
    if binding.device == "mouse":
        return {1: "마우스 왼쪽", 2: "마우스 가운데", 3: "마우스 오른쪽",
                4: "마우스 측면 1", 5: "마우스 측면 2"}.get(binding.code, "마우스")
    name = sdl2.SDL_GetKeyName(binding.code).decode("utf-8", errors="replace")
    parts = [label for bit, label in ((MOD_SHIFT, "Shift"), (MOD_CTRL, "Ctrl"), (MOD_ALT, "Alt"))
             if binding.mods & bit]
    return " + ".join((*parts, name))


def _valid(action, binding):
    return (action in DEFAULT_BINDINGS and binding.device in ("key", "mouse")
            and type(binding.code) is int and 1 <= binding.code < 2**31
            and type(binding.mods) is int and 0 <= binding.mods <= 7
            and (binding.device != "mouse" or action in MOUSE_ACTIONS and
                 binding.code in (1, 2, 3, 4, 5) and binding.mods == 0)
            and not (binding.device == "key" and binding.code in
                     (sdl2.SDLK_ESCAPE, sdl2.SDLK_F1, sdl2.SDLK_F2,
                      sdl2.SDLK_F3, sdl2.SDLK_F4)))


@dataclass
class AppSettings:
    bindings: dict[str, Binding] = field(default_factory=lambda: DEFAULT_BINDINGS.copy())
    window_mode: str = "windowed"
    window_size: tuple[int, int] = (1280, 720)
    path: Path = field(default_factory=settings_path)
    extra: dict = field(default_factory=dict)

    def action_for_key(self, code, mods=0):
        matches = ((binding.mods.bit_count(), action) for action, binding in self.bindings.items()
                   if binding.device == "key" and binding.code == code
                   and binding.mods & mods == binding.mods)
        return max(matches, default=(-1, None))[1]

    def action_for_mouse(self, button):
        return next((action for action, binding in self.bindings.items()
                     if binding.device == "mouse" and binding.code == button), None)

    def held(self, action, keys, buttons):
        binding = self.bindings[action]
        if binding.device == "mouse":
            return binding.code in buttons
        if binding.code not in keys:
            alternate = {sdl2.SDLK_LSHIFT: sdl2.SDLK_RSHIFT,
                         sdl2.SDLK_LCTRL: sdl2.SDLK_RCTRL}.get(binding.code)
            if alternate is None or alternate not in keys:
                return False
        active = input_mods_from_keys(keys)
        return active & binding.mods == binding.mods

    def rebind(self, action, binding):
        if not _valid(action, binding):
            raise ValueError("이 동작에는 해당 키를 지정할 수 없습니다")
        other = next((name for name, current in self.bindings.items()
                      if name != action and current == binding), None)
        if other is not None:
            previous = self.bindings[action]
            if not _valid(other, previous):
                raise ValueError("다른 동작에 사용 중인 키입니다")
            self.bindings[other] = previous
        self.bindings[action] = binding
        return other

    def reset_bindings(self):
        self.bindings = DEFAULT_BINDINGS.copy()

    def save(self):
        payload = {**self.extra, "version": 1,
                   "bindings": {name: binding.as_dict() for name, binding in self.bindings.items()},
                   "display": {"mode": self.window_mode, "size": list(self.window_size)}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def input_mods_from_keys(keys):
    return (MOD_SHIFT if keys & {sdl2.SDLK_LSHIFT, sdl2.SDLK_RSHIFT} else 0) | (
        MOD_CTRL if keys & {sdl2.SDLK_LCTRL, sdl2.SDLK_RCTRL} else 0) | (
        MOD_ALT if keys & {sdl2.SDLK_LALT, sdl2.SDLK_RALT} else 0)


def load_settings(path=None):
    settings = AppSettings(path=Path(path) if path is not None else settings_path())
    try:
        payload = json.loads(settings.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return settings
        settings.extra = {name: value for name, value in payload.items()
                          if name not in ("version", "bindings", "display")}
        display = payload.get("display", {})
        if isinstance(display, dict):
            if display.get("mode") in DISPLAY_MODES:
                settings.window_mode = display["mode"]
            size = display.get("size")
            if isinstance(size, list) and tuple(size) in WINDOW_SIZES:
                settings.window_size = tuple(size)
        stored = payload.get("bindings", {})
        if isinstance(stored, dict):
            loaded = settings.bindings.copy()
            for action, value in stored.items():
                if action not in DEFAULT_BINDINGS or not isinstance(value, dict):
                    continue
                binding = Binding(value.get("device"), value.get("code"), value.get("mods", 0))
                if _valid(action, binding):
                    loaded[action] = binding
            if len(set(loaded.values())) == len(loaded):
                settings.bindings = loaded
    except (OSError, ValueError, TypeError):
        pass
    return settings


def apply_display(backend, mode, size):
    """SDL 논리 해상도는 고정하고 물리 창만 변경한다."""
    window = backend.window
    if mode == "borderless":
        result = backend.SDL_SetWindowFullscreen(window, backend.SDL_WINDOW_FULLSCREEN_DESKTOP)
    else:
        result = backend.SDL_SetWindowFullscreen(window, 0)
        if result == 0:
            backend.SDL_SetWindowBordered(window, backend.SDL_TRUE)
            backend.SDL_SetWindowSize(window, *size)
            backend.SDL_SetWindowPosition(window, backend.SDL_WINDOWPOS_CENTERED,
                                          backend.SDL_WINDOWPOS_CENTERED)
    if result != 0:
        raise RuntimeError("화면 모드를 변경할 수 없습니다")


def available_sizes(backend):
    """현재 모니터보다 큰 창 크기는 선택지에서 제외한다."""
    import ctypes

    display = backend.SDL_DisplayMode()
    if backend.SDL_GetDesktopDisplayMode(0, ctypes.byref(display)) != 0:
        return WINDOW_SIZES
    return tuple(size for size in WINDOW_SIZES if size[0] <= display.w and size[1] <= display.h) or (WINDOW_SIZES[0],)
