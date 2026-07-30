from __future__ import annotations

import ast
import importlib
import sys
import time
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    route_lang = importlib.import_module("route_lang")

    cases = {
        "region.name != 'eu'": ("cmp", "region.name", "!=", "eu"),
        "score<10 and ready=false": (
            "and",
            ("cmp", "score", "<", 10),
            ("cmp", "ready", "=", False),
        ),
        "x=1 OR y=2 OR z=3": (
            "or",
            ("or", ("cmp", "x", "=", 1), ("cmp", "y", "=", 2)),
            ("cmp", "z", "=", 3),
        ),
        r"path = 'c:\\tmp'": ("cmp", "path", "=", r"c:\tmp"),
    }
    for expression, expected in cases.items():
        actual = route_lang.parse_filter(expression)
        if actual != expected:
            raise AssertionError(f"{expression!r}: expected {expected!r}, got {actual!r}")

    tree = route_lang.parse_filter("(tier='pro' OR credits>=5) AND active=true")
    records = [
        ({"tier": "pro", "credits": 0, "active": True}, True),
        ({"tier": "free", "credits": 5, "active": True}, True),
        ({"tier": "free", "credits": 4, "active": True}, False),
        ({"tier": "pro", "credits": 9, "active": False}, False),
    ]
    for record, expected in records:
        actual = route_lang.evaluate(tree, record)
        if actual is not expected:
            raise AssertionError(f"evaluate({record!r}) returned {actual!r}")

    expression = " AND ".join(f"f{i}={i}" for i in range(250))
    started = time.perf_counter()
    large_tree = route_lang.parse_filter(expression)
    elapsed = time.perf_counter() - started
    if elapsed > 1.0:
        raise AssertionError(f"250-term parse took {elapsed:.3f}s")
    record = {f"f{i}": i for i in range(250)}
    if not route_lang.evaluate(large_tree, record):
        raise AssertionError("large expression did not evaluate true")

    parser_path = workspace / "src" / "route_lang" / "parser.py"
    parsed_source = ast.parse(parser_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(parsed_source):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"__future__", "route_lang"}
    unexpected = sorted(imported - allowed)
    if unexpected:
        raise AssertionError(f"non-standard or unapproved imports: {unexpected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
