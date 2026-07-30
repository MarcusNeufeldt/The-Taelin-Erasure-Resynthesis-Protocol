from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import CheckConfig


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: list[str]
    returncode: int | None
    duration_seconds: float
    timed_out: bool
    passed: bool
    stdout_path: str
    stderr_path: str
    error: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def expand_command(
    command: tuple[str, ...],
    *,
    workspace: Path,
    task_dir: Path,
) -> list[str]:
    values = {
        "python": sys.executable,
        "workspace": str(workspace.resolve()),
        "task_dir": str(task_dir.resolve()),
    }
    try:
        return [part.format_map(values) for part in command]
    except KeyError as exc:
        raise ValueError(f"Unknown command placeholder: {exc.args[0]}") from exc


def display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "check"


def run_check(
    check: CheckConfig,
    *,
    workspace: Path,
    task_dir: Path,
    log_dir: Path,
    default_timeout_seconds: int,
) -> CheckResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    command = expand_command(check.command, workspace=workspace, task_dir=task_dir)
    stem = _safe_name(check.name)
    stdout_path = log_dir / f"{stem}.stdout.log"
    stderr_path = log_dir / f"{stem}.stderr.log"
    timeout = check.timeout_seconds or default_timeout_seconds
    start = time.perf_counter()
    returncode: int | None = None
    timed_out = False
    error: str | None = None

    try:
        result = subprocess.run(
            command,
            cwd=str(workspace.resolve()),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode = result.returncode
        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        error = f"check timed out after {timeout} seconds"
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
    except OSError as exc:
        error = f"unable to launch check: {exc}"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(error, encoding="utf-8")

    duration = time.perf_counter() - start
    return CheckResult(
        name=check.name,
        command=command,
        returncode=returncode,
        duration_seconds=duration,
        timed_out=timed_out,
        passed=returncode == 0 and not timed_out and error is None,
        stdout_path=str(stdout_path.resolve()),
        stderr_path=str(stderr_path.resolve()),
        error=error,
    )


def run_checks(
    checks: tuple[CheckConfig, ...],
    *,
    workspace: Path,
    task_dir: Path,
    log_dir: Path,
    default_timeout_seconds: int,
) -> list[CheckResult]:
    return [
        run_check(
            check,
            workspace=workspace,
            task_dir=task_dir,
            log_dir=log_dir,
            default_timeout_seconds=default_timeout_seconds,
        )
        for check in checks
    ]
