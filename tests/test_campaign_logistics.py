from dataclasses import asdict
import unittest

from campaign.logistics import plan_transport
from campaign.resources import assert_invariants
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class LogisticsTests(unittest.TestCase):
    def test_autonomous_supply_and_consumption_without_player(self):
        state = load_scenario()
        Simulation(state).advance_to(21600)
        self.assertGreater(state.ledger.consumed["supplies"], 200)
        self.assertEqual(state.ledger.consumed["ammunition"], 0)
        self.assertTrue(any(op.origin_id == state.source.id and op.delivered_at is not None
                            for op in state.operations.values()))
        self.assertTrue(any(op.phase == "completed" for op in state.operations.values()))
        self.assertEqual(sum(u.soldiers for u in state.units.values()), state.initial_soldiers)
        assert_invariants(state)

    def test_destination_changes_only_after_arrival_and_unloading(self):
        state = load_scenario()
        state.started = True
        operation = plan_transport(state, "depot_a", "hq_b", {"supplies": 50})
        simulation = Simulation(state)
        initial = state.facilities["hq_b"].inventory["supplies"]
        simulation.advance_to(29)
        self.assertEqual(operation.phase, "loading")
        simulation.advance_to(279)
        self.assertEqual(operation.phase, "outbound")
        simulation.advance_to(299)
        self.assertEqual(operation.phase, "unloading")
        self.assertEqual(state.facilities["hq_b"].inventory["supplies"], initial)
        simulation.advance_to(300)
        self.assertEqual(state.facilities["hq_b"].inventory["supplies"], initial + 50)
        self.assertEqual(state.vehicles[operation.vehicle_ids[0]].status, "in_transit")
        simulation.advance_to(670)
        self.assertEqual(operation.phase, "completed")
        assert_invariants(state)

    def test_six_hour_skip_matches_incremental_progress_with_same_actions(self):
        left, right = Simulation(load_scenario()), Simulation(load_scenario())
        for simulation in (left, right):
            simulation.advance_to(100)
            simulation.apply_action({"type": "block_road", "road_id": "a_b", "blocked": True})
        left.advance_to(21600)
        for time in range(300, 21601, 300):
            right.advance_to(time)
        self.assertEqual(asdict(left.state), asdict(right.state))
        self.assertTrue(any(row["kind"] == "road_discovered" for row in left.state.history))

    def test_finite_supply_exhaustion_and_infinite_departure_constraints(self):
        for mode in ("finite", "infinite"):
            state = load_scenario(support=mode)
            for name, value in list(state.source.inventory.items()):
                state.source.inventory[name] = 0
                if mode == "finite":
                    state.ledger.initial[name] -= value
            Simulation(state).advance_to(10800)
            external = [op for op in state.operations.values()
                        if op.origin_id == state.source.id and op.departed_at is not None]
            if mode == "finite":
                self.assertEqual(external, [])
            else:
                self.assertGreater(len(external), 1)
                departures = sorted(op.departed_at for op in external)
                self.assertTrue(all(b - a >= 600 for a, b in zip(departures, departures[1:])))
                for operation in external:
                    departure = next(row for row in state.history
                                     if row["kind"] == "departed" and row["operation_id"] == operation.id)
                    self.assertLessEqual(sum(departure["cargo"].values()), 180)
            assert_invariants(state)
