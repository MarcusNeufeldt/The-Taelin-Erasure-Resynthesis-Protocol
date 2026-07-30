from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    AgentConfig,
    CheckConfig,
    ConfigError,
    _parse_checks,
    _read_toml,
    _relative_path,
    _required_string,
    _string_list,
)


CONTROLLER_TYPES = {
    "candidate_pool",
    "reviewer_implementer",
    "self_erasure",
}


@dataclass(frozen=True)
class DebtMarkerConfig:
    name: str
    pattern: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class TrajectoryStepConfig:
    id: str
    prompt_path: Path
    public_checks: tuple[CheckConfig, ...]
    hidden_checks: tuple[CheckConfig, ...]


@dataclass(frozen=True)
class TrajectoryTaskConfig:
    id: str
    source_path: Path
    root: Path
    seed_dir: Path
    target_paths: tuple[str, ...]
    erasable_paths: tuple[str, ...]
    metric_paths: tuple[str, ...]
    immutable_paths: tuple[str, ...]
    baseline_checks: tuple[CheckConfig, ...]
    steps: tuple[TrajectoryStepConfig, ...]
    debt_markers: tuple[DebtMarkerConfig, ...]


@dataclass(frozen=True)
class ControllerConfig:
    name: str
    type: str
    instruction: str
    candidate_count: int

    @property
    def calls_per_step(self) -> int:
        if self.type == "candidate_pool":
            return self.candidate_count
        return 2


@dataclass(frozen=True)
class LoopConfig:
    source_path: Path
    root: Path
    name: str
    seed: int
    repetitions: int
    results_dir: Path
    calls_per_step: int
    contract_max_chars: int
    max_total_tokens: int | None
    max_total_agent_seconds: float | None
    stop_on_failure: bool
    agent: AgentConfig
    controllers: dict[str, ControllerConfig]
    controller_order: tuple[str, ...]
    task: TrajectoryTaskConfig


def _optional_positive_int(
    data: dict[str, Any],
    field: str,
    *,
    context: str,
) -> int | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{context}.{field} must be a positive integer")
    return value


def _optional_positive_float(
    data: dict[str, Any],
    field: str,
    *,
    context: str,
) -> float | None:
    value = data.get(field)
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ConfigError(f"{context}.{field} must be a positive number")
    return float(value)


def _load_agent(raw: dict[str, Any]) -> AgentConfig:
    sandbox = _required_string(raw, "sandbox", context="agent")
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ConfigError(
            "agent.sandbox must be read-only, workspace-write, or danger-full-access"
        )
    approval_policy = _required_string(raw, "approval_policy", context="agent")
    if approval_policy not in {"never", "on-request", "on-failure", "untrusted"}:
        raise ConfigError("agent.approval_policy is not recognized")
    timeout_seconds = raw.get("timeout_seconds")
    check_timeout_seconds = raw.get("check_timeout_seconds")
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ConfigError("agent.timeout_seconds must be a positive integer")
    if not isinstance(check_timeout_seconds, int) or check_timeout_seconds <= 0:
        raise ConfigError("agent.check_timeout_seconds must be a positive integer")
    return AgentConfig(
        command=_required_string(raw, "command", context="agent"),
        model=_required_string(raw, "model", context="agent"),
        reasoning_effort=_required_string(raw, "reasoning_effort", context="agent"),
        sandbox=sandbox,
        approval_policy=approval_policy,
        timeout_seconds=timeout_seconds,
        check_timeout_seconds=check_timeout_seconds,
        ignore_user_config=bool(raw.get("ignore_user_config", True)),
        ignore_rules=bool(raw.get("ignore_rules", True)),
        ephemeral=bool(raw.get("ephemeral", True)),
    )


