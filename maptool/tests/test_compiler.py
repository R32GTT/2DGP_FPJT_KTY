import json
from pathlib import Path
import unittest

from maptool.compiler import compile_module


FIXTURE = Path(__file__).parent / "fixtures" / "sample_module.tmj"


class CompilerTests(unittest.TestCase):
    def test_compiles_module_metadata_and_geometry(self):
        result = compile_module(FIXTURE)
        self.assertEqual(result["module"]["id"], "sample_hq")
        self.assertEqual(result["module"]["type"], "facility")
        self.assertEqual(result["facility"]["facility_type"], "hq")
        self.assertEqual(result["facility"]["allowed_region_sizes"], ["medium", "large"])
        self.assertEqual(result["facility"]["allowed_importance_tiers"], ["important", "core"])
        self.assertEqual(result["facility"]["supports"], ["command", "communication"])
        self.assertTrue(result["facility"]["has_vehicle_entry"])
        self.assertEqual(result["facility"]["attached_watchposts"], 2)
        self.assertEqual(result["coordinate_system"]["tile_meters"], 0.5)
        self.assertEqual(result["counts"]["decoration_tiles"], 1)
        self.assertEqual(result["visual_layers"]["decoration"]["tiles"][9], 2)
        self.assertEqual(result["counts"]["wall_edges"], 1)
        self.assertEqual(result["navigation"]["grid"]["blocked_edges"][0]["edge"], "east")
        self.assertIn({"x": 1.3125, "y": 1.0, "width": 0.375, "height": 0.5}, result["geometry"]["collision_rects"])
        self.assertGreater(len(result["geometry"]["collision_rects"]), 0)
        self.assertEqual(result["counts"]["connectors"], 1)
        self.assertEqual(result["counts"]["enemy_spawns"], 1)
        self.assertEqual(result["counts"]["patrol_routes"], 1)
        self.assertEqual(result["counts"]["cover_points"], 1)
        self.assertEqual(result["counts"]["doors"], 1)
        door = next(item for item in result["interactions"] if item["kind"] == "door")
        self.assertEqual(door["interaction"]["access"], "free")
        self.assertEqual(door["interaction"]["open_angle"], 90.0)

    def test_output_is_json_serializable(self):
        result = compile_module(FIXTURE)
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
