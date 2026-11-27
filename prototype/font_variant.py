"""원본 TTF를 보존하고 HUD용 가로 확장 시안을 만든다.

실행: python -m prototype.font_variant 입력.ttf 출력.ttf 1.10
별도 작업 도구이며 실행 시 fonttools 패키지가 필요하다.
"""

import argparse
from pathlib import Path


def stretch_font(source: Path, destination: Path, width_scale: float):
    from fontTools.ttLib import TTFont

    if not 0.75 <= width_scale <= 1.5:
        raise ValueError("가로 비율은 0.75~1.5 사이여야 합니다")
    if source.resolve() == destination.resolve():
        raise ValueError("원본과 출력 경로가 같으면 안 됩니다")

    font = TTFont(source)
    glyphs = font["glyf"]
    for name in font.getGlyphOrder():
        glyph = glyphs[name]
        if glyph.isComposite():
            for component in glyph.components:
                if hasattr(component, "x"):
                    component.x = round(component.x * width_scale)
        elif hasattr(glyph, "coordinates"):
            glyph.coordinates.scale((width_scale, 1.0))
            glyph.coordinates.toInt()

    font["hmtx"].metrics = {
        name: (round(advance * width_scale), round(bearing * width_scale))
        for name, (advance, bearing) in font["hmtx"].metrics.items()
    }
    font["hhea"].advanceWidthMax = round(font["hhea"].advanceWidthMax * width_scale)
    font["OS/2"].xAvgCharWidth = round(font["OS/2"].xAvgCharWidth * width_scale)
    if "kern" in font:
        for table in font["kern"].kernTables:
            table.kernTable = {pair: round(value * width_scale)
                              for pair, value in table.kernTable.items()}

    suffix = f"Wide{round(width_scale * 100)}"
    names = font["name"]
    for record in list(names.names):
        if record.nameID not in (1, 3, 4, 6, 16):
            continue
        original = record.toUnicode()
        value = f"{original}-{suffix}" if record.nameID == 6 else f"{original} {suffix}"
        names.setName(value, record.nameID, record.platformID, record.platEncID, record.langID)

    destination.parent.mkdir(parents=True, exist_ok=True)
    font.save(destination)
    font.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("width_scale", type=float)
    arguments = parser.parse_args()
    stretch_font(arguments.source, arguments.destination, arguments.width_scale)