def load_trajectory_task(path: str | Path) -> TrajectoryTaskConfig:
    source_path = Path(path).resolve()
    raw = _read_toml(source_path)
    root = source_path.parent
    task_id = _required_string(raw, "id", context=str(source_path))

    seed_rel = _relative_path(
        _required_string(raw, "seed_dir", context=task_id),
        field=f"{task_id}.seed_dir",
    )
    seed_dir = (root / seed_rel).resolve()
    if not seed_dir.is_dir():
        raise ConfigError(f"{task_id}.seed_dir does not exist: {seed_dir}")

    target_paths = _string_list(raw, "target_paths", context=task_id)
    erasable_paths = _string_list(raw, "erasable_paths", context=task_id)
    metric_paths = _string_list(raw, "metric_paths", context=task_id)
    immutable_paths = _string_list(raw, "immutable_paths", context=task_id)
    for relative in set(target_paths + erasable_paths + metric_paths + immutable_paths):
        if not (seed_dir / relative).exists():
            raise ConfigError(f"{task_id} seed path does not exist: {relative}")
    if not set(erasable_paths).issubset(set(target_paths)):
        raise ConfigError(f"{task_id}.erasable_paths must be included in target_paths")
    for relative in erasable_paths:
        if Path(relative).suffix != ".py":
            raise ConfigError(
                f"{task_id}.erasable_paths currently supports Python files only: "
                f"{relative}"
            )

    baseline_checks = _parse_checks(
        raw.get("baseline_checks"),
        context=f"{task_id}.baseline_checks",
    )
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ConfigError(f"{task_id}.steps must be a non-empty array of tables")
    steps: list[TrajectoryStepConfig] = []
    seen_steps: set[str] = set()
    for index, step_raw in enumerate(steps_raw):
        context = f"{task_id}.steps[{index}]"
        if not isinstance(step_raw, dict):
            raise ConfigError(f"{context} must be a table")
        step_id = _required_string(step_raw, "id", context=context)
        if step_id in seen_steps:
            raise ConfigError(f"Duplicate trajectory step id: {step_id}")
        seen_steps.add(step_id)
        prompt_path = (
            root
            / _relative_path(
                _required_string(step_raw, "prompt_file", context=context),
                field=f"{context}.prompt_file",
            )
        ).resolve()
        if not prompt_path.is_file():
            raise ConfigError(f"Trajectory prompt does not exist: {prompt_path}")
        steps.append(
            TrajectoryStepConfig(
                id=step_id,
                prompt_path=prompt_path,
                public_checks=_parse_checks(
                    step_raw.get("public_checks"),
                    context=f"{context}.public_checks",
                    required=False,
                ),
                hidden_checks=_parse_checks(
                    step_raw.get("hidden_checks"),
                    context=f"{context}.hidden_checks",
                ),
            )
        )

    markers_raw = raw.get("debt_markers", [])
    if not isinstance(markers_raw, list):
        raise ConfigError(f"{task_id}.debt_markers must be an array of tables")
    markers: list[DebtMarkerConfig] = []
    seen_markers: set[str] = set()
    for index, marker_raw in enumerate(markers_raw):
        context = f"{task_id}.debt_markers[{index}]"
        if not isinstance(marker_raw, dict):
            raise ConfigError(f"{context} must be a table")
        name = _required_string(marker_raw, "name", context=context)
        if name in seen_markers:
            raise ConfigError(f"Duplicate debt marker: {name}")
        seen_markers.add(name)
        pattern = _required_string(marker_raw, "pattern", context=context)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(
                f"{context}.pattern is not a valid regular expression: {exc}"
            ) from exc
        markers.append(
            DebtMarkerConfig(
                name=name,
                pattern=pattern,
                paths=_string_list(marker_raw, "paths", context=context),
            )
        )

    return TrajectoryTaskConfig(
        id=task_id,
        source_path=source_path,
        root=root,
        seed_dir=seed_dir,
        target_paths=target_paths,
        erasable_paths=erasable_paths,
        metric_paths=metric_paths,
        immutable_paths=immutable_paths,
        baseline_checks=baseline_checks,
        steps=tuple(steps),
        debt_markers=tuple(markers),
    )


