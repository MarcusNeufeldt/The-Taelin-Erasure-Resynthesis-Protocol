from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StepView:
    name: str
    status: str
    attempts: int
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class JobView:
    job_id: str
    status: str
    steps: tuple[StepView, ...]
    current_step: str | None
    attempt: int
    metadata: dict[str, Any] = field(default_factory=dict)
    failure: str | None = None


@dataclass(frozen=True)
class Event:
    sequence: int
    kind: str
    job_id: str
    detail: dict[str, Any] = field(default_factory=dict)
