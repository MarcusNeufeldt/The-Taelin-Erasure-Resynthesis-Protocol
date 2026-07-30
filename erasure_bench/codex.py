from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AgentConfig


class CodexExecError(RuntimeError):
    """Raised when the local Codex executable cannot be launched."""


@dataclass(frozen=True)
class EventSummary:
    valid_events: int
    invalid_lines: int
    event_counts: dict[str, int]
    item_counts: dict[str, int]
    thread_id: str | None
    usage: dict[str, int]


@dataclass(frozen=True)
class CodexRunResult:
    command: list[str]
    returncode: int | None
    duration_seconds: float
    timed_out: bool
    final_message: str
    final_response: dict[str, Any] | None
    event_summary: EventSummary
    events_path: str
    stderr_path: str
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["event_summary"] = asdict(self.event_summary)
        return result


def resolve_codex_command(command: str) -> str:
    resolved = shutil.which(command)
    if resolved:
        return str(Path(resolved).resolve())
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    raise CodexExecError(
        f"Codex CLI not found: {command!r}. Install it or configure agent.command."
    )


def build_codex_command(
    *,
    agent: AgentConfig,
    cwd: Path,
    output_schema: Path,
    output_last_message: Path,
) -> list[str]:
    command = [resolve_codex_command(agent.command), "exec"]
    if agent.ephemeral:
        command.append("--ephemeral")
    command.append("--skip-git-repo-check")
    if agent.ignore_user_config:
        command.append("--ignore-user-config")
    if agent.ignore_rules:
        command.append("--ignore-rules")
    command.extend(
        [
            "--sandbox",
            agent.sandbox,
            "-C",
            str(cwd.resolve()),
            "-m",
            agent.model,
            "-c",
            f'model_reasoning_effort="{agent.reasoning_effort}"',
            "-c",
            f'approval_policy="{agent.approval_policy}"',
            "--color",
            "never",
            "--json",
            "--output-schema",
            str(output_schema.resolve()),
            "--output-last-message",
            str(output_last_message.resolve()),
            "-",
        ]
    )
    return command


def parse_event_log(path: Path) -> EventSummary:
    event_counts: dict[str, int] = {}
    item_counts: dict[str, int] = {}
    valid_events = 0
    invalid_lines = 0
    thread_id: str | None = None
    usage: dict[str, int] = {}

    if not path.is_file():
        return EventSummary(0, 0, {}, {}, None, {})

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(event, dict):
                invalid_lines += 1
                continue

            valid_events += 1
            event_type = str(event.get("type", "unknown"))
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

            candidate_thread_id = event.get("thread_id")
            if isinstance(candidate_thread_id, str):
                thread_id = candidate_thread_id

            item = event.get("item")
            if event_type == "item.completed" and isinstance(item, dict):
                item_type = str(item.get("type", "unknown"))
                item_counts[item_type] = item_counts.get(item_type, 0) + 1

            candidate_usage = event.get("usage")
            if isinstance(candidate_usage, dict):
                normalized_usage: dict[str, int] = {}
                for key, value in candidate_usage.items():
                    if isinstance(value, int) and value >= 0:
                        normalized_usage[str(key)] = value
                if normalized_usage:
                    usage = normalized_usage

    return EventSummary(
        valid_events=valid_events,
        invalid_lines=invalid_lines,
        event_counts=event_counts,
        item_counts=item_counts,
        thread_id=thread_id,
        usage=usage,
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _stderr_tail(path: Path, limit: int = 2000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:].strip()


def run_codex(
    prompt: str,
    *,
    agent: AgentConfig,
    cwd: Path,
    output_schema: Path,
    artifact_dir: Path,
) -> CodexRunResult:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    events_path = artifact_dir / "events.jsonl"
    stderr_path = artifact_dir / "stderr.log"
    final_path = artifact_dir / "final-message.json"
    command = build_codex_command(
        agent=agent,
        cwd=cwd,
        output_schema=output_schema,
        output_last_message=final_path,
    )

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    start = time.perf_counter()
    timed_out = False
    returncode: int | None = None
    launch_error: str | None = None

    try:
        with events_path.open("w", encoding="utf-8", newline="\n") as events, stderr_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(cwd.resolve()),
                env=env,
                stdin=subprocess.PIPE,
                stdout=events,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                process.communicate(input=prompt, timeout=agent.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
            returncode = process.returncode
    except OSError as exc:
        launch_error = f"Unable to launch Codex: {exc}"

    duration = time.perf_counter() - start
    final_message = ""
    final_response: dict[str, Any] | None = None
    if final_path.is_file():
        final_message = final_path.read_text(encoding="utf-8", errors="replace").strip()
        if final_message:
            try:
                parsed = json.loads(final_message)
                if isinstance(parsed, dict):
                    final_response = parsed
            except json.JSONDecodeError:
                pass

    error = launch_error
    if error is None and timed_out:
        error = f"codex exec timed out after {agent.timeout_seconds} seconds"
    elif error is None and returncode != 0:
        detail = _stderr_tail(stderr_path)
        error = f"codex exec exited with {returncode}"
        if detail:
            error += f": {detail}"
    elif error is None and not final_message:
        error = "codex exec wrote no final message"

    return CodexRunResult(
        command=command,
        returncode=returncode,
        duration_seconds=duration,
        timed_out=timed_out,
        final_message=final_message,
        final_response=final_response,
        event_summary=parse_event_log(events_path),
        events_path=str(events_path.resolve()),
        stderr_path=str(stderr_path.resolve()),
        error=error,
    )
