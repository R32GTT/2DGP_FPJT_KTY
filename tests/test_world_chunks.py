import math
from pathlib import Path
import tempfile
from threading import current_thread, main_thread
import unittest
from unittest.mock import patch

from campaign.models import Position
from campaign.resources import assert_invariants
from prototype.chunk_terrain import ChunkTerrain, CHUNK_SIZE
from prototype.generated_world import generate_world
from prototype.geometry import Rect
from prototype.savegame import save_game, load_game
from prototype.simulation import PlaySimulation


class WorldChunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=4)

    def test_neighbor_generation_order_has_no_duplicates_or_spacing_seams(self):
        terrain = ChunkTerrain(self.scene)
        point = self.scene['player_spawn']
        key = tuple(math.floor(value / CHUNK_SIZE) for value in point)
        keys = [key, (key[0] + 1, key[1])]
        forward = {key: terrain.generate(key) for key in keys}
        other = ChunkTerrain(self.scene)
        self.assertEqual(forward, {key: other.generate(key) for key in reversed(keys)})
        rows = [row for _, colliders in forward.values() for row in colliders]
        self.assertTrue(rows)
        self.assertEqual(len(rows), len({row['id'] for row in rows}))
        for first in forward[keys[0]][1]:
            for second in forward[keys[1]][1]:
                a, b = Rect(*first['rect']), Rect(*second['rect'])
                self.assertFalse(a.expanded(.75).overlaps(b))

    def test_prefetched_indexes_and_disk_chunks_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            play = PlaySimulation(self.scene, world_root=directory)
            world, chunks = play.world, play.world.chunks
            y = play.player.xy[1]
            west, east = (63.9, y), (64.1, y)
            world.update_chunks(west)
            original_tree = world.geometry.index.tree
            chunks.start_prefetch()
            try:
                world.update_chunks(west)
                self.assertIsNotNone(chunks.pending)
                chunks.pending[1].result(timeout=20)
                prepare = chunks._prepare

                def background_only(*args):
                    self.assertIsNot(current_thread(), main_thread(), "prepared window expected")
                    return prepare(*args)

                with patch.object(chunks, "_prepare", side_effect=background_only):
                    world.update_chunks(east)
                    self.assertIsNotNone(world.geometry.index.tree)
                    self.assertIs(world.geometry.index.tree, world.collider_index.tree)
                    self.assertIsNot(world.geometry.index.tree, original_tree)
                    world.update_chunks(west)
                    self.assertIs(world.geometry.index.tree, original_tree)
            finally:
                chunks.close()

            with patch.object(ChunkTerrain, "generate", side_effect=AssertionError("disk cache expected")):
                restored = PlaySimulation(self.scene, world_root=directory)
                restored.world.update_chunks(east)
            world.update_chunks(east)
            self.assertEqual(world.chunks.loaded, restored.world.chunks.loaded)
            self.assertEqual(world.geometry.objects, restored.world.geometry.objects)

    def test_unloaded_changes_save_and_distant_campaign_continue(self):
        play = PlaySimulation(self.scene)
        world, home = play.world, play.player.xy
        self.assertLessEqual(len(world.chunks.loaded), 25)
        far = max(self.scene['generated_regions'], key=lambda row: math.dist(home, row['origin']))['origin']
        self.assertFalse(world.detail_loaded(far))
        self.assertFalse(any(row.get('kind') in {'tree', 'rock'} for row in world.data['colliders']))
        door = min((c for c in world.geometry.objects.values() if c.kind == 'door'),
                   key=lambda c: math.dist(home, (c.rect.x, c.rect.y)))
        tree = min((c for c in world.geometry.objects.values() if c.kind == 'tree'),
                   key=lambda c: math.dist(home, (c.rect.x, c.rect.y)))
        original = tree.rect
        door.active = tree.active = False
        dropped = (play.player.xy, play.player.loadout.copy())
        play.dropped.append(dropped)
        casualty = next(actor for actor in play.guards if '/hq/' in actor.id)
        casualty.hp = 0
        play.events.emit('killed', play.time, casualty)
        play._dispatch()
        remaining = play.campaign.simulation.state.units[casualty.source_unit].soldiers
        old_chunks = set(world.chunks.loaded)
        play.player.position = Position(*far)
        play._sync_guards()
        self.assertFalse(tree.id in world.geometry.objects)
        self.assertFalse(door.id in world.geometry.objects)
        self.assertTrue(old_chunks - world.chunks.loaded.keys())
        play.roster.materialized = False
        play._sync_guards()
        play.campaign.advance_to(600)
        play.time = 600.
        state = play.campaign.simulation.state
        self.assertTrue(any(op.kind == 'supply' and op.delivered_at is not None for op in state.operations.values()))
        assert_invariants(state)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chunks.gz'
            save_game(play, path)
            self.assertLess(path.stat().st_size, 40_000)
            restored = load_game(path)
        self.assertEqual(restored.player.xy, tuple(far))
        self.assertFalse(restored.world.detail_loaded(home))
        for current in (play, restored):
            current.player.position = Position(*home)
            current.roster.materialized = True
            current._sync_guards()
            self.assertEqual(current.world.geometry.objects[tree.id].rect, original)
            self.assertFalse(current.world.geometry.objects[tree.id].active)
            self.assertFalse(current.world.geometry.objects[door.id].active)
            self.assertEqual(current.roster.by_id[casualty.id].hp, 0)
            self.assertEqual(current.campaign.simulation.state.units[casualty.source_unit].soldiers, remaining)
            self.assertEqual(current.dropped[0][0], dropped[0])
            self.assertLessEqual(len(current.world.chunks.loaded), 25)
            self.assertEqual(len(current.world.layers), len({layer.id for layer in current.world.layers}))
        self.assertIs(play.roster.by_id[casualty.id], casualty)
