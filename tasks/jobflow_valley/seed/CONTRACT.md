# JobFlow behavioral contract

`WorkflowEngine` is an in-memory deterministic workflow engine. A job has a
unique string ID, an ordered non-empty list of unique step names, optional
metadata, an attempt number beginning at 1, and one externally visible status.

Baseline statuses are `queued`, `running`, `paused`, `failed`, and `completed`.
Only the current step may be completed or failed. Completing a non-final step
advances the workflow and leaves the next step pending; completing the final
step makes the job completed. Failed jobs may retry the failed current step.
Paused jobs retain their current step and can resume. Terminal successful jobs
are immutable.

Public methods return immutable `JobView` values where practical. Unknown job
IDs, duplicate IDs, invalid step names, invalid transitions, and malformed
inputs raise `ValueError`. Idempotent repetitions of an already-applied action
may return the current view but must never duplicate events or side effects.

Every accepted state change emits an ordered `Event`. `drain_events()` returns
all not-yet-drained events in sequence order and clears only the delivery
buffer; it does not delete job state.

The five maintenance prompts extend this contract cumulatively. Earlier
behavior remains required after every later task.
