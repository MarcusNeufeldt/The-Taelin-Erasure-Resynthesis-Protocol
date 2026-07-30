from __future__ import annotations

import json
import platform
import random
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import audit_event_log
from .codex import resolve_codex_command, run_codex
from .config import (
    ArmConfig,
    BenchmarkConfig,
    ChallengeConfig,
    TaskConfig,
    load_task,
)
from .metrics import capture_git_diff, collect_source_metrics
from .verification import display_command, expand_command, run_checks
from .workspace import (
    clone_candidate_workspace,
    hash_path,
    hash_tree,
    immutable_violations,
    prepare_workspace,
    snapshot_paths,
)


@dataclass(frozen=True)
class PlannedRun:
    index: int
    task_id: str
    arm_name: str
    repetition: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_plan(
    config: BenchmarkConfig,
    *,
    task_ids: set[str] | None = None,
    arm_names: set[str] | None = None,
    repetitions: int | None = None,
    max_runs: int | None = None,
) -> list[PlannedRun]:
    selected_tasks = [
        task for task in config.tasks if task_ids is None or task.id in task_ids
    ]
    selected_arms = [
        name for name in config.arm_order if arm_names is None or name in arm_names
    ]
    if not selected_tasks:
        raise ValueError("No tasks matched the requested filter")
    if not selected_arms:
        raise ValueError("No arms matched the requested filter")

    count = repetitions if repetitions is not None else config.repetitions
    if count <= 0:
        raise ValueError("repetitions must be positive")
    rng = random.Random(config.seed)
    blocks = [(task, repetition) for task in selected_tasks for repetition in range(1, count + 1)]
    rng.shuffle(blocks)

    ordered: list[tuple[str, str, int]] = []
    for task, repetition in blocks:
        local_arms = selected_arms.copy()
        rng.shuffle(local_arms)
        ordered.extend((task.id, arm_name, repetition) for arm_name in local_arms)

    if max_runs is not None:
        if max_runs <= 0:
            raise ValueError("max_runs must be positive")
        ordered = ordered[:max_runs]
    return [
        PlannedRun(index=index, task_id=task_id, arm_name=arm_name, repetition=repetition)
        for index, (task_id, arm_name, repetition) in enumerate(ordered, start=1)
    ]


def _command_for_prompt(
    command: tuple[str, ...],
    *,
    workspace: Path,
    task: TaskConfig,
) -> str:
    return display_command(
        expand_command(command, workspace=workspace, task_dir=task.root)
    )


def build_generation_prompt(
    task: TaskConfig,
    arm: ArmConfig,
    *,
    workspace: Path,
) -> str:
    public_checks = "\n".join(
        f"- `{_command_for_prompt(check.command, workspace=workspace, task=task)}`"
        for check in task.public_checks
    )
    targets = "\n".join(f"- `{path}`" for path in task.target_paths)
    immutable = "\n".join(f"- `{path}`" for path in task.immutable_paths)
    task_prompt = task.prompt_path.read_text(encoding="utf-8").strip()
    return f"""You are one blinded subject in a controlled code-maintenance benchmark.

Operate only inside the current workspace. Follow its AGENTS.md. Do not inspect
parent directories, external files, prior sessions, Git history, network
resources, benchmark configuration, hidden checks, or alternative fixture
versions.

Task:

{task_prompt}

Experimental instruction:

{arm.instruction.strip()}

Target paths:

{targets}

Frozen artifacts that must not change:

{immutable}

Public checks:

{public_checks}

Implement the task completely in the workspace and run the public checks. Do
not merely describe a solution. Your final response must follow the supplied
JSON schema.
"""


