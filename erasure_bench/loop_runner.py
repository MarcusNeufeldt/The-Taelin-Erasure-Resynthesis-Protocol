from __future__ import annotations

import ast
import copy
import json
import platform
import random
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import audit_event_log
from .codex import CodexRunResult, run_codex
from .loop_config import (
    ControllerConfig,
    LoopConfig,
    TrajectoryStepConfig,
    TrajectoryTaskConfig,
    load_trajectory_task,
)
from .metrics import capture_git_diff, collect_source_metrics
from .runner import _codex_version, _git_identity
from .verification import display_command, expand_command, run_checks
from .workspace import (
    hash_path,
    hash_tree,
    immutable_violations,
    initialize_repository,
    snapshot_paths,
)


IGNORED_COPY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "loop-runs",
    "runs",
}


@dataclass(frozen=True)
class PlannedEpisode:
    index: int
    controller_name: str
    repetition: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BudgetTracker:
    calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    agent_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def consume(self, result: CodexRunResult) -> None:
        usage = result.event_summary.usage
        self.calls += 1
        self.input_tokens += int(usage.get("input_tokens", 0))
        self.cached_input_tokens += int(usage.get("cached_input_tokens", 0))
        self.output_tokens += int(usage.get("output_tokens", 0))
        self.reasoning_output_tokens += int(
            usage.get("reasoning_output_tokens", 0)
        )
        self.agent_seconds += result.duration_seconds

    def within_limits(self, config: LoopConfig, call_limit: int) -> bool:
        if self.calls > call_limit:
            return False
        if (
            config.max_total_tokens is not None
            and self.total_tokens > config.max_total_tokens
        ):
            return False
        if (
            config.max_total_agent_seconds is not None
            and self.agent_seconds > config.max_total_agent_seconds
        ):
            return False
        return True

    def can_start(self, config: LoopConfig, call_limit: int) -> bool:
        if self.calls >= call_limit:
            return False
        if (
            config.max_total_tokens is not None
            and self.total_tokens >= config.max_total_tokens
        ):
            return False
        if (
            config.max_total_agent_seconds is not None
            and self.agent_seconds >= config.max_total_agent_seconds
        ):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "total_tokens": self.total_tokens,
        }


def build_loop_plan(
    config: LoopConfig,
    *,
    controller_names: set[str] | None = None,
    repetitions: int | None = None,
    max_episodes: int | None = None,
) -> list[PlannedEpisode]:
    selected = [
        name
        for name in config.controller_order
        if controller_names is None or name in controller_names
    ]
    if not selected:
        raise ValueError("No controllers matched the requested filter")
    count = repetitions if repetitions is not None else config.repetitions
    if count <= 0:
        raise ValueError("repetitions must be positive")

    rng = random.Random(config.seed)
    blocks: list[tuple[str, int]] = []
    repetitions_order = list(range(1, count + 1))
    rng.shuffle(repetitions_order)
    for repetition in repetitions_order:
        local = selected.copy()
        rng.shuffle(local)
        blocks.extend((name, repetition) for name in local)
    if max_episodes is not None:
        if max_episodes <= 0:
            raise ValueError("max_episodes must be positive")
        blocks = blocks[:max_episodes]
    return [
        PlannedEpisode(index=index, controller_name=name, repetition=repetition)
        for index, (name, repetition) in enumerate(blocks, start=1)
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ignore_copy(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in IGNORED_COPY_NAMES}
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo", ".log")))
    return ignored


def _copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=_ignore_copy)


def _stub_function(node: ast.FunctionDef | ast.AsyncFunctionDef):
    replacement = copy.deepcopy(node)
    replacement.body = [
        ast.Raise(
            exc=ast.Call(
                func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
                args=[ast.Constant(value="implementation erased by TERP")],
                keywords=[],
            ),
            cause=None,
        )
    ]
    return replacement


def _stub_class(node: ast.ClassDef) -> ast.ClassDef:
    replacement = copy.deepcopy(node)
    body: list[ast.stmt] = []
    for item in replacement.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body.append(_stub_function(item))
        elif isinstance(item, ast.ClassDef):
            body.append(_stub_class(item))
        elif isinstance(item, ast.AnnAssign):
            item.value = None
            body.append(item)
    replacement.body = body or [ast.Pass()]
    return replacement


