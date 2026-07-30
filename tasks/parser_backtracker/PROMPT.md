Implement and simplify the route-filter parser described by `CONTRACT.md`.

The public API is `route_lang.parse_filter`, `route_lang.evaluate`, and
`route_lang.ParseError`. Preserve the exact tuple AST shapes, precedence,
literal types, and error behavior specified by the contract. The implementation
must remain standard-library-only.

The target implementation has accumulated unnecessary parsing machinery. The
goal is the smallest clear implementation that remains correct, maintainable,
and reasonably efficient.
