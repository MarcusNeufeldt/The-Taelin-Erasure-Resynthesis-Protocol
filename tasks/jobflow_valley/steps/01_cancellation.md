# Maintenance 1: cancellation

Add `WorkflowEngine.cancel(job_id, reason="requested") -> JobView`.

- A queued, running, paused, or failed job can be cancelled.
- The returned and subsequently fetched job has status `cancelled`, no current
  step, and `failure` equal to the cancellation reason.
- The reason must be a non-empty string.
- Cancelling an already-cancelled job is idempotent: it emits no second event
  and retains the original reason.
- Cancelling a completed job raises `ValueError`.
- Cancellation emits one `cancelled` event with the reason in its detail.
- Existing behavior remains unchanged, and `list_jobs(status="cancelled")`
  supports the new status.

Treat this requirement as cumulative for all later maintenance tasks.
