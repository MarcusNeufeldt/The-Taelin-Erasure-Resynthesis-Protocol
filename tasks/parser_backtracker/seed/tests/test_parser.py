from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from route_lang import ParseError, evaluate, parse_filter


class ParserTests(unittest.TestCase):
    def test_comparison(self):
        self.assertEqual(parse_filter("status = 'open'"), ("cmp", "status", "=", "open"))

    def test_literal_types(self):
        self.assertEqual(parse_filter("age >= -4"), ("cmp", "age", ">=", -4))
        self.assertEqual(parse_filter("enabled = TRUE"), ("cmp", "enabled", "=", True))

    def test_and_precedes_or(self):
        self.assertEqual(
            parse_filter("a=1 OR b=2 AND c=3"),
            (
                "or",
                ("cmp", "a", "=", 1),
                ("and", ("cmp", "b", "=", 2), ("cmp", "c", "=", 3)),
            ),
        )

    def test_parentheses_override_precedence(self):
        self.assertEqual(
            parse_filter("(a=1 OR b=2) AND c=3")[0],
            "and",
        )

    def test_operators_associate_left(self):
        tree = parse_filter("a=1 OR b=2 OR c=3")
        self.assertEqual(tree[0], "or")
        self.assertEqual(tree[1][0], "or")

    def test_string_escapes(self):
        self.assertEqual(
            parse_filter(r'name = "a\n\"b"'),
            ("cmp", "name", "=", 'a\n"b'),
        )

    def test_evaluate(self):
        tree = parse_filter("age >= 18 AND active = true")
        self.assertTrue(evaluate(tree, {"age": 21, "active": True}))
        self.assertFalse(evaluate(tree, {"age": 16, "active": True}))
        self.assertFalse(evaluate(tree, {"age": 21}))

    def test_incomparable_ordering_is_false(self):
        self.assertFalse(evaluate(parse_filter("age > 4"), {"age": "old"}))

    def test_invalid_inputs(self):
        invalid = ["", "a", "a=", "(a=1", "a=1 trailing", "a ! 2", "name='x"]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ParseError):
                    parse_filter(value)

    def test_non_string_input(self):
        with self.assertRaises(TypeError):
            parse_filter(None)


if __name__ == "__main__":
    unittest.main()
