from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from erasure_bench.loop_config import load_loop_config
from erasure_bench.loop_runner import (
    _select_candidate,
    _validate_decision,
    build_loop_plan,
    count_debt_markers,
    erase_python_implementation,
)
from erasure_bench.workspace import hash_tree


ROOT = Path(__file__).resolve().parents[1]


class LoopRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_loop_config(ROOT / "loop.toml")

    def test_plan_is_seeded_balanced_and_filterable(self) -> None:
        first = build_loop_plan(self.config)
        second = build_loop_plan(self.config)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 12)
        counts = {
            name: sum(item.controller_name == name for item in first)
            for name in self.config.controller_order
        }
        self.assertEqual(set(counts.values()), {3})
        filtered = build_loop_plan(
            self.config,
            controller_names={"self_erasure"},
            repetitions=1,
            max_episodes=1,
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].controller_name, "self_erasure")

    def test_erasure_preserves_signatures_but_removes_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "target.py"
            path.write_text(
                '''
CONSTANT = 3

def public(value: int = 1) -> int:
    """contract"""
    secret = value + CONSTANT
    return secret

class Service:
    label = "visible"

    def run(self, item: str) -> str:
        hidden = item.upper()
        return hidden
'''.lstrip(),
                encoding="utf-8",
            )
            erase_python_implementation(path)
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            self.assertIn("def public(value: int=1) -> int:", text)
            self.assertIn("def run(self, item: str) -> str:", text)
            self.assertNotIn("secret =", text)
            self.assertNotIn("hidden =", text)
            functions = [
                node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
            ]
            self.assertTrue(
                all(
                    any(
                        isinstance(item, ast.Raise)
                        or (
                            isinstance(item, ast.Expr)
                            and isinstance(item.value, ast.Constant)
                        )
                        for item in node.body
                    )
                    for node in functions
                )
            )

    def test_contract_decision_rejects_ineligible_paths_and_size(self) -> None:
        task = self.config.task
        response = {
            "status": "completed",
            "should_compact": True,
            "selected_paths": ["outside.py"],
            "contract": "x" * 20,
            "rationale": "rewrite valley",
        }
        decision = _validate_decision(
            response,
            task=task,
            max_chars=10,
            source_workspace=task.seed_dir,
        )
        self.assertFalse(decision["valid"])
        self.assertEqual(decision["selected_paths"], [])
        self.assertEqual(len(decision["contract"]), 10)
        self.assertGreaterEqual(len(decision["errors"]), 2)

    def test_contract_leakage_tracks_private_architecture_not_public_api(self) -> None:
        task = self.config.task
        decision = _validate_decision(
            {
                "status": "completed",
                "should_compact": True,
                "selected_paths": ["src/jobflow/engine.py"],
                "contract": "Call start publicly; do not copy _status.",
                "rationale": "rewrite valley",
            },
            task=task,
            max_chars=1000,
            source_workspace=task.seed_dir,
        )
        self.assertTrue(decision["valid"])
        self.assertEqual(decision["architecture_terms"], ["_status"])

    def test_debt_markers_find_seed_flag_architecture(self) -> None:
        counts = count_debt_markers(self.config.task.seed_dir, self.config.task)
        self.assertGreater(counts["lifecycle_boolean_keys"], 20)
        self.assertGreater(counts["boolean_branch_conjunctions"], 0)

    def test_candidate_selection_prefers_passing_lower_debt(self) -> None:
        base = {
            "passed": True,
            "metrics": {"cyclomatic_proxy": 10, "lines": 100},
            "diff": {"lines_added": 10, "lines_deleted": 10},
        }
        candidates = [
            {**base, "debt_markers": {"flags": 8}},
            {**base, "debt_markers": {"flags": 2}},
            {**base, "passed": False, "debt_markers": {"flags": 0}},
        ]
        self.assertEqual(_select_candidate(candidates), 1)

    def test_harness_hash_ignores_loop_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.py").write_text("value = 1\n", encoding="utf-8")
            before = hash_tree(root)
            (root / "loop-runs" / "run").mkdir(parents=True)
            (root / "loop-runs" / "run" / "events.jsonl").write_text(
                '{"private": "runtime artifact"}\n',
                encoding="utf-8",
            )
            self.assertEqual(hash_tree(root), before)


if __name__ == "__main__":
    unittest.main()
