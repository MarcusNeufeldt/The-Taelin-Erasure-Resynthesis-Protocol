from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def _rate(successes: int, total: int) -> dict[str, int | float | None]:
    return {
        "successes": successes,
        "total": total,
        "rate": successes / total if total else None,
    }


def _median(values: Iterable[int | float]) -> int | float | None:
    materialized = list(values)
    return statistics.median(materialized) if materialized else None


def _mean(values: Iterable[int | float]) -> float | None:
    materialized = list(values)
    return statistics.fmean(materialized) if materialized else None


def _metric_delta(result: dict[str, Any], field: str) -> int | float:
    baseline = result.get("baseline_metrics", {})
    final = result.get("final_metrics", {})
    return final.get(field, 0) - baseline.get(field, 0)


def _selected_candidates(
    result: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    for step in result.get("steps", []):
        candidate = step.get("selected_candidate")
        if isinstance(candidate, dict):
            yield candidate


def _contract_stats(results: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for result in results:
        for step in result.get("steps", []):
            controller = step.get("controller")
            if not isinstance(controller, dict):
                continue
            decision = controller.get("decision")
            if isinstance(decision, dict):
                decisions.append(decision)
    compacted = [item for item in decisions if item.get("should_compact")]
    architecture_terms = sum(
        len(item.get("architecture_terms", [])) for item in decisions
    )
    return {
        "decisions": len(decisions),
        "valid": _rate(
            sum(bool(item.get("valid")) for item in decisions),
            len(decisions),
        ),
        "compactions": len(compacted),
        "contract_chars_median": _median(
            len(str(item.get("contract", ""))) for item in decisions
        ),
        "architecture_term_mentions": architecture_terms,
        "decisions_with_architecture_leakage": sum(
            bool(item.get("architecture_terms")) for item in decisions
        ),
    }


def _maintenance_by_step(
    results: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for position in range(1, horizon + 1):
        attempts: list[dict[str, Any]] = []
        token_costs: list[int] = []
        time_costs: list[float] = []
        churn: list[int] = []
        for result in results:
            steps = result.get("steps", [])
            if len(steps) < position:
                continue
            step = steps[position - 1]
            attempts.append(step)
            previous_budget = (
                steps[position - 2].get("budget_after", {})
                if position > 1
                else {}
            )
            budget = step.get("budget_after", {})
            token_costs.append(
                int(budget.get("total_tokens", 0))
                - int(previous_budget.get("total_tokens", 0))
            )
            time_costs.append(
                float(budget.get("agent_seconds", 0.0))
                - float(previous_budget.get("agent_seconds", 0.0))
            )
            candidate = step.get("selected_candidate")
            if isinstance(candidate, dict):
                diff = candidate.get("diff", {})
                churn.append(
                    int(diff.get("lines_added", 0))
                    + int(diff.get("lines_deleted", 0))
                )
        summary[str(position)] = {
            "attempted": len(attempts),
            "success": _rate(
                sum(bool(item.get("passed")) for item in attempts),
                len(attempts),
            ),
            "incremental_tokens_median": _median(token_costs),
            "incremental_agent_seconds_median": _median(time_costs),
            "accepted_churn_median": _median(churn),
        }
    return summary


def _summarize_controller(
    results: list[dict[str, Any]],
    *,
    horizon: int,
) -> dict[str, Any]:
    completed = [
        int(result.get("steps_completed_before_failure", 0))
        for result in results
    ]
    total_steps = sum(int(result.get("steps_total", 0)) for result in results)
    attempted_steps = sum(
        int(result.get("steps_attempted", 0)) for result in results
    )
    passed_steps = sum(
        bool(step.get("passed"))
        for result in results
        for step in result.get("steps", [])
    )
    rollbacks = sum(
        bool(step.get("rollback"))
        for result in results
        for step in result.get("steps", [])
    )
    calls = sum(int(result.get("budget", {}).get("calls", 0)) for result in results)
    total_tokens = sum(
        int(result.get("budget", {}).get("total_tokens", 0))
        for result in results
    )
    agent_seconds = sum(
        float(result.get("budget", {}).get("agent_seconds", 0.0))
        for result in results
    )
    candidates = [
        candidate for result in results for candidate in _selected_candidates(result)
    ]
    lines_added = sum(
        int(candidate.get("diff", {}).get("lines_added", 0))
        for candidate in candidates
    )
    lines_deleted = sum(
        int(candidate.get("diff", {}).get("lines_deleted", 0))
        for candidate in candidates
    )
    files_changed = sum(
        int(candidate.get("diff", {}).get("files_changed", 0))
        for candidate in candidates
    )
    baseline_debt = sum(
        sum(int(value) for value in result.get("baseline_debt_markers", {}).values())
        for result in results
    )
    final_debt = sum(
        sum(int(value) for value in result.get("final_debt_markers", {}).values())
        for result in results
    )
    metric_fields = (
        "lines",
        "code_lines",
        "ast_nodes",
        "branch_points",
        "cyclomatic_proxy",
        "max_nesting",
    )
    horizon_success = {
        str(step): _rate(sum(value >= step for value in completed), len(results))
        for step in range(1, horizon + 1)
    }
    first_failures: dict[str, int] = defaultdict(int)
    for result in results:
        failure = result.get("first_failure")
        if failure:
            first_failures[str(failure)] += 1
    candidate_recoveries = 0
    for result in results:
        for step in result.get("steps", []):
            controller = step.get("controller")
            if not isinstance(controller, dict):
                continue
            selected = controller.get("selected_index")
            candidates = controller.get("candidates", [])
            if isinstance(selected, int) and any(
                not bool(candidate.get("passed"))
                for candidate in candidates[:selected]
                if isinstance(candidate, dict)
            ):
                candidate_recoveries += 1
    return {
        "episodes": len(results),
        "trajectory_success": _rate(
            sum(bool(result.get("trajectory_success")) for result in results),
            len(results),
        ),
        "horizon_success": horizon_success,
        "steps_completed_median": _median(completed),
        "steps_completed_mean": _mean(completed),
        "step_success": _rate(passed_steps, attempted_steps),
        "unattempted_steps": total_steps - attempted_steps,
        "rollbacks": rollbacks,
        "rollback_rate": rollbacks / attempted_steps if attempted_steps else None,
        "candidate_recoveries": candidate_recoveries,
        "first_failure_counts": dict(sorted(first_failures.items())),
        "maintenance_by_step": _maintenance_by_step(
            results,
            horizon=horizon,
        ),
        "budget_limit_violations": sum(
            not bool(result.get("budget_within_limits")) for result in results
        ),
        "usage": {
            "calls": calls,
            "total_tokens": total_tokens,
            "agent_seconds": agent_seconds,
            "tokens_per_accepted_step": (
                total_tokens / passed_steps if passed_steps else None
            ),
            "agent_seconds_per_accepted_step": (
                agent_seconds / passed_steps if passed_steps else None
            ),
        },
        "churn": {
            "files_changed": files_changed,
            "lines_added": lines_added,
            "lines_deleted": lines_deleted,
            "lines_changed_per_accepted_step": (
                (lines_added + lines_deleted) / passed_steps
                if passed_steps
                else None
            ),
        },
        "final_metric_delta_median": {
            field: _median(_metric_delta(result, field) for result in results)
            for field in metric_fields
        },
        "debt_markers": {
            "baseline_total": baseline_debt,
            "final_total": final_debt,
            "survival_rate": (
                final_debt / baseline_debt if baseline_debt else None
            ),
        },
        "contract_extraction": _contract_stats(results),
    }


def summarize_loop_results(
    results: list[dict[str, Any]],
    *,
    task_sequence: list[str],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        plan = result.get("plan", {})
        grouped[str(plan.get("controller_name", "unknown"))].append(result)
    return {
        "protocol": "TERP-Loop",
        "episodes": len(results),
        "task_sequence": task_sequence,
        "controllers": {
            name: _summarize_controller(items, horizon=len(task_sequence))
            for name, items in sorted(grouped.items())
        },
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _render_markdown(
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    lines = [
        "# TERP-Loop report",
        "",
        f"- Benchmark: `{manifest.get('name', 'unknown')}`",
        f"- Created: `{manifest.get('created_at', 'unknown')}`",
        f"- Model: `{manifest.get('agent', {}).get('model', 'unknown')}`",
        (
            "- Reasoning effort: "
            f"`{manifest.get('agent', {}).get('reasoning_effort', 'unknown')}`"
        ),
        f"- Calls per step: `{manifest.get('calls_per_step', 'unknown')}`",
        f"- Episodes: `{summary['episodes']}`",
        "",
        "## Long-horizon endpoints",
        "",
        "| Controller | Full trajectory | Median horizon | Step success | "
        "Tokens / accepted step | Rollbacks | Debt survival |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, controller in summary["controllers"].items():
        usage = controller["usage"]
        debt = controller["debt_markers"]
        tokens_per_step = usage["tokens_per_accepted_step"]
        lines.append(
            f"| `{name}` | "
            f"{_percent(controller['trajectory_success']['rate'])} | "
            f"{controller['steps_completed_median']} | "
            f"{_percent(controller['step_success']['rate'])} | "
            f"{tokens_per_step:.0f} | "
            f"{controller['rollbacks']} | "
            f"{_percent(debt['survival_rate'])} |"
            if tokens_per_step is not None
            else (
                f"| `{name}` | "
                f"{_percent(controller['trajectory_success']['rate'])} | "
                f"{controller['steps_completed_median']} | "
                f"{_percent(controller['step_success']['rate'])} | n/a | "
                f"{controller['rollbacks']} | "
                f"{_percent(debt['survival_rate'])} |"
            )
        )
    lines.extend(["", "## Success by maintenance horizon", ""])
    headings = " | ".join(f"Task {index}" for index in range(1, len(summary["task_sequence"]) + 1))
    lines.extend(
        [
            f"| Controller | {headings} |",
            "| --- | " + " | ".join("---:" for _ in summary["task_sequence"]) + " |",
        ]
    )
    for name, controller in summary["controllers"].items():
        rates = " | ".join(
            _percent(controller["horizon_success"][str(index)]["rate"])
            for index in range(1, len(summary["task_sequence"]) + 1)
        )
        lines.append(f"| `{name}` | {rates} |")
    lines.extend(
        [
            "",
            "## Final maintenance-task cost",
            "",
            "| Controller | Attempted | Success | Median tokens | "
            "Median agent seconds | First failures |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    final_position = str(len(summary["task_sequence"]))
    for name, controller in summary["controllers"].items():
        final_task = controller["maintenance_by_step"][final_position]
        lines.append(
            f"| `{name}` | {final_task['attempted']} | "
            f"{_percent(final_task['success']['rate'])} | "
            f"{final_task['incremental_tokens_median'] or 'n/a'} | "
            f"{final_task['incremental_agent_seconds_median'] or 'n/a'} | "
            f"`{json.dumps(controller['first_failure_counts'], sort_keys=True)}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- The primary endpoint is full-trajectory survival under matched agent-call budgets.",
            "- Horizon rates show where irreversible regressions first appear.",
            "- Contract leakage counts source-level symbol names repeated in extractor contracts; it is a diagnostic, not a complete semantic-leakage detector.",
            "- Code-size, complexity, churn, and debt-marker outcomes are secondary and must not override behavioral verification.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_loop_report(run_root: str | Path) -> tuple[Path, Path]:
    root = Path(run_root).resolve()
    manifest = _read_json(root / "manifest.json")
    result_paths = sorted((root / "episodes").glob("*/result.json"))
    results = [_read_json(path) for path in result_paths]
    summary = summarize_loop_results(
        results,
        task_sequence=list(manifest.get("task_sequence", [])),
    )
    summary_path = root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = root / "REPORT.md"
    report_path.write_text(
        _render_markdown(manifest, summary),
        encoding="utf-8",
    )
    return summary_path, report_path
