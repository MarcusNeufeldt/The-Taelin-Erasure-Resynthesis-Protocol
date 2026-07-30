from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    position: int


def _read_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    index = start + 1
    result: list[str] = []
    escapes = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}
    while index < len(text):
        current = text[index]
        if current == quote:
            return "".join(result), index + 1
        if current == "\\":
            index += 1
            if index >= len(text) or text[index] not in escapes:
                raise ParseError(f"invalid escape at position {index}")
            result.append(escapes[text[index]])
        else:
            result.append(current)
        index += 1
    raise ParseError(f"unterminated string at position {start}")


def _tokens(text: str) -> list[_Token]:
    result: list[_Token] = []
    index = 0
    while index < len(text):
        current = text[index]
        if current.isspace():
            index += 1
            continue
        if current in "'\"":
            value, next_index = _read_string(text, index)
            result.append(_Token("VALUE", value, index))
            index = next_index
            continue
        if current == "(":
            result.append(_Token("LPAREN", current, index))
            index += 1
            continue
        if current == ")":
            result.append(_Token("RPAREN", current, index))
            index += 1
            continue
        if current in "=!<>":
            operator = current
            if index + 1 < len(text) and text[index + 1] == "=":
                operator += "="
                index += 1
            if operator not in {"=", "!=", "<", "<=", ">", ">="}:
                raise ParseError(f"invalid operator at position {index}")
            result.append(_Token("OP", operator, index))
            index += 1
            continue
        if current == "-" or current.isdigit():
            end = index + 1
            if current == "-" and (end >= len(text) or not text[end].isdigit()):
                raise ParseError(f"invalid number at position {index}")
            while end < len(text) and text[end].isdigit():
                end += 1
            result.append(_Token("VALUE", int(text[index:end]), index))
            index = end
            continue
        if current.isalpha() or current == "_":
            end = index + 1
            while end < len(text) and (
                text[end].isalnum() or text[end] in "_."
            ):
                end += 1
            word = text[index:end]
            upper = word.upper()
            if upper in {"AND", "OR"}:
                result.append(_Token(upper, upper, index))
            elif upper == "TRUE":
                result.append(_Token("VALUE", True, index))
            elif upper == "FALSE":
                result.append(_Token("VALUE", False, index))
            else:
                result.append(_Token("IDENT", word, index))
            index = end
            continue
        raise ParseError(f"invalid token at position {index}")
    result.append(_Token("EOF", None, len(text)))
    return result


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.cursor = 0

    def current(self) -> _Token:
        return self.tokens[self.cursor]

    def consume(self, kind: str) -> _Token:
        token = self.current()
        if token.kind != kind:
            raise ParseError(
                f"expected {kind} at position {token.position}, got {token.kind}"
            )
        self.cursor += 1
        return token

    def maybe(self, kind: str) -> _Token | None:
        if self.current().kind == kind:
            token = self.current()
            self.cursor += 1
            return token
        return None

    def attempt(self, callback):
        saved = self.cursor
        try:
            return callback()
        except ParseError:
            self.cursor = saved
            return None

    def comparison(self):
        field = self.consume("IDENT").value
        operator = self.consume("OP").value
        value = self.consume("VALUE").value
        return ("cmp", field, operator, value)

    def parenthesized(self):
        self.consume("LPAREN")
        value = self.or_expression()
        self.consume("RPAREN")
        return value

    def atom(self):
        value = self.attempt(self.parenthesized)
        if value is not None:
            return value
        value = self.attempt(self.comparison)
        if value is not None:
            return value
        token = self.current()
        raise ParseError(f"expected expression at position {token.position}")

    def and_expression(self):
        left = self.atom()
        while True:
            saved = self.cursor
            if self.maybe("AND") is None:
                self.cursor = saved
                break
            right = self.attempt(self.atom)
            if right is None:
                self.cursor = saved
                break
            left = ("and", left, right)
        return left

    def or_expression(self):
        left = self.and_expression()
        while True:
            saved = self.cursor
            if self.maybe("OR") is None:
                self.cursor = saved
                break
            right = self.attempt(self.and_expression)
            if right is None:
                self.cursor = saved
                break
            left = ("or", left, right)
        return left

    def parse(self):
        if self.current().kind == "EOF":
            raise ParseError("empty expression")
        result = self.or_expression()
        self.consume("EOF")
        return result


def parse_filter(text: str):
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return _Parser(_tokens(text)).parse()


def evaluate(ast, record: Mapping[str, Any]) -> bool:
    kind = ast[0]
    if kind == "and":
        return evaluate(ast[1], record) and evaluate(ast[2], record)
    if kind == "or":
        return evaluate(ast[1], record) or evaluate(ast[2], record)
    if kind != "cmp":
        raise ValueError(f"unknown AST node: {kind}")
    _, field, operator, expected = ast
    if field not in record:
        return False
    actual = record[field]
    if operator == "=":
        return actual == expected
    if operator == "!=":
        return actual != expected
    try:
        if operator == "<":
            return actual < expected
        if operator == "<=":
            return actual <= expected
        if operator == ">":
            return actual > expected
        if operator == ">=":
            return actual >= expected
    except TypeError:
        return False
    raise ValueError(f"unknown comparison operator: {operator}")
