import unittest

from campaign.models import Node, Position, Road, stock
from campaign.persistence import deserialize_campaign, serialize_campaign
from campaign.resources import assert_invariants, depart, reserve
from campaign.routes import find_route, move_one_second, position, request_return, set_route
from campaign.scenario import load_scenario
from tests.campaign_helpers import make_operation


class RouteTests(unittest.TestCase):
    def test_distance_cost_survives_save_and_is_independent_of_road_piece_count(self):
        whole, original = self.transport(destination="hq_a")
        whole.rules["road_cost_distance_m"] = 100
        split = deserialize_campaign(serialize_campaign(whole))
        split.roads.pop("a_hq")
        path = ["depot_a"]
        for index in range(1, 5):
            key = f"split/{index}"
            split.nodes[key] = Node(key, Position(0, index * 100))
            path.append(key)
        path.append("hq_a")
        for index, (a, b) in enumerate(zip(path, path[1:])):
            key = f"split/road/{index}"
            split.roads[key] = Road(key, a, b, 100)
        divided = split.operations[original.id]
        set_route(split, divided)
        for state, operation in ((whole, original), (split, divided)):
            for _ in range(30):
                move_one_second(state, operation)
        whole, split = (deserialize_campaign(serialize_campaign(state)) for state in (whole, split))
        for state in (whole, split):
            operation = state.operations[original.id]
            for _ in range(20):
                move_one_second(state, operation)
            self.assertEqual(position(state, operation), Position(0, 500))
            self.assertEqual(operation.operating_distance_m, 500)
            self.assertEqual(operation.provisions["supplies"], 15)
            self.assertEqual(state.ledger.consumed["supplies"], 5)
            assert_invariants(state)

    def transport(self, destination="depot_c"):
        state = load_scenario()
        operation = make_operation(state, destination=destination)
        reserve(state, operation, stock(supplies=100), stock(supplies=20))
        depart(state, operation.id)
        set_route(state, operation)
        return state, operation

    def test_known_block_selects_longer_real_detour(self):
        state = load_scenario()
        state.roads["a_b"].blocked = True
        self.assertEqual(find_route(state, "depot_a", "depot_c"),
                         ["depot_a", "depot_b", "depot_c"])
        self.assertEqual(find_route(state, "depot_a", "depot_c", ["a_b"]),
                         ["depot_a", "bypass", "depot_c"])

    def test_mid_road_block_reverses_without_teleporting_then_detours(self):
        state, operation = self.transport()
        for _ in range(50):
            move_one_second(state, operation)
        self.assertEqual(position(state, operation).x, 500)
        state.roads["a_b"].blocked = True
        move_one_second(state, operation)
        self.assertEqual(position(state, operation).x, 490)
        self.assertTrue(operation.reversing)
        for _ in range(49):
            move_one_second(state, operation)
        self.assertEqual(operation.route, ["depot_a", "bypass", "depot_c"])
        self.assertEqual(position(state, operation).x, 0)
        for _ in range(499):
            self.assertFalse(move_one_second(state, operation))
        self.assertTrue(move_one_second(state, operation))
        self.assertEqual(position(state, operation).x, 4000)
        assert_invariants(state)

    def test_return_waits_at_isolated_node_then_resumes_when_road_reopens(self):
        state, operation = self.transport(destination="hq_a")
        for _ in range(50):
            move_one_second(state, operation)
        request_return(state, operation)
        state.roads["a_hq"].blocked = True
        self.assertFalse(move_one_second(state, operation))
        self.assertEqual(operation.waiting_reason, "route_unavailable")
        self.assertEqual(position(state, operation).y, 500)
        state.roads["a_hq"].blocked = False
        for _ in range(49):
            self.assertFalse(move_one_second(state, operation))
        self.assertTrue(move_one_second(state, operation))
        assert_invariants(state)

    def test_return_request_on_road_keeps_current_position(self):
        state, operation = self.transport()
        for _ in range(20):
            move_one_second(state, operation)
        request_return(state, operation)
        self.assertEqual(position(state, operation).x, 200)
        for _ in range(19):
            self.assertFalse(move_one_second(state, operation))
        self.assertTrue(move_one_second(state, operation))
        self.assertEqual(position(state, operation).x, 0)
