from __future__ import annotations

import unittest

from erasure_bench.report import summarize_results


class ReportTests(unittest.TestCase):
    def test_generation_failure_counts_as_pipeline_failure(self):
        result = {
            "plan": {
                "task_id": "fixture",
                "arm_name": "hidden_resynthesis",
                "repetition": 1,
            },
            "generation_passed": False,
            "codex": {
                "duration_seconds": 3,
                "event_summary": {
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                    "item_counts": {"command_execution": 1},
                },
            },
            "challenges": [
                {
                    "id": "change",
                    "status": "not_run_generation_failed",
                    "passed": False,
                    "codex": None,
                    "leakage_audit": None,
                }
            ],
            "leakage_audit": {"clean": True, "violations": []},
        }

        summary = summarize_results([result])["arms"]["hidden_resynthesis"]

        self.assertEqual(summary["generation"]["successes"], 0)
        self.assertEqual(summary["pipeline"]["total"], 1)
        self.assertEqual(summary["pipeline"]["successes"], 0)
        self.assertEqual(summary["usage"]["input_tokens"], 10)

    def test_paired_effect_is_clustered_by_fixture(self):
        results = []
        for task_id, hidden, visible in [
            ("a", [True, True], [False, True]),
            ("b", [True, False], [False, False]),
        ]:
            for arm, values in [
                ("hidden_resynthesis", hidden),
                ("visible_rewrite", visible),
            ]:
                for repetition, passed in enumerate(values, start=1):
                    results.append(
                        {
                            "plan": {
                                "task_id": task_id,
                                "arm_name": arm,
                                "repetition": repetition,
                            },
                            "generation_passed": passed,
                            "codex": None,
                            "challenges": [{"id": "change", "passed": passed}],
                            "leakage_audit": {"clean": True},
                        }
                    )

        summary = summarize_results(results, seed=7)
        comparison = next(
            item
            for item in summary["comparisons"]
            if item["control"] == "visible_rewrite"
            and item["endpoint"] == "pipeline"
        )

        self.assertEqual(comparison["fixture_blocks"], 2)
        self.assertEqual(comparison["improved_blocks"], 2)
        self.assertAlmostEqual(comparison["effect"], 0.5)
        self.assertIsNotNone(comparison["cluster_bootstrap_95"])


if __name__ == "__main__":
    unittest.main()
