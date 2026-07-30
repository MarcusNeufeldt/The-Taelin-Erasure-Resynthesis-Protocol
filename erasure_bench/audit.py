from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditViolation:
    event_index: int
    category: str
    detail: str


@dataclass(frozen=True)
class LeakageAudit:
    clean: bool
    commands_audited: int
    file_changes_audited: int
    violations: list[AuditViolation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "commands_audited": self.commands_audited,
            "file_changes_audited": self.file_changes_audited,
            "violations": [asdict(item) for item in self.violations],
        }


NETWORK_PATTERN = re.compile(
    r"(?i)(https?://|\bcurl(?:\.exe)?\b|\bwget(?:\.exe)?\b|"
    r"\binvoke-webrequest\b|\binvoke-restmethod\b)"
)
HISTORY_PATTERN = re.compile(
    r"(?i)\bgit(?:\.exe)?\b[^\r\n]*(?:\blog\b|\bshow\b|\breflog\b|"
    r"\bblame\b|\bcheckout\s+HEAD[~^:]|\bcat-file\b)"
)
PARENT_PATTERN = re.compile(r"(?i)(?:^|[\s'\"=/\\])\.\.(?:[/\\]|$)")


def _inside(workspace: Path, candidate: Path) -> bool:
    workspace = workspace.resolve()
    candidate = candidate.resolve()
    return candidate == workspace or workspace in candidate.parents


def audit_event_log(
    events_path: str | Path,
    *,
    workspace: Path,
    forbidden_paths: tuple[Path, ...] = (),
    forbidden_names: tuple[str, ...] = (),
) -> LeakageAudit:
    path = Path(events_path)
    violations: list[AuditViolation] = []
    commands_audited = 0
    file_changes_audited = 0
    seen_commands: set[str] = set()
    normalized_forbidden = [
        str(item.resolve()).replace("/", "\\").lower()
        for item in forbidden_paths
    ]
    names = tuple(item.lower() for item in forbidden_names)

    if not path.is_file():
        return LeakageAudit(False, 0, 0, [AuditViolation(0, "missing-events", str(path))])

    for index, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "command_execution":
            command = item.get("command")
            if not isinstance(command, str) or command in seen_commands:
                continue
            seen_commands.add(command)
            commands_audited += 1
            lower = command.replace("/", "\\").lower()
            if NETWORK_PATTERN.search(command):
                violations.append(AuditViolation(index, "network", command[:1000]))
            if HISTORY_PATTERN.search(command):
                violations.append(AuditViolation(index, "git-history", command[:1000]))
            if PARENT_PATTERN.search(command):
                violations.append(AuditViolation(index, "parent-traversal", command[:1000]))
            if any(value and value in lower for value in normalized_forbidden):
                violations.append(AuditViolation(index, "forbidden-path", command[:1000]))
            if any(name and name in lower for name in names):
                violations.append(AuditViolation(index, "forbidden-name", command[:1000]))

        if item_type == "file_change":
            changes = item.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                changed_path = change.get("path")
                if not isinstance(changed_path, str):
                    continue
                file_changes_audited += 1
                candidate = Path(changed_path)
                if not candidate.is_absolute():
                    candidate = workspace / candidate
                if not _inside(workspace, candidate):
                    violations.append(
                        AuditViolation(index, "outside-file-change", changed_path)
                    )

        if item_type in {"mcp_tool_call", "web_search"}:
            serialized = json.dumps(item, ensure_ascii=False)
            lower = serialized.replace("/", "\\").lower()
            if item_type == "web_search" or NETWORK_PATTERN.search(serialized):
                violations.append(
                    AuditViolation(index, "network-tool", serialized[:1000])
                )
            if any(value and value in lower for value in normalized_forbidden):
                violations.append(
                    AuditViolation(index, "forbidden-tool-path", serialized[:1000])
                )
            if any(name and name in lower for name in names):
                violations.append(
                    AuditViolation(index, "forbidden-tool-name", serialized[:1000])
                )

    return LeakageAudit(
        clean=not violations,
        commands_audited=commands_audited,
        file_changes_audited=file_changes_audited,
        violations=violations,
    )
