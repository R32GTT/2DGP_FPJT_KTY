from dataclasses import asdict
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campaign.models import Position
from campaign.resources import assert_invariants
from campaign.routes import find_route
from prototype.actors import Actor
from prototype.generated_world import generate_world
from prototype.movement import move_actor
from prototype.savegame import load_game, save_game
from prototype.simulation import PlaySimulation
from prototype.world import World
from prototype.site_planning import SAFEHOUSE_FACILITY_RADIUS
from shapely import LineString, Point, box


def walk(actor, world, points):
    for goal in points:
        for _ in range(math.ceil(math.dist(actor.xy, goal) / .3) + 5):
            world.update_chunks(actor.xy)
            distance = math.dist(actor.xy, goal)
            if distance < .02:
                break
            move_actor(actor, (goal[0] - actor.position.x, goal[1] - actor.position.y), world,
                       min(distance, .3) / 3, speed=3)
        if math.dist(actor.xy, goal) >= .02:
            raise AssertionError(f'실제 보행 경로 막힘: {actor.xy} -> {goal}')


class BranchWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=4, region_sizes=['small', 'medium', 'small', 'small'],
                                   region_importances=['normal', 'core', 'normal', 'normal'])

    def test_exhausted_candidates_complete_a_walkable_world_with_the_original_seed(self):
        with patch('prototype.world_roads.build_roads', side_effect=ValueError('진입로 후보 없음')):
            scene = generate_world(43, region_count=2, region_sizes=['small', 'medium'],
                                   region_importances=['core', 'important'])
        self.assertEqual(scene['generation']['seed'], 43)
        self.assertTrue(scene['generation']['basic'])
        self.assertEqual([row['bounds'][2:] for row in scene['generated_regions']], [[160, 160], [256, 256]])
        self.assertEqual(scene, generate_world(**json.loads(json.dumps(scene['generation']))))
        play = PlaySimulation(scene)
        state = play.campaign.simulation.state
        safehouse = next(site for site in play.world.facilities if site['kind'] == 'safehouse')
        self.assertEqual(play.player.xy, tuple(safehouse['position']))
        home = safehouse['service_node']
        for index in (1, 2):
            self.assertEqual({site['kind'] for site in scene['planned_facilities'] if site['region_id'] == f'region/{index}'},
                             {'safehouse', 'hq', 'warehouse', 'comms'})
        for target in play.world.facilities:
            route = find_route(state, home, target['service_node'])
            self.assertTrue(route)
            actor = Actor('route_check', Position(*play.world.safehouse))
            walk(actor, play.world, [play.world.navigation.nodes[key] for key in route])

    def test_seed_rebuilds_global_roads_that_cross_region_boundaries(self):
        scene = self.scene
        self.assertEqual(scene, generate_world(**json.loads(json.dumps(scene['generation']))))
        self.assertFalse(scene['generation'].get('basic'))
        self.assertEqual({key for key in scene['road_network']['terminals'] if '/entry/' in key}, set())
        regions = scene['generated_regions']
        self.assertTrue(all(region['origin'][0] and region['origin'][1] for region in regions[1:]))
        self.assertEqual(regions[1]['region_size'], 'medium')
        self.assertEqual(regions[1]['importance'], 'core')
        main_roads = [LineString([road['start'], road['end']]) for road in scene['roads']
                      if road['kind'] == 'main']
        for region in regions:
            x, y, w, h = region['bounds']
            self.assertTrue(any(road.crosses(box(x, y, x + w, y + h)) for road in main_roads))
        for safehouse in (site for site in scene['planned_facilities'] if site['kind'] == 'safehouse'):
            for site in scene['planned_facilities']:
                if site['id'] != safehouse['id']:
                    x, y, w, h = site['rect']
                    self.assertGreaterEqual(box(x, y, x + w, y + h).distance(Point(safehouse['position'])),
                                            SAFEHOUSE_FACILITY_RADIUS)

    def test_player_walks_every_branch_and_returns_with_same_campaign_and_losses(self):
        play = PlaySimulation(self.scene)
        campaign = play.campaign.simulation
        casualty = next(actor for actor in play.guards if '/hq/' in actor.id)
        casualty.hp = 0
        play.events.emit('killed', play.time, casualty)
        play._dispatch()
        unit = campaign.state.units[casualty.source_unit]
        remaining = unit.soldiers
        home = next(site['service_node'] for site in play.world.facilities if site['kind'] == 'safehouse')
        current = home
        walk(play.player, play.world, [(campaign.state.nodes[home].position.x, campaign.state.nodes[home].position.y)])
        for index in range(2, 5):
            target = next(f.node_id for f in campaign.state.facilities.values()
                          if f.region_id == f'region/{index}' and 'command' in f.capabilities)
            path = find_route(campaign.state, current, target)
            self.assertTrue(path)
            walk(play.player, play.world, [(campaign.state.nodes[key].position.x, campaign.state.nodes[key].position.y) for key in path])
            play._sync_guards()
            self.assertEqual(play.world.territory_at(play.player.xy), f'region/{index}')
            current = target
        path = find_route(campaign.state, current, home)
        walk(play.player, play.world, [(campaign.state.nodes[key].position.x, campaign.state.nodes[key].position.y) for key in path])
        play._sync_guards()
        self.assertIs(play.campaign.simulation, campaign)
        self.assertEqual(play.world.territory_at(play.player.xy), 'region/1')
        self.assertEqual(unit.soldiers, remaining)
        self.assertIs(play.roster.by_id[casualty.id], casualty)
        self.assertEqual(casualty.hp, 0)

    def test_sighting_qrf_cross_branch_delivery_and_save_continue(self):
        play = PlaySimulation(self.scene)
        witness = next(actor for actor in play.guards if '/hq/' in actor.id)
        play.player.position = Position(witness.position.x + math.cos(witness.facing) * 4,
                                        witness.position.y + math.sin(witness.facing) * 4)
        for frame in range(1, 49):
            play.time = frame / 60
            play.ai.update(play.guards, play.player, play.time)
        qrf, = [op for op in play.campaign.simulation.state.operations.values() if op.kind == 'qrf']
        play.player.position = Position(*play.world.safehouse)
        play._sync_guards()
        play.campaign.advance_to(40)
        play.time = 40.
        play._sync_guards()
        self.assertEqual(qrf.phase, 'outbound')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'branches.json.gz'
            save_game(play, path)
            self.assertLess(path.stat().st_size, 40_000)
            restored = load_game(path)
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))
        self.assertEqual(play.world.data['generation'], restored.world.data['generation'])
        for simulation in (play, restored):
            simulation.roster.materialized = False
            simulation._sync_guards()
            simulation.campaign.advance_to(600)
            state = simulation.campaign.simulation.state
            self.assertEqual(state.operations[qrf.id].phase, 'completed')
            self.assertTrue(any(op.kind == 'supply' and op.delivered_at is not None and op.origin_id in state.facilities
                                and state.facilities[op.origin_id].region_id != state.facilities[op.destination_id].region_id
                                for op in state.operations.values()))
            assert_invariants(state)
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))

    def test_navigation_can_walk_around_a_tree_between_regions(self):
        world = World(self.scene)
        point = next(tuple((a + b) / 2 for a, b in zip(road['start'], road['end']))
                     for road in self.scene['roads'] if road['kind'] == 'main'
                     and any(world.territory_at(road[end]) for end in ('start', 'end'))
                     and world.territory_at(road['start']) != world.territory_at(road['end']))
        world.update_chunks(point)
        for tree in (c for c in list(world.geometry.objects.values()) if c.id.startswith('terrain/') and c.kind == 'tree'
                     and world.territory_at((c.rect.x, c.rect.y)) is None):
            x, y = tree.rect.x + tree.rect.w / 2, tree.rect.y + tree.rect.h / 2
            start, goal = (x - 1.2, y), (x + 1.2, y)
            if not world.navigation.walkable(start) or not world.navigation.walkable(goal):
                continue
            path = world.navigation.route(start, goal)
            if not path:
                continue
            self.assertGreater(len(path), 1)
            actor = Actor('forest_walker', Position(*start))
            walk(actor, world, path)
            return
        self.fail('지역 사이 숲에서 실제 우회 보행 경로를 찾지 못함')
