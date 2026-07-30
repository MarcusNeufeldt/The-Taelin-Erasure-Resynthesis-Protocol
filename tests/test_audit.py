from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from erasure_bench.audit import audit_event_log


class AuditTests(unittest.TestCase):
    def _write(self, root: Path, commands: list[str]) -> Path:
        path = root / "events.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": command,
                        },
                    }
                )
                for command in commands
            ),
            encoding="utf-8",
        )
        return path

    def test_accepts_workspace_local_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self._write(root, ["rg --files", "python -m unittest -v"])
            audit = audit_event_log(events, workspace=root)

        self.assertTrue(audit.clean)
        self.assertEqual(audit.commands_audited, 2)

    def test_rejects_network_history_and_oracle_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oracle = root.parent / "fixture-oracle"
            events = self._write(
                root,
                [
                    "git show HEAD~1:parser.py",
                    "curl https://example.test",
                    f"Get-Content '{oracle / 'verify_hidden.py'}'",
                ],
            )
            audit = audit_event_log(
                events,
                workspace=root,
                forbidden_paths=(oracle,),
                forbidden_names=("verify_hidden.py",),
            )

        categories = {item.category for item in audit.violations}
        self.assertFalse(audit.clean)
        self.assertIn("git-history", categories)
        self.assertIn("network", categories)
        self.assertIn("forbidden-path", categories)
        self.assertIn("forbidden-name", categories)


if __name__ == "__main__":
    unittest.main()
