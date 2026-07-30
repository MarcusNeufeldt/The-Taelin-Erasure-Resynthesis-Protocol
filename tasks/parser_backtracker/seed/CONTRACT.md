# Route filter contract

`parse_filter(text)` parses this grammar:

```text
expression  := or_expression
or_expression := and_expression ("OR" and_expression)*
and_expression := atom ("AND" atom)*
atom        := "(" expression ")" | comparison
comparison  := IDENT OP VALUE
OP          := "=" | "!=" | "<" | "<=" | ">" | ">="
VALUE       := quoted string | signed integer | true | false
```

`AND`, `OR`, `true`, and `false` are case-insensitive. `AND` binds more tightly
than `OR`. Whitespace may appear between tokens. Identifiers begin with a letter
or underscore and may subsequently contain letters, digits, underscores, or
dots.

Strings may use single or double quotes. They support `\\`, `\'`, `\"`, `\n`,
and `\t` escapes. Integers are returned as Python `int`; booleans as `bool`.

The AST is composed only of tuples and literals:

```python
("cmp", field, operator, value)
("and", left, right)
("or", left, right)
```

Repeated `AND` and `OR` operators associate to the left.

Empty input, invalid tokens, missing operands, unbalanced parentheses, trailing
tokens, and unterminated strings raise `ParseError`, which is a `ValueError`.

`evaluate(ast, record)` evaluates an AST against a mapping. A missing field
evaluates to `False`. Equality and inequality work for all values. Ordered
comparisons return `False` rather than raising when values cannot be compared.
