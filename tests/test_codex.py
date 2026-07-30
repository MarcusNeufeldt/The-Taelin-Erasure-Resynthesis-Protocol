from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from erasure_bench.codex import build_codex_command, parse_event_log
from erasure_bench.config import load_benchmark_config


ROOT = Path(__file__).resolve().parents[1]


class CodexTests(unittest.TestCase):
    def test_builds_ephemeral_jsonl_xhigh_command(self):
        config = load_benchmark_config(ROOT / "benchmark.toml")
        with tempfile.TemporaryDirectory() as temp, patch(
            "erasure_bench.codex.resolve_codex_command",
            return_value="codex-test",
        ):
            command = build_codex_command(
                agent=config.agent,
                cwd=Path(temp),
                output_schema=ROOT / "schemas" / "agent_final.schema.json",
                output_last_message=Path(temp) / "final.json",
            )

        self.assertEqual(command[:3], ["codex-test", "exec", "--ephemeral"])
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("-m") + 1], "gpt-5.6-sol")
        self.assertEqual(
            command[command.index("-c") + 1],
            'model_reasoning_effort="xhigh"',
        )
        self.assertIn('approval_policy="never"', command)
        self.assertEqual(command[-1], "-")

    def test_parses_usage_and_item_counts_from_jsonl(self):
        events = [
            {"type": "thread.started", "thread_id": "thread-1"},
            {
                "type": "item.completed",
                "item": {"type": "command_execution"},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\nnot-json\n",
                encoding="utf-8",
            )
            summary = parse_event_log(path)

        self.assertEqual(summary.thread_id, "thread-1")
        self.assertEqual(summary.invalid_lines, 1)
        self.assertEqual(summary.usage["input_tokens"], 100)
        self.assertEqual(summary.item_counts["command_execution"], 1)


if __name__ == "__main__":
    unittest.main()
