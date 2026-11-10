from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from worldgen.constants import (
    CELLS_PER_METER,
    Flag,
    PIXELS_PER_METER,
    PLAYER_COLLISION_RADIUS_M,
    PLAYER_DISPLAY_PX,
    PLAYER_SOURCE_SPRITE_PX,
    TILE_METERS,
    TILE_PIXELS,
)
from worldgen.facility import load_prefab
from worldgen.generation import generate_world
from worldgen.nav import find_world_path
from worldgen.tmj_export import export_tmj

ROOT = Path(__file__).resolve().parents[2]


class WorldgenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.world = generate_world(
            seed=2026,
            width_m=64.0,
            height_m=64.0,
            facility_count=1,
        )

    def test_scale_contract(self) -> None:
        self.assertEqual(PIXELS_PER_METER, 64)
        self.assertEqual(TILE_PIXELS, 32)
        self.assertEqual(TILE_METERS, 0.5)
        self.assertEqual(CELLS_PER_METER, 2)
        self.assertEqual(self.world.grid.width, 128)
        self.assertEqual(self.world.grid.height, 128)
        self.assertEqual(PLAYER_SOURCE_SPRITE_PX, (96, 48))
        self.assertEqual(PLAYER_DISPLAY_PX, (48, 24))
        self.assertEqual(PLAYER_COLLISION_RADIUS_M, 0.30)

    def test_generation_is_deterministic(self) -> None:
        other = generate_world(
            seed=2026,
            width_m=64.0,
            height_m=64.0,
            facility_count=1,
        )
        summary_a = self.world.summary()
        summary_b = other.summary()
        summary_a.pop("generation_ms", None)
        summary_b.pop("generation_ms", None)
        self.assertEqual(summary_a, summary_b)
        self.assertEqual(
            [(tree.x, tree.y, tree.canopy_radius_m) for tree in self.world.trees],
            [(tree.x, tree.y, tree.canopy_radius_m) for tree in other.trees],
        )
        self.assertEqual(
            [edge.points for edge in (self.world.roads.edges if self.world.roads else [])],
            [edge.points for edge in (other.roads.edges if other.roads else [])],
        )

    def test_validation_passes(self) -> None:
        self.assertIsNotNone(self.world.validation)
        assert self.world.validation is not None
        self.assertTrue(self.world.validation.ok, self.world.validation.errors)
        self.assertGreater(self.world.validation.reachable_cells, 0)
        self.assertAlmostEqual(
            self.world.validation.reachable_cells
            / max(1, self.world.validation.walkable_cells),
            1.0,
            places=4,
        )

    def test_roads_are_connected(self) -> None:
        self.assertIsNotNone(self.world.roads)
        assert self.world.roads is not None
        self.assertTrue(self.world.roads.connected())
        self.assertGreaterEqual(len(self.world.roads.edges), 1)

    def test_trees_do_not_block_roads(self) -> None:
        grid = self.world.grid
        for tree in self.world.trees:
            for cx, cy in tree.trunk_cells:
                self.assertFalse(
                    grid.get_flag(cx, cy, Flag.ROAD),
                    f"tree {tree.id} overlaps road at {(cx, cy)}",
                )

    def test_tree_canopy_does_not_overlap_road(self) -> None:
        grid = self.world.grid
        for tree in self.world.trees:
            cx0 = max(0, int((tree.x - tree.canopy_radius_m) / TILE_METERS))
            cx1 = min(grid.width - 1, int((tree.x + tree.canopy_radius_m) / TILE_METERS))
            cy0 = max(0, int((tree.y - tree.canopy_radius_m) / TILE_METERS))
            cy1 = min(grid.height - 1, int((tree.y + tree.canopy_radius_m) / TILE_METERS))
            for cy in range(cy0, cy1 + 1):
                for cx in range(cx0, cx1 + 1):
                    dx = (cx + 0.5) * TILE_METERS - tree.x
                    dy = (cy + 0.5) * TILE_METERS - tree.y
                    if dx * dx + dy * dy <= tree.canopy_radius_m ** 2:
                        self.assertFalse(
                            grid.get_flag(cx, cy, Flag.ROAD),
                            f"tree {tree.id} canopy overlaps road at {(cx, cy)}",
                        )

    def test_zones_are_broad_and_forest_density_blends(self) -> None:
        self.assertGreaterEqual(len(set(self.world.grid.zone)), 2)
        densities = self.world.grid.forest_density
        self.assertTrue(any(0 < value < 255 for value in densities))

    def test_generated_path_reaches_facility(self) -> None:
        self.assertTrue(self.world.facilities)
        entrance = self.world.facilities[0].entrance
        path = find_world_path(self.world, self.world.spawn, entrance)
        self.assertIsNotNone(path)
        self.assertGreater(len(path or []), 1)

    def test_prefab_loader_uses_compiled_runtime(self) -> None:
        prefab = load_prefab(ROOT / "modules" / "facilities" / "small_facility_01.tmj")
        self.assertGreater(prefab.width_m, 0)
        self.assertGreater(prefab.height_m, 0)
        self.assertTrue(prefab.nav_nodes)
        self.assertTrue(prefab.entrance_local)

    def test_tmj_export_is_tiled_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = export_tmj(self.world, Path(temp_dir) / "world.tmj")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["type"], "map")
            self.assertEqual(data["tilewidth"], 32)
            self.assertEqual(data["width"], self.world.grid.width)
            layer_names = {layer["name"] for layer in data["layers"]}
            self.assertTrue({"Ground", "Collision", "Occluder", "Objects"} <= layer_names)
            ground = next(layer for layer in data["layers"] if layer["name"] == "Ground")
            self.assertEqual(len(ground["data"]), self.world.grid.size)

            from maptool.compiler import compile_module

            runtime = compile_module(path)
            self.assertEqual(runtime["format"], "revenant_lost.module")
            self.assertGreater(runtime["counts"]["navigation_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
