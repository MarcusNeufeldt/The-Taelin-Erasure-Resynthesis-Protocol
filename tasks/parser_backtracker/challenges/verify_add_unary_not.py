from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(workspace / "src"))
    route_lang = importlib.import_module("route_lang")

    expected = (
        "or",
        (
            "and",
            ("not", ("cmp", "a", "=", 1)),
            ("cmp", "b", "=", 2),
        ),
        ("cmp", "c", "=", 3),
    )
    actual = route_lang.parse_filter("NOT a=1 AND b=2 OR c=3")
    if actual != expected:
        raise AssertionError(f"precedence mismatch: {actual!r}")

    grouped = route_lang.parse_filter("not (a=1 or b=2)")
    if grouped != (
        "not",
        ("or", ("cmp", "a", "=", 1), ("cmp", "b", "=", 2)),
    ):
        raise AssertionError(f"grouped NOT mismatch: {grouped!r}")

    double = route_lang.parse_filter("NoT nOt enabled=true")
    if double != ("not", ("not", ("cmp", "enabled", "=", True))):
        raise AssertionError(f"double NOT mismatch: {double!r}")

    if route_lang.evaluate(actual, {"a": 2, "b": 2, "c": 0}) is not True:
        raise AssertionError("NOT evaluation should be true")
    if route_lang.evaluate(grouped, {"a": 1, "b": 0}) is not False:
        raise AssertionError("grouped NOT evaluation should be false")
    if route_lang.evaluate(double, {"enabled": True}) is not True:
        raise AssertionError("double NOT evaluation should be true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
