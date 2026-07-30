from __future__ import annotations

import unittest
from pathlib import Path

from erasure_bench.config import load_benchmark_config
from erasure_bench.cli import _require_sandbox_acknowledgement


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_loads_fixed_codex_runtime_and_fixture(self):
        config = load_benchmark_config(ROOT / "benchmark.toml")

        self.assertEqual(config.agent.model, "gpt-5.6-sol")
        self.assertEqual(config.agent.reasoning_effort, "xhigh")
        self.assertEqual(config.agent.sandbox, "danger-full-access")
        self.assertEqual(config.agent.approval_policy, "never")
        self.assertEqual(config.arm_order[1:], ("visible_rewrite", "hidden_resynthesis"))
        self.assertEqual(config.tasks[0].id, "parser_backtracker")
        self.assertEqual(config.tasks[0].challenges[0].id, "add_unary_not")

    def test_hidden_target_has_signature_stub(self):
        config = load_benchmark_config(ROOT / "benchmark.toml")
        task = config.tasks[0]

        self.assertIsNotNone(task.stub_dir)
        stub = task.stub_dir / task.target_paths[0]
        self.assertTrue(stub.is_file())
        self.assertIn("NotImplementedError", stub.read_text(encoding="utf-8"))

    def test_danger_full_access_requires_explicit_run_acknowledgement(self):
        config = load_benchmark_config(ROOT / "benchmark.toml")

        with self.assertRaisesRegex(RuntimeError, "allow-danger-full-access"):
            _require_sandbox_acknowledgement(config, allowed=False)
        _require_sandbox_acknowledgement(config, allowed=True)


if __name__ == "__main__":
    unittest.main()
