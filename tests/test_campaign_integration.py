from dataclasses import asdict
import unittest

from campaign.clock import TRANSFER, schedule
from campaign.logistics import plan_transport
from campaign.persistence import deserialize_campaign, serialize_campaign
from campaign.resources import assert_invariants
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class CampaignIntegrationTests(unittest.TestCase):
    def test_seconds_speedup_and_skip_produce_identical_world(self):
        normal, fast, skipped = (Simulation(load_scenario(seed=15)) for _ in range(3))
        for time in range(1, 901):
            normal.advance_to(time)
        for time in range(30, 901, 30):
            fast.advance_to(time)
        skipped.advance_to(900)
        self.assertEqual(asdict(normal.state), asdict(fast.state))
        self.assertEqual(asdict(normal.state), asdict(skipped.state))

    def test_full_day_blockade_changes_downstream_delivery_without_resource_creation(self):
        baseline, blocked = Simulation(load_scenario()), Simulation(load_scenario())
        blocked.apply_action({"type": "block_road", "road_id": "c_exit", "blocked": True})
        for simulation in (baseline, blocked):
            simulation.advance_to(86400)
            assert_invariants(simulation.state)
        self.assertGreater(baseline.state.demand.delivered["supplies"], 0)
        self.assertEqual(sum(blocked.state.demand.delivered.values()), 0)
        self.assertGreater(blocked.state.demand.outstanding["supplies"],
                           baseline.state.demand.outstanding["supplies"])

    def test_duplicate_events_cannot_repeat_departure_or_delivery(self):
        state = load_scenario()
        state.started = True
        operation = plan_transport(state, "depot_a", "hq_b", {"supplies": 50})
        for time, kind in ((30, "depart"), (300, "unload"), (420, "begin_return")):
            schedule(state, time, kind, priority=TRANSFER, operation_id=operation.id)
        simulation = Simulation(state)
        simulation.advance_to(1000)
        self.assertEqual(state.facilities["hq_b"].inventory["supplies"], 70)
        self.assertEqual(sum(row["kind"] == "departed" for row in state.history), 1)
        self.assertEqual(sum(row["kind"] == "handed_over" for row in state.history), 1)
        assert_invariants(state)

    def test_receiver_loss_returns_actual_incoming_goods_and_keeps_damage_on_load(self):
        state = load_scenario()
        state.started = True
        operation = plan_transport(state, state.source.id, "hq_b", {"supplies": 100})
        simulation = Simulation(state)
        simulation.advance_to(100)
        simulation.apply_action({"type": "damage_facility", "facility_id": "hq_b", "damage": 100})
        simulation.advance_to(1200)
        self.assertEqual(operation.phase, "completed")
        self.assertIsNone(operation.delivered_at)
        self.assertEqual(state.facilities["hq_b"].inventory["supplies"], 20)
        restored = deserialize_campaign(serialize_campaign(state))
        self.assertEqual(restored.facilities["hq_b"].damage, 100)
        self.assertEqual(asdict(state), asdict(restored))

    def test_no_new_external_departures_without_valid_receiving_regions(self):
        state = load_scenario(support="infinite")
        for region in state.regions.values():
            region.owner = "player"
        Simulation(state).advance_to(21600)
        self.assertFalse(any(op.origin_id == state.source.id for op in state.operations.values()))
        self.assertEqual(sum(state.ledger.imported.values()), 0)
        assert_invariants(state)
