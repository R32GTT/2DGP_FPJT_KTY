"""Build a deterministic Tiled atlas from individual AI/source assets.

The image model is not trusted with tile coordinates or wall topology. This
module owns the 2048px atlas layout, 32px tile slicing, and 24px wall masks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


ATLAS_SIZE = 2048
TILE_SIZE = 32
ATLAS_COLUMNS = ATLAS_SIZE // TILE_SIZE
PIXELS_PER_METER = 64
WALL_THICKNESS_PX = 24

ASSET_REQUIREMENTS = {
    "ground": {"label": "지면 재질", "size": "256×256px 정사각형 PNG/JPG"},
    "road": {"label": "도로 재질", "size": "256×256px 정사각형 PNG/JPG"},
    "wall": {"label": "일반 벽 재질", "size": "256×256px 정사각형 PNG/JPG"},
    "half_wall": {"label": "Half wall 재질", "size": "256×256px 정사각형 PNG/JPG"},
    "door": {"label": "문 엔티티", "size": "64×32px 또는 32×64px 투명 PNG"},
    "window": {"label": "창문 엔티티", "size": "32×32px 투명 PNG"},
    "decoration": {"label": "장식 오브젝트", "size": "32px 배수, 최대 256×256px 투명 PNG"},
}

REGIONS = {
    "ground": (0, 0, 32, 16),
    "road": (32, 0, 32, 16),
    "wall": (0, 16, 32, 16),
    "opening": (32, 16, 32, 16),
    "decoration": (0, 32, 64, 32),
}


def _files(folder: Path) -> list[Path]:
    return sorted(
        [path for path in folder.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"}],
        key=lambda path: path.name.lower(),
    ) if folder.is_dir() else []


def inspect_source_assets(source_dir: Path) -> list[dict[str, Any]]:
    """Return UI-friendly source asset status without modifying files."""
    source_dir = Path(source_dir)
    result: list[dict[str, Any]] = []
    for category, requirement in ASSET_REQUIREMENTS.items():
        folder = source_dir / category
        files = _files(folder)
        errors: list[str] = []
        if not folder.is_dir():
            errors.append("폴더 없음")
        for path in files:
            try:
                with Image.open(path) as image:
                    size = image.size
            except OSError:
                errors.append(f"읽기 실패: {path.name}")
                continue
            valid = True
            if category in {"ground", "road", "wall", "half_wall"}:
                valid = size == (256, 256)
            elif category == "door":
                valid = size in {(64, 32), (32, 64)}
            elif category == "decoration":
                valid = (
                    size[0] % TILE_SIZE == 0
                    and size[1] % TILE_SIZE == 0
                    and 0 < size[0] <= 256
                    and 0 < size[1] <= 256
                )
            else:
                valid = size == (32, 32)
            if not valid:
                errors.append(f"{path.name} {size[0]}×{size[1]}")
        result.append({
            "category": category,
            "label": requirement["label"],
            "required_size": requirement["size"],
            "count": len(files),
            "errors": errors,
            "ok": not errors,
        })
    return result


def _fit_texture(image: Image.Image) -> Image.Image:
    return image.convert("RGBA").resize((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS)


def _wall_mask(edge: str) -> Image.Image:
    mask = Image.new("L", (TILE_SIZE, TILE_SIZE), 0)
    draw = ImageDraw.Draw(mask)
    thickness = WALL_THICKNESS_PX
    edge = edge.lower()
    if "north" in edge:
        draw.rectangle((0, 0, TILE_SIZE - 1, thickness - 1), fill=255)
    if "south" in edge:
        draw.rectangle((0, TILE_SIZE - thickness, TILE_SIZE - 1, TILE_SIZE - 1), fill=255)
    if "west" in edge:
        draw.rectangle((0, 0, thickness - 1, TILE_SIZE - 1), fill=255)
    if "east" in edge:
        draw.rectangle((TILE_SIZE - thickness, 0, TILE_SIZE - 1, TILE_SIZE - 1), fill=255)
    return mask


def _masked_tile(texture: Image.Image, edges: str) -> Image.Image:
    tile = _fit_texture(texture)
    result = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
    result.paste(tile, (0, 0), _wall_mask(edges))
    return result


def _paste_tile(atlas: Image.Image, tile: Image.Image, column: int, row: int) -> None:
    atlas.alpha_composite(tile, (column * TILE_SIZE, row * TILE_SIZE))


def _place_single(
    atlas: Image.Image,
    manifest: list[dict[str, Any]],
    image: Image.Image,
    category: str,
    name: str,
    column: int,
    row: int,
    variant: str = "",
) -> None:
    gid = row * ATLAS_COLUMNS + column + 1
    _paste_tile(atlas, _fit_texture(image), column, row)
    manifest.append({
        "gid": gid,
        "category": category,
        "name": name,
        "variant": variant,
        "column": column,
        "row": row,
    })


def _pack_materials(
    atlas: Image.Image,
    manifest: list[dict[str, Any]],
    source_dir: Path,
    category: str,
    start_column: int,
    start_row: int,
    max_columns: int,
    max_rows: int,
) -> None:
    files = _files(source_dir / category)
    slot = 0
    for path in files:
        with Image.open(path) as image:
            source = image.convert("RGBA")
            for angle in (0, 90, 180, 270):
                column = start_column + slot % max_columns
                row = start_row + slot // max_columns
                if row >= start_row + max_rows:
                    raise ValueError(f"Too many {category} assets for its atlas region")
                rotated = source if angle == 0 else source.rotate(angle, expand=False)
                _place_single(atlas, manifest, rotated, category, path.stem, column, row, f"rotate_{angle}")
                slot += 1


def _pack_wall_family(
    atlas: Image.Image,
    manifest: list[dict[str, Any]],
    source_dir: Path,
    source_category: str,
    manifest_category: str,
    start_column: int,
    start_row: int,
    max_columns: int,
    max_rows: int,
    start_slot: int = 0,
) -> int:
    files = _files(source_dir / source_category)
    edge_variants = (
        "north", "east", "south", "west",
        "north_east", "east_south", "south_west", "west_north",
        "north_east_south", "east_south_west",
        "south_west_north", "west_north_east",
    )
    slot = start_slot
    for path in files:
        with Image.open(path) as image:
            for variant in edge_variants:
                column = start_column + slot % max_columns
                row = start_row + slot // max_columns
                if row >= start_row + max_rows:
                    raise ValueError(f"Too many {manifest_category} wall variants for its atlas region")
                gid = row * ATLAS_COLUMNS + column + 1
                _paste_tile(atlas, _masked_tile(image, variant), column, row)
                manifest.append({
                    "gid": gid,
                    "category": manifest_category,
                    "wall_type": manifest_category,
                    "name": path.stem,
                    "variant": variant,
                    "wall_thickness_px": WALL_THICKNESS_PX,
                    "column": column,
                    "row": row,
                })
                slot += 1
    return slot


def _pack_decorations(
    atlas: Image.Image,
    manifest: list[dict[str, Any]],
    source_dir: Path,
) -> None:
    files = _files(source_dir / "decoration")
    start_column, start_row, max_columns, max_rows = REGIONS["decoration"]
    for index, path in enumerate(files):
        column = start_column + index % max_columns
        row = start_row + index // max_columns
        if row >= start_row + max_rows:
            raise ValueError("Too many decoration assets for the atlas region")
        with Image.open(path) as image:
            _place_single(atlas, manifest, image, "decoration", path.stem, column, row)


def _copy_entity_assets(source_dir: Path, output_dir: Path) -> Path:
    entity_dir = output_dir / "entities"
    entity_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    for category in ("door", "window", "decoration"):
        for path in _files(source_dir / category):
            target = entity_dir / f"{category}_{path.name}"
            target.write_bytes(path.read_bytes())
            with Image.open(path) as image:
                width_tiles = max(1, round(image.width / TILE_SIZE))
                height_tiles = max(1, round(image.height / TILE_SIZE))
            assets.append({
                "asset_id": f"{category}_{path.stem}",
                "category": category,
                "file": f"entities/{target.name}",
                "width_tiles": width_tiles,
                "height_tiles": height_tiles,
                "recommended_span_tiles": 2 if category == "door" else 1,
            })
    manifest_path = output_dir / "entities.manifest.json"
    manifest_path.write_text(
        json.dumps({"format": "revenant_lost.entities", "assets": assets}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_asset_atlas(source_dir: Path, output_dir: Path, name: str = "revenant_tileset") -> dict[str, Path]:
    """Pack individual source images into a fixed 2048x2048 Tiled atlas."""
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)
    inspection = inspect_source_assets(source_dir)
    errors = [
        f'{item["category"]}: {", ".join(item["errors"])}'
        for item in inspection
        if item["errors"]
    ]
    if errors:
        raise ValueError("원본 자산 규격을 확인하세요.\n" + "\n".join(errors))
    output_dir.mkdir(parents=True, exist_ok=True)
    atlas = Image.new("RGBA", (ATLAS_SIZE, ATLAS_SIZE), (0, 0, 0, 0))
    manifest: list[dict[str, Any]] = []

    _pack_materials(atlas, manifest, source_dir, "ground", 0, 0, 32, 16)
    _pack_materials(atlas, manifest, source_dir, "road", 32, 0, 32, 16)
    wall_slot = _pack_wall_family(atlas, manifest, source_dir, "wall", "wall", 0, 16, 32, 16)
    _pack_wall_family(atlas, manifest, source_dir, "half_wall", "half_wall", 0, 16, 32, 16, wall_slot)

    image_path = output_dir / f"{name}.png"
    manifest_path = output_dir / f"{name}.manifest.json"
    tsx_path = output_dir / f"{name}.tsx"
    entities_manifest_path = _copy_entity_assets(source_dir, output_dir)
    atlas.save(image_path, format="PNG", optimize=True)
    manifest_data = {
        "format": "revenant_lost.tileset",
        "format_version": 1,
        "tile_size_px": TILE_SIZE,
        "tile_size_m": TILE_SIZE / PIXELS_PER_METER,
        "pixels_per_meter": PIXELS_PER_METER,
        "atlas_size_px": ATLAS_SIZE,
        "regions": REGIONS,
        "wall_thickness_px": WALL_THICKNESS_PX,
        "wall_thickness_m": WALL_THICKNESS_PX / PIXELS_PER_METER,
        "tiles": manifest,
    }
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tsx_path.write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>\n'''
        f'''<tileset version="1.10" tiledversion="1.12.2" name="{name}" tilewidth="{TILE_SIZE}" tileheight="{TILE_SIZE}" tilecount="{ATLAS_COLUMNS * ATLAS_COLUMNS}" columns="{ATLAS_COLUMNS}">\n'''
        f''' <image source="{image_path.name}" width="{ATLAS_SIZE}" height="{ATLAS_SIZE}"/>\n'''
        f'''</tileset>\n''',
        encoding="utf-8",
    )
    return {"image": image_path, "manifest": manifest_path, "tsx": tsx_path, "entities": entities_manifest_path}
