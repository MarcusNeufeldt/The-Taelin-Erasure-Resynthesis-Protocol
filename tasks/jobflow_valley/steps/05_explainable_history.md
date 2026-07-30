# Maintenance 5: explainable lifecycle history

Add `WorkflowEngine.explain(job_id) -> tuple[dict[str, Any], ...]`.

Every accepted lifecycle transition from submission onward must append exactly
one durable history entry containing:

```text
sequence       positive, strictly increasing integer
from_state     previous public state, or null for submission
to_state       resulting public state
action         stable action name
reason         human-readable non-empty explanation
attempt        job attempt number at that transition
```

Idempotent operations add no entry. History survives restart and snapshot /
restore, cannot be mutated through returned values, and remains ordered across
attempts.

Use method-level action names: `submit`, `start`, `complete_step`, `fail_step`,
`retry_step`, `pause`, `resume`, `cancel`, and `restart`.

To eliminate contradictory explanations, each serialized job record must now
have one authoritative string field named `state`. Snapshot job records must
not contain lifecycle boolean keys beginning with `is_`, `has_`, `was_`, or
`should_`. Step records should likewise use one authoritative string `status`.
This is a behavioral serialization requirement, not a request to merely hide
old fields during export.

All earlier maintenance requirements remain cumulative.
