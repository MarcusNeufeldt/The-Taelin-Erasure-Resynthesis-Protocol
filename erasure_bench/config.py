from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when benchmark configuration is invalid."""


@dataclass(frozen=True)
class CheckConfig:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ChallengeConfig:
    id: str
    prompt_path: Path
    checks: tuple[CheckConfig, ...]


@dataclass(frozen=True)
class TaskConfig:
    id: str
    root: Path
    seed_dir: Path
    stub_dir: Path | None
    prompt_path: Path
    target_paths: tuple[str, ...]
    metric_paths: tuple[str, ...]
    immutable_paths: tuple[str, ...]
    public_checks: tuple[CheckConfig, ...]
    hidden_checks: tuple[CheckConfig, ...]
    challenges: tuple[ChallengeConfig, ...]


@dataclass(frozen=True)
class AgentConfig:
    command: str
    model: str
    reasoning_effort: str
    sandbox: str
    approval_policy: str
    timeout_seconds: int
    check_timeout_seconds: int
    ignore_user_config: bool
    ignore_rules: bool
    ephemeral: bool


@dataclass(frozen=True)
class ArmConfig:
    name: str
    implementation: str
    instruction: str


@dataclass(frozen=True)
class BenchmarkConfig:
    source_path: Path
    root: Path
    name: str
    seed: int
    repetitions: int
    results_dir: Path
    agent: AgentConfig
    arms: dict[str, ArmConfig]
    arm_order: tuple[str, ...]
    tasks: tuple[TaskConfig, ...]


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from exc


def _relative_path(value: str, *, field: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(f"{field} must be a safe relative path: {value!r}")
    return path


def _required_string(data: dict[str, Any], field: str, *, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def _string_list(data: dict[str, Any], field: str, *, context: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{context}.{field} must be a non-empty string array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{context}.{field} contains an invalid value")
        normalized = _relative_path(item.strip(), field=f"{context}.{field}")
        result.append(normalized.as_posix())
    return tuple(result)


def _parse_checks(
    value: Any,
    *,
    context: str,
    required: bool = True,
) -> tuple[CheckConfig, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value):
        raise ConfigError(f"{context} must be a non-empty array of check tables")

    checks: list[CheckConfig] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{item_context} must be a table")
        name = _required_string(raw, "name", context=item_context)
        if name in seen:
            raise ConfigError(f"Duplicate check name in {context}: {name}")
        seen.add(name)
        command = raw.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
        ):
            raise ConfigError(f"{item_context}.command must be a non-empty string array")
        timeout = raw.get("timeout_seconds")
        if timeout is not None and (not isinstance(timeout, int) or timeout <= 0):
            raise ConfigError(f"{item_context}.timeout_seconds must be a positive integer")
        checks.append(CheckConfig(name=name, command=tuple(command), timeout_seconds=timeout))
    return tuple(checks)


def load_task(path: str | Path) -> TaskConfig:
    task_path = Path(path).resolve()
    data = _read_toml(task_path)
    root = task_path.parent
    task_id = _required_string(data, "id", context=str(task_path))

    seed_rel = _relative_path(
        _required_string(data, "seed_dir", context=task_id),
        field=f"{task_id}.seed_dir",
    )
    seed_dir = (root / seed_rel).resolve()
    if not seed_dir.is_dir():
        raise ConfigError(f"{task_id}.seed_dir does not exist: {seed_dir}")

    stub_value = data.get("stub_dir")
    stub_dir: Path | None = None
    if stub_value is not None:
        if not isinstance(stub_value, str):
            raise ConfigError(f"{task_id}.stub_dir must be a string")
        stub_dir = (root / _relative_path(stub_value, field=f"{task_id}.stub_dir")).resolve()
        if not stub_dir.is_dir():
            raise ConfigError(f"{task_id}.stub_dir does not exist: {stub_dir}")

    prompt_rel = _relative_path(
        _required_string(data, "prompt_file", context=task_id),
        field=f"{task_id}.prompt_file",
    )
    prompt_path = (root / prompt_rel).resolve()
    if not prompt_path.is_file():
        raise ConfigError(f"{task_id}.prompt_file does not exist: {prompt_path}")

    target_paths = _string_list(data, "target_paths", context=task_id)
    metric_paths = _string_list(data, "metric_paths", context=task_id)
    immutable_paths = _string_list(data, "immutable_paths", context=task_id)

    for target in target_paths:
        if not (seed_dir / target).exists():
            raise ConfigError(f"{task_id} target is missing from seed: {target}")

    public_checks = _parse_checks(data.get("public_checks"), context=f"{task_id}.public_checks")
    hidden_checks = _parse_checks(data.get("hidden_checks"), context=f"{task_id}.hidden_checks")

    challenges_raw = data.get("challenges", [])
    if not isinstance(challenges_raw, list):
        raise ConfigError(f"{task_id}.challenges must be an array of tables")
    challenges: list[ChallengeConfig] = []
    challenge_ids: set[str] = set()
    for index, raw in enumerate(challenges_raw):
        context = f"{task_id}.challenges[{index}]"
        if not isinstance(raw, dict):
            raise ConfigError(f"{context} must be a table")
        challenge_id = _required_string(raw, "id", context=context)
        if challenge_id in challenge_ids:
            raise ConfigError(f"Duplicate challenge id in {task_id}: {challenge_id}")
        challenge_ids.add(challenge_id)
        challenge_prompt = (
            root
            / _relative_path(
                _required_string(raw, "prompt_file", context=context),
                field=f"{context}.prompt_file",
            )
        ).resolve()
        if not challenge_prompt.is_file():
            raise ConfigError(f"Challenge prompt does not exist: {challenge_prompt}")
        challenge_checks = _parse_checks(
            raw.get("checks"),
            context=f"{context}.checks",
        )
        challenges.append(
            ChallengeConfig(
                id=challenge_id,
                prompt_path=challenge_prompt,
                checks=challenge_checks,
            )
        )

    return TaskConfig(
        id=task_id,
        root=root,
        seed_dir=seed_dir,
        stub_dir=stub_dir,
        prompt_path=prompt_path,
        target_paths=target_paths,
        metric_paths=metric_paths,
        immutable_paths=immutable_paths,
        public_checks=public_checks,
        hidden_checks=hidden_checks,
        challenges=tuple(challenges),
    )


def load_benchmark_config(path: str | Path) -> BenchmarkConfig:
    source_path = Path(path).resolve()
    root = source_path.parent
    data = _read_toml(source_path)

    benchmark = data.get("benchmark")
    agent_raw = data.get("agent")
    arms_raw = data.get("arms")
    if not isinstance(benchmark, dict):
        raise ConfigError("Missing [benchmark] table")
    if not isinstance(agent_raw, dict):
        raise ConfigError("Missing [agent] table")
    if not isinstance(arms_raw, dict):
        raise ConfigError("Missing [arms.*] tables")

    name = _required_string(benchmark, "name", context="benchmark")
    seed = benchmark.get("seed")
    repetitions = benchmark.get("repetitions")
    if not isinstance(seed, int):
        raise ConfigError("benchmark.seed must be an integer")
    if not isinstance(repetitions, int) or repetitions <= 0:
        raise ConfigError("benchmark.repetitions must be a positive integer")

    results_value = _required_string(benchmark, "results_dir", context="benchmark")
    results_dir = (root / _relative_path(results_value, field="benchmark.results_dir")).resolve()

    arm_order_raw = benchmark.get("arms")
    if (
        not isinstance(arm_order_raw, list)
        or not arm_order_raw
        or any(not isinstance(name, str) or not name for name in arm_order_raw)
    ):
        raise ConfigError("benchmark.arms must be a non-empty string array")
    arm_order = tuple(arm_order_raw)

    arms: dict[str, ArmConfig] = {}
    for arm_name in arm_order:
        raw = arms_raw.get(arm_name)
        if not isinstance(raw, dict):
            raise ConfigError(f"Missing [arms.{arm_name}] table")
        implementation = _required_string(raw, "implementation", context=f"arms.{arm_name}")
        if implementation not in {"visible", "stub"}:
            raise ConfigError(
                f"arms.{arm_name}.implementation must be 'visible' or 'stub'"
            )
        instruction = _required_string(raw, "instruction", context=f"arms.{arm_name}")
        arms[arm_name] = ArmConfig(
            name=arm_name,
            implementation=implementation,
            instruction=instruction,
        )

    sandbox = _required_string(agent_raw, "sandbox", context="agent")
    if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
        raise ConfigError(
            "agent.sandbox must be read-only, workspace-write, or danger-full-access"
        )
    approval_policy = _required_string(
        agent_raw,
        "approval_policy",
        context="agent",
    )
    if approval_policy not in {"never", "on-request", "on-failure", "untrusted"}:
        raise ConfigError("agent.approval_policy is not recognized")
    timeout_seconds = agent_raw.get("timeout_seconds")
    check_timeout_seconds = agent_raw.get("check_timeout_seconds")
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ConfigError("agent.timeout_seconds must be a positive integer")
    if not isinstance(check_timeout_seconds, int) or check_timeout_seconds <= 0:
        raise ConfigError("agent.check_timeout_seconds must be a positive integer")

    agent = AgentConfig(
        command=_required_string(agent_raw, "command", context="agent"),
        model=_required_string(agent_raw, "model", context="agent"),
        reasoning_effort=_required_string(
            agent_raw,
            "reasoning_effort",
            context="agent",
        ),
        sandbox=sandbox,
        approval_policy=approval_policy,
        timeout_seconds=timeout_seconds,
        check_timeout_seconds=check_timeout_seconds,
        ignore_user_config=bool(agent_raw.get("ignore_user_config", True)),
        ignore_rules=bool(agent_raw.get("ignore_rules", True)),
        ephemeral=bool(agent_raw.get("ephemeral", True)),
    )

    task_values = benchmark.get("tasks")
    if (
        not isinstance(task_values, list)
        or not task_values
        or any(not isinstance(item, str) or not item for item in task_values)
    ):
        raise ConfigError("benchmark.tasks must be a non-empty string array")
    tasks = tuple(
        load_task(root / _relative_path(item, field="benchmark.tasks"))
        for item in task_values
    )
    task_ids = [task.id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ConfigError("Task ids must be unique")

    return BenchmarkConfig(
        source_path=source_path,
        root=root,
        name=name,
        seed=seed,
        repetitions=repetitions,
        results_dir=results_dir,
        agent=agent,
        arms=arms,
        arm_order=arm_order,
        tasks=tasks,
    )
