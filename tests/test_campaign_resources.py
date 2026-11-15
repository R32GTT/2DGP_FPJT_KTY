import unittest

from campaign.models import stock
from campaign.resources import (assert_invariants, available, cancel, consume, depart,
                                finish_return, handover, remove, reserve)
from campaign.scenario import load_scenario
from tests.campaign_helpers import make_operation


class ResourceTests(unittest.TestCase):
    def test_reservation_prevents_double_assignment_and_cancel_does_not_move_stock(self):
        state = load_scenario()
        initial = dict(state.facilities["depot_a"].inventory)
        operation = make_operation(state)
        reserve(state, operation, stock(supplies=100), stock(supplies=10))
        self.assertEqual(state.facilities["depot_a"].inventory, initial)
        self.assertEqual(available(state, "depot_a", "supplies"), initial["supplies"] - 110)
        with self.assertRaises(ValueError):
            reserve(state, make_operation(state), stock(supplies=1))
        self.assertTrue(cancel(state, operation.id))
        self.assertFalse(cancel(state, operation.id))
        self.assertEqual(state.facilities["depot_a"].inventory, initial)
        assert_invariants(state)

    def test_depart_capture_destroy_handover_and_return_conserve_resources(self):
        state = load_scenario()
        operation = make_operation(state)
        destination_before = state.facilities["hq_b"].inventory["supplies"]
        reserve(state, operation, stock(supplies=100), stock(supplies=10))
        self.assertTrue(depart(state, operation.id))
        self.assertFalse(depart(state, operation.id))
        self.assertFalse(cancel(state, operation.id))
        self.assertIsNone(state.vehicles["a_truck_1"].node_id)
        remove(state, operation.cargo, {"supplies": 30}, outcome="captured")
        remove(state, operation.cargo, {"supplies": 20}, outcome="destroyed")
        consume(state, operation.provisions, {"supplies": 2}, reason="vehicle_operation")
        operation.phase = "unloading"
        operation.route = [operation.destination_node]
        self.assertTrue(handover(state, operation.id))
        self.assertFalse(handover(state, operation.id))
        self.assertEqual(state.facilities["hq_b"].inventory["supplies"], destination_before + 50)
        operation.phase, operation.route = "returning", [operation.home_node]
        self.assertTrue(finish_return(state, operation.id))
        self.assertFalse(finish_return(state, operation.id))
        self.assertEqual(state.player_stock["supplies"], 30)
        self.assertEqual(state.ledger.destroyed["supplies"], 20)
        assert_invariants(state)

    def test_rejects_minimum_stock_garrison_and_capacity_violations(self):
        state = load_scenario()
        for cargo in (stock(supplies=151), stock(ammunition=141)):
            with self.assertRaises(ValueError):
                reserve(state, make_operation(state), cargo)
        state.regions["a"].minimum_garrison = 16
        with self.assertRaises(ValueError):
            reserve(state, make_operation(state), stock(supplies=1))

    def test_warehouse_loss_cancels_unusable_loading_reservation(self):
        state = load_scenario()
        operation = make_operation(state)
        reserve(state, operation, stock(supplies=100))
        remove(state, state.facilities["depot_a"].inventory, {"supplies": 650},
               outcome="destroyed", owner_id="depot_a")
        self.assertEqual(operation.phase, "cancelled")
        self.assertFalse(depart(state, operation.id))
        assert_invariants(state)

    def test_infinite_source_records_creation_at_departure_and_unused_return(self):
        state = load_scenario(support="infinite")
        operation = make_operation(state, origin=state.source.id,
                                   vehicle="source_truck_1", unit="source_escort_1")
        reserve(state, operation, stock(supplies=100), stock(supplies=20))
        self.assertEqual(state.ledger.imported["supplies"], 0)
        depart(state, operation.id)
        self.assertEqual(state.ledger.imported["supplies"], 120)
        operation.phase, operation.route = "returning", [operation.home_node]
        finish_return(state, operation.id)
        self.assertEqual(state.ledger.returned_external["supplies"], 120)
        assert_invariants(state)
