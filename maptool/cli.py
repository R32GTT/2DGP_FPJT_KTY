"""Command line entry point for the Tiled-to-runtime map pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from .assets import build_asset_atlas
from .compiler import compile_module, create_template_map, write_ai_package, write_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m maptool",
        description="Compile a Tiled module into Revenant: L.O.S.T runtime data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser(
        "compile",
        help="Compile a Tiled .tmj map and create runtime/AI-readable outputs.",
    )
    compile_parser.add_argument("map", type=Path, help="Tiled .tmj map file")
    compile_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: <map directory>/compiled/<map name>)",
    )
    compile_parser.add_argument(
        "--copy-source",
        action="store_true",
        help="Copy the source .tmj into the AI package",
    )

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Print a short summary of a compiled runtime JSON file.",
    )
    inspect_parser.add_argument("runtime", type=Path, help="Runtime JSON file")

    edit_parser = subparsers.add_parser(
        "edit",
        help="Open the user-facing module editor.",
    )
    edit_parser.add_argument("map", type=Path, nargs="?", help="Optional .tmj map to open")

    init_parser = subparsers.add_parser(
        "init",
        help="Create a blank Tiled module with the standard layer layout.",
    )
    init_parser.add_argument("name", help="Module file/name, without .tmj")
    init_parser.add_argument(
        "--output",
        type=Path,
        default=Path("maptool_workspace"),
        help="Directory for the new module (default: maptool_workspace)",
    )
    init_parser.add_argument("--type", default="module", help="module or facility")
    init_parser.add_argument("--variant", default=None, help="variant_id (default: module name)")
    init_parser.add_argument("--facility-type", default="generic", help="HQ, communication, supply, garrison, or anti_air")
    init_parser.add_argument("--region-sizes", default="small,medium,large,extra_large")
    init_parser.add_argument("--importance-tiers", default="general,important,core")
    init_parser.add_argument("--supports", default="", help="Comma-separated facility effects")
    init_parser.add_argument("--vehicle-entry", action="store_true")
    init_parser.add_argument("--no-pedestrian-entry", action="store_true")
    init_parser.add_argument("--watchposts", type=int, default=0)
    init_parser.add_argument("--width", type=int, default=32, help="Map width in 0.5m tiles")
    init_parser.add_argument("--height", type=int, default=24, help="Map height in 0.5m tiles")

    assets_parser = subparsers.add_parser(
        "pack-assets",
        help="Build a deterministic 32px/2048px Tiled atlas from source assets.",
    )
    assets_parser.add_argument("source", type=Path, help="Source asset directory")
    assets_parser.add_argument("--output", type=Path, default=Path("assets/generated"))
    assets_parser.add_argument("--name", default="revenant_tileset")
    return parser


def command_compile(args: argparse.Namespace) -> int:
    source = args.map.resolve()
    if not source.is_file():
        print(f"Map file not found: {source}", file=sys.stderr)
        return 2

    output = args.output
    if output is None:
        output = source.parent / "compiled" / source.stem
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    runtime = compile_module(source)
    runtime_path = output / f"{source.stem}.runtime.json"
    write_runtime(runtime, runtime_path)
    ai_dir = output / "ai_package"
    write_ai_package(runtime, ai_dir, source=source if args.copy_source else None)

    print(f"Compiled: {source}")
    print(f"Runtime : {runtime_path}")
    print(f"AI pack : {ai_dir}")
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    try:
        data = json.loads(args.runtime.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read runtime JSON: {exc}", file=sys.stderr)
        return 2

    module = data.get("module", {})
    print(f"Module: {module.get('id', args.runtime.stem)}")
    print(f"Type:   {module.get('type', 'unknown')}")
    print(f"Size:   {data.get('bounds', {}).get('width_m', 0)}m x "
          f"{data.get('bounds', {}).get('height_m', 0)}m")
    for label, key in (
        ("Connectors", "connectors"),
        ("Patrol routes", "patrol_routes"),
        ("Enemy spawns", "enemy_spawns"),
        ("Cover points", "cover_points"),
        ("Doors", "doors"),
    ):
        value = data.get("counts", {}).get(key, 0)
        print(f"{label}: {value}")
    return 0


def command_init(args: argparse.Namespace) -> int:
    try:
        destination = create_template_map(
            name=args.name,
            output_dir=args.output,
            module_type=args.type,
            variant_id=args.variant,
            facility_type=args.facility_type,
            allowed_region_sizes=[item.strip() for item in args.region_sizes.split(",") if item.strip()],
            allowed_importance_tiers=[item.strip() for item in args.importance_tiers.split(",") if item.strip()],
            supports=[item.strip() for item in args.supports.split(",") if item.strip()],
            has_vehicle_entry=args.vehicle_entry,
            has_pedestrian_entry=not args.no_pedestrian_entry,
            attached_watchposts=args.watchposts,
            width=args.width,
            height=args.height,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Created: {destination}")
    print("Open the file in Tiled, add a tileset, then use the documented object classes.")
    return 0


def command_pack_assets(args: argparse.Namespace) -> int:
    try:
        outputs = build_asset_atlas(args.source, args.output, args.name)
    except (OSError, ValueError) as exc:
        print(f"Asset packing failed: {exc}", file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "compile":
        raise SystemExit(command_compile(args))
    if args.command == "inspect":
        raise SystemExit(command_inspect(args))
    if args.command == "init":
        raise SystemExit(command_init(args))
    if args.command == "edit":
        from .editor import launch
        launch(args.map)
    if args.command == "pack-assets":
        raise SystemExit(command_pack_assets(args))
    parser.error("Unknown command")
