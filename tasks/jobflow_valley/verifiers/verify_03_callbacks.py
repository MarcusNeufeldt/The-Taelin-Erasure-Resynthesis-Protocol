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
    calls: list[tuple[str, str, int]] = []

    def first(view) -> None:
        calls.append(("first", view.job_id, view.attempt))

    def broken(view) -> None:
        calls.append(("broken", view.job_id, view.attempt))
        raise RuntimeError("callback boom")

    def last(view) -> None:
        calls.append(("last", view.job_id, view.attempt))

    engine.register_completion_callback("first", first)
    engine.register_completion_callback("broken", broken)
    engine.register_completion_callback("last", last)
    expect_value_error(
        lambda: engine.register_completion_callback("first", first)
    )
    expect_value_error(
        lambda: engine.register_completion_callback("", first)
    )
    expect_value_error(
        lambda: engine.register_completion_callback("not-callable", 3)
    )

    engine.submit("job", ["one"])
    engine.start("job")
    final = engine.complete_step("job", "one")
    assert final.status == "completed"
    assert calls == [
        ("first", "job", 1),
        ("broken", "job", 1),
        ("last", "job", 1),
    ]
    engine.complete_step("job", "one")
    engine.drain_events()
    engine.update_metadata("job", {"after": True})
    assert len(calls) == 3
    errors = engine.callback_errors()
    assert errors == (
        {
            "job_id": "job",
            "attempt": 1,
            "callback": "broken",
            "error": "callback boom",
        },
    )
    mutated = errors[0]
    mutated["error"] = "changed"
    assert engine.callback_errors()[0]["error"] == "callback boom"

    engine.unregister_completion_callback("broken")
    engine.unregister_completion_callback("unknown")
    engine.submit("retry", ["one"])
    engine.start("retry")
    engine.fail_step("retry", "one", "first attempt")
    engine.restart("retry")
    engine.start("retry")
    engine.complete_step("retry", "one")
    assert calls[-2:] == [
        ("first", "retry", 2),
        ("last", "retry", 2),
    ]


if __name__ == "__main__":
    main(sys.argv[1])
