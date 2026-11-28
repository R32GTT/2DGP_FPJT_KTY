import unittest
from datetime import datetime, timezone
from xml.etree import ElementTree

from prototype.profiler import summarize_speedscope
from prototype.profile_report import flamegraph_svg, interactive_html, markdown_report


class ProfilerSummaryTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "shared": {"frames": [
                {"name": "process 123", "file": None, "line": None},
                {"name": "parent", "file": "game.py", "line": 10},
                {"name": "child", "file": "game.py", "line": 20},
            ]},
            "profiles": [
                {"type": "sampled", "unit": "seconds", "samples": [[0, 1, 2], [0, 1, 1]],
                 "weights": [0.01, 0.01]},
                {"type": "sampled", "unit": "seconds", "samples": [[0, 2]], "weights": [0.02]},
            ],
        }
    def test_self_and_inclusive_time_are_separate_across_threads_and_recursion(self):
        summary = dict(summarize_speedscope(self.document))
        self.assertNotIn(("", "process 123", 0), summary)
        self.assertEqual(summary[("game.py", "parent", 10)], [0.01, 0.02, 1, 2])
        self.assertEqual(summary[("game.py", "child", 20)], [0.03, 0.03, 2, 2])

    def test_report_and_flame_graph_use_the_sampled_times(self):
        rows = summarize_speedscope(self.document)
        svg = flamegraph_svg(self.document)
        tree = ElementTree.fromstring(svg)
        groups = tree.findall('.//{http://www.w3.org/2000/svg}g')
        parent = next(group for group in groups if group.get('data-name') == 'parent')
        child = next(group for group in groups if group.get('data-name') == 'child')
        self.assertEqual(parent.get('data-inclusive'), '20.0')
        self.assertGreater(float(child[1].get('y')), float(parent[1].get('y')))
        html = interactive_html(svg)
        self.assertIn('id="search"', html)
        self.assertIn('id="reset"', html)
        self.assertIn('data-inclusive="20.0"', html)
        report = markdown_report(rows, {'sample_rate_hz': 100, 'sampled_active_thread_s': 0.04,
                                        'sampler_cpu_s': 0.01, 'reporter_cpu_s': 0.001},
                                 datetime(2026, 11, 27, tzinfo=timezone.utc), ['--headless'])
        self.assertIn('| child | game.py:20 | 30.0 | 30.0 | 2 |', report)
        self.assertIn('[대화형 플레임 그래프](flamegraph.html)', report)


if __name__ == "__main__":
    unittest.main()
