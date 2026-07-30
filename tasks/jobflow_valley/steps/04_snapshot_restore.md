# Maintenance 4: deterministic snapshot and restore

Add:

```python
snapshot() -> dict[str, Any]
@classmethod
restore(snapshot: Mapping[str, Any]) -> WorkflowEngine
```

- `snapshot()` returns a deep-copied, JSON-serializable mapping with
  `version == 1` and a top-level `jobs` mapping keyed by job ID.
- It preserves all job identities, metadata, attempts, step state,
  outputs/errors, current lifecycle status, transition/event sequence, pending
  undrained events, callback delivery receipts, and callback error records.
- Python callback functions themselves are process-local and are not serialized.
  A restored engine begins with no registered callbacks; newly registered
  callbacks must still respect restored exactly-once receipts.
- `restore()` rejects malformed, unsupported, or internally inconsistent input
  with `ValueError` and must not retain aliases into the caller's mapping.
- After a JSON round trip, a restored engine returns the same job views and can
  continue valid transitions with strictly increasing event sequence numbers.

All earlier maintenance requirements remain cumulative.
