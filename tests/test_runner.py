from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from erasure_bench.config import load_benchmark_config
from erasure_bench.runner import _freeze_inputs, build_generation_prompt, build_plan


ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_benchmark_config(ROOT / "benchmark.toml")

    def test_plan_is_seeded_and_interleaves_arms_by_block(self):
        first = build_plan(self.config)
        second = build_plan(self.config)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 9)
        for offset in range(0, len(first), 3):
            block = first[offset : offset + 3]
            self.assertEqual(len({item.repetition for item in block}), 1)
            self.assertEqual(len({item.arm_name for item in block}), 3)

    def test_visible_rewrite_and_hidden_resynthesis_share_rewrite_goal(self):
        task = self.config.tasks[0]
        visible = build_generation_prompt(
            task,
            self.config.arms["visible_rewrite"],
            workspace=task.seed_dir,
        )
        hidden = build_generation_prompt(
            task,
            self.config.arms["hidden_resynthesis"],
            workspace=task.seed_dir,
        )

        self.assertIn("Re-derive the implementation", visible)
        self.assertIn("Re-derive the implementation", hidden)
        self.assertEqual(
            self.config.arms["visible_rewrite"].instruction,
            self.config.arms["hidden_resynthesis"].instruction,
        )
        self.assertIn("gpt-5.6-sol", self.config.agent.model)
        self.assertNotIn("hidden-parser-behavior", visible)
        self.assertNotIn("hidden-parser-behavior", hidden)

    def test_freezes_tasks_and_oracles_inside_run(self):
        with tempfile.TemporaryDirectory() as temp:
            frozen_tasks, schema, manifest = _freeze_inputs(
                self.config,
                Path(temp),
            )

            frozen = frozen_tasks["parser_backtracker"]
            self.assertTrue(schema.is_file())
            self.assertTrue(frozen.root.is_relative_to(Path(temp)))
            self.assertTrue((frozen.root / "verify_hidden.py").is_file())
            self.assertEqual(len(manifest["frozen_tree_hash"]), 64)


if __name__ == "__main__":
    unittest.main()
