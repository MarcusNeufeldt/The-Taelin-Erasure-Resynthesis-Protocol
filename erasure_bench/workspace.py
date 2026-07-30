from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ArmConfig, TaskConfig


IGNORED_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "runs",
}


@dataclass(frozen=True)
class PreparedWorkspace:
    path: Path
    fixture_hash: str
    initial_tree_hash: str
    immutable_hashes: dict[str, str]


def resolve_inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Path escapes workspace: {relative!r}")
    return target


def _ignore_copy(_directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in IGNORED_NAMES}
    ignored.update(name for name in names if name.endswith((".pyc", ".pyo")))
    return ignored


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _overlay_stub(task: TaskConfig, workspace: Path) -> None:
    if task.stub_dir is None:
        raise ValueError(f"Task {task.id} has no stub_dir for hidden resynthesis")
    for relative in task.target_paths:
        destination = resolve_inside(workspace, relative)
        if destination.exists() or destination.is_symlink():
            _remove_path(destination)
        source = resolve_inside(task.stub_dir, relative)
        if not source.exists():
            raise ValueError(f"Stub is missing target path: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_ignore_copy)
        else:
            shutil.copy2(source, destination)


def _iter_hash_files(root: Path):
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if any(part in IGNORED_NAMES for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix not in {".pyc", ".pyo", ".log"}:
            yield path


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    root = root.resolve()
    if not root.exists():
        return "MISSING"
    for path in _iter_hash_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def hash_path(path: Path) -> str:
    if not path.exists() and not path.is_symlink():
        return "MISSING"
    if path.is_file():
        digest = hashlib.sha256()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        return digest.hexdigest()
    return hash_tree(path)


def snapshot_paths(root: Path, relatives: tuple[str, ...]) -> dict[str, str]:
    return {
        relative: hash_path(resolve_inside(root, relative))
        for relative in sorted(set(relatives))
    }


def immutable_violations(
    workspace: Path,
    expected: dict[str, str],
) -> list[str]:
    return [
        relative
        for relative, original_hash in expected.items()
        if hash_path(resolve_inside(workspace, relative)) != original_hash
    ]


def _run_git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def initialize_repository(workspace: Path) -> None:
    _run_git(workspace, "init", "--initial-branch=benchmark")
    hooks_dir = workspace / ".git" / "benchmark-empty-hooks"
    hooks_dir.mkdir()
    _run_git(workspace, "config", "user.name", "Erasure Benchmark")
    _run_git(workspace, "config", "user.email", "benchmark@invalid.local")
    _run_git(workspace, "config", "core.autocrlf", "false")
    _run_git(workspace, "config", "core.longpaths", "true")
    _run_git(workspace, "config", "core.hooksPath", str(hooks_dir))
    _run_git(workspace, "config", "commit.gpgsign", "false")
    _run_git(workspace, "config", "tag.gpgsign", "false")
    _run_git(workspace, "add", "--all")
    _run_git(workspace, "commit", "--quiet", "-m", "frozen benchmark input")


def write_benchmark_rules(task: TaskConfig, workspace: Path) -> None:
    immutable = "\n".join(f"- `{path}`" for path in task.immutable_paths)
    targets = "\n".join(f"- `{path}`" for path in task.target_paths)
    content = f"""# Benchmark run rules

Work only inside this workspace. Do not inspect parent directories, external
repositories, prior sessions, Git history, network resources, or benchmark
oracles. The current tree is the complete authorized input.

You may modify only implementation files needed for the task. Do not modify,
delete, rename, or bypass these frozen artifacts:

{immutable}

The benchmark target paths are:

{targets}

Run the public checks stated in the prompt. Do not weaken checks or replace
behavior with hard-coded examples.
"""
    (workspace / "AGENTS.md").write_text(content, encoding="utf-8")


def prepare_workspace(
    task: TaskConfig,
    arm: ArmConfig,
    destination: Path,
) -> PreparedWorkspace:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(task.seed_dir, destination, ignore=_ignore_copy)

    if arm.implementation == "stub":
        _overlay_stub(task, destination)
    write_benchmark_rules(task, destination)

    immutable = tuple(task.immutable_paths) + ("AGENTS.md",)
    immutable_hashes = snapshot_paths(destination, immutable)
    fixture_hash = hash_tree(task.seed_dir)
    initial_tree_hash = hash_tree(destination)
    initialize_repository(destination)
    return PreparedWorkspace(
        path=destination,
        fixture_hash=fixture_hash,
        initial_tree_hash=initial_tree_hash,
        immutable_hashes=immutable_hashes,
    )


def clone_candidate_workspace(source: Path, destination: Path) -> PreparedWorkspace:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"Workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, ignore=_ignore_copy)
    fixture_hash = hash_tree(source)
    initial_tree_hash = hash_tree(destination)
    initialize_repository(destination)
    return PreparedWorkspace(
        path=destination,
        fixture_hash=fixture_hash,
        initial_tree_hash=initial_tree_hash,
        immutable_hashes={},
    )
