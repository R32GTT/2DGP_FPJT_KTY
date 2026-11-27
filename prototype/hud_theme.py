"""HUD의 글꼴, 흰·검·빨 팔레트와 투명도. 화면별 수치는 이곳에서 조정한다."""

from dataclasses import dataclass
from hashlib import blake2s
import os
from pathlib import Path


_font_override = os.environ.get("REVENANT_HUD_FONT")
FONT_PATH = Path(_font_override) if _font_override else (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf")
FONT_SIZES = {"detail": 13, "value": 16, "weapon": 20, "heading": 24}

@dataclass(frozen=True)
class HUDTheme:
    white: tuple[int, int, int] = (248, 248, 248)
    muted: tuple[int, int, int] = (205, 205, 205)
    black: tuple[int, int, int] = (0, 0, 0)
    red: tuple[int, int, int] = (232, 48, 52)
    red_dim: tuple[int, int, int] = (150, 30, 34)
    modal_alpha: int = 112
    panel_alpha: int = 135
    card_alpha: int = 150
    label_alpha: int = 178
    hover_alpha: int = 86
    edge_alpha: int = 226
    track_alpha: int = 156
    map_ground: tuple[int, int, int] = (28, 28, 28)
    map_road: tuple[int, int, int] = (138, 138, 138)
    map_safehouse: tuple[int, int, int] = (196, 196, 196)
    map_facility: tuple[int, int, int] = (112, 112, 112)
    map_tone_floor: int = 28
    map_tone_gain: float = 0.6
    map_tone_min: int = 32
    map_tone_max: int = 190


THEME = HUDTheme()
ACCENT = THEME.red
TEXT = THEME.white
PANEL = THEME.black
PANEL_EDGE = THEME.white


def map_tone(color):
    """원본 지형의 명암 차이는 유지하면서 지도 색을 회색조로 맞춘다."""
    value = round(THEME.map_tone_floor + THEME.map_tone_gain *
                  (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]))
    value = max(THEME.map_tone_min, min(THEME.map_tone_max, value))
    return value, value, value


MAP_CACHE_KEY = blake2s(repr((2, THEME.map_ground, THEME.map_road,
                              THEME.map_safehouse, THEME.map_facility,
                              THEME.map_tone_floor, THEME.map_tone_gain,
                              THEME.map_tone_min, THEME.map_tone_max)).encode(),
                        digest_size=4).hexdigest()
