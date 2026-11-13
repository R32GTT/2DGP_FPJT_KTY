from dataclasses import asdict
import unittest

from campaign.clock import advance_to, schedule
from campaign.scenario import load_scenario


class ClockTests(unittest.TestCase):
    def test_same_time_uses_priority_then_insertion_order(self):
        state = load_scenario()
        schedule(state, 10, "last", priority=40)
        schedule(state, 10, "first", priority=10)
        schedule(state, 10, "second", priority=10)
        result = []
        advance_to(state, 10, lambda event: result.append(event.kind))
        self.assertEqual(result, ["first", "second", "last"])

    def test_six_hour_skip_matches_seconds_and_schedules_followups(self):
        left, right = load_scenario(), load_scenario()

        def handler(state):
            def process(event):
                state.record(event.kind, choice=state.choose(["a", "b"]))
                schedule(state, event.time + 300, event.kind)
            return process

        for state in (left, right):
            schedule(state, 0, "recurring")
        advance_to(left, 21600, handler(left))
        for time in range(21601):
            advance_to(right, time, handler(right))
        self.assertEqual(asdict(left), asdict(right))
        self.assertEqual(len(left.history), 73)

    def test_tick_occurs_before_events_at_each_second(self):
        state = load_scenario()
        seen = []
        schedule(state, 2, "arrival")
        advance_to(state, 3, lambda e: seen.append(("event", state.world_time)),
                   tick=lambda: seen.append(("tick", state.world_time)))
        self.assertEqual(seen, [("tick", 1), ("tick", 2), ("event", 2), ("tick", 3)])
        with self.assertRaises(ValueError):
            advance_to(state, 2, lambda e: None)
        with self.assertRaises(ValueError):
            schedule(state, 2, "past")
