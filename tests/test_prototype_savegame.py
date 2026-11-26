from dataclasses import asdict
import gzip
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from campaign.models import Position
from campaign.resources import assert_invariants
from prototype.generated_world import generate_world
from prototype.launcher import ROOT, build_command, discover_targets
from prototype.savegame import CampaignSaveError, load_game, save_game
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState


class PlaySaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def test_safehouse_stash_survives_save_and_respawn(self):
        play = PlaySimulation(self.scene)
        first, second = [site for site in play.world.facilities if site['kind'] == 'safehouse']
        play.player.loadout = {key: WeaponState.ready(play.specs[key]) for key in ('ar_a', 'ar_b')}
        play.player.active_weapon = 'ar_a'
        play.use_facility_alternate()
        self.assertEqual(play.safehouse_stash[first['id']].spec.id, 'ar_a')
        self.assertEqual(play.player.active_weapon, 'ar_b')

        stored = play.player.weapon
        stored.magazine = 5
        play.reload()
        stored.tick(stored.spec.reload_stages[0].duration)
        play.use_facility_alternate()
        self.assertEqual(play.player.active_weapon, 'ar_a')
        self.assertEqual(stored.stage, 1)
        self.assertFalse(stored.reloading)

        play.player.position = Position(0, 0)
        play.use_facility_alternate()
        self.assertIs(play.player.weapon, play.player.loadout['ar_a'])
        self.assertIs(play.safehouse_stash[first['id']], stored)
        play.player.position = Position(*second['terminal'])
        play.use_facility_alternate()
        self.assertIsNone(play.player.weapon)
        self.assertEqual(play.safehouse_stash[second['id']].spec.id, 'ar_a')

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            restored = load_game(path)
            restored.player.hp = 0
            restored.request_respawn()
            self.assertTrue(restored.respawn_selecting)
            self.assertIsNone(restored.respawn_target)
            restored.request_respawn(slot=1)
            self.assertFalse(restored.respawn_selecting)
            self.assertEqual(restored.respawn_target, 21600)
            save_game(restored, path)
            restored = load_game(path)
        self.assertEqual(restored.respawn_site_id, second['id'])
        for _ in range(360):
            restored.step()
        self.assertEqual(restored.player.xy, tuple(second['position']))
        self.assertEqual(restored.campaign.simulation.state.world_time, 21600)
        self.assertIsNone(restored.player.weapon)
        restored.use_facility_alternate()
        self.assertEqual(restored.player.weapon.spec.id, 'ar_a')
        self.assertNotIn(second['id'], restored.safehouse_stash)
        self.assertEqual(vars(restored.safehouse_stash[first['id']]), vars(stored))

    def test_warehouse_sabotage_destroys_stock_and_stops_dispatch(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        key = state.demand.collection_facility_id
        site = next(site for site in play.world.facilities if site['campaign_id'] == key)
        facility = state.facilities[key]
        scheduled = [op for op in state.operations.values() if op.origin_id == key and op.phase == 'loading']
        self.assertTrue(scheduled)

        play.player.position = Position(*site['terminal'])
        play.take_supplies()
        self.assertEqual(state.player_stock['supplies'], 10)
        remaining = facility.inventory.copy()
        play.use_facility_alternate()
        self.assertEqual(facility.damage, 25)
        self.assertEqual(facility.inventory, remaining)
        for _ in range(3):
            play.use_facility_alternate()
        self.assertEqual(facility.damage, 100)
        self.assertFalse(any(facility.inventory.values()))
        self.assertEqual(state.ledger.destroyed, remaining)
        self.assertTrue(all(op.phase == 'cancelled' for op in scheduled))
        play.take_supplies()
        play.use_facility_alternate()
        self.assertEqual(state.player_stock['supplies'], 10)
        self.assertEqual(state.ledger.destroyed, remaining)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            restored = load_game(path)
        campaign = restored.campaign.simulation
        campaign.advance_to(300)
        self.assertEqual(campaign.state.facilities[key].damage, 100)
        self.assertTrue(all(op.phase == 'cancelled' for op in campaign.state.operations.values()
                            if op.origin_id == key or op.destination_id == key))
        assert_invariants(campaign.state)

    def test_world_changes_losses_weapons_and_clock_survive_real_entry(self):
        play = PlaySimulation(self.scene)
        state = play.campaign.simulation.state
        casualty = next(actor for actor in play.guards if '/hq/' in actor.id)
        casualty.hp = 0
        play.events.emit('killed', play.time, casualty)
        play._dispatch()
        play._sync_guards()
        door = next(c for c in play.world.geometry.objects.values() if c.kind == 'door')
        play.world.toggle_door((door.rect.x + door.rect.w / 2, door.rect.y + door.rect.h / 2))
        road = next(iter(state.roads))
        play.campaign.simulation.apply_action({'type': 'block_road', 'road_id': road, 'blocked': True})
        play.player.loadout = {'ar_a': WeaponState.ready(play.specs['ar_a'])}
        play.player.active_weapon = 'ar_a'
        play.player.weapon.magazine = 5
        play.reload()
        play.player.weapon.tick(.3)
        play.player.hp, play.player.armor = 67, 12
        play.player.position = Position(0, 0)
        play.time = .75
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            self.assertLess(path.stat().st_size, 24_000)
            payload = json.loads(gzip.decompress(path.read_bytes()))
            self.assertNotIn('layers', payload)
            self.assertNotIn('colliders', payload)
            restored = load_game(path)
            self.assertEqual(asdict(restored.campaign.simulation.state), asdict(state))
            self.assertEqual(restored.world.data['generation'], play.world.data['generation'])
            self.assertEqual(restored.world.data['navigation'], play.world.data['navigation'])
            self.assertEqual(restored.time, .75)
            self.assertEqual(restored.player.hp, 67)
            self.assertEqual(restored.player.armor, 12)
            self.assertEqual(restored.player.xy, (0, 0))
            self.assertNotEqual(restored.player.xy, restored.world.safehouse)
            self.assertEqual(vars(restored.player.weapon), vars(play.player.weapon))
            self.assertEqual(restored.world.geometry.objects[door.id].active, door.active)
            self.assertEqual(restored.roster.by_id[casualty.id].hp, 0)
            self.assertEqual(restored.roster.local_deaths, play.roster.local_deaths)
            self.assertEqual(restored.dropped[0][0], play.dropped[0][0])
            self.assertEqual(restored.combat.random.getstate(), play.combat.random.getstate())
            for _ in range(3):
                restored.step()
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'prototype', '--load', str(path),
                                     '--headless', '--frames', '2'], cwd=ROOT, capture_output=True,
                                    text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('목업 코어 실행', result.stdout)

    def test_qrf_mid_route_and_future_campaign_continue_without_duplication(self):
        play = PlaySimulation(self.scene)
        witness = next(actor for actor in play.guards if '/hq/' in actor.id)
        play.player.position = Position(witness.position.x + math.cos(witness.facing) * 4,
                                        witness.position.y + math.sin(witness.facing) * 4)
        for index in range(1, 49):
            play.time = index / 60
            play.ai.update(play.guards, play.player, play.time)
        operation, = [op for op in play.campaign.simulation.state.operations.values() if op.kind == 'qrf']
        play.player.position = Position(0, 0)
        play._sync_guards()
        while operation.id not in play.presence.marches and play.time < 150:
            play.time = float(play.campaign.simulation.state.world_time + 1)
            play.campaign.advance_to(play.time)
            play._sync_guards()
        self.assertIn(operation.id, play.presence.marches)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            restored = load_game(path)
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))
        self.assertEqual([actor.id for actor in play.presence.marches[operation.id]],
                         [actor.id for actor in restored.presence.marches[operation.id]])
        for actor in play.presence.marches[operation.id]:
            self.assertEqual(actor.xy, restored.roster.by_id[actor.id].xy)
        for simulation in (play, restored):
            simulation.roster.materialized = False
            simulation._sync_guards()
            simulation.campaign.advance_to(180)
            assert_invariants(simulation.campaign.simulation.state)
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))

    def test_versions_corruption_and_failed_write_preserve_existing_save(self):
        play = PlaySimulation(self.scene)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            original = path.read_bytes()
            with patch('campaign.persistence.os.replace', side_effect=OSError('blocked')):
                with self.assertRaises(CampaignSaveError):
                    save_game(play, path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            for mutate in (lambda p: p.update(version=0), lambda p: p.update(content='old'),
                           lambda p: p['generation'].update(version=0),
                           lambda p: p['generation'].update(prefab_versions={}),
                           lambda p: p['generation'].update(seed=43),
                           lambda p: p['changes'].update(missing=False),
                           lambda p: p['local']['runtime'].update(time=100)):
                payload = json.loads(gzip.decompress(original))
                mutate(payload)
                path.write_bytes(gzip.compress(json.dumps(payload).encode()))
                with self.assertRaises(CampaignSaveError):
                    load_game(path)
            path.write_bytes(original[:30])
            with self.assertRaises(CampaignSaveError):
                load_game(path)

    def test_pending_facility_attack_survives_save_and_dispatches_once(self):
        play = PlaySimulation(self.scene)
        site = next(site for site in play.world.facilities if site["kind"] == "warehouse")
        play.player.position = Position(*site["terminal"])
        play.step()
        play.use_facility_alternate()
        self.assertEqual([event.kind for event in play.events.pending], ["noise", "facility_damaged"])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "play.json.gz"
            save_game(play, path)
            restored = load_game(path)

        self.assertEqual(play.events.pending, restored.events.pending)
        for simulation in (play, restored):
            simulation.step()
            simulation.step()
            state = simulation.campaign.simulation.state
            reports = [entry for entry in state.history if entry["kind"] == "qrf_report"]
            self.assertEqual(len(reports), 1)
            self.assertTrue(any(op.kind == "motor_qrf" for op in state.operations.values()))
            self.assertTrue(any(brain.memory.report_source == site["id"] for brain in simulation.ai.brains.values()))
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))

    def test_invalid_reload_states_are_rejected_at_load(self):
        play = PlaySimulation(self.scene)
        play.player.active_weapon = "ar_a"
        weapon = play.player.loadout["ar_a"] = WeaponState.ready(play.specs["ar_a"])
        self.assertTrue(weapon.fire(play.time))
        play.reload()
        play.step()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "play.json.gz"
            save_game(play, path)
            original = path.read_bytes()
            restored = load_game(path)
            self.assertTrue(restored.player.weapon.reloading)
            self.assertEqual(restored.player.weapon.stage_elapsed, weapon.stage_elapsed)
            for invalid in ({"stage": 99}, {"stage": 1}, {"stage_elapsed": -1}, {"stage_elapsed": 99}):
                with self.subTest(invalid=invalid):
                    payload = json.loads(gzip.decompress(original))
                    payload["local"]["player"]["loadout"]["ar_a"].update(invalid)
                    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
                    with self.assertRaisesRegex(CampaignSaveError, "잘못된 재장전 상태"):
                        load_game(path)

    def test_stopped_vehicle_seats_casualty_and_dismount_positions_survive_load(self):
        play = PlaySimulation(self.scene)
        play.campaign.advance_to(40)
        play.time = 40.
        play._sync_guards()
        truck = next(truck for truck in play.transports.trucks if truck.crew and not truck.stopped)
        play.combat._damage(truck.crew[0], 1000, play.time, vehicle_id=truck.id, incoming=truck.heading)
        play._dispatch()
        play._sync_guards()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'play.json.gz'
            save_game(play, path)
            restored = load_game(path)
        self.assertEqual(asdict(play.campaign.simulation.state), asdict(restored.campaign.simulation.state))
        self.assertEqual(play.roster.seated_deaths, restored.roster.seated_deaths)
        self.assertEqual({key: trip.exits for key, trip in play.transports.trips.items()},
                         {key: trip.exits for key, trip in restored.transports.trips.items()})
        for actor in play.transports.dismounted:
            self.assertEqual(actor.xy, restored.roster.by_id[actor.id].xy)
        restored.step()

    def test_player_cannot_walk_through_stopped_convoy(self):
        play = PlaySimulation(self.scene)
        play.campaign.advance_to(40)
        play.time = 40.
        play._sync_guards()
        truck = next(truck for truck in play.transports.trucks if truck.crew and not truck.stopped)
        play.combat._damage(truck.crew[0], 1000, play.time, vehicle_id=truck.id, incoming=truck.heading)
        play._dispatch()
        truck = next(row for row in play.transports.trucks if row.id == truck.id)
        self.assertTrue(truck.stopped)
        start, end = truck.world_point(0, -2), truck.world_point(0, 2)
        play.player.position = Position(*start)
        play.player.hp = 1000
        for _ in range(86):
            play.step((end[0] - start[0], end[1] - start[1]))
        self.assertAlmostEqual(truck.local_point(play.player.xy)[1], -1 - play.player.radius, delta=0.06)

        play.player.position = Position(*truck.world_point(0, 0))
        for _ in range(45):
            play.step((end[0] - start[0], end[1] - start[1]))
        self.assertGreater(truck.local_point(play.player.xy)[1], 1 + play.player.radius)

    def test_launcher_continue_uses_existing_entry_without_new_script(self):
        values = {'mode': '기본', 'frames': '2', 'headless': True}
        command = build_command(discover_targets()['continue'], values)
        self.assertIn('--load', command)
        self.assertNotIn('--generate', command)
