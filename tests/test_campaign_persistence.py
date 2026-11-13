from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campaign.logistics import EXTERNAL_FRONT, plan_transport
from campaign.models import stock
from campaign.persistence import (CampaignSaveError, deserialize_campaign, load_campaign,
                                  save_campaign, serialize_campaign)
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class PersistenceTests(unittest.TestCase):
    def test_roundtrip_and_future_match_during_every_transport_phase(self):
        for time, phase in ((10, "loading"), (100, "outbound"), (540, "unloading"),
                            (600, "turnaround"), (800, "returning")):
            with self.subTest(phase=phase):
                state = load_scenario()
                state.started = True
                operation = plan_transport(state, "depot_a", EXTERNAL_FRONT,
                                           stock(supplies=120, ammunition=20, repair_parts=10), kind="export")
                original = Simulation(state)
                original.advance_to(time)
                self.assertEqual(operation.phase, phase)
                restored = deserialize_campaign(serialize_campaign(state))
                self.assertEqual(asdict(state), asdict(restored))
                resumed = Simulation(restored)
                self.assertEqual(asdict(state), asdict(resumed.state))
                original.advance_to(1500)
                resumed.advance_to(1500)
                self.assertEqual(asdict(state), asdict(restored))

    def test_autonomous_resume_preserves_random_events_ids_and_reservation_order(self):
        original = Simulation(load_scenario(support="infinite", seed=123))
        original.advance_to(7501)
        restored = Simulation(deserialize_campaign(serialize_campaign(original.state)))
        for simulation in (original, restored):
            simulation.apply_action({"type": "block_road", "road_id": "a_b", "blocked": True})
            simulation.advance_to(86400)
        self.assertEqual(asdict(original.state), asdict(restored.state))

    def test_stranded_escorts_and_wreck_positions_survive_load(self):
        original = Simulation(load_scenario())
        original.advance_to(100)
        operation = next(op for op in original.state.operations.values() if op.phase == "outbound")
        original.apply_action({"type": "destroy_vehicle", "vehicle_id": operation.vehicle_ids[0]})
        restored = Simulation(deserialize_campaign(serialize_campaign(original.state)))
        original.advance_to(10000)
        restored.advance_to(10000)
        self.assertEqual(asdict(original.state), asdict(restored.state))

    def test_corrupt_and_unsupported_saves_are_rejected(self):
        simulation = Simulation(load_scenario())
        simulation.advance_to(100)
        baseline = serialize_campaign(simulation.state)
        mutations = [
            lambda p: p.update(save_version=999),
            lambda p: p["state"].update(rules_version=999),
            lambda p: p["state"].pop("events"),
            lambda p: p["state"]["facilities"]["depot_a"]["inventory"].update(supplies=-1),
            lambda p: p["state"].update(next_sequence=0),
            lambda p: p["state"].update(rng_state=[1, 2]),
            lambda p: p["state"]["operations"][next(iter(p["state"]["operations"]))].update(edge_index=999),
            lambda p: p["state"]["vehicles"]["a_truck_1"].update(operation_id="missing"),
        ]
        for mutate in mutations:
            payload = json.loads(baseline)
            mutate(payload)
            with self.assertRaises(CampaignSaveError):
                deserialize_campaign(json.dumps(payload))
        with self.assertRaises(CampaignSaveError):
            deserialize_campaign("{broken")

    def test_atomic_file_save_missing_file_and_failed_replace(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("CAMPAIGN_TEST_TMPDIR")) as directory:
            path = Path(directory) / "campaign.json"
            state = load_scenario()
            save_campaign(state, path)
            self.assertEqual(asdict(load_campaign(path)), asdict(state))
            original = path.read_text(encoding="utf-8")
            Simulation(state).advance_to(100)
            with patch("campaign.persistence.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(CampaignSaveError):
                    save_campaign(state, path)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaises(CampaignSaveError):
                load_campaign(Path(directory) / "missing.json")
