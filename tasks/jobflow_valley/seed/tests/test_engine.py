from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobflow import WorkflowEngine  # noqa: E402


class WorkflowEngineTests(unittest.TestCase):
    def test_happy_path_advances_and_completes(self) -> None:
        engine = WorkflowEngine()
        submitted = engine.submit(
            "build-1",
            ["compile", "package"],
            metadata={"branch": "main"},
        )
        self.assertEqual(submitted.status, "queued")
        self.assertEqual(submitted.attempt, 1)

        started = engine.start("build-1")
        self.assertEqual(started.status, "running")
        self.assertEqual(started.current_step, "compile")
        self.assertEqual(started.steps[0].attempts, 1)

        advanced = engine.complete_step(
            "build-1",
            "compile",
            output={"warnings": 0},
        )
        self.assertEqual(advanced.status, "running")
        self.assertEqual(advanced.current_step, "package")
        self.assertEqual(advanced.steps[0].status, "completed")
        self.assertEqual(advanced.steps[1].status, "running")

        completed = engine.complete_step("build-1", "package", output="ok")
        self.assertEqual(completed.status, "completed")
        self.assertEqual(engine.completed_outputs("build-1")["package"], "ok")

    def test_failure_retry_and_pause_resume(self) -> None:
        engine = WorkflowEngine()
        engine.submit("deploy-1", ["upload", "activate"])
        engine.start("deploy-1")
        failed = engine.fail_step("deploy-1", "upload", "network")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failure, "network")

        retried = engine.retry_step("deploy-1")
        self.assertEqual(retried.status, "running")
        self.assertEqual(retried.steps[0].attempts, 2)
        self.assertIsNone(retried.failure)

        paused = engine.pause("deploy-1")
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.current_step, "upload")
        resumed = engine.resume("deploy-1")
        self.assertEqual(resumed.status, "running")
        self.assertEqual(resumed.current_step, "upload")

    def test_events_are_ordered_and_drained_once(self) -> None:
        engine = WorkflowEngine()
        engine.submit("one", ["a"])
        engine.start("one")
        engine.complete_step("one", "a")
        events = engine.drain_events()
        self.assertEqual(
            [event.kind for event in events],
            ["submitted", "started", "step_completed", "completed"],
        )
        self.assertEqual(
            [event.sequence for event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(engine.drain_events(), ())

    def test_validation_and_idempotence(self) -> None:
        engine = WorkflowEngine()
        with self.assertRaises(ValueError):
            engine.submit("", ["a"])
        with self.assertRaises(ValueError):
            engine.submit("x", ["a", "a"])
        engine.submit("x", ["a"])
        with self.assertRaises(ValueError):
            engine.submit("x", ["b"])
        engine.start("x")
        event_count = len(engine.peek_events())
        engine.start("x")
        self.assertEqual(len(engine.peek_events()), event_count)
        engine.complete_step("x", "a")
        event_count = len(engine.peek_events())
        engine.complete_step("x", "a")
        self.assertEqual(len(engine.peek_events()), event_count)

    def test_listing_metadata_and_pending_steps(self) -> None:
        engine = WorkflowEngine()
        engine.submit("b", ["one", "two"], metadata={"owner": "a"})
        engine.submit("a", ["only"])
        engine.start("b")
        self.assertEqual([job.job_id for job in engine.list_jobs()], ["a", "b"])
        self.assertEqual(
            [job.job_id for job in engine.list_jobs(status="running")],
            ["b"],
        )
        updated = engine.update_metadata("b", {"owner": "b", "priority": 2})
        self.assertEqual(updated.metadata, {"owner": "b", "priority": 2})
        self.assertEqual(engine.pending_steps("b"), ("two",))


if __name__ == "__main__":
    unittest.main()
