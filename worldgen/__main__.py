"""Command line entry point for the world generator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .generation import generate_world
from .tmj_export import export_tmj


def _default_facilities() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "assets" / "modules" / "TestFab1.tmj",
        root / "modules" / "facilities" / "small_facility_01.tmj",
    ]
    return [path for path in candidates if path.exists()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m worldgen",
        description="Generate and preview a Revenant: L.O.S.T region.",
    )
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--width", type=float, default=128.0, help="region width in meters")
    parser.add_argument("--height", type=float, default=128.0, help="region height in meters")
    parser.add_argument(
        "--facility",
        type=Path,
        action="append",
        default=None,
        help="facility .tmj or compiled runtime .json (repeatable)",
    )
    parser.add_argument("--facility-count", type=int, default=2)
    parser.add_argument("--export", type=Path, default=None, help="write a Tiled .tmj")
    parser.add_argument("--no-play", action="store_true", help="generate and validate only")
    parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="do not return a failure code when validation fails",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = args.facility if args.facility else _default_facilities()

    try:
        world = generate_world(
            seed=args.seed,
            width_m=args.width,
            height_m=args.height,
            prefab_sources=sources or None,
            facility_count=args.facility_count,
        )
    except Exception as exc:
        print(f"world generation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(world.summary(), ensure_ascii=False, indent=2))
    if args.export is not None:
        exported = export_tmj(world, args.export)
        print(f"exported: {exported}")

    report = world.validation
    if report is not None and not report.ok:
        print("validation failed", file=sys.stderr)
        for error in report.errors:
            print(f"  - {error}", file=sys.stderr)
        if not args.allow_invalid:
            return 3

    if args.no_play:
        return 0

    from .demo import run_demo

    return run_demo(world)


if __name__ == "__main__":
    raise SystemExit(main())
