from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .codex import resolve_codex_command
from .config import BenchmarkConfig, ConfigError, load_benchmark_config
from .loop_config import LoopConfig, load_loop_config
from .loop_report import generate_loop_report
from .loop_runner import build_loop_plan, run_loop_benchmark
from .report import generate_report
from .runner import build_plan, run_benchmark
from .verification import run_checks
from .workspace import prepare_workspace


def _common_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("benchmark.toml"),
        help="Benchmark TOML file (default: benchmark.toml).",
    )


def _plan_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", action="append", dest="tasks")
    parser.add_argument("--arm", action="append", dest="arms")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--max-runs", type=int)


def _loop_common_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("loop.toml"),
        help="TERP-Loop TOML file (default: loop.toml).",
    )


def _loop_plan_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--controller", action="append", dest="controllers")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--max-episodes", type=int)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="erasure-bench",
        description=(
            "Run TERP-Core visibility ablations and TERP-Loop maintenance "
            "trajectories."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="Validate configuration, fixtures, Codex CLI, and baseline checks.",
    )
    _common_config(validate)

    plan = subparsers.add_parser(
        "plan",
        help="Print the seeded randomized run plan without launching agents.",
    )
    _common_config(plan)
    _plan_filters(plan)

    run = subparsers.add_parser("run", help="Run benchmark agents and verification.")
    _common_config(run)
    _plan_filters(run)
    run.add_argument(
        "--skip-challenges",
        action="store_true",
        help="Run generation only; this does not measure the primary endpoint.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and do not launch Codex.",
    )
    run.add_argument(
        "--allow-danger-full-access",
        action="store_true",
        help=(
            "Required acknowledgement when agent.sandbox is danger-full-access. "
            "Fixtures are disposable, but this is not an OS security boundary."
        ),
    )

    report = subparsers.add_parser("report", help="Regenerate a run report.")
    report.add_argument("run_dir", type=Path)

    loop_validate = subparsers.add_parser(
        "loop-validate",
        help="Validate TERP-Loop configuration, fixture, Codex, and baseline.",
    )
    _loop_common_config(loop_validate)

    loop_plan = subparsers.add_parser(
        "loop-plan",
        help="Print the randomized TERP-Loop episode plan.",
    )
    _loop_common_config(loop_plan)
    _loop_plan_filters(loop_plan)

    loop_run = subparsers.add_parser(
        "loop-run",
        help="Run compute-matched sequential TERP-Loop episodes.",
    )
    _loop_common_config(loop_run)
    _loop_plan_filters(loop_run)
    loop_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the episode plan and do not launch Codex.",
    )
    loop_run.add_argument(
        "--allow-danger-full-access",
        action="store_true",
        help=(
            "Required acknowledgement when agent.sandbox is danger-full-access. "
            "Fixtures are disposable, but this is not an OS security boundary."
        ),
    )

    loop_report = subparsers.add_parser(
        "loop-report",
        help="Regenerate a TERP-Loop run report.",
    )
    loop_report.add_argument("run_dir", type=Path)
    return parser


def _load(path: Path) -> BenchmarkConfig:
    return load_benchmark_config(path.resolve())


def _load_loop(path: Path) -> LoopConfig:
    return load_loop_config(path.resolve())


def _selected_plan(args: argparse.Namespace, config: BenchmarkConfig):
    return build_plan(
        config,
        task_ids=set(args.tasks) if args.tasks else None,
        arm_names=set(args.arms) if args.arms else None,
        repetitions=args.repetitions,
        max_runs=args.max_runs,
    )


