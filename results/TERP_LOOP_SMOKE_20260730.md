# TERP-Loop self-erasure smoke — 2026-07-30

This is the first calibrated end-to-end TERP-Loop episode. It validates the
harness and demonstrates the long-horizon endpoint. It is **not** a comparative
study: there is one repetition of one controller.

## Frozen setup

- Harness commit: `e7d0d7c`
- Protocol: `TERP-Loop`
- Controller: `self_erasure`
- Model: `gpt-5.6-sol`
- Reasoning effort: `xhigh`
- Codex CLI: `0.144.6`
- Agent calls per maintenance step: `2`
- Environment: `jobflow-valley`
- Ordered tasks: cancellation, restart, exactly-once callbacks,
  snapshot/restore, explainable history

## Result

The episode completed four tasks before the first unrecoverable regression.

| Horizon | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Success | 1 | 1 | 1 | 1 | 0 |

The selector erased `src/jobflow/engine.py` for task 1. Its next four decisions
declined erasure. All five decisions were structurally valid, and none leaked a
private implementation symbol into the extracted contract.

The task-5 candidate passed the new explainable-history verifier plus baseline,
cancellation, restart, and callback verification. It regressed the cumulative
snapshot/restore contract by changing `snapshot()["version"]` away from the
required integer `1`. The harness rejected the candidate and retained the
accepted task-4 snapshot.

## Cost and secondary metrics

| Metric | Value |
| --- | ---: |
| Agent calls | 10 |
| Input tokens | 4,319,244 |
| Output tokens | 96,747 |
| Input + output tokens | 4,415,991 |
| Reasoning output tokens | 54,211 |
| Agent time | 2,364.7 s |
| Rollbacks | 1 |
| Leakage violations | 0 |
| Immutable-file violations | 0 |

Accepted-state source metrics moved from 609 to 1,342 lines and from 149 to 294
on the cyclomatic proxy. Tracked boolean-flag debt fell from 169 occurrences to
zero after the first erasure and remained at zero.

The sharp cost and source growth at snapshot/restore is a concrete signal for
future comparative runs: task 4 consumed 1,483,524 tokens and added 711 changed
lines, while the selector still declined a second erasure at task 5.

## Calibration exclusions

Earlier launch attempts were excluded before interpretation because they found
harness/fixture issues: Windows path length, an unsupported structured-output
schema keyword, an ambiguous callback error type, and an overly tight contract
character cap. Each was fixed before the frozen run above. None is counted as a
benchmark outcome.

## Interpretation

This smoke shows that TERP-Loop can:

- make and audit a self-selected erasure decision;
- rebuild from a bounded contract without original implementation bodies;
- persist accepted code across cumulative tasks;
- detect a later regression against an earlier hidden contract;
- roll back the failed candidate; and
- report maintenance horizon, cost slope, churn, debt, and leakage.

It does not estimate a treatment effect. The configured study requires all four
compute-matched controllers and three repetitions each.
