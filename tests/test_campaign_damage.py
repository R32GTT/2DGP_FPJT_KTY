from copy import deepcopy
from dataclasses import asdict
import unittest

from campaign.logistics import EXTERNAL_FRONT, plan_transport
from campaign.models import stock
from campaign.resources import assert_invariants
from campaign.routes import position
from campaign.scenario import load_scenario
from campaign.simulation import Simulation


class DamageTests(unittest.TestCase):
    def convoy(self, *, export=False):
        state = load_scenario()
        state.started = True
        operation = plan_transport(state, "depot_a", EXTERNAL_FRONT if export else "hq_b",
                                   stock(supplies=120, ammunition=20, repair_parts=10)
                                   if export else stock(supplies=50),
                                   kind="export" if export else "supply")
        return Simulation(state), operation

    def test_destroying_reserved_vehicle_cancels_without_destroying_warehouse_goods(self):
        simulation, operation = self.convoy()
        before = deepcopy(simulation.state.facilities["depot_a"].inventory)
        simulation.apply_action({"type": "destroy_vehicle", "vehicle_id": operation.vehicle_ids[0]})
        simulation.advance_to(1000)
        self.assertEqual(operation.phase, "cancelled")
        self.assertEqual(simulation.state.facilities["depot_a"].inventory, before)
        self.assertEqual(sum(simulation.state.ledger.destroyed.values()), 0)
        assert_invariants(simulation.state)

    def test_vehicle_loss_freezes_wreck_and_reduces_cargo_without_duplication(self):
        simulation, operation = self.convoy(export=True)
        simulation.advance_to(100)
        state = simulation.state
        where = deepcopy(position(state, operation))
        lost = operation.vehicle_ids[0]
        simulation.apply_action({"type": "destroy_vehicle", "vehicle_id": lost})
        self.assertEqual(state.vehicles[lost].position, where)
        self.assertEqual(operation.cargo["supplies"], 60)
        simulation.advance_to(1500)
        self.assertEqual(operation.phase, "completed")
        self.assertEqual(state.demand.delivered["supplies"], 60)
        self.assertEqual(state.vehicles[lost].position, where)
        self.assertEqual(state.vehicles[lost].status, "destroyed")
        assert_invariants(state)

    def test_total_vehicle_loss_preserves_surviving_escorts_at_stranded_position(self):
        simulation, operation = self.convoy()
        simulation.advance_to(100)
        where = position(simulation.state, operation)
        simulation.apply_action({"type": "destroy_vehicle", "vehicle_id": operation.vehicle_ids[0]})
        simulation.advance_to(5000)
        self.assertEqual(operation.phase, "stranded")
        self.assertEqual(position(simulation.state, operation), where)
        self.assertEqual(simulation.state.facilities["hq_b"].inventory["supplies"], 20)
        self.assertEqual(sum(operation.cargo.values()), 0)
        assert_invariants(simulation.state)

    def test_escort_loss_causes_physical_return_and_stale_unload_cannot_deliver(self):
        simulation, operation = self.convoy()
        simulation.advance_to(290)
        uid = operation.unit_ids[0]
        simulation.apply_action({"type": "damage_unit", "unit_id": uid, "soldiers": 2})
        simulation.advance_to(1000)
        self.assertEqual(operation.phase, "completed")
        self.assertIsNone(operation.delivered_at)
        self.assertEqual(simulation.state.facilities["hq_b"].inventory["supplies"], 20)
        self.assertEqual(simulation.state.units[uid].losses, 2)
        assert_invariants(simulation.state)

    def test_garrison_damage_cancels_departure_that_would_violate_minimum(self):
        simulation, operation = self.convoy(export=True)
        simulation.apply_action({"type": "damage_unit", "unit_id": "a_garrison", "soldiers": 3})
        simulation.advance_to(100)
        self.assertEqual(operation.phase, "cancelled")
        self.assertIsNone(operation.departed_at)
        assert_invariants(simulation.state)

    def test_invalid_loss_is_atomic(self):
        simulation, operation = self.convoy()
        before = asdict(simulation.state)
        with self.assertRaises(ValueError):
            simulation.apply_action({"type": "damage_unit", "unit_id": operation.unit_ids[0], "soldiers": -1})
        self.assertEqual(asdict(simulation.state), before)
