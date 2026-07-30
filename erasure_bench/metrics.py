from __future__ import annotations

import ast
import hashlib
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .workspace import resolve_inside


@dataclass(frozen=True)
class SourceMetrics:
    files: int
    bytes: int
    lines: int
    nonblank_lines: int
    code_lines: int
    python_files: int
    python_parse_errors: int
    ast_nodes: int
    functions: int
    classes: int
    imports: int
    branch_points: int
    cyclomatic_proxy: int
    max_nesting: int
    missing_paths: list[str]
    source_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiffSummary:
    files_changed: int
    lines_added: int
    lines_deleted: int
    patch_path: str
    status_path: str

    def to_dict(self) -> dict:
        return asdict(self)


class _PythonMetricsVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.nodes = 0
        self.functions = 0
        self.classes = 0
        self.import_names: set[str] = set()
        self.branch_points = 0
        self.depth = 0
        self.max_nesting = 0

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes += 1
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions += 1
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions += 1
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.import_names.update(alias.name.split(".")[0] for alias in node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.import_names.add(node.module.split(".")[0])
        self.generic_visit(node)

    def _visit_control(self, node: ast.AST, points: int = 1) -> None:
        self.branch_points += points
        self.depth += 1
        self.max_nesting = max(self.max_nesting, self.depth)
        self.generic_visit(node)
        self.depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self._visit_control(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_control(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_control(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_control(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_control(node, max(1, len(node.handlers)))

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._visit_control(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.branch_points += max(1, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_control(node, max(1, len(node.cases)))

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_control(node, len(node.generators))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_control(node, len(node.generators))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_control(node, len(node.generators))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_control(node, len(node.generators))


def _metric_files(root: Path, relative_paths: tuple[str, ...]) -> tuple[list[Path], list[str]]:
    files: set[Path] = set()
    missing: list[str] = []
    for relative in relative_paths:
        path = resolve_inside(root, relative)
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(candidate for candidate in path.rglob("*") if candidate.is_file())
        else:
            missing.append(relative)
    return sorted(files, key=lambda item: item.as_posix()), missing


def collect_source_metrics(root: Path, relative_paths: tuple[str, ...]) -> SourceMetrics:
    files, missing = _metric_files(root.resolve(), relative_paths)
    digest = hashlib.sha256()
    total_bytes = 0
    total_lines = 0
    nonblank_lines = 0
    code_lines = 0
    python_files = 0
    python_parse_errors = 0
    ast_nodes = 0
    functions = 0
    classes = 0
    import_names: set[str] = set()
    branch_points = 0
    max_nesting = 0

    for path in files:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        total_bytes += len(content)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")

        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total_lines += len(lines)
        nonblank_lines += sum(bool(line.strip()) for line in lines)
        code_lines += sum(
            bool(line.strip()) and not line.lstrip().startswith("#")
            for line in lines
        )

        if path.suffix == ".py":
            python_files += 1
            try:
                tree = ast.parse(text, filename=relative)
            except SyntaxError:
                python_parse_errors += 1
                continue
            visitor = _PythonMetricsVisitor()
            visitor.visit(tree)
            ast_nodes += visitor.nodes
            functions += visitor.functions
            classes += visitor.classes
            import_names.update(visitor.import_names)
            branch_points += visitor.branch_points
            max_nesting = max(max_nesting, visitor.max_nesting)

    return SourceMetrics(
        files=len(files),
        bytes=total_bytes,
        lines=total_lines,
        nonblank_lines=nonblank_lines,
        code_lines=code_lines,
        python_files=python_files,
        python_parse_errors=python_parse_errors,
        ast_nodes=ast_nodes,
        functions=functions,
        classes=classes,
        imports=len(import_names),
        branch_points=branch_points,
        cyclomatic_proxy=functions + branch_points,
        max_nesting=max_nesting,
        missing_paths=missing,
        source_hash=digest.hexdigest(),
    )


def _git_output(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout


def capture_git_diff(workspace: Path, artifact_dir: Path) -> DiffSummary:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(workspace), "add", "--intent-to-add", "--all"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    patch_path = artifact_dir / "candidate.patch"
    status_path = artifact_dir / "git-status.txt"
    patch_path.write_text(
        _git_output(workspace, "diff", "--no-ext-diff", "--no-color"),
        encoding="utf-8",
    )
    status_path.write_text(
        _git_output(workspace, "status", "--short"),
        encoding="utf-8",
    )

    files_changed = 0
    lines_added = 0
    lines_deleted = 0
    for line in _git_output(workspace, "diff", "--numstat").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        files_changed += 1
        if parts[0].isdigit():
            lines_added += int(parts[0])
        if parts[1].isdigit():
            lines_deleted += int(parts[1])
    return DiffSummary(
        files_changed=files_changed,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        patch_path=str(patch_path.resolve()),
        status_path=str(status_path.resolve()),
    )