def erase_python_implementation(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    body: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            body.append(copy.deepcopy(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body.append(_stub_function(node))
        elif isinstance(node, ast.ClassDef):
            body.append(_stub_class(node))
    if not body:
        body.append(ast.Pass())
    stub = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(stub)
    path.write_text(
        "# Signature-only TERP stub. Original implementation is unavailable.\n\n"
        + ast.unparse(stub)
        + "\n",
        encoding="utf-8",
    )


def _write_loop_rules(task: TrajectoryTaskConfig, workspace: Path) -> None:
    immutable = "\n".join(f"- `{item}`" for item in task.immutable_paths)
    targets = "\n".join(f"- `{item}`" for item in task.target_paths)
    content = f"""# TERP trajectory rules

Work only inside this workspace. Do not inspect parent directories, external
repositories, prior sessions, Git history, network resources, benchmark
configuration, hidden checks, or alternative fixture versions.

You may modify implementation files needed for the current maintenance task.
Do not modify, delete, rename, or bypass these frozen artifacts:

{immutable}

The allowed implementation targets are:

{targets}

Run the public checks stated in the prompt. Preserve behavior accumulated from
earlier tasks. Do not weaken checks or hard-code examples.
"""
    (workspace / "AGENTS.md").write_text(content, encoding="utf-8")


def _prepare_workspace(
    source: Path,
    destination: Path,
    task: TrajectoryTaskConfig,
    *,
    erased_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    _copy_tree(source, destination)
    for relative in erased_paths:
        target = destination / relative
        if not target.is_file():
            raise FileNotFoundError(f"Cannot erase missing implementation: {relative}")
        erase_python_implementation(target)
    _write_loop_rules(task, destination)
    immutable = tuple(task.immutable_paths) + ("AGENTS.md",)
    immutable_hashes = snapshot_paths(destination, immutable)
    initial_tree_hash = hash_tree(destination)
    initialize_repository(destination)
    return {
        "workspace": destination,
        "immutable_hashes": immutable_hashes,
        "initial_tree_hash": initial_tree_hash,
        "erased_paths": list(erased_paths),
    }


def _snapshot_accepted(source: Path, destination: Path) -> Path:
    _copy_tree(source, destination)
    git_dir = destination / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)
    return destination


def count_debt_markers(
    workspace: Path,
    task: TrajectoryTaskConfig,
) -> dict[str, int]:
    result: dict[str, int] = {}
    for marker in task.debt_markers:
        expression = re.compile(marker.pattern, re.MULTILINE)
        count = 0
        for relative in marker.paths:
            path = workspace / relative
            if path.is_file():
                count += len(expression.findall(path.read_text(encoding="utf-8")))
        result[marker.name] = count
    return result


def _commands_for_prompt(
    checks,
    *,
    workspace: Path,
    task: TrajectoryTaskConfig,
) -> str:
    return "\n".join(
        "- `"
        + display_command(
            expand_command(
                check.command,
                workspace=workspace,
                task_dir=task.root,
            )
        )
        + "`"
        for check in checks
    )


def _common_guard() -> str:
    return """Operate only inside the current workspace and follow AGENTS.md.
Do not inspect parent directories, external files, prior sessions, Git history,
network resources, benchmark configuration, hidden checks, or alternative
fixture versions. Implement or analyze the current task completely; do not
bypass verification."""


def build_maintenance_prompt(
    task: TrajectoryTaskConfig,
    step: TrajectoryStepConfig,
    controller: ControllerConfig,
    *,
    workspace: Path,
    context: str = "",
) -> str:
    request = step.prompt_path.read_text(encoding="utf-8").strip()
    checks = task.baseline_checks + step.public_checks
    return f"""You are a coding agent in a long-horizon maintenance benchmark.

{_common_guard()}

Incoming maintenance request:

{request}

Controller instruction:

{controller.instruction.strip()}

Additional bounded context from this controller:

{context.strip() or "(none)"}

Public checks:

{_commands_for_prompt(checks, workspace=workspace, task=task)}

Make the requested change in the persistent codebase while preserving all
previous behavior. Run the public checks. Your final response must follow the
supplied JSON schema.
"""


def build_reviewer_prompt(
    task: TrajectoryTaskConfig,
    step: TrajectoryStepConfig,
    controller: ControllerConfig,
) -> str:
    request = step.prompt_path.read_text(encoding="utf-8").strip()
    return f"""You are an adversarial architecture reviewer in a controlled
long-horizon maintenance benchmark.

{_common_guard()}

Do not implement the change. Inspect the current source, tests, and callers.
Identify which existing abstraction, if any, will make this request brittle or
cause debt to compound. Produce a concrete implementation plan that a separate
agent can execute. Prefer behavioral language over helper/class names.

Incoming request:

{request}

Review instruction:

{controller.instruction.strip()}

Return the plan in the `summary` field of the supplied JSON schema. Report no
files changed.
"""


def build_extractor_prompt(
    task: TrajectoryTaskConfig,
    step: TrajectoryStepConfig,
    controller: ControllerConfig,
    *,
    max_chars: int,
) -> str:
    request = step.prompt_path.read_text(encoding="utf-8").strip()
    allowed = "\n".join(f"- `{item}`" for item in task.erasable_paths)
    return f"""You are the selector and behavioral-contract extractor in a
controlled code-compaction benchmark.

{_common_guard()}

Inspect the source, callers, contract, and tests. Decide whether the incoming
request has reached an architectural rewrite valley where retaining the current
implementation would anchor the next builder to a harmful abstraction.

Incoming request:

{request}

Controller instruction:

{controller.instruction.strip()}

Paths eligible for erasure:

{allowed}

If compaction is warranted, select only eligible paths and describe their
externally observable obligations in architecture-neutral language. Do not
name private helpers or prescribe the current architecture. Include inputs,
outputs, errors, state transitions, ordering, idempotency, and performance
requirements that callers rely on. The contract must be at most {max_chars}
characters. If compaction is not warranted, select no paths but still provide a
brief behavioral handoff for the maintenance builder.

Return only the supplied decision schema. Do not modify files.
"""


def build_blind_builder_prompt(
    task: TrajectoryTaskConfig,
    step: TrajectoryStepConfig,
    controller: ControllerConfig,
    *,
    workspace: Path,
    contract: str,
    erased_paths: tuple[str, ...],
) -> str:
    request = step.prompt_path.read_text(encoding="utf-8").strip()
    paths = "\n".join(f"- `{item}`" for item in erased_paths) or "(none)"
    role = (
        "source-blind resynthesis builder"
        if erased_paths
        else "maintenance builder after an explicit no-erasure decision"
    )
    visibility = (
        "The original implementation bodies of the selected paths are "
        "unavailable. Reconstruct their behavior from the bounded contract, "
        "public interfaces, callers, and tests."
        if erased_paths
        else "No path was erased. The accepted implementation remains visible; "
        "use the behavioral handoff without assuming erasure was required."
    )
    return f"""You are the {role} in a controlled code-compaction benchmark.

{_common_guard()}

{visibility}

Do not attempt to recover or infer hidden source history.

Incoming maintenance request:

{request}

Selected paths:

{paths}

Extracted behavioral contract:

{contract.strip()}

Controller instruction:

{controller.instruction.strip()}

Public checks:

{_commands_for_prompt(task.baseline_checks + step.public_checks, workspace=workspace, task=task)}

Implement the request completely and run the public checks. Your final response
must follow the supplied JSON schema.
"""


def _agent_completed(codex: dict[str, Any]) -> bool:
    response = codex.get("final_response")
    return (
        codex.get("error") is None
        and isinstance(response, dict)
        and response.get("status") == "completed"
    )


def _forbidden_names(task: TrajectoryTaskConfig) -> tuple[str, ...]:
    names = {
        "benchmark.toml",
        "loop.toml",
        task.source_path.name,
    }
    names.update(
        path.name
        for path in task.root.rglob("*")
        if path.is_file() and path.name.startswith("verify")
    )
    return tuple(sorted(names))


def _run_stage(
    *,
    prompt: str,
    workspace: Path,
    schema_path: Path,
    artifact_dir: Path,
    config: LoopConfig,
    task: TrajectoryTaskConfig,
    budget: BudgetTracker,
    call_limit: int,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    if not budget.can_start(config, call_limit):
        return {
            "started": False,
            "completed": False,
            "budget_blocked": True,
            "codex": None,
            "leakage_audit": None,
        }
    codex_result = run_codex(
        prompt,
        agent=config.agent,
        cwd=workspace,
        output_schema=schema_path,
        artifact_dir=artifact_dir / "agent",
    )
    budget.consume(codex_result)
    codex = codex_result.to_dict()
    leakage = audit_event_log(
        codex_result.events_path,
        workspace=workspace,
        forbidden_paths=(task.root,),
        forbidden_names=_forbidden_names(task),
    )
    return {
        "started": True,
        "completed": _agent_completed(codex),
        "budget_blocked": False,
        "codex": codex,
        "leakage_audit": leakage.to_dict(),
    }


def _cumulative_checks(
    task: TrajectoryTaskConfig,
    step_index: int,
):
    checks = list(task.baseline_checks)
    for step in task.steps[: step_index + 1]:
        checks.extend(step.public_checks)
        checks.extend(step.hidden_checks)
    return tuple(checks)


def _evaluate_candidate(
    *,
    prepared: dict[str, Any],
    stage: dict[str, Any],
    prerequisite_passed: bool,
    task: TrajectoryTaskConfig,
    step_index: int,
    artifact_dir: Path,
    config: LoopConfig,
    budget: BudgetTracker,
    call_limit: int,
) -> dict[str, Any]:
    workspace: Path = prepared["workspace"]
    checks = run_checks(
        _cumulative_checks(task, step_index),
        workspace=workspace,
        task_dir=task.root,
        log_dir=artifact_dir / "checks",
        default_timeout_seconds=config.agent.check_timeout_seconds,
    )
    violations = immutable_violations(
        workspace,
        prepared["immutable_hashes"],
    )
    metrics = collect_source_metrics(workspace, task.metric_paths)
    debt = count_debt_markers(workspace, task)
    diff = capture_git_diff(workspace, artifact_dir / "artifacts")
    leakage = stage.get("leakage_audit")
    stage_passed = bool(
        stage.get("completed")
        and isinstance(leakage, dict)
        and leakage.get("clean")
    )
    passed = bool(
        prerequisite_passed
        and stage_passed
        and budget.within_limits(config, call_limit)
        and not violations
        and all(check.passed for check in checks)
    )
    return {
        "passed": passed,
        "stage": stage,
        "checks": [check.to_dict() for check in checks],
        "immutable_violations": violations,
        "metrics": metrics.to_dict(),
        "debt_markers": debt,
        "diff": diff.to_dict(),
        "initial_tree_hash": prepared["initial_tree_hash"],
        "erased_paths": prepared["erased_paths"],
        "_workspace": workspace,
    }


def _candidate_score(candidate: dict[str, Any], index: int) -> tuple[int, ...]:
    metrics = candidate["metrics"]
    diff = candidate["diff"]
    return (
        sum(int(value) for value in candidate["debt_markers"].values()),
        int(metrics["cyclomatic_proxy"]),
        int(metrics["lines"]),
        int(diff["lines_added"]) + int(diff["lines_deleted"]),
        index,
    )


def _select_candidate(candidates: list[dict[str, Any]]) -> int | None:
    passing = [
        (index, candidate)
        for index, candidate in enumerate(candidates)
        if candidate["passed"]
    ]
    if not passing:
        return None
    return min(
        passing,
        key=lambda item: _candidate_score(item[1], item[0]),
    )[0]


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if not key.startswith("_")}


def _run_candidate_pool(
    *,
    config: LoopConfig,
    task: TrajectoryTaskConfig,
    controller: ControllerConfig,
    step: TrajectoryStepConfig,
    step_index: int,
    accepted: Path,
    step_dir: Path,
    standard_schema: Path,
    budget: BudgetTracker,
    call_limit: int,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for candidate_index in range(1, controller.candidate_count + 1):
        root = step_dir / "candidates" / f"{candidate_index:02d}"
        prepared = _prepare_workspace(
            accepted,
            root / "workspace",
            task,
        )
        prompt = build_maintenance_prompt(
            task,
            step,
            controller,
            workspace=prepared["workspace"],
            context=(
                f"You are independent candidate {candidate_index} of "
                f"{controller.candidate_count}. Do not assume another candidate "
                "will repair your work."
            ),
        )
        stage = _run_stage(
            prompt=prompt,
            workspace=prepared["workspace"],
            schema_path=standard_schema,
            artifact_dir=root / "builder",
            config=config,
            task=task,
            budget=budget,
            call_limit=call_limit,
        )
        candidates.append(
            _evaluate_candidate(
                prepared=prepared,
                stage=stage,
                prerequisite_passed=True,
                task=task,
                step_index=step_index,
                artifact_dir=root,
                config=config,
                budget=budget,
                call_limit=call_limit,
            )
        )
    selected = _select_candidate(candidates)
    return {
        "controller_type": controller.type,
        "candidates": candidates,
        "selected_index": selected,
        "decision": None,
        "review": None,
    }


def _run_reviewer_implementer(
    *,
    config: LoopConfig,
    task: TrajectoryTaskConfig,
    controller: ControllerConfig,
    step: TrajectoryStepConfig,
    step_index: int,
    accepted: Path,
    step_dir: Path,
    standard_schema: Path,
    budget: BudgetTracker,
    call_limit: int,
) -> dict[str, Any]:
    review_prepared = _prepare_workspace(
        accepted,
        step_dir / "reviewer" / "workspace",
        task,
    )
    review = _run_stage(
        prompt=build_reviewer_prompt(task, step, controller),
        workspace=review_prepared["workspace"],
        schema_path=standard_schema,
        artifact_dir=step_dir / "reviewer",
        config=config,
        task=task,
        budget=budget,
        call_limit=call_limit,
    )
    response = (review.get("codex") or {}).get("final_response") or {}
    plan = response.get("summary") if isinstance(response, dict) else ""
    review_audit = review.get("leakage_audit")
    review_passed = bool(
        review.get("completed")
        and isinstance(review_audit, dict)
        and review_audit.get("clean")
    )

    root = step_dir / "candidates" / "01"
    prepared = _prepare_workspace(accepted, root / "workspace", task)
    prompt = build_maintenance_prompt(
        task,
        step,
        controller,
        workspace=prepared["workspace"],
        context=f"Adversarial architecture review:\n\n{plan or '(review unavailable)'}",
    )
    stage = _run_stage(
        prompt=prompt,
        workspace=prepared["workspace"],
        schema_path=standard_schema,
        artifact_dir=root / "builder",
        config=config,
        task=task,
        budget=budget,
        call_limit=call_limit,
    )
    candidate = _evaluate_candidate(
        prepared=prepared,
        stage=stage,
        prerequisite_passed=review_passed,
        task=task,
        step_index=step_index,
        artifact_dir=root,
        config=config,
        budget=budget,
        call_limit=call_limit,
    )
    return {
        "controller_type": controller.type,
        "candidates": [candidate],
        "selected_index": 0 if candidate["passed"] else None,
        "decision": None,
        "review": review,
    }


def _source_symbols(workspace: Path, paths: tuple[str, ...]) -> set[str]:
    symbols: set[str] = set()
    for relative in paths:
        path = workspace / relative
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ) and node.name.startswith("_") and not node.name.startswith("__"):
                symbols.add(node.name)
    return {name for name in symbols if len(name) >= 3}


def _validate_decision(
    response: Any,
    *,
    task: TrajectoryTaskConfig,
    max_chars: int,
    source_workspace: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(response, dict):
        return {
            "valid": False,
            "errors": ["decision response is not an object"],
            "should_compact": False,
            "selected_paths": [],
            "contract": "",
            "rationale": "",
            "architecture_terms": [],
        }
    should_compact = response.get("should_compact")
    selected_raw = response.get("selected_paths")
    contract = response.get("contract")
    rationale = response.get("rationale")
    if response.get("status") != "completed":
        errors.append("decision status is not completed")
    if not isinstance(should_compact, bool):
        errors.append("should_compact is not boolean")
        should_compact = False
    if not isinstance(selected_raw, list) or any(
        not isinstance(item, str) for item in selected_raw
    ):
        errors.append("selected_paths is not a string array")
        selected: tuple[str, ...] = ()
    else:
        selected = tuple(dict.fromkeys(selected_raw))
    invalid_paths = sorted(set(selected) - set(task.erasable_paths))
    if invalid_paths:
        errors.append(f"selected paths are not eligible: {invalid_paths}")
        selected = tuple(item for item in selected if item in task.erasable_paths)
    if should_compact and not selected:
        errors.append("compaction selected without any eligible path")
    if not should_compact and selected:
        errors.append("paths selected while should_compact is false")
    if not isinstance(contract, str) or not contract.strip():
        errors.append("contract is empty")
        contract = ""
    elif len(contract) > max_chars:
        errors.append(f"contract exceeds {max_chars} characters")
        contract = contract[:max_chars]
    if not isinstance(rationale, str):
        errors.append("rationale is not a string")
        rationale = ""
    symbols = _source_symbols(source_workspace, selected)
    architecture_terms = sorted(
        symbol
        for symbol in symbols
        if re.search(rf"\b{re.escape(symbol)}\b", contract or "")
    )
    return {
        "valid": not errors,
        "errors": errors,
        "should_compact": should_compact,
        "selected_paths": list(selected),
        "contract": contract,
        "rationale": rationale,
        "architecture_terms": architecture_terms,
    }


def _run_self_erasure(
    *,
    config: LoopConfig,
    task: TrajectoryTaskConfig,
    controller: ControllerConfig,
    step: TrajectoryStepConfig,
    step_index: int,
    accepted: Path,
    step_dir: Path,
    standard_schema: Path,
    decision_schema: Path,
    budget: BudgetTracker,
    call_limit: int,
) -> dict[str, Any]:
    extractor_prepared = _prepare_workspace(
        accepted,
        step_dir / "extractor" / "workspace",
        task,
    )
    extractor = _run_stage(
        prompt=build_extractor_prompt(
            task,
            step,
            controller,
            max_chars=config.contract_max_chars,
        ),
        workspace=extractor_prepared["workspace"],
        schema_path=decision_schema,
        artifact_dir=step_dir / "extractor",
        config=config,
        task=task,
        budget=budget,
        call_limit=call_limit,
    )
    response = (extractor.get("codex") or {}).get("final_response")
    decision = _validate_decision(
        response,
        task=task,
        max_chars=config.contract_max_chars,
        source_workspace=extractor_prepared["workspace"],
    )
    extractor_audit = extractor.get("leakage_audit")
    extractor_passed = bool(
        extractor.get("completed")
        and isinstance(extractor_audit, dict)
        and extractor_audit.get("clean")
        and decision["valid"]
    )
    _write_json(step_dir / "decision.json", decision)

    selected_paths = (
        tuple(decision["selected_paths"]) if decision["should_compact"] else ()
    )
    root = step_dir / "candidates" / "01"
    prepared = _prepare_workspace(
        accepted,
        root / "workspace",
        task,
        erased_paths=selected_paths,
    )
    prompt = build_blind_builder_prompt(
        task,
        step,
        controller,
        workspace=prepared["workspace"],
        contract=decision["contract"] or "(no valid extracted contract)",
        erased_paths=selected_paths,
    )
    stage = _run_stage(
        prompt=prompt,
        workspace=prepared["workspace"],
        schema_path=standard_schema,
        artifact_dir=root / "builder",
        config=config,
        task=task,
        budget=budget,
        call_limit=call_limit,
    )
    candidate = _evaluate_candidate(
        prepared=prepared,
        stage=stage,
        prerequisite_passed=extractor_passed,
        task=task,
        step_index=step_index,
        artifact_dir=root,
        config=config,
        budget=budget,
        call_limit=call_limit,
    )
    return {
        "controller_type": controller.type,
        "candidates": [candidate],
        "selected_index": 0 if candidate["passed"] else None,
        "decision": decision,
        "review": extractor,
    }


def _freeze_loop_inputs(
    config: LoopConfig,
    run_root: Path,
) -> tuple[TrajectoryTaskConfig, Path, Path, dict[str, Any]]:
    frozen = run_root / "frozen"
    task_destination = frozen / "task"
    _copy_tree(config.task.root, task_destination)
    frozen_task = load_trajectory_task(
        task_destination / config.task.source_path.name
    )
    schemas = frozen / "schemas"
    schemas.mkdir(parents=True)
    standard_source = config.root / "schemas" / "agent_final.schema.json"
    decision_source = config.root / "schemas" / "loop_decision.schema.json"
    if not standard_source.is_file() or not decision_source.is_file():
        raise FileNotFoundError("TERP-Loop output schemas are missing")
    standard = schemas / standard_source.name
    decision = schemas / decision_source.name
    shutil.copy2(standard_source, standard)
    shutil.copy2(decision_source, decision)
    return (
        frozen_task,
        standard,
        decision,
        {
            "task_tree_hash": hash_tree(task_destination),
            "standard_schema_hash": hash_path(standard),
            "decision_schema_hash": hash_path(decision),
            "frozen_tree_hash": hash_tree(frozen),
        },
    )


def _new_run_root(config: LoopConfig) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Keep the physical path compact for Windows tools that still inherit the
    # legacy MAX_PATH limit. The full benchmark name lives in manifest.json.
    base = config.results_dir / f"{timestamp}-terp-loop"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _episode_name(
    plan: PlannedEpisode,
    task: TrajectoryTaskConfig,
) -> str:
    del task
    code = "".join(
        part[0] for part in plan.controller_name.split("_") if part
    )
    return f"{plan.index:03d}__{code}__r{plan.repetition:02d}"


def _run_episode(
    *,
    config: LoopConfig,
    task: TrajectoryTaskConfig,
    controller: ControllerConfig,
    plan: PlannedEpisode,
    episode_dir: Path,
    standard_schema: Path,
    decision_schema: Path,
) -> dict[str, Any]:
    call_limit = len(task.steps) * config.calls_per_step
    budget = BudgetTracker()
    baseline = _snapshot_accepted(
        task.seed_dir,
        episode_dir / "accepted" / "000__baseline",
    )
    baseline_metrics = collect_source_metrics(baseline, task.metric_paths)
    baseline_debt = count_debt_markers(baseline, task)
    accepted = baseline
    step_results: list[dict[str, Any]] = []
    first_failure: str | None = None

    for step_index, step in enumerate(task.steps):
        step_dir = episode_dir / "steps" / f"{step_index + 1:02d}__{step.id}"
        if not budget.can_start(config, call_limit):
            step_result = {
                "id": step.id,
                "index": step_index + 1,
                "passed": False,
                "rollback": True,
                "failure": "budget_exhausted_before_step",
                "accepted_tree_hash": hash_tree(accepted),
                "budget_after": budget.to_dict(),
                "controller": None,
            }
            _write_json(step_dir / "result.json", step_result)
            step_results.append(step_result)
            first_failure = step.id
            break

        if controller.type == "candidate_pool":
            controller_result = _run_candidate_pool(
                config=config,
                task=task,
                controller=controller,
                step=step,
                step_index=step_index,
                accepted=accepted,
                step_dir=step_dir,
                standard_schema=standard_schema,
                budget=budget,
                call_limit=call_limit,
            )
        elif controller.type == "reviewer_implementer":
            controller_result = _run_reviewer_implementer(
                config=config,
                task=task,
                controller=controller,
                step=step,
                step_index=step_index,
                accepted=accepted,
                step_dir=step_dir,
                standard_schema=standard_schema,
                budget=budget,
                call_limit=call_limit,
            )
        elif controller.type == "self_erasure":
            controller_result = _run_self_erasure(
                config=config,
                task=task,
                controller=controller,
                step=step,
                step_index=step_index,
                accepted=accepted,
                step_dir=step_dir,
                standard_schema=standard_schema,
                decision_schema=decision_schema,
                budget=budget,
                call_limit=call_limit,
            )
        else:
            raise ValueError(f"Unsupported controller type: {controller.type}")

        selected_index = controller_result["selected_index"]
        passed = selected_index is not None
        accepted_snapshot: Path | None = None
        selected_public: dict[str, Any] | None = None
        if passed:
            selected = controller_result["candidates"][selected_index]
            selected_public = _public_candidate(selected)
            accepted_snapshot = _snapshot_accepted(
                selected["_workspace"],
                episode_dir
                / "accepted"
                / f"{step_index + 1:03d}__{step.id}",
            )
            accepted = accepted_snapshot
        else:
            first_failure = first_failure or step.id

        public_controller = {
            "controller_type": controller_result["controller_type"],
            "candidates": [
                _public_candidate(candidate)
                for candidate in controller_result["candidates"]
            ],
            "selected_index": selected_index,
            "decision": controller_result["decision"],
            "review": controller_result["review"],
        }
        step_result = {
            "id": step.id,
            "index": step_index + 1,
            "passed": passed,
            "rollback": not passed,
            "failure": None if passed else "no_passing_candidate",
            "selected_candidate": selected_public,
            "accepted_snapshot": (
                str(accepted_snapshot.resolve()) if accepted_snapshot else None
            ),
            "accepted_tree_hash": hash_tree(accepted),
            "budget_after": budget.to_dict(),
            "controller": public_controller,
        }
        _write_json(step_dir / "result.json", step_result)
        step_results.append(step_result)
        _write_json(
            episode_dir / "trajectory.json",
            {
                "plan": plan.to_dict(),
                "controller": asdict(controller),
                "steps": step_results,
                "budget": budget.to_dict(),
            },
        )
        if not passed and config.stop_on_failure:
            break

    final_metrics = collect_source_metrics(accepted, task.metric_paths)
    final_debt = count_debt_markers(accepted, task)
    completed = 0
    for result in step_results:
        if not result["passed"]:
            break
        completed += 1
    result = {
        "plan": plan.to_dict(),
        "task_id": task.id,
        "controller": asdict(controller),
        "call_limit": call_limit,
        "steps_total": len(task.steps),
        "steps_attempted": len(step_results),
        "steps_completed_before_failure": completed,
        "first_failure": first_failure,
        "trajectory_success": completed == len(task.steps),
        "baseline_metrics": baseline_metrics.to_dict(),
        "final_metrics": final_metrics.to_dict(),
        "baseline_debt_markers": baseline_debt,
        "final_debt_markers": final_debt,
        "final_accepted_tree_hash": hash_tree(accepted),
        "budget": budget.to_dict(),
        "budget_within_limits": budget.within_limits(config, call_limit),
        "steps": step_results,
    }
    _write_json(episode_dir / "result.json", result)
    return result


def run_loop_benchmark(
    config: LoopConfig,
    *,
    plan: list[PlannedEpisode],
) -> Path:
    run_root = _new_run_root(config)
    task, standard_schema, decision_schema, frozen_inputs = _freeze_loop_inputs(
        config,
        run_root,
    )
    shutil.copy2(config.source_path, run_root / config.source_path.name)
    manifest = {
        "protocol": "TERP-Loop",
        "name": config.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": config.seed,
        "repetitions": config.repetitions,
        "calls_per_step": config.calls_per_step,
        "max_total_tokens": config.max_total_tokens,
        "max_total_agent_seconds": config.max_total_agent_seconds,
        "agent": asdict(config.agent),
        "controllers": {
            name: asdict(controller)
            for name, controller in config.controllers.items()
        },
        "task_sequence": [step.id for step in task.steps],
        "plan": [item.to_dict() for item in plan],
        "codex": _codex_version(config.agent.command),
        "harness_tree_hash": hash_tree(config.root),
        "harness_git": _git_identity(config.root),
        "frozen_inputs": frozen_inputs,
        "python": sys.version,
        "platform": platform.platform(),
    }
    _write_json(run_root / "manifest.json", manifest)

    for item in plan:
        controller = config.controllers[item.controller_name]
        episode_dir = run_root / "episodes" / _episode_name(item, task)
        _run_episode(
            config=config,
            task=task,
            controller=controller,
            plan=item,
            episode_dir=episode_dir,
            standard_schema=standard_schema,
            decision_schema=decision_schema,
        )

    from .loop_report import generate_loop_report

    generate_loop_report(run_root)
    return run_root
