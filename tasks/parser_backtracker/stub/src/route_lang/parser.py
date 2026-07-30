from __future__ import annotations

from typing import Any, Mapping


class ParseError(ValueError):
    """Raised when a route-filter expression is invalid."""


def parse_filter(text: str):
    """Parse text according to CONTRACT.md and return its tuple AST."""
    raise NotImplementedError


def evaluate(ast, record: Mapping[str, Any]) -> bool:
    """Evaluate a contract-compliant tuple AST against a record."""
    raise NotImplementedError
