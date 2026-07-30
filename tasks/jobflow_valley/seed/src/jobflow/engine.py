from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

from .model import Event, JobView, StepView


class WorkflowEngine:
    """A deliberately mature-looking workflow engine built around flag state.

    The implementation is internally consistent for its original requirements,
    but each transition must coordinate overlapping booleans. That makes small
    changes cheap at first and increasingly dangerous across a maintenance
    trajectory.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: list[Event] = []
        self._next_sequence = 1

    # ------------------------------------------------------------------
    # Validation and record construction
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        if not isinstance(job_id, str):
            raise ValueError("job_id must be a string")
        normalized = job_id.strip()
        if not normalized:
            raise ValueError("job_id must not be empty")
        if normalized != job_id:
            raise ValueError("job_id must not have surrounding whitespace")
        return normalized

    @staticmethod
    def _validate_steps(steps: Iterable[str]) -> list[str]:
        if isinstance(steps, (str, bytes)):
            raise ValueError("steps must be an iterable of names")
        try:
            materialized = list(steps)
        except TypeError as exc:
            raise ValueError("steps must be iterable") from exc
        if not materialized:
            raise ValueError("a job needs at least one step")
        normalized: list[str] = []
        seen: set[str] = set()
        for step in materialized:
            if not isinstance(step, str):
                raise ValueError("step names must be strings")
            name = step.strip()
            if not name or name != step:
                raise ValueError("step names must be non-empty and trimmed")
            if name in seen:
                raise ValueError(f"duplicate step name: {name}")
            seen.add(name)
            normalized.append(name)
        return normalized

    @staticmethod
    def _validate_metadata(
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if metadata is None:
            return {}
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        result: dict[str, Any] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not key:
                raise ValueError("metadata keys must be non-empty strings")
            result[key] = deepcopy(value)
        return result

    @classmethod
    def _new_record(
        cls,
        job_id: str,
        steps: list[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "attempt": 1,
            "metadata": metadata,
            "steps": [
                {
                    "name": name,
                    "is_pending": True,
                    "is_running": False,
                    "is_completed": False,
                    "is_failed": False,
                    "was_retried": False,
                    "attempts": 0,
                    "output": None,
                    "error": None,
                }
                for name in steps
            ],
            "current_index": None,
            "is_queued": True,
            "is_started": False,
            "is_running": False,
            "is_paused": False,
            "is_failed": False,
            "is_completed": False,
            "has_failure": False,
            "has_started_step": False,
            "should_retry": False,
            "was_retried": False,
            "is_locked": False,
            "failure": None,
            "revision": 0,
        }

    def _require(self, job_id: str) -> dict[str, Any]:
        normalized = self._validate_job_id(job_id)
        try:
            return self._jobs[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown job: {normalized}") from exc

    @staticmethod
    def _require_step(
        record: dict[str, Any],
        step_name: str,
    ) -> tuple[int, dict[str, Any]]:
        if not isinstance(step_name, str) or not step_name:
            raise ValueError("step_name must be a non-empty string")
        for index, step in enumerate(record["steps"]):
            if step["name"] == step_name:
                return index, step
        raise ValueError(f"unknown step: {step_name}")

    # ------------------------------------------------------------------
    # Status derivation and consistency checks
    # ------------------------------------------------------------------

    @staticmethod
    def _status(record: dict[str, Any]) -> str:
        if record["is_completed"]:
            return "completed"
        if record["is_failed"] or record["has_failure"]:
            return "failed"
        if record["is_paused"]:
            return "paused"
        if record["is_running"] and record["is_started"]:
            return "running"
        if record["is_queued"] and not record["is_started"]:
            return "queued"
        if record["is_running"]:
            return "running"
        return "queued"

    @staticmethod
    def _step_status(step: dict[str, Any]) -> str:
        if step["is_completed"]:
            return "completed"
        if step["is_failed"]:
            return "failed"
        if step["is_running"]:
            return "running"
        return "pending"

    @staticmethod
    def _current_step(record: dict[str, Any]) -> dict[str, Any] | None:
        index = record["current_index"]
        if index is None:
            return None
        if index < 0 or index >= len(record["steps"]):
            raise RuntimeError("corrupt current step index")
        return record["steps"][index]

    @classmethod
    def _assert_consistent(cls, record: dict[str, Any]) -> None:
        terminal_flags = int(record["is_failed"]) + int(record["is_completed"])
        if terminal_flags > 1:
            raise RuntimeError("job has multiple terminal flags")
        if record["is_completed"] and record["is_running"]:
            raise RuntimeError("completed job is still running")
        if record["is_failed"] and not record["has_failure"]:
            raise RuntimeError("failed job has no failure marker")
        if record["is_paused"] and record["is_running"]:
            raise RuntimeError("paused job is still running")
        if record["is_queued"] and record["is_started"]:
            raise RuntimeError("queued job is already started")

        running_steps = [
            step for step in record["steps"] if step["is_running"]
        ]
        failed_steps = [
            step for step in record["steps"] if step["is_failed"]
        ]
        if len(running_steps) > 1:
            raise RuntimeError("job has multiple running steps")
        if len(failed_steps) > 1:
            raise RuntimeError("job has multiple failed steps")
        if record["is_running"] and len(running_steps) != 1:
            raise RuntimeError("running job needs exactly one running step")
        if record["is_failed"] and len(failed_steps) != 1:
            raise RuntimeError("failed job needs exactly one failed step")
        if record["is_completed"] and any(
            not step["is_completed"] for step in record["steps"]
        ):
            raise RuntimeError("completed job has incomplete steps")

        current = cls._current_step(record)
        if record["has_started_step"] and current is None:
            raise RuntimeError("started-step marker lacks a current step")
        if current is not None and not (
            current["is_running"]
            or (record["is_paused"] and current["is_pending"])
            or current["is_failed"]
            or current["is_completed"]
        ):
            raise RuntimeError("current step has no active or terminal flag")

    # ------------------------------------------------------------------
    # Views and event delivery
    # ------------------------------------------------------------------

    def _emit(
        self,
        kind: str,
        record: dict[str, Any],
        **detail: Any,
    ) -> None:
        event = Event(
            sequence=self._next_sequence,
            kind=kind,
            job_id=record["job_id"],
            detail=deepcopy(detail),
        )
        self._next_sequence += 1
        self._events.append(event)

    @classmethod
    def _view(cls, record: dict[str, Any]) -> JobView:
        current = cls._current_step(record)
        return JobView(
            job_id=record["job_id"],
            status=cls._status(record),
            steps=tuple(
                StepView(
                    name=step["name"],
                    status=cls._step_status(step),
                    attempts=step["attempts"],
                    output=deepcopy(step["output"]),
                    error=step["error"],
                )
                for step in record["steps"]
            ),
            current_step=current["name"] if current is not None else None,
            attempt=record["attempt"],
            metadata=deepcopy(record["metadata"]),
            failure=record["failure"],
        )

    def get(self, job_id: str) -> JobView:
        record = self._require(job_id)
        self._assert_consistent(record)
        return self._view(record)

    def status(self, job_id: str) -> str:
        return self.get(job_id).status

    def list_jobs(self, *, status: str | None = None) -> tuple[JobView, ...]:
        allowed = {"queued", "running", "paused", "failed", "completed"}
        if status is not None and status not in allowed:
            raise ValueError(f"unknown status filter: {status}")
        views = [
            self._view(record)
            for job_id, record in sorted(self._jobs.items())
            if status is None or self._status(record) == status
        ]
        return tuple(views)

    def peek_events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def drain_events(self) -> tuple[Event, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    # ------------------------------------------------------------------
    # Baseline lifecycle operations
    # ------------------------------------------------------------------

    def submit(
        self,
        job_id: str,
        steps: Iterable[str],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> JobView:
        normalized_id = self._validate_job_id(job_id)
        if normalized_id in self._jobs:
            raise ValueError(f"duplicate job: {normalized_id}")
        normalized_steps = self._validate_steps(steps)
        normalized_metadata = self._validate_metadata(metadata)
        record = self._new_record(
            normalized_id,
            normalized_steps,
            normalized_metadata,
        )
        self._jobs[normalized_id] = record
        self._emit(
            "submitted",
            record,
            steps=list(normalized_steps),
            attempt=record["attempt"],
        )
        self._assert_consistent(record)
        return self._view(record)

    def start(self, job_id: str) -> JobView:
        record = self._require(job_id)
        if record["is_completed"]:
            return self._view(record)
        if record["is_failed"] or record["has_failure"]:
            raise ValueError("failed job must be retried")
        if record["is_paused"]:
            raise ValueError("paused job must be resumed")
        if record["is_running"] and record["is_started"]:
            return self._view(record)
        if not record["is_queued"] or record["is_started"]:
            raise ValueError("job cannot be started")

        record["is_queued"] = False
        record["is_started"] = True
        record["is_running"] = True
        record["is_paused"] = False
        record["is_failed"] = False
        record["is_completed"] = False
        record["has_failure"] = False
        record["should_retry"] = False
        record["current_index"] = 0
        record["has_started_step"] = True
        record["revision"] += 1

        step = record["steps"][0]
        step["is_pending"] = False
        step["is_running"] = True
        step["is_completed"] = False
        step["is_failed"] = False
        step["attempts"] += 1
        self._emit(
            "started",
            record,
            step=step["name"],
            attempt=record["attempt"],
        )
        self._assert_consistent(record)
        return self._view(record)

    def complete_step(
        self,
        job_id: str,
        step_name: str,
        *,
        output: Any = None,
    ) -> JobView:
        record = self._require(job_id)
        index, step = self._require_step(record, step_name)
        if step["is_completed"]:
            return self._view(record)
        if record["is_completed"]:
            raise ValueError("completed job cannot accept step results")
        if not record["is_running"] or record["is_paused"]:
            raise ValueError("job is not running")
        if record["current_index"] != index or not step["is_running"]:
            raise ValueError("only the running current step may complete")

        step["is_pending"] = False
        step["is_running"] = False
        step["is_completed"] = True
        step["is_failed"] = False
        step["output"] = deepcopy(output)
        step["error"] = None
        record["revision"] += 1
        self._emit("step_completed", record, step=step_name)

        next_index = index + 1
        if next_index == len(record["steps"]):
            record["is_queued"] = False
            record["is_started"] = True
            record["is_running"] = False
            record["is_paused"] = False
            record["is_failed"] = False
            record["is_completed"] = True
            record["has_failure"] = False
            record["should_retry"] = False
            record["failure"] = None
            record["current_index"] = index
            record["revision"] += 1
            self._emit("completed", record, attempt=record["attempt"])
        else:
            next_step = record["steps"][next_index]
            next_step["is_pending"] = False
            next_step["is_running"] = True
            next_step["is_completed"] = False
            next_step["is_failed"] = False
            next_step["attempts"] += 1
            record["current_index"] = next_index
            record["has_started_step"] = True
            self._emit("step_started", record, step=next_step["name"])

        self._assert_consistent(record)
        return self._view(record)

    def fail_step(
        self,
        job_id: str,
        step_name: str,
        error: str,
    ) -> JobView:
        record = self._require(job_id)
        index, step = self._require_step(record, step_name)
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be a non-empty string")
        if step["is_failed"] and step["error"] == error:
            return self._view(record)
        if not record["is_running"] or record["is_paused"]:
            raise ValueError("job is not running")
        if record["current_index"] != index or not step["is_running"]:
            raise ValueError("only the running current step may fail")

        step["is_pending"] = False
        step["is_running"] = False
        step["is_completed"] = False
        step["is_failed"] = True
        step["error"] = error
        record["is_queued"] = False
        record["is_started"] = True
        record["is_running"] = False
        record["is_paused"] = False
        record["is_failed"] = True
        record["is_completed"] = False
        record["has_failure"] = True
        record["should_retry"] = True
        record["failure"] = error
        record["revision"] += 1
        self._emit("step_failed", record, step=step_name, error=error)
        self._assert_consistent(record)
        return self._view(record)

    def retry_step(self, job_id: str) -> JobView:
        record = self._require(job_id)
        if not record["is_failed"] or not record["has_failure"]:
            raise ValueError("only a failed job may retry")
        if not record["should_retry"]:
            raise ValueError("failed job is not retryable")
        step = self._current_step(record)
        if step is None or not step["is_failed"]:
            raise RuntimeError("failed job lacks a failed current step")

        previous_error = step["error"]
        step["is_pending"] = False
        step["is_running"] = True
        step["is_completed"] = False
        step["is_failed"] = False
        step["was_retried"] = True
        step["attempts"] += 1
        step["error"] = None
        record["is_queued"] = False
        record["is_started"] = True
        record["is_running"] = True
        record["is_paused"] = False
        record["is_failed"] = False
        record["is_completed"] = False
        record["has_failure"] = False
        record["should_retry"] = False
        record["was_retried"] = True
        record["failure"] = None
        record["revision"] += 1
        self._emit(
            "step_retried",
            record,
            step=step["name"],
            previous_error=previous_error,
        )
        self._assert_consistent(record)
        return self._view(record)

    def pause(self, job_id: str) -> JobView:
        record = self._require(job_id)
        if record["is_paused"]:
            return self._view(record)
        if record["is_completed"] or record["is_failed"]:
            raise ValueError("terminal job cannot be paused")
        if not record["is_running"] or not record["is_started"]:
            raise ValueError("only a running job may be paused")
        step = self._current_step(record)
        if step is None or not step["is_running"]:
            raise RuntimeError("running job lacks a running step")

        record["is_running"] = False
        record["is_paused"] = True
        record["is_queued"] = False
        record["revision"] += 1
        step["is_running"] = False
        step["is_pending"] = True
        self._emit("paused", record, step=step["name"])
        self._assert_consistent(record)
        return self._view(record)

    def resume(self, job_id: str) -> JobView:
        record = self._require(job_id)
        if record["is_running"] and not record["is_paused"]:
            return self._view(record)
        if not record["is_paused"]:
            raise ValueError("only a paused job may resume")
        if record["is_completed"] or record["is_failed"]:
            raise ValueError("terminal job cannot resume")
        step = self._current_step(record)
        if step is None:
            raise RuntimeError("paused job lacks a current step")

        record["is_running"] = True
        record["is_paused"] = False
        record["is_queued"] = False
        record["revision"] += 1
        step["is_pending"] = False
        step["is_running"] = True
        self._emit("resumed", record, step=step["name"])
        self._assert_consistent(record)
        return self._view(record)

    # ------------------------------------------------------------------
    # Non-lifecycle maintenance helpers
    # ------------------------------------------------------------------

    def update_metadata(
        self,
        job_id: str,
        updates: Mapping[str, Any],
    ) -> JobView:
        record = self._require(job_id)
        normalized = self._validate_metadata(updates)
        changed: dict[str, Any] = {}
        for key, value in normalized.items():
            if record["metadata"].get(key) != value:
                record["metadata"][key] = deepcopy(value)
                changed[key] = deepcopy(value)
        if changed:
            record["revision"] += 1
            self._emit("metadata_updated", record, updates=changed)
        return self._view(record)

    def pending_steps(self, job_id: str) -> tuple[str, ...]:
        record = self._require(job_id)
        return tuple(
            step["name"]
            for step in record["steps"]
            if step["is_pending"] and not step["is_completed"]
        )

    def completed_outputs(self, job_id: str) -> dict[str, Any]:
        record = self._require(job_id)
        return {
            step["name"]: deepcopy(step["output"])
            for step in record["steps"]
            if step["is_completed"]
        }

    def revision(self, job_id: str) -> int:
        return int(self._require(job_id)["revision"])

    def __len__(self) -> int:
        return len(self._jobs)