def build_challenge_prompt(
    task: TaskConfig,
    challenge: ChallengeConfig,
    *,
    workspace: Path,
) -> str:
    public_checks = "\n".join(
        f"- `{_command_for_prompt(check.command, workspace=workspace, task=task)}`"
        for check in task.public_checks
    )
    immutable = "\n".join(f"- `{path}`" for path in task.immutable_paths)
    challenge_prompt = challenge.prompt_path.read_text(encoding="utf-8").strip()
    return f"""You are a fresh, blinded agent performing an unseen maintenance
task in a controlled benchmark.

Operate only inside the current workspace. Follow its AGENTS.md. Do not inspect
parent directories, external files, prior sessions, Git history, network
resources, benchmark configuration, hidden checks, or alternative versions.
You are not told how this implementation was produced.

Maintenance request:

{challenge_prompt}

Frozen artifacts that must not change:

{immutable}

Existing public regression checks:

{public_checks}

Implement the maintenance request completely and run the public regression
checks. Do not modify tests or contracts. Your final response must follow the
supplied JSON schema.
"""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _codex_version(command: str) -> dict[str, str | None]:
    executable = resolve_codex_command(command)
    result = subprocess.run(
        [executable, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "executable": executable,
        "version": (result.stdout or result.stderr).strip() or None,
    }


def _git_identity(root: Path) -> dict[str, Any]:
    commit_result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    status_result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain=v1"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "commit": commit_result.stdout.strip() or None,
        "dirty": bool(status_result.stdout.strip()),
    }


def _freeze_inputs(
    config: BenchmarkConfig,
    run_root: Path,
) -> tuple[dict[str, TaskConfig], Path, dict[str, Any]]:
    frozen_root = run_root / "frozen-inputs"
    frozen_root.mkdir()
    frozen_tasks: dict[str, TaskConfig] = {}
    task_manifest: dict[str, Any] = {}
    for task in config.tasks:
        destination = frozen_root / "tasks" / task.id
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            task.root,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                "__pycache__",
                "*.pyc",
                "*.pyo",
                ".pytest_cache",
            ),
        )
        frozen = load_task(destination / "task.toml")
        frozen_tasks[frozen.id] = frozen
        task_manifest[frozen.id] = {
            "source": str(task.root),
            "tree_hash": hash_tree(destination),
        }

    source_schema = config.root / "schemas" / "agent_final.schema.json"
    if not source_schema.is_file():
        raise FileNotFoundError(f"Agent output schema is missing: {source_schema}")
    frozen_schema = frozen_root / "agent_final.schema.json"
    shutil.copy2(source_schema, frozen_schema)
    return (
        frozen_tasks,
        frozen_schema,
        {
            "tasks": task_manifest,
            "schema_hash": hash_path(frozen_schema),
            "frozen_tree_hash": hash_tree(frozen_root),
        },
    )


def _new_run_root(config: BenchmarkConfig) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = config.results_dir / f"{timestamp}-{config.name}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _case_name(plan: PlannedRun) -> str:
    return (
        f"{plan.index:03d}__{plan.task_id}__{plan.arm_name}"
        f"__r{plan.repetition:02d}"
    )


def _agent_completed(codex_result: dict[str, Any]) -> bool:
    response = codex_result.get("final_response")
    return (
        codex_result.get("error") is None
        and isinstance(response, dict)
        and response.get("status") == "completed"
    )


