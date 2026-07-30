from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _wilson(successes: int, total: int) -> tuple[float, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _rate(successes: int, total: int) -> dict[str, Any]:
    interval = _wilson(successes, total)
    return {
        "successes": successes,
        "total": total,
        "rate": successes / total if total else None,
        "wilson_95": list(interval) if interval else None,
    }


def _collect_usage(result: dict[str, Any]) -> tuple[dict[str, int], float, int]:
    usage: dict[str, int] = defaultdict(int)
    duration = 0.0
    tool_events = 0
    codex_runs = [result.get("codex")]
    codex_runs.extend(
        challenge.get("codex")
        for challenge in result.get("challenges", [])
        if isinstance(challenge, dict)
    )
    for codex in codex_runs:
        if not isinstance(codex, dict):
            continue
        duration += float(codex.get("duration_seconds") or 0)
        summary = codex.get("event_summary")
        if not isinstance(summary, dict):
            continue
        raw_usage = summary.get("usage")
        if isinstance(raw_usage, dict):
            for key, value in raw_usage.items():
                if isinstance(value, int):
                    usage[str(key)] += value
        item_counts = summary.get("item_counts")
        if isinstance(item_counts, dict):
            tool_events += sum(
                int(value)
                for key, value in item_counts.items()
                if key in {"command_execution", "mcp_tool_call", "file_change"}
                and isinstance(value, int)
            )
    return dict(usage), duration, tool_events


def _metric_deltas(result: dict[str, Any]) -> dict[str, int | float]:
    baseline = result.get("baseline_metrics")
    candidate = result.get("candidate_metrics")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return {}
    fields = [
        "bytes",
        "lines",
        "nonblank_lines",
        "code_lines",
        "ast_nodes",
        "branch_points",
        "cyclomatic_proxy",
        "max_nesting",
        "imports",
    ]
    deltas: dict[str, int | float] = {}
    for field in fields:
        before = baseline.get(field)
        after = candidate.get(field)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            deltas[field] = after - before
    return deltas


def _endpoint_values(result: dict[str, Any], endpoint: str) -> list[int]:
    if endpoint == "generation":
        return [int(bool(result.get("generation_passed")))]
    if endpoint == "pipeline":
        challenges = result.get("challenges")
        if not isinstance(challenges, list):
            return []
        return [
            int(bool(challenge.get("passed")))
            for challenge in challenges
            if isinstance(challenge, dict)
        ]
    raise ValueError(f"Unknown endpoint: {endpoint}")


def _paired_effect(
    results: list[dict[str, Any]],
    *,
    treatment: str,
    control: str,
    endpoint: str,
    seed: int,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for result in results:
        plan = result.get("plan")
        if not isinstance(plan, dict):
            continue
        task_id = str(plan.get("task_id", "unknown"))
        arm_name = str(plan.get("arm_name", "unknown"))
        grouped[(task_id, arm_name)].extend(_endpoint_values(result, endpoint))

    tasks = sorted(
        task_id
        for task_id, arm_name in grouped
        if arm_name == treatment
        and grouped[(task_id, treatment)]
        and grouped.get((task_id, control))
    )
    deltas: list[float] = []
    per_task: dict[str, float] = {}
    for task_id in tasks:
        treatment_values = grouped[(task_id, treatment)]
        control_values = grouped[(task_id, control)]
        delta = statistics.mean(treatment_values) - statistics.mean(control_values)
        deltas.append(delta)
        per_task[task_id] = delta

    interval: list[float] | None = None
    if len(deltas) >= 2:
        rng = random.Random(seed)
        samples = sorted(
            statistics.mean(rng.choices(deltas, k=len(deltas)))
            for _ in range(10_000)
        )
        interval = [samples[249], samples[9749]]

    return {
        "treatment": treatment,
        "control": control,
        "endpoint": endpoint,
        "fixture_blocks": len(deltas),
        "effect": statistics.mean(deltas) if deltas else None,
        "cluster_bootstrap_95": interval,
        "improved_blocks": sum(delta > 0 for delta in deltas),
        "tied_blocks": sum(delta == 0 for delta in deltas),
        "worse_blocks": sum(delta < 0 for delta in deltas),
        "per_task_effect": per_task,
    }


def summarize_results(
    results: list[dict[str, Any]],
    *,
    seed: int = 0,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        plan = result.get("plan")
        if isinstance(plan, dict):
            grouped[str(plan.get("arm_name", "unknown"))].append(result)

    arms: dict[str, Any] = {}
    for arm_name, arm_results in sorted(grouped.items()):
        generation_successes = sum(bool(item.get("generation_passed")) for item in arm_results)
        pipeline_trials = 0
        pipeline_successes = 0
        challenge_runs = 0
        usage_totals: dict[str, int] = defaultdict(int)
        duration = 0.0
        tool_events = 0
        accepted_deltas: dict[str, list[float]] = defaultdict(list)
        failures: list[str] = []
        leakage_failures: list[str] = []

        for result in arm_results:
            plan = result.get("plan", {})
            case_label = (
                f"{plan.get('task_id', '?')}/r{plan.get('repetition', '?')}"
            )
            challenges = result.get("challenges", [])
            if isinstance(challenges, list):
                pipeline_trials += len(challenges)
                pipeline_successes += sum(
                    bool(challenge.get("passed"))
                    for challenge in challenges
                    if isinstance(challenge, dict)
                )
                challenge_runs += sum(
                    challenge.get("status") == "completed"
                    for challenge in challenges
                    if isinstance(challenge, dict)
                )

            usage, case_duration, case_tool_events = _collect_usage(result)
            for key, value in usage.items():
                usage_totals[key] += value
            duration += case_duration
            tool_events += case_tool_events

            if result.get("generation_passed"):
                for key, value in _metric_deltas(result).items():
                    accepted_deltas[key].append(float(value))
            else:
                failures.append(case_label)
            audit = result.get("leakage_audit")
            if isinstance(audit, dict) and not audit.get("clean", False):
                leakage_failures.append(f"{case_label}/generation")
            challenge_items = challenges if isinstance(challenges, list) else []
            for challenge in challenge_items:
                if not isinstance(challenge, dict):
                    continue
                challenge_audit = challenge.get("leakage_audit")
                if isinstance(challenge_audit, dict) and not challenge_audit.get(
                    "clean",
                    False,
                ):
                    leakage_failures.append(
                        f"{case_label}/{challenge.get('id', '?')}"
                    )

        arms[arm_name] = {
            "cases": len(arm_results),
            "generation": _rate(generation_successes, len(arm_results)),
            "pipeline": _rate(pipeline_successes, pipeline_trials),
            "challenge_runs": challenge_runs,
            "usage": dict(usage_totals),
            "agent_duration_seconds": duration,
            "tool_events": tool_events,
            "tokens_per_pipeline_success": (
                (
                    usage_totals.get("input_tokens", 0)
                    + usage_totals.get("output_tokens", 0)
                )
                / pipeline_successes
                if pipeline_successes
                else None
            ),
            "accepted_metric_delta_median": {
                key: statistics.median(values)
                for key, values in sorted(accepted_deltas.items())
                if values
            },
            "generation_failure_blocks": failures,
            "leakage_failures": leakage_failures,
        }
    comparisons = [
        _paired_effect(
            results,
            treatment="hidden_resynthesis",
            control=control,
            endpoint=endpoint,
            seed=seed,
        )
        for control in ("visible_rewrite", "visible_refactor")
        for endpoint in ("generation", "pipeline")
    ]
    causal_pipeline = next(
        item
        for item in comparisons
        if item["control"] == "visible_rewrite" and item["endpoint"] == "pipeline"
    )
    hidden_generation = arms.get("hidden_resynthesis", {}).get("generation", {}).get("rate")
    visible_generation_rates = [
        value["generation"]["rate"]
        for name, value in arms.items()
        if name != "hidden_resynthesis" and value["generation"]["rate"] is not None
    ]
    generation_gap = (
        hidden_generation - max(visible_generation_rates)
        if hidden_generation is not None and visible_generation_rates
        else None
    )
    leakage_failures = sum(
        len(value["leakage_failures"])
        for value in arms.values()
    )
    enough_blocks = causal_pipeline["fixture_blocks"] >= 6
    criteria = {
        "at_least_six_fixture_blocks": enough_blocks,
        "pipeline_gain_at_least_10pp": (
            causal_pipeline["effect"] is not None
            and causal_pipeline["effect"] >= 0.10
        ),
        "improves_at_least_five_blocks": causal_pipeline["improved_blocks"] >= 5,
        "generation_within_5pp_of_best_visible": (
            generation_gap is not None and generation_gap >= -0.05
        ),
        "zero_leakage": leakage_failures == 0,
    }
    stop_go = {
        "verdict": (
            "insufficient_fixture_blocks"
            if not enough_blocks
            else "advance"
            if all(criteria.values())
            else "stop_or_redesign"
        ),
        "criteria": criteria,
        "generation_gap_to_best_visible": generation_gap,
    }
    return {
        "arms": arms,
        "comparisons": comparisons,
        "stop_go": stop_go,
        "total_cases": len(results),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _fraction(rate: dict[str, Any]) -> str:
    return (
        f"{rate['successes']}/{rate['total']} ({_percent(rate['rate'])})"
        if rate["total"]
        else "n/a"
    )


def _render_markdown(
    run_root: Path,
    manifest: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    lines = [
        "# Erasure benchmark report",
        "",
        f"- Run: `{run_root.name}`",
        f"- Created: `{manifest.get('created_at', 'unknown')}`",
        f"- Model: `{manifest.get('agent', {}).get('model', 'unknown')}`",
        (
            "- Reasoning effort: "
            f"`{manifest.get('agent', {}).get('reasoning_effort', 'unknown')}`"
        ),
        f"- Codex: `{manifest.get('codex', {}).get('version', 'unknown')}`",
        f"- Seed: `{manifest.get('seed', 'unknown')}`",
        "",
        "## Primary results",
        "",
        "| Arm | Cases | Generation correctness | Pipeline success | Agent time |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm_name, arm in summary["arms"].items():
        lines.append(
            f"| `{arm_name}` | {arm['cases']} | "
            f"{_fraction(arm['generation'])} | {_fraction(arm['pipeline'])} | "
            f"{arm['agent_duration_seconds']:.1f}s |"
        )

    lines.extend(["", "## Paired fixture effects", ""])
    for comparison in summary["comparisons"]:
        effect = comparison["effect"]
        rendered_effect = "n/a" if effect is None else f"{100 * effect:+.1f} pp"
        lines.append(
            f"- `{comparison['treatment']}` vs `{comparison['control']}` "
            f"on `{comparison['endpoint']}`: {rendered_effect} across "
            f"{comparison['fixture_blocks']} fixture blocks; "
            f"bootstrap 95% `{comparison['cluster_bootstrap_95']}`."
        )
    lines.extend(
        [
            "",
            f"Stop/go verdict: `{summary['stop_go']['verdict']}`.",
            "",
        ]
    )

    if all(arm["pipeline"]["total"] == 0 for arm in summary["arms"].values()):
        lines.extend(
            [
                "",
                "> Challenges were skipped or absent. This run validates generation only;",
                "> it does not measure the primary traversability endpoint.",
            ]
        )

    lines.extend(["", "## Cost and deterministic secondary metrics", ""])
    for arm_name, arm in summary["arms"].items():
        lines.extend(
            [
                f"### `{arm_name}`",
                "",
                f"- Usage: `{json.dumps(arm['usage'], sort_keys=True)}`",
                f"- Recorded tool/file events: `{arm['tool_events']}`",
                (
                    "- Tokens per pipeline success: "
                    f"`{arm['tokens_per_pipeline_success']}`"
                ),
                (
                    "- Median accepted-candidate metric deltas: "
                    f"`{json.dumps(arm['accepted_metric_delta_median'], sort_keys=True)}`"
                ),
                (
                    "- Generation failures: "
                    f"`{json.dumps(arm['generation_failure_blocks'])}`"
                ),
                f"- Leakage audit failures: `{json.dumps(arm['leakage_failures'])}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "Generation failures remain in the pipeline denominator. Static metric",
            "improvements are reported only for correctness-passing candidates and are",
            "secondary. The bundled parser fixture is a harness smoke test, not a basis",
            "for a claim about erasure. A real pilot requires frozen multi-module",
            "fixtures, negative controls, multiple repetitions, and leakage review.",
            "",
        ]
    )
    return "\n".join(lines)


def generate_report(run_root: str | Path) -> tuple[Path, Path]:
    root = Path(run_root).resolve()
    manifest = _read_json(root / "manifest.json")
    result_paths = sorted((root / "cases").glob("*/result.json"))
    results = [_read_json(path) for path in result_paths]
    summary = summarize_results(results, seed=int(manifest.get("seed", 0)))

    summary_path = root / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = root / "report.md"
    report_path.write_text(
        _render_markdown(root, manifest, summary),
        encoding="utf-8",
    )
    return summary_path, report_path
