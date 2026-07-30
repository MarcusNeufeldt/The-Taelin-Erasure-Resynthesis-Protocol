# Maintenance 3: exactly-once completion callbacks

Add these methods:

```python
register_completion_callback(name: str, callback: Callable[[JobView], None]) -> None
unregister_completion_callback(name: str) -> None
callback_errors() -> tuple[dict[str, Any], ...]
```

- Callback names are non-empty and unique; invalid or duplicate registration
  raises `ValueError`.
- Registered callbacks run when a job reaches successful `completed` state.
- Each callback name runs at most once for each `(job_id, job attempt)`, even if
  completion is repeated, events are drained, or other operations follow.
- A restarted job has a new attempt and may invoke the callback once again if
  that attempt completes successfully.
- Callbacks run in registration order and receive the final immutable
  `JobView`.
- One callback raising must not prevent later callbacks or undo completion.
  Store each failure as `{"job_id", "attempt", "callback", "error"}` in
  `callback_errors()`, with `attempt` as an integer and the other values as
  strings. `error` is `str(the_exception)`.
- Unregistering an unknown name is idempotent.

All earlier maintenance requirements remain cumulative.
