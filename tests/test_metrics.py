from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from erasure_bench.config import load_benchmark_config
from erasure_bench.metrics import capture_git_diff, collect_source_metrics
from erasure_bench.workspace import initialize_repository


ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def test_collects_deterministic_python_metrics(self):
        config = load_benchmark_config(ROOT / "benchmark.toml")
        task = config.tasks[0]

        metrics = collect_source_metrics(task.seed_dir, task.metric_paths)

        self.assertGreater(metrics.lines, 100)
        self.assertGreater(metrics.ast_nodes, 100)
        self.assertGreater(metrics.branch_points, 5)
        self.assertEqual(metrics.python_parse_errors, 0)
        self.assertFalse(metrics.missing_paths)
        self.assertEqual(len(metrics.source_hash), 64)

    def test_reports_missing_metric_path(self):
        with tempfile.TemporaryDirectory() as temp:
            metrics = collect_source_metrics(Path(temp), ("missing.py",))

        self.assertEqual(metrics.files, 0)
        self.assertEqual(metrics.missing_paths, ["missing.py"])

    def test_diff_includes_untracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "tracked.txt").write_text("base\n", encoding="utf-8")
            initialize_repository(root)
            (root / "new.txt").write_text("new\n", encoding="utf-8")

            diff = capture_git_diff(root, root / "artifacts")

        self.assertEqual(diff.files_changed, 1)
        self.assertEqual(diff.lines_added, 1)


if __name__ == "__main__":
    unittest.main()
