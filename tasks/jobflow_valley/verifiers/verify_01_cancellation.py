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


def main(workspace: str) -> None:
    WorkflowEngine = load_engine(workspace)
    engine = WorkflowEngine()
    engine.submit("queued", ["one"])
    before = len(engine.peek_events())
    view = engine.cancel("queued", "operator request")
    assert view.status == "cancelled"
    assert view.current_step is None
    assert view.failure == "operator request"
    assert engine.get("queued") == view
    assert len(engine.peek_events()) == before + 1
    assert engine.peek_events()[-1].kind == "cancelled"
    assert engine.peek_events()[-1].detail["reason"] == "operator request"

    repeated = engine.cancel("queued", "different")
    assert repeated.failure == "operator request"
    assert len(engine.peek_events()) == before + 1
    assert [job.job_id for job in engine.list_jobs(status="cancelled")] == [
        "queued"
    ]

    engine.submit("running", ["one", "two"])
    engine.start("running")
    assert engine.cancel("running").status == "cancelled"
    engine.submit("paused", ["one"])
    engine.start("paused")
    engine.pause("paused")
    assert engine.cancel("paused").status == "cancelled"
    engine.submit("failed", ["one"])
    engine.start("failed")
    engine.fail_step("failed", "one", "boom")
    assert engine.cancel("failed", "abandoned").status == "cancelled"

    engine.submit("done", ["one"])
    engine.start("done")
    engine.complete_step("done", "one")
    expect_value_error(lambda: engine.cancel("done"))
    expect_value_error(lambda: engine.cancel("running", ""))


if __name__ == "__main__":
    main(sys.argv[1])
