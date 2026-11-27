"""기본 HUD의 글꼴과 색상. 이 파일만 바꿔 시각 스타일을 조정한다."""

import os
from pathlib import Path


_font_override = os.environ.get("REVENANT_HUD_FONT")
FONT_PATH = Path(_font_override) if _font_override else (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf")
FONT_SIZES = {"detail": 13, "value": 16, "weapon": 20, "heading": 24}

ACCENT = (102, 215, 194)
TEXT = (245, 242, 230)
PANEL = (12, 21, 25)
PANEL_EDGE = (75, 106, 105)
