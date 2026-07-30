from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from erasure_bench.config import ConfigError
from erasure_bench.loop_config import load_loop_config, load_trajectory_task


ROOT = Path(__file__).resolve().parents[1]


class LoopConfigTests(unittest.TestCase):
    def test_repository_loop_config_is_compute_matched(self) -> None:
        config = load_loop_config(ROOT / "loop.toml")
        self.assertEqual(config.agent.model, "gpt-5.6-sol")
        self.assertEqual(config.agent.reasoning_effort, "xhigh")
        self.assertEqual(config.calls_per_step, 2)
        self.assertEqual(len(config.task.steps), 5)
        self.assertEqual(
            set(config.controller_order),
            {
                "ordinary_maintenance",
                "visible_rewrite",
                "adversarial_review",
                "self_erasure",
            },
        )
        self.assertTrue(
            all(
                controller.calls_per_step == config.calls_per_step
                for controller in config.controllers.values()
            )
        )

    def test_invalid_debt_marker_regex_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seed = root / "seed"
            (seed / "src").mkdir(parents=True)
            (seed / "src" / "target.py").write_text(
                "def f():\n    return 1\n",
                encoding="utf-8",
            )
            (seed / "LOCKED").write_text("locked", encoding="utf-8")
            (root / "step.md").write_text("change it", encoding="utf-8")
            (root / "trajectory.toml").write_text(
                """
id = "mini"
seed_dir = "seed"
target_paths = ["src/target.py"]
erasable_paths = ["src/target.py"]
metric_paths = ["src"]
immutable_paths = ["LOCKED"]
[[baseline_checks]]
name = "baseline"
command = ["{python}", "-c", "print('ok')"]
[[steps]]
id = "one"
prompt_file = "step.md"
[[steps.hidden_checks]]
name = "hidden"
command = ["{python}", "-c", "print('ok')"]
[[debt_markers]]
name = "broken"
pattern = "["
paths = ["src/target.py"]
""".strip(),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "valid regular expression"):
                load_trajectory_task(root / "trajectory.toml")


if __name__ == "__main__":
    unittest.main()
