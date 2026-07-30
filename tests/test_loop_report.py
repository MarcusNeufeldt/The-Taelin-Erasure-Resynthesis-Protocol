from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from erasure_bench.loop_report import (
    generate_loop_report,
    summarize_loop_results,
)


def episode(controller: str, completed: int, total: int = 5) -> dict:
    steps = []
    for index in range(min(completed + 1, total)):
        passed = index < completed
        steps.append(
            {
                "passed": passed,
                "rollback": not passed,
                "budget_after": {
                    "total_tokens": (index + 1) * 100,
                    "agent_seconds": (index + 1) * 2.0,
                },
                "selected_candidate": (
                    {
                        "diff": {
                            "files_changed": 1,
                            "lines_added": 3,
                            "lines_deleted": 2,
                        }
                    }
                    if passed
                    else None
                ),
                "controller": {"decision": None},
            }
        )
        if not passed:
            break
    metrics = {
        "lines": 100,
        "code_lines": 90,
        "ast_nodes": 300,
        "branch_points": 20,
        "cyclomatic_proxy": 30,
        "max_nesting": 4,
    }
    final_metrics = {**metrics, "lines": 105}
    return {
        "plan": {"controller_name": controller},
        "steps_total": total,
        "steps_attempted": len(steps),
        "steps_completed_before_failure": completed,
        "trajectory_success": completed == total,
        "budget_within_limits": True,
        "budget": {
            "calls": len(steps) * 2,
            "total_tokens": len(steps) * 100,
            "agent_seconds": len(steps) * 2.0,
        },
        "baseline_metrics": metrics,
        "final_metrics": final_metrics,
        "baseline_debt_markers": {"flags": 10},
        "final_debt_markers": {"flags": 5},
        "steps": steps,
    }


class LoopReportTests(unittest.TestCase):
    def test_summary_reports_horizon_and_compute_slope(self) -> None:
        summary = summarize_loop_results(
            [episode("control", 5), episode("control", 2)],
            task_sequence=["a", "b", "c", "d", "e"],
        )
        control = summary["controllers"]["control"]
        self.assertEqual(control["trajectory_success"]["successes"], 1)
        self.assertEqual(control["horizon_success"]["2"]["successes"], 2)
        self.assertEqual(control["horizon_success"]["3"]["successes"], 1)
        self.assertEqual(control["steps_completed_median"], 3.5)
        self.assertIsNotNone(control["usage"]["tokens_per_accepted_step"])
        self.assertEqual(control["debt_markers"]["survival_rate"], 0.5)
        self.assertEqual(
            control["maintenance_by_step"]["5"]["incremental_tokens_median"],
            100,
        )

    def test_generate_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "episodes" / "one").mkdir(parents=True)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "test",
                        "task_sequence": ["a", "b", "c", "d", "e"],
                        "agent": {
                            "model": "gpt-5.6-sol",
                            "reasoning_effort": "xhigh",
                        },
                        "calls_per_step": 2,
                    }
                ),
                encoding="utf-8",
            )
            (root / "episodes" / "one" / "result.json").write_text(
                json.dumps(episode("control", 5)),
                encoding="utf-8",
            )
            summary_path, report_path = generate_loop_report(root)
            self.assertTrue(summary_path.is_file())
            self.assertIn("Full trajectory", report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