def _codex_identity(config: BenchmarkConfig | LoopConfig) -> dict[str, str]:
    command = resolve_codex_command(config.agent.command)
    result = subprocess.run(
        [command, "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return {"command": command, "version": result.stdout.strip()}


def _require_sandbox_acknowledgement(
    config: BenchmarkConfig | LoopConfig,
    *,
    allowed: bool,
) -> None:
    if config.agent.sandbox == "danger-full-access" and not allowed:
        raise RuntimeError(
            "agent.sandbox is danger-full-access; rerun with "
            "--allow-danger-full-access after reviewing the fixture and README"
        )


def _validate(config: BenchmarkConfig) -> dict:
    checks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="erasure-bench-validate-") as temp:
        temp_root = Path(temp)
        visible_arm = next(
            arm
            for arm in config.arms.values()
            if arm.implementation == "visible"
        )
        for task in config.tasks:
            workspace = temp_root / task.id / "visible"
            prepare_workspace(task, visible_arm, workspace)
            results = run_checks(
                task.public_checks + task.hidden_checks,
                workspace=workspace,
                task_dir=task.root,
                log_dir=temp_root / task.id / "checks",
                default_timeout_seconds=config.agent.check_timeout_seconds,
            )
            checks.extend(
                {
                    "task": task.id,
                    "name": result.name,
                    "passed": result.passed,
                    "returncode": result.returncode,
                    "error": result.error,
                }
                for result in results
            )
            for arm in config.arms.values():
                if arm.implementation == "stub":
                    stub_workspace = temp_root / task.id / f"stub-{arm.name}"
                    prepare_workspace(task, arm, stub_workspace)

    return {
        "valid": all(item["passed"] for item in checks),
        "codex": _codex_identity(config),
        "model": config.agent.model,
        "reasoning_effort": config.agent.reasoning_effort,
        "tasks": [task.id for task in config.tasks],
        "arms": list(config.arm_order),
        "baseline_checks": checks,
    }


def _validate_loop(config: LoopConfig) -> dict:
    task = config.task
    with tempfile.TemporaryDirectory(prefix="terp-loop-validate-") as temp:
        root = Path(temp)
        results = run_checks(
            task.baseline_checks,
            workspace=task.seed_dir,
            task_dir=task.root,
            log_dir=root / "baseline-checks",
            default_timeout_seconds=config.agent.check_timeout_seconds,
        )
    checks = [
        {
            "name": result.name,
            "passed": result.passed,
            "returncode": result.returncode,
            "error": result.error,
        }
        for result in results
    ]
    return {
        "valid": all(item["passed"] for item in checks),
        "protocol": "TERP-Loop",
        "codex": _codex_identity(config),
        "model": config.agent.model,
        "reasoning_effort": config.agent.reasoning_effort,
        "task": task.id,
        "task_sequence": [step.id for step in task.steps],
        "controllers": list(config.controller_order),
        "calls_per_step": config.calls_per_step,
        "baseline_checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            summary_path, report_path = generate_report(args.run_dir)
            print(json.dumps({"summary": str(summary_path), "report": str(report_path)}))
            return 0
        if args.command == "loop-report":
            summary_path, report_path = generate_loop_report(args.run_dir)
            print(json.dumps({"summary": str(summary_path), "report": str(report_path)}))
            return 0

        if args.command.startswith("loop-"):
            loop_config = _load_loop(args.config)
            if args.command == "loop-validate":
                result = _validate_loop(loop_config)
                print(json.dumps(result, indent=2))
                return 0 if result["valid"] else 1
            loop_plan = build_loop_plan(
                loop_config,
                controller_names=(
                    set(args.controllers) if args.controllers else None
                ),
                repetitions=args.repetitions,
                max_episodes=args.max_episodes,
            )
            if args.command == "loop-plan" or args.dry_run:
                print(
                    json.dumps(
                        {
                            "protocol": "TERP-Loop",
                            "seed": loop_config.seed,
                            "model": loop_config.agent.model,
                            "reasoning_effort": (
                                loop_config.agent.reasoning_effort
                            ),
                            "episodes": [item.to_dict() for item in loop_plan],
                        },
                        indent=2,
                    )
                )
                return 0
            _require_sandbox_acknowledgement(
                loop_config,
                allowed=args.allow_danger_full_access,
            )
            run_root = run_loop_benchmark(loop_config, plan=loop_plan)
            print(str(run_root))
            return 0

        config = _load(args.config)
        if args.command == "validate":
            result = _validate(config)
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1

        plan = _selected_plan(args, config)
        if args.command == "plan" or args.dry_run:
            print(
                json.dumps(
                    {
                        "seed": config.seed,
                        "model": config.agent.model,
                        "reasoning_effort": config.agent.reasoning_effort,
                        "runs": [item.to_dict() for item in plan],
                    },
                    indent=2,
                )
            )
            return 0

        _require_sandbox_acknowledgement(
            config,
            allowed=args.allow_danger_full_access,
        )
        run_root = run_benchmark(
            config,
            plan=plan,
            skip_challenges=args.skip_challenges,
        )
        print(str(run_root))
        return 0
    except (ConfigError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