def _run_challenge(
    *,
    config: BenchmarkConfig,
    task: TaskConfig,
    challenge: ChallengeConfig,
    candidate_workspace: Path,
    case_dir: Path,
    schema_path: Path,
) -> dict[str, Any]:
    challenge_dir = case_dir / "challenges" / challenge.id
    workspace = challenge_dir / "workspace"
    clone_candidate_workspace(candidate_workspace, workspace)
    frozen = tuple(task.immutable_paths) + ("AGENTS.md",)
    immutable_before = snapshot_paths(workspace, frozen)
    prompt = build_challenge_prompt(task, challenge, workspace=workspace)
    (challenge_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    codex = run_codex(
        prompt,
        agent=config.agent,
        cwd=workspace,
        output_schema=schema_path,
        artifact_dir=challenge_dir / "agent",
    )
    leakage_audit = audit_event_log(
        codex.events_path,
        workspace=workspace,
        forbidden_paths=(task.root,),
        forbidden_names=(
            "verify_hidden.py",
            "verify_add_unary_not.py",
            "task.toml",
            "benchmark.toml",
        ),
    )
    checks = run_checks(
        task.public_checks + task.hidden_checks + challenge.checks,
        workspace=workspace,
        task_dir=task.root,
        log_dir=challenge_dir / "checks",
        default_timeout_seconds=config.agent.check_timeout_seconds,
    )
    violations = immutable_violations(workspace, immutable_before)
    diff = capture_git_diff(workspace, challenge_dir / "artifacts")
    passed = (
        _agent_completed(codex.to_dict())
        and leakage_audit.clean
        and not violations
        and all(check.passed for check in checks)
    )
    return {
        "id": challenge.id,
        "status": "completed",
        "passed": passed,
        "codex": codex.to_dict(),
        "leakage_audit": leakage_audit.to_dict(),
        "checks": [check.to_dict() for check in checks],
        "immutable_violations": violations,
        "metrics": collect_source_metrics(workspace, task.metric_paths).to_dict(),
        "diff": diff.to_dict(),
    }


def _skipped_challenges(task: TaskConfig) -> list[dict[str, Any]]:
    return [
        {
            "id": challenge.id,
            "status": "not_run_generation_failed",
            "passed": False,
            "codex": None,
            "leakage_audit": None,
            "checks": [],
            "immutable_violations": [],
            "metrics": None,
            "diff": None,
        }
        for challenge in task.challenges
    ]


def run_benchmark(
    config: BenchmarkConfig,
    *,
    plan: list[PlannedRun],
    skip_challenges: bool = False,
) -> Path:
    run_root = _new_run_root(config)
    tasks, schema_path, frozen_inputs = _freeze_inputs(config, run_root)

    shutil.copy2(config.source_path, run_root / "benchmark.toml")
    manifest = {
        "name": config.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": config.seed,
        "repetitions": config.repetitions,
        "agent": asdict(config.agent),
        "codex": _codex_version(config.agent.command),
        "harness_tree_hash": hash_tree(config.root),
        "harness_git": _git_identity(config.root),
        "frozen_inputs": frozen_inputs,
        "python": sys.version,
        "platform": platform.platform(),
        "skip_challenges": skip_challenges,
        "plan": [item.to_dict() for item in plan],
    }
    _write_json(run_root / "manifest.json", manifest)

    for item in plan:
        task = tasks[item.task_id]
        arm = config.arms[item.arm_name]
        case_dir = run_root / "cases" / _case_name(item)
        workspace = case_dir / "workspace"
        prepared = prepare_workspace(task, arm, workspace)
        prompt = build_generation_prompt(task, arm, workspace=workspace)
        (case_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        baseline_metrics = collect_source_metrics(
            task.seed_dir,
            task.metric_paths,
        )
        initial_metrics = collect_source_metrics(workspace, task.metric_paths)
        codex = run_codex(
            prompt,
            agent=config.agent,
            cwd=workspace,
            output_schema=schema_path,
            artifact_dir=case_dir / "agent",
        )
        leakage_audit = audit_event_log(
            codex.events_path,
            workspace=workspace,
            forbidden_paths=(task.root,),
            forbidden_names=(
                "verify_hidden.py",
                "verify_add_unary_not.py",
                "task.toml",
                "benchmark.toml",
            ),
        )
        checks = run_checks(
            task.public_checks + task.hidden_checks,
            workspace=workspace,
            task_dir=task.root,
            log_dir=case_dir / "checks",
            default_timeout_seconds=config.agent.check_timeout_seconds,
        )
        violations = immutable_violations(workspace, prepared.immutable_hashes)
        candidate_metrics = collect_source_metrics(workspace, task.metric_paths)
        diff = capture_git_diff(workspace, case_dir / "artifacts")
        codex_dict = codex.to_dict()
        generation_passed = (
            _agent_completed(codex_dict)
            and leakage_audit.clean
            and not violations
            and all(check.passed for check in checks)
        )

        if skip_challenges:
            challenges: list[dict[str, Any]] = []
        elif generation_passed:
            challenges = [
                _run_challenge(
                    config=config,
                    task=task,
                    challenge=challenge,
                    candidate_workspace=workspace,
                    case_dir=case_dir,
                    schema_path=schema_path,
                )
                for challenge in task.challenges
            ]
        else:
            challenges = _skipped_challenges(task)

        result = {
            "plan": item.to_dict(),
            "task_root": str(task.root),
            "fixture_hash": prepared.fixture_hash,
            "initial_tree_hash": prepared.initial_tree_hash,
            "generation_passed": generation_passed,
            "codex": codex_dict,
            "leakage_audit": leakage_audit.to_dict(),
            "checks": [check.to_dict() for check in checks],
            "immutable_violations": violations,
            "baseline_metrics": baseline_metrics.to_dict(),
            "initial_metrics": initial_metrics.to_dict(),
            "candidate_metrics": candidate_metrics.to_dict(),
            "diff": diff.to_dict(),
            "challenges": challenges,
        }
        _write_json(case_dir / "result.json", result)

    from .report import generate_report

    generate_report(run_root)
    return run_root
