from dataclasses import asdict
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from campaign.persistence import deserialize_campaign, serialize_campaign
from campaign.resources import assert_invariants
from campaign.routes import find_route
from campaign.simulation import Simulation
from prototype import generated_world
from prototype.generated_world import generate_world, region_seed
from prototype.map_navigation import MapNavigation, RESPAWN_SIDEBAR
from prototype.movement import move_actor
from prototype.respawn_ui import hit_respawn_map, page_rects, row_rect
from prototype.simulation import PlaySimulation
from shapely import Point, box
from prototype.site_planning import SAFEHOUSE_FACILITY_RADIUS


class GeneratedWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def test_region_ids_and_seeds_rebuild_from_world_settings(self):
        scene = self.scene
        self.assertEqual(generate_world(**json.loads(json.dumps(scene["generation"]))), scene)
        for row in scene["generated_regions"]:
            self.assertEqual(row["seed"], region_seed(42, row["id"]))
        ids = [site["id"] for site in scene["planned_facilities"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotEqual(*[row["seed"] for row in scene["generated_regions"]])
        single = generate_world(42)
        self.assertEqual(single['generated_regions'][0]['seed'], scene['generated_regions'][0]['seed'])

    def test_new_world_reuses_generated_scene_and_recovers_cache(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scene = generate_world(42, region_count=2, world_root=root)
            with patch("prototype.generated_world._assemble_world", side_effect=AssertionError("cached scene expected")):
                self.assertEqual(generate_world(42, region_count=2, world_root=root), scene)
            cached, = root.glob("*/scene.json.gz")
            cached.write_bytes(b"broken")
            self.assertEqual(generate_world(42, region_count=2, world_root=root), scene)
            with patch("prototype.generated_world._assemble_world", side_effect=AssertionError("repaired scene expected")):
                self.assertEqual(generate_world(42, region_count=2, world_root=root), scene)

    def test_xlarge_roads_avoid_facilities_on_first_layout(self):
        with patch("prototype.generated_world._assemble_world", wraps=generated_world._assemble_world) as assemble:
            scene = generate_world(42, region_count=16, region_size="xlarge")
        self.assertEqual(assemble.call_count, 1)
        self.assertEqual(len(scene["generated_regions"]), 16)

    def test_respawn_selects_a_safehouse_on_the_second_page(self):
        play = PlaySimulation(generate_world(42, region_count=5, basic=True))
        sites = list(play.respawn_sites)
        play.player.hp = 0
        play.request_respawn()
        self.assertEqual(play.respawn_pages, 2)
        _, next_page = page_rects((1280, 720))
        page_click = ((next_page[0] + next_page[2]) // 2,
                      (next_page[1] + next_page[3]) // 2)
        self.assertEqual(hit_respawn_map(play, MapNavigation(), (1280, 720), page_click), ("page", 1))
        play.change_respawn_page(10)
        self.assertEqual(play.respawn_page, 1)
        play.choose_respawn_slot(3)
        self.assertIsNone(play.respawn_target)
        self.assertIsNone(play.respawn_selected_id)
        play.choose_respawn_slot(0)
        self.assertEqual(play.respawn_selected_id, sites[4])
        self.assertIsNone(play.respawn_target)
        self.assertTrue(play.respawn_selecting)
        self.assertTrue(play.confirm_respawn())
        self.assertEqual(play.respawn_site_id, sites[4])
        self.assertFalse(play.respawn_selecting)
        play.respawn_target = play.campaign.simulation.state.world_time + 1
        play.step()
        self.assertEqual(play.player.xy, tuple(play.respawn_sites[sites[4]]["position"]))
        self.assertEqual(play.player.hp, 100)

    def test_single_safehouse_requires_mouse_selection_then_confirmation(self):
        play = PlaySimulation(generate_world(42, region_count=1, basic=True))
        play.player.hp = 0
        play.request_respawn()
        self.assertTrue(play.respawn_selecting)
        self.assertFalse(play.confirm_respawn())
        self.assertIsNone(play.respawn_target)

        site = next(iter(play.respawn_sites.values()))
        navigation = MapNavigation()
        size = (1280, 720)
        projection = navigation.transform(play.world.bounds, size, sidebar=RESPAWN_SIDEBAR)
        marker = tuple(round(value) for value in projection.screen(*site["position"]))
        self.assertEqual(hit_respawn_map(play, navigation, size, marker), ("site", site["id"]))
        navigation.zoom_at(2, marker, size, sidebar=RESPAWN_SIDEBAR)
        navigation.move(25, -18)
        zoomed = navigation.transform(play.world.bounds, size, sidebar=RESPAWN_SIDEBAR)
        zoomed_marker = tuple(round(value) for value in zoomed.screen(*site["position"]))
        self.assertEqual(hit_respawn_map(play, navigation, size, zoomed_marker), ("site", site["id"]))
        left, top, right, bottom = row_rect(size, 0)
        self.assertEqual(hit_respawn_map(play, navigation, size,
                                         ((left + right) // 2, (top + bottom) // 2)),
                         ("site", site["id"]))
        self.assertTrue(play.select_respawn_site(site["id"]))
        self.assertIsNone(play.respawn_target)
        self.assertTrue(play.confirm_respawn())
        self.assertEqual(play.respawn_site_id, site["id"])

    def test_player_starts_in_the_first_regions_safehouse(self):
        play = PlaySimulation(self.scene)
        safehouses = [site for site in play.world.facilities if site['kind'] == 'safehouse']
        self.assertEqual(len(safehouses), 2)
        self.assertEqual(play.player.xy, tuple(safehouses[0]['position']))
        self.assertNotEqual(play.player.xy, tuple(safehouses[1]['position']))
        self.assertEqual(play.world.territory_at(play.player.xy), 'region/1')

    def test_safehouses_keep_other_facilities_outside_their_radius_across_regions(self):
        for scene in (self.scene, generate_world(43, region_count=2, importance='core')):
            play = PlaySimulation(scene)
            for safehouse in (site for site in play.world.facilities if site['kind'] == 'safehouse'):
                center = Point(safehouse['position'])
                for site in play.world.facilities:
                    if site['id'] == safehouse['id']:
                        continue
                    x, y, w, h = site['rect']
                    self.assertGreaterEqual(box(x, y, x + w, y + h).distance(center), SAFEHOUSE_FACILITY_RADIUS)

    def test_player_crosses_territories_and_returns_without_resetting_campaign(self):
        play = PlaySimulation(self.scene)
        campaign, actor = play.campaign.simulation, play.player
        state = campaign.state
        hq = next(f for f in state.facilities.values() if f.region_id == "region/2" and "command" in f.capabilities)
        unit = next(unit for unit in state.units.values() if unit.node_id == hq.node_id)
        campaign.apply_action({"type": "damage_unit", "unit_id": unit.id, "soldiers": 1})
        spawn = next(site['service_node'] for site in play.world.facilities if site['kind'] == 'safehouse')
        path = find_route(state, spawn, hq.node_id)
        self.assertTrue(path)
        self.assertEqual(path[0], spawn)
        self.assertEqual(path[-1], hq.node_id)

        def walk(keys):
            for key in keys:
                node = state.nodes[key].position
                goal = node.x, node.y
                for _ in range(math.ceil(math.dist(actor.xy, goal) / 0.3) + 5):
                    distance = math.dist(actor.xy, goal)
                    if distance < 0.02:
                        break
                    move_actor(actor, (goal[0] - actor.position.x, goal[1] - actor.position.y), play.world,
                               min(distance, 0.3) / 3, speed=3)
                self.assertLess(math.dist(actor.xy, goal), 0.02)

        walk(path)
        play._sync_guards()
        self.assertEqual(play.world.territory_at(actor.xy), "region/2")
        walk(reversed(path))
        play._sync_guards()
        self.assertEqual(play.world.territory_at(actor.xy), "region/1")
        self.assertIs(play.campaign.simulation, campaign)
        self.assertEqual(unit.soldiers, 9)
        self.assertEqual(state.world_time, 0)

    def test_autonomous_cross_region_delivery_and_save_resume_preserve_resources(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        initial_player = play.player.xy
        depots = [f for f in state.facilities.values() if "logistics" in f.capabilities]
        self.assertIsNot(depots[0].inventory, depots[1].inventory)
        play.campaign.advance_to(600)
        delivered = [op for op in state.operations.values() if op.kind == "supply" and op.delivered_at is not None
                     and op.origin_id in state.facilities
                     and state.facilities[op.origin_id].region_id != state.facilities[op.destination_id].region_id]
        self.assertTrue(delivered)
        self.assertEqual(play.player.xy, initial_player)
        restored = Simulation(deserialize_campaign(serialize_campaign(state)))
        play.campaign.advance_to(720)
        restored.advance_to(720)
        self.assertEqual(asdict(state), asdict(restored.state))
        assert_invariants(state)
