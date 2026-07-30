from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from erasure_bench.config import load_benchmark_config
from erasure_bench.workspace import (
    immutable_violations,
    prepare_workspace,
)


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.config = load_benchmark_config(ROOT / "benchmark.toml")
        self.task = self.config.tasks[0]

    def test_hidden_workspace_has_no_original_implementation_or_history(self):
        arm = self.config.arms["hidden_resynthesis"]
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            prepared = prepare_workspace(self.task, arm, workspace)
            target = workspace / self.task.target_paths[0]
            current = target.read_text(encoding="utf-8")
            history = subprocess.run(
                ["git", "-C", str(workspace), "show", "HEAD:src/route_lang/parser.py"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout
            hooks_path = subprocess.run(
                ["git", "-C", str(workspace), "config", "--get", "core.hooksPath"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout.strip()

            self.assertIn("NotImplementedError", current)
            self.assertNotIn("class _Parser", current)
            self.assertNotIn("class _Parser", history)
            self.assertEqual(
                Path(hooks_path),
                workspace / ".git" / "benchmark-empty-hooks",
            )
            self.assertFalse(
                immutable_violations(workspace, prepared.immutable_hashes)
            )

    def test_detects_frozen_contract_change(self):
        arm = self.config.arms["visible_refactor"]
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            prepared = prepare_workspace(self.task, arm, workspace)
            (workspace / "CONTRACT.md").write_text("changed", encoding="utf-8")

            self.assertEqual(
                immutable_violations(workspace, prepared.immutable_hashes),
                ["CONTRACT.md"],
            )


if __name__ == "__main__":
    unittest.main()
