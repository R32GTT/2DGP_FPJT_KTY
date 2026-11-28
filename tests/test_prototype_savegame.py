from dataclasses import asdict
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from campaign.models import Position
from campaign.persistence import campaign_payload
from campaign.resources import assert_invariants
from prototype.generated_world import generate_world
from prototype.interactions import exchange_safehouse_weapon
from prototype.launcher import ROOT, build_command, discover_targets
from prototype.savegame import CampaignSaveError, load_game, save_game
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState
from prototype.world_files import WorldFiles, json_bytes, write_bytes


class PlaySaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = generate_world(42, region_count=2)

    def test_identical_equipped_guns_and_bag_gun_survive_save(self):
        play = PlaySimulation(self.scene)
        first = WeaponState.ready(play.specs["ar_a"])
        second = WeaponState.ready(play.specs["ar_a"])
        second.magazine = 8
        bag_gun = WeaponState.ready(play.specs["ar_b"])
        play.player.loadout = {"ar_a": first, "ar_a#2": second}
        play.player.active_weapon = "ar_a#2"
        play.inventory.stow_reserve(bag_gun)
        play.inventory.carried_weapons = {"ar_b": bag_gun}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weapons.json.gz"
            save_game(play, path)
            restored = load_game(path)
        self.assertEqual(restored.player.active_weapon, "ar_a#2")
        self.assertEqual([weapon.magazine for weapon in restored.player.loadout.values()], [30, 8])
        self.assertEqual(restored.inventory.carried_weapons["ar_b"].spec.id, "ar_b")
        self.assertEqual(restored.inventory.carried_weapons["ar_b"].reserve, 0)

    def test_safehouse_can_exchange_for_a_second_identical_primary(self):
        play = PlaySimulation(self.scene)
        site = next(site for site in play.world.facilities if site["kind"] == "safehouse")
        first = WeaponState.ready(play.specs["ar_a"])
        equipped = WeaponState.ready(play.specs["ar_b"])
        stored = WeaponState.ready(play.specs["ar_a"])
        play.player.loadout = {"ar_a": first, "ar_b": equipped}
        play.player.active_weapon = "ar_b"
        play.safehouse_stash[site["id"]] = stored
        exchange_safehouse_weapon(play, site)
        self.assertEqual(len(play.player.loadout), 2)
        self.assertIs(play.player.weapon, stored)
        self.assertIn(first, play.player.loadout.values())
        self.assertIs(play.safehouse_stash[site["id"]], equipped)

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
        play.inventory.take_facility_stock(key, 'supplies')
        captured = state.player_stock['supplies']
        self.assertGreater(captured, 0)
        self.assertEqual(play.inventory.carried['cargo:supplies'], captured)
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
        self.assertEqual(state.player_stock['supplies'], captured)
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
        simulation = play.campaign.simulation
        state = simulation.state
        # 도보 증원 저장을 재현하도록 차량 증원에 필요한 수송차를 제거한다.
        for vehicle in state.vehicles.values():
            if vehicle.troop_seats:
                simulation.apply_action({"type": "destroy_vehicle", "vehicle_id": vehicle.id})
        witness = next(actor for actor in play.guards if '/hq/' in actor.id)
        site = play.campaign.node_facilities[witness.home_node]
        status, operation_id = play.campaign.report_contact(witness, site.region_id, witness.xy, play.time)
        self.assertEqual(status, "ordered")
        operation = state.operations[operation_id]
        self.assertEqual(operation.kind, "qrf")
        home = state.nodes[operation.home_node].position
        play.player.position = Position(home.x + 35, home.y)
        play._sync_guards()
        while not (operation.id in play.presence.marches and (operation.edge_index or operation.progress_m)) and play.time < 150:
            play.time = float(play.campaign.simulation.state.world_time + 1)
            play.campaign.advance_to(play.time)
            play._sync_guards()
        self.assertIn(operation.id, play.presence.marches)
        self.assertEqual(operation.phase, "outbound")
        self.assertTrue(operation.edge_index or operation.progress_m)
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
            self.assertFalse(list(Path(directory).rglob("*.tmp")))
            for corrupt in ({"version": 0}, {"content": "old"}, {"world": "../bad"},
                            {"revision": "0" * 64}):
                payload = json.loads(gzip.decompress(original))
                payload.update(corrupt)
                write_bytes(path, json_bytes(payload))
                with self.assertRaises(CampaignSaveError):
                    load_game(path)
            path.write_bytes(original)
            payload = json.loads(gzip.decompress(original))
            files = WorldFiles(path.parent / "worlds", payload["world"])
            snapshot = files.snapshot_path(payload["revision"])
            state = json.loads(gzip.decompress(snapshot.read_bytes()))
            state["local"]["runtime"]["time"] = 100
            write_bytes(snapshot, json_bytes(state))
            with self.assertRaisesRegex(CampaignSaveError, "월드 파일 내용"):
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
            slot = json.loads(gzip.decompress(original))
            files = WorldFiles(path.parent / "worlds", slot["world"])
            snapshot = gzip.decompress(files.snapshot_path(slot["revision"]).read_bytes())
            for invalid in ({"stage": 99}, {"stage": 1}, {"stage_elapsed": -1}, {"stage_elapsed": 99}):
                with self.subTest(invalid=invalid):
                    payload = json.loads(snapshot)
                    payload["local"]["player"]["loadout"]["ar_a"].update(invalid)
                    data = json_bytes(payload)
                    revision = sha256(data).hexdigest()
                    write_bytes(files.snapshot_path(revision), data)
                    write_bytes(path, json_bytes({**slot, "revision": revision}))
                    with self.assertRaisesRegex(CampaignSaveError, "잘못된 재장전 상태"):
                        load_game(path)

    def test_quick_and_full_slots_share_terrain_and_restore_distinct_state(self):
        play = PlaySimulation(self.scene)
        door = next(c for c in play.world.geometry.objects.values() if c.kind == "door")
        active = door.active
        with tempfile.TemporaryDirectory() as directory:
            quick = Path(directory) / "quick.json.gz"
            full = quick.with_name("prototype.json.gz")
            save_game(play, quick)
            door.active = not active
            play.player.hp -= 7
            save_game(play, full)

            quick_slot, full_slot = (json.loads(gzip.decompress(path.read_bytes())) for path in (quick, full))
            self.assertEqual(quick_slot["world"], full_slot["world"])
            self.assertNotEqual(quick_slot["revision"], full_slot["revision"])
            files = WorldFiles(quick.parent / "worlds", quick_slot["world"])
            self.assertEqual(len(list(files.directory.glob("scene.json.gz"))), 1)
            with patch("prototype.savegame.generate_world", side_effect=AssertionError("scene should be read")):
                quick_play, full_play = load_game(quick), load_game(full)
            self.assertEqual(quick_play.player.hp - full_play.player.hp, 7)
            self.assertEqual(quick_play.world.geometry.objects[door.id].active, active)
            self.assertEqual(full_play.world.geometry.objects[door.id].active, not active)
            self.assertIsNotNone(quick_play.world.geometry.index.tree)

            # 이전 형식은 내장된 생성 설정과 상태를 사용한다.
            old = quick.with_name("legacy.json.gz")
            snapshot = json.loads(gzip.decompress(files.snapshot_path(quick_slot["revision"]).read_bytes()))
            snapshot.pop("archive")
            snapshot["campaign"] = campaign_payload(quick_play.campaign.simulation.state)
            write_bytes(old, json_bytes({"format": quick_slot["format"], "version": 6,
                                         "content": quick_slot["content"], "generation": self.scene["generation"],
                                         **snapshot}))
            self.assertEqual(load_game(old).player.hp, quick_play.player.hp)

    def test_completed_operations_and_history_round_trip_through_archive_pages(self):
        play = PlaySimulation(self.scene)
        play.time = 120
        play.campaign.advance_to(120)
        state = play.campaign.simulation.state
        for index in range(600):
            state.record("archive_test", sequence=index)
        self.assertTrue(any(op.phase == "completed" for op in state.operations.values()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quick.json.gz"
            save_game(play, path)
            slot = json.loads(gzip.decompress(path.read_bytes()))
            files = WorldFiles(path.parent / "worlds", slot["world"])
            snapshot = json.loads(gzip.decompress(files.snapshot_path(slot["revision"]).read_bytes()))
            self.assertGreaterEqual(len(snapshot["archive"]["history"]), 3)
            self.assertTrue(snapshot["archive"]["operations"])
            self.assertNotIn("history", snapshot["campaign"]["state"])
            restored = load_game(path).campaign.simulation.state
            self.assertEqual(list(restored.operations), list(state.operations))
            self.assertEqual(asdict(restored), asdict(state))

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
