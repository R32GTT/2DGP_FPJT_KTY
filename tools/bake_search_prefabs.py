"""실행: python tools/bake_search_prefabs.py"""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prototype.search_bake import bake_definition  # noqa: E402


def main():
    directory = ROOT / "prototype/data/prefabs"
    output = directory / "search_bakes"
    output.mkdir(exist_ok=True)
    for path in sorted(directory.glob("*.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))
        bake = bake_definition(definition)
        if bake is None:
            continue
        target = output / path.name
        target.write_text(json.dumps(bake, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(bake['points'])} points")


if __name__ == "__main__":
    main()