def load_loop_config(path: str | Path) -> LoopConfig:
    source_path = Path(path).resolve()
    root = source_path.parent
    raw = _read_toml(source_path)
    loop_raw = raw.get("loop")
    agent_raw = raw.get("agent")
    controllers_raw = raw.get("controllers")
    if not isinstance(loop_raw, dict):
        raise ConfigError("Missing [loop] table")
    if not isinstance(agent_raw, dict):
        raise ConfigError("Missing [agent] table")
    if not isinstance(controllers_raw, dict):
        raise ConfigError("Missing [controllers.*] tables")

    seed = loop_raw.get("seed")
    repetitions = loop_raw.get("repetitions")
    calls_per_step = loop_raw.get("calls_per_step")
    contract_max_chars = loop_raw.get("contract_max_chars")
    if not isinstance(seed, int):
        raise ConfigError("loop.seed must be an integer")
    if not isinstance(repetitions, int) or repetitions <= 0:
        raise ConfigError("loop.repetitions must be a positive integer")
    if not isinstance(calls_per_step, int) or calls_per_step <= 0:
        raise ConfigError("loop.calls_per_step must be a positive integer")
    if not isinstance(contract_max_chars, int) or contract_max_chars <= 0:
        raise ConfigError("loop.contract_max_chars must be a positive integer")

    controller_names = loop_raw.get("controllers")
    if (
        not isinstance(controller_names, list)
        or not controller_names
        or any(not isinstance(item, str) or not item for item in controller_names)
    ):
        raise ConfigError("loop.controllers must be a non-empty string array")
    controller_order = tuple(controller_names)
    controllers: dict[str, ControllerConfig] = {}
    for name in controller_order:
        item = controllers_raw.get(name)
        if not isinstance(item, dict):
            raise ConfigError(f"Missing [controllers.{name}] table")
        controller_type = _required_string(
            item,
            "type",
            context=f"controllers.{name}",
        )
        if controller_type not in CONTROLLER_TYPES:
            raise ConfigError(
                f"controllers.{name}.type must be one of "
                f"{sorted(CONTROLLER_TYPES)}"
            )
        candidate_count = item.get("candidate_count", 1)
        if not isinstance(candidate_count, int) or candidate_count <= 0:
            raise ConfigError(
                f"controllers.{name}.candidate_count must be positive"
            )
        controller = ControllerConfig(
            name=name,
            type=controller_type,
            instruction=_required_string(
                item,
                "instruction",
                context=f"controllers.{name}",
            ),
            candidate_count=candidate_count,
        )
        if controller.calls_per_step != calls_per_step:
            raise ConfigError(
                f"Controller {name} uses {controller.calls_per_step} calls per "
                f"step, but loop.calls_per_step is {calls_per_step}"
            )
        controllers[name] = controller

    results_value = _required_string(loop_raw, "results_dir", context="loop")
    task_value = _required_string(loop_raw, "task", context="loop")
    return LoopConfig(
        source_path=source_path,
        root=root,
        name=_required_string(loop_raw, "name", context="loop"),
        seed=seed,
        repetitions=repetitions,
        results_dir=(
            root / _relative_path(results_value, field="loop.results_dir")
        ).resolve(),
        calls_per_step=calls_per_step,
        contract_max_chars=contract_max_chars,
        max_total_tokens=_optional_positive_int(
            loop_raw,
            "max_total_tokens",
            context="loop",
        ),
        max_total_agent_seconds=_optional_positive_float(
            loop_raw,
            "max_total_agent_seconds",
            context="loop",
        ),
        stop_on_failure=bool(loop_raw.get("stop_on_failure", True)),
        agent=_load_agent(agent_raw),
        controllers=controllers,
        controller_order=controller_order,
        task=load_trajectory_task(
            root / _relative_path(task_value, field="loop.task")
        ),
    )
