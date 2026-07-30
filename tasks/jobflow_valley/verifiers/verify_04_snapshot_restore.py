from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path


def load_engine(workspace: str):
    sys.path.insert(0, str(Path(workspace) / "src"))
    from jobflow import WorkflowEngine

    return WorkflowEngine


def expect_value_error(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def main(workspace: str) -> None:
    WorkflowEngine = load_engine(workspace)
    engine = WorkflowEngine()
    completed_calls: list[tuple[str, int]] = []
    engine.register_completion_callback(
        "audit",
        lambda view: completed_calls.append((view.job_id, view.attempt)),
    )

    engine.submit("done", ["one"], metadata={"nested": {"v": 1}})
    engine.start("done")
    engine.complete_step("done", "one", output={"ok": True})
    assert completed_calls == [("done", 1)]

    engine.submit("active", ["one", "two"])
    engine.start("active")
    engine.complete_step("active", "one", output=[1, 2])
    before_views = {job.job_id: job for job in engine.list_jobs()}
    before_events = engine.peek_events()

    snapshot = engine.snapshot()
    assert snapshot["version"] == 1
    assert isinstance(snapshot["jobs"], dict)
    encoded = json.dumps(snapshot, sort_keys=True)
    round_tripped = json.loads(encoded)
    snapshot_copy = deepcopy(round_tripped)
    restored = WorkflowEngine.restore(round_tripped)
    assert round_tripped == snapshot_copy
    assert {job.job_id: job for job in restored.list_jobs()} == before_views
    assert restored.peek_events() == before_events

    round_tripped["jobs"]["active"]["metadata"]["tamper"] = True
    assert "tamper" not in restored.get("active").metadata
    new_calls: list[tuple[str, int]] = []
    restored.register_completion_callback(
        "audit",
        lambda view: new_calls.append((view.job_id, view.attempt)),
    )
    restored.complete_step("done", "one")
    assert new_calls == []

    max_sequence = max(event.sequence for event in restored.peek_events())
    restored.complete_step("active", "two")
    assert restored.status("active") == "completed"
    assert restored.peek_events()[-1].sequence > max_sequence
    assert new_calls == [("active", 1)]

    expect_value_error(lambda: WorkflowEngine.restore({}))
    malformed = engine.snapshot()
    malformed["version"] = 999
    expect_value_error(lambda: WorkflowEngine.restore(malformed))
    malformed = engine.snapshot()
    malformed["jobs"]["active"]["current_index"] = 999
    expect_value_error(lambda: WorkflowEngine.restore(malformed))


if __name__ == "__main__":
    main(sys.argv[1])
