from __future__ import annotations

import sys
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


def assert_reset(view) -> None:
    assert view.status == "queued"
    assert view.current_step is None
    assert view.failure is None
    assert all(step.status == "pending" for step in view.steps)
    assert all(step.attempts == 0 for step in view.steps)
    assert all(step.output is None and step.error is None for step in view.steps)


def main(workspace: str) -> None:
    WorkflowEngine = load_engine(workspace)
    engine = WorkflowEngine()
    engine.submit("failed", ["fetch", "store"], metadata={"owner": "ops"})
    engine.start("failed")
    engine.complete_step("failed", "fetch", output={"rows": 3})
    engine.fail_step("failed", "store", "disk full")
    before = len(engine.peek_events())
    restarted = engine.restart("failed")
    assert restarted.attempt == 2
    assert restarted.metadata == {"owner": "ops"}
    assert_reset(restarted)
    events = engine.peek_events()
    assert len(events) == before + 1
    assert events[-1].kind == "restarted"
    assert events[-1].detail["attempt"] == 2
    assert engine.start("failed").current_step == "fetch"

    engine.submit("cancelled", ["one"])
    engine.cancel("cancelled", "later")
    assert_reset(engine.restart("cancelled"))

    engine.submit("queued", ["one"])
    expect_value_error(lambda: engine.restart("queued"))
    engine.start("queued")
    expect_value_error(lambda: engine.restart("queued"))
    engine.complete_step("queued", "one")
    expect_value_error(lambda: engine.restart("queued"))


if __name__ == "__main__":
    main(sys.argv[1])
