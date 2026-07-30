# The Taelin Erasure-Resynthesis Protocol (TERP)

TERP is an open benchmark for a provocative software-engineering idea: delete
the implementation, preserve the behavioral contract, and ask a fresh agent to
rebuild the code from first principles.

The protocol is named for [Victor Taelin](https://github.com/victortaelin),
whose [original post](https://x.com/VictorTaelin/status/2082827517338005700)
inspired the experiment. It tests whether implementation erasure can break
architectural anchoring and improve later agent maintenance.

The suite has two protocols:

- **TERP-Core** is the original one-shot implementation-visibility ablation.
- **TERP-Loop** treats erasure as semantic garbage collection inside a
  persistent maintenance trajectory: select, specify, erase, resynthesize,
  verify, then accept or roll back before the next task.

## TERP-Core pilot result

The first pilot ran one deterministic parser fixture through three arms, with
three repetitions per arm. Every generated candidate then faced a fresh,
blinded Codex agent and an unseen maintenance task.

| Arm | Generation correctness | Fresh-agent maintenance | Median line delta | Median complexity delta | Input + output tokens | Agent time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hidden resynthesis | 3/3 | 3/3 | -23 | -5 | 1,321,944 | 15.54 min |
| Visible refactor | 3/3 | 3/3 | -31 | -7 | 1,225,799 | 12.12 min |
| Visible rewrite | 3/3 | 3/3 | -45 | -11 | 1,134,371 | 12.64 min |

The result is mixed:

- All three approaches tied on correctness and downstream agent traversability.
- Hidden resynthesis caused no correctness or leakage failures.
- Hidden resynthesis used 16.5% more tokens and took 22.9% longer than visible
  rewriting without improving the primary endpoint.
- Visible rewriting produced the strongest median line and complexity
  reductions.

This is not evidence against the idea. One small fixture with a 100% pass rate
is saturated and cannot distinguish the approaches. The honest verdict was
`insufficient_fixture_blocks`.

## TERP-Core causal comparison

TERP-Core runs each fixture in three arms:

1. `visible_refactor`: the original implementation is visible and the agent is
   asked to improve it in place.
2. `visible_rewrite`: the original implementation is visible, but the agent is
   told to re-derive and replace it.
3. `hidden_resynthesis`: the same rewrite instruction is used, but the original
   implementation is replaced with a signature-only stub.

The visible-rewrite and hidden-resynthesis arms isolate the effect of
implementation visibility. The primary intention-to-treat endpoint requires
both the generated candidate and the fresh-agent maintenance challenge to pass.
Failed generation remains in the denominator.

## TERP-Loop

TERP-Loop follows one accepted codebase through five cumulative maintenance
tasks. A failed step is rolled back and, by default, ends that episode. Four
controllers receive the same fixed budget of two Codex calls per step:

1. `ordinary_maintenance`: two independent in-place candidates; the harness
   deterministically accepts the least-debt passing candidate.
2. `visible_rewrite`: two independent candidates explicitly allowed to replace
   the visible implementation.
3. `adversarial_review`: one architecture reviewer followed by one implementer.
4. `self_erasure`: one selector/contract extractor followed by one builder. If
   erasure is selected, the builder receives a signature-only target with no
   original bodies or recoverable local Git history.

The extractor may decline erasure. Erasure is secondary to trajectory success,
not an unconditional objective. TERP-Loop currently measures a fresh-agent
chain at every step; a persistent-agent-context variant should be a separate
experiment.

The included `jobflow-valley` environment is about 610 source lines. Its locally
reasonable boolean-flag workflow design faces cancellation, restart,
exactly-once callbacks, snapshot/restore, and explainable transition history.
The later tasks deliberately turn the original representation into a rewrite
valley.

The primary endpoint is full-trajectory survival under matched agent-call
budgets: how many independent episodes complete all five sequential hidden
maintenance tasks before the first unrecoverable regression. Reports also show:

- success at every maintenance horizon;
- tokens and agent time per accepted task;
- rollback and first-failure rates;
- churn and source-size/complexity deltas;
- debt-marker survival; and
- contract size and source-symbol leakage diagnostics.

Behavioral verification remains authoritative. Static cleanliness metrics are
secondary.

### First TERP-Loop smoke

The first calibrated, source-traceable self-erasure episode completed four of
five maintenance tasks before its first unrecoverable regression. The selector
erased once (on cancellation), then declined erasure for the next four tasks.
The final candidate implemented explainable history but broke the earlier
snapshot-version contract, so the harness rejected it and rolled back.

| Endpoint | Result |
| --- | ---: |
| Maintenance horizon | 4/5 |
| Valid selector decisions | 5/5 |
| Erasures selected | 1/5 |
| Leakage / immutable violations | 0 / 0 |
| Agent calls | 10 |
| Input + output tokens | 4,415,991 |
| Agent time | 39.4 min |
| Tracked flag-debt survival | 0/169 |
| Accepted lines | 609 → 1,342 |
| Accepted complexity proxy | 149 → 294 |

This is an end-to-end harness smoke, not evidence that one controller beats
another. See the [full smoke note](results/TERP_LOOP_SMOKE_20260730.md).

## Execution and isolation

All benchmark agents are launched through the locally authenticated Codex CLI:

```text
codex exec --ephemeral --ignore-user-config --ignore-rules \
  --sandbox danger-full-access -m gpt-5.6-sol \
  -c model_reasoning_effort="xhigh" -c approval_policy="never" --json ...
```

The harness does not use an API key or a second model runtime. This Windows
installation currently rejects file writes and Python checks under Codex's
`workspace-write` sandbox, so trusted disposable fixtures use
`danger-full-access`. This is an auditable local isolation boundary, not a
hardened filesystem boundary.

At run start, configurations, fixtures, prompts, hidden oracles, and response
schemas are snapshotted and hashed. Agent workspaces are copied from frozen
inputs, stripped of caches and Git history, and initialized as new repositories.
Immutable paths are hashed before and after execution. JSONL is audited for
network use, parent traversal, Git-history access, oracle access, and
out-of-workspace file changes; detected leakage fails the candidate.

Every run retains verification results, source metrics, usage, wall time, exact
prompts, content hashes, raw agent JSONL, final responses, and Git diffs.

## Quick start

No third-party Python packages are required.

```powershell
git clone https://github.com/MarcusNeufeldt/The-Taelin-Erasure-Resynthesis-Protocol.git
Set-Location "The-Taelin-Erasure-Resynthesis-Protocol"

python -m unittest discover -s tests -v

# TERP-Core
python -m erasure_bench validate --config benchmark.toml
python -m erasure_bench plan --config benchmark.toml

# TERP-Loop
python -m erasure_bench loop-validate --config loop.toml
python -m erasure_bench loop-plan --config loop.toml
```

Run one complete TERP-Loop episode:

```powershell
python -m erasure_bench loop-run `
  --config loop.toml `
  --controller self_erasure `
  --repetitions 1 `
  --max-episodes 1 `
  --allow-danger-full-access
```

Run the configured compute-matched TERP-Loop study:

```powershell
python -m erasure_bench loop-run `
  --config loop.toml `
  --allow-danger-full-access
```

Regenerate reports:

```powershell
python -m erasure_bench report runs\<RUN_ID>
python -m erasure_bench loop-report loop-runs\<RUN_ID>
```

Commit the harness and frozen fixtures before a real study. Every run records
the harness commit and content hashes so results remain traceable.

## Fixture contracts

A TERP-Core task contains:

```text
task.toml
PROMPT.md
seed/                  complete original snapshot
stub/                  signature-only hidden-resynthesis overlay
verify_hidden.py       harness-side verification
challenges/            unseen downstream maintenance tasks
```

A TERP-Loop trajectory contains:

```text
trajectory.toml
seed/                  persistent episode baseline
steps/                 cumulative maintenance requests
verifiers/             harness-side hidden checks
```

Task commands are argument arrays, not shell strings. Supported placeholders
are `{python}`, `{workspace}`, and `{task_dir}`. `trajectory.toml` declares
erasable paths, immutable artifacts, debt markers, baseline checks, and ordered
steps. `loop.toml` declares the compute-matched controllers and Codex budget.

## Build on TERP

Fork it, add a fixture, challenge the protocol, and publish the result—even if
the result is negative. Strong follow-ups should add independently authored
rewrite valleys for other local minima, such as authorization conditionals,
regex transformers, retry mazes, or cache invalidation, plus a clean negative
control where erasure should not help.

If you believe erasure should help a failure mode, encode that belief as a
reproducible fixture and open a pull request. The goal is not to prove TERP
right; it is to discover where, if anywhere, implementation erasure reliably
improves agentic software work.

## License

TERP is available under the [MIT License](LICENSE).
