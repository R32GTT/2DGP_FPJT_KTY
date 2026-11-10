"""Run the generated-world gameplay slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from worldgen import generate_world


def _default_facilities() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "assets" / "modules" / "TestFab1.tmj",
        root / "modules" / "facilities" / "small_facility_01.tmj",
    ]
    return [path for path in candidates if path.exists()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gameplay")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--width", type=float, default=128.0)
    parser.add_argument("--height", type=float, default=128.0)
    parser.add_argument("--facility-count", type=int, default=2)
    parser.add_argument("--no-play", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = _default_facilities()
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
    if world.validation is not None and not world.validation.ok:
        for error in world.validation.errors:
            print(f"validation: {error}", file=sys.stderr)
    if args.no_play:
        return 0

    from .view import run_game

    return run_game(world)


if __name__ == "__main__":
    raise SystemExit(main())
