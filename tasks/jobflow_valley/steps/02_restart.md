# Maintenance 2: whole-job restart

Add `WorkflowEngine.restart(job_id) -> JobView`.

- Only failed or cancelled jobs can be restarted. Other states raise
  `ValueError`.
- Restart increments the job-level `attempt`, resets every step to pending with
  zero attempts/output/error, clears the failure or cancellation reason, and
  returns the job to `queued` with no current step.
- A subsequent `start()` begins the first step exactly as for a new job.
- Restart emits exactly one `restarted` event whose detail includes the new
  attempt number.
- Metadata and job identity survive restart.
- Cancellation and all baseline behavior remain correct.

Treat this requirement as cumulative for all later maintenance tasks.
