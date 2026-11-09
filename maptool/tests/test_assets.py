import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from maptool.assets import ATLAS_SIZE, build_asset_atlas


class AssetPackerTests(unittest.TestCase):
    def test_builds_fixed_atlas_and_wall_variants(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            output = Path(temporary) / "output"
            for category in ("ground", "road", "wall", "half_wall", "door", "window", "decoration"):
                (source / category).mkdir(parents=True)
            for category, color in (("ground", (50, 120, 50, 255)), ("road", (40, 40, 40, 255)), ("wall", (100, 70, 50, 255)), ("half_wall", (80, 90, 100, 255))):
                Image.new("RGBA", (256, 256), color).save(source / category / f"{category}.png")
            Image.new("RGBA", (64, 32), (20, 80, 120, 255)).save(source / "door" / "door.png")
            Image.new("RGBA", (32, 32), (20, 120, 180, 255)).save(source / "window" / "window.png")
            Image.new("RGBA", (32, 32), (180, 120, 40, 255)).save(source / "decoration" / "decoration.png")

            files = build_asset_atlas(source, output)
            with Image.open(files["image"]) as atlas:
                self.assertEqual(atlas.size, (ATLAS_SIZE, ATLAS_SIZE))
            manifest = json.loads(files["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["tile_size_px"], 32)
            self.assertEqual(manifest["wall_thickness_px"], 24)
            self.assertTrue(any(item["category"] == "wall" for item in manifest["tiles"]))
            self.assertTrue(any(item["category"] == "half_wall" for item in manifest["tiles"]))
            self.assertFalse(any(item["category"] == "decoration" for item in manifest["tiles"]))
            self.assertTrue(files["tsx"].is_file())
            entities = json.loads(files["entities"].read_text(encoding="utf-8"))
            self.assertEqual({item["category"] for item in entities["assets"]}, {"door", "window", "decoration"})


if __name__ == "__main__":
    unittest.main()
