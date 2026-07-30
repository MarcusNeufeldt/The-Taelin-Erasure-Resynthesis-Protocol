Extend the filter language with case-insensitive unary `NOT`.

`NOT` binds more tightly than `AND` and `OR`, may precede either a comparison or
a parenthesized expression, and may be repeated. Its AST shape is:

```python
("not", child)
```

Update both parsing and evaluation. Preserve all existing behavior and public
APIs. Do not edit `CONTRACT.md` or the existing tests.
