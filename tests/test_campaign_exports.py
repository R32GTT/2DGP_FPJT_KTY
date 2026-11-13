import unittest

from campaign.logistics import EXTERNAL_FRONT, plan_transport
from campaign.models import stock
from campaign.resources import assert_invariants
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class ExportTests(unittest.TestCase):
    def export(self):
        state = load_scenario()
        state.started = True
        state.demand.outstanding = stock(supplies=120, ammunition=20, repair_parts=10)
        operation = plan_transport(state, "depot_a", EXTERNAL_FRONT,
                                   state.demand.outstanding, kind="export")
        self.assertEqual(len(operation.vehicle_ids), 2)
        self.assertEqual(len(operation.unit_ids), 2)
        return Simulation(state), operation

    def test_export_requires_exit_handover_and_same_assets_return(self):
        simulation, operation = self.export()
        state = simulation.state
        vehicle_ids = list(operation.vehicle_ids)
        simulation.advance_to(529)
        self.assertEqual(sum(state.demand.delivered.values()), 0)
        simulation.advance_to(549)
        self.assertEqual(operation.phase, "unloading")
        self.assertEqual(sum(state.demand.delivered.values()), 0)
        simulation.advance_to(550)
        self.assertEqual(state.demand.delivered, stock(supplies=120, ammunition=20, repair_parts=10))
        self.assertEqual(operation.phase, "turnaround")
        self.assertTrue(all(state.vehicles[vid].node_id is None for vid in vehicle_ids))
        simulation.advance_to(1169)
        self.assertEqual(operation.phase, "returning")
        simulation.advance_to(1170)
        self.assertEqual(operation.phase, "completed")
        self.assertTrue(all(state.vehicles[vid].node_id == "depot_a" for vid in vehicle_ids))
        assert_invariants(state)

    def test_captured_and_destroyed_cargo_never_counts_as_delivered(self):
        simulation, operation = self.export()
        state = simulation.state
        simulation.advance_to(100)
        simulation.apply_action({"type": "capture", "operation_id": operation.id,
                                 "amount": {"supplies": 50}})
        simulation.apply_action({"type": "destroy_stock", "operation_id": operation.id,
                                 "amount": {"supplies": 20}})
        simulation.advance_to(1200)
        self.assertEqual(state.demand.delivered["supplies"], 50)
        self.assertEqual(state.demand.outstanding["supplies"], 70)
        self.assertEqual(state.player_stock["supplies"], 50)
        self.assertEqual(state.ledger.destroyed["supplies"], 20)
        assert_invariants(state)

    def test_isolated_exit_causes_real_return_with_cargo(self):
        simulation, operation = self.export()
        simulation.apply_action({"type": "block_road", "road_id": "c_exit", "blocked": True})
        simulation.advance_to(1500)
        self.assertEqual(operation.phase, "completed")
        self.assertIsNone(operation.delivered_at)
        self.assertEqual(sum(simulation.state.demand.delivered.values()), 0)
        self.assertEqual(sum(operation.cargo.values()), 0)
        assert_invariants(simulation.state)

    def test_twenty_four_hours_runs_import_distribution_export_and_return(self):
        for mode in ("finite", "infinite"):
            with self.subTest(mode=mode):
                state = load_scenario(support=mode)
                Simulation(state).advance_to(86400)
                exports = [op for op in state.operations.values() if op.kind == "export"]
                self.assertGreater(sum(state.demand.delivered.values()), 500)
                self.assertTrue(any(op.phase == "completed" for op in exports))
                self.assertTrue(any(op.origin_id == state.source.id and op.delivered_at is not None
                                    for op in state.operations.values()))
                self.assertEqual(sum(row["kind"] == "export_demand" for row in state.history), 12)
                assert_invariants(state)
