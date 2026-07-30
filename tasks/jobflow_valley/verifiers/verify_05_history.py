from __future__ import annotations

import sys
from pathlib import Path


FORBIDDEN_PREFIXES = ("is_", "has_", "was_", "should_")
REQUIRED_KEYS = {
    "sequence",
    "from_state",
    "to_state",
    "action",
    "reason",
    "attempt",
}


def load_engine(workspace: str):
    sys.path.insert(0, str(Path(workspace) / "src"))
    from jobflow import WorkflowEngine

    return WorkflowEngine


def assert_no_flag_keys(value, *, path: str = "record") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert not str(key).startswith(FORBIDDEN_PREFIXES), (
                f"lifecycle flag key remains at {path}.{key}"
            )
            assert_no_flag_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_flag_keys(child, path=f"{path}[{index}]")


def main(workspace: str) -> None:
    WorkflowEngine = load_engine(workspace)
    engine = WorkflowEngine()
    engine.submit("history", ["one", "two"])
    engine.start("history")
    engine.pause("history")
    before = engine.explain("history")
    engine.pause("history")
    assert engine.explain("history") == before
    engine.resume("history")
    engine.fail_step("history", "one", "network")
    engine.restart("history")
    engine.start("history")
    engine.complete_step("history", "one")
    engine.complete_step("history", "two")

    history = engine.explain("history")
    expected_actions = [
        "submit",
        "start",
        "pause",
        "resume",
        "fail_step",
        "restart",
        "start",
        "complete_step",
        "complete_step",
    ]
    assert [entry["action"] for entry in history] == expected_actions
    assert history[0]["from_state"] is None
    assert history[0]["to_state"] == "queued"
    assert history[-1]["to_state"] == "completed"
    assert [entry["sequence"] for entry in history] == sorted(
        entry["sequence"] for entry in history
    )
    assert len({entry["sequence"] for entry in history}) == len(history)
    for entry in history:
        assert set(entry) == REQUIRED_KEYS
        assert isinstance(entry["reason"], str) and entry["reason"].strip()
        assert entry["attempt"] in {1, 2}

    history[0]["reason"] = "tampered"
    assert engine.explain("history")[0]["reason"] != "tampered"

    snapshot = engine.snapshot()
    record = snapshot["jobs"]["history"]
    assert isinstance(record.get("state"), str)
    assert all(
        isinstance(step.get("status"), str) for step in record["steps"]
    )
    assert_no_flag_keys(record)
    restored = WorkflowEngine.restore(snapshot)
    assert restored.explain("history") == engine.explain("history")

    engine.submit("cancel", ["one"])
    engine.cancel("cancel", "operator")
    assert [entry["action"] for entry in engine.explain("cancel")] == [
        "submit",
        "cancel",
    ]


if __name__ == "__main__":
    main(sys.argv[1])
