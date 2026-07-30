# The Taelin Erasure-Resynthesis Protocol (TERP)

TERP is an open benchmark for a provocative software-engineering idea: delete
the implementation, preserve the behavioral contract, and ask a fresh agent to
rebuild the code from first principles.

The protocol is named for [Victor Taelin](https://github.com/victortaelin),
whose [original post](https://x.com/VictorTaelin/status/2082827517338005700)
inspired the experiment. The benchmark tests whether hiding an existing
implementation before resynthesis produces code that is easier for later agents
to understand and change than ordinary refactoring or a visible rewrite.

## First benchmark result

The first completed pilot ran one deterministic parser fixture through all
three arms, with three repetitions per arm. Every generated candidate then
faced a fresh, blinded Codex agent and an unseen maintenance task.

| Arm | Generation correctness | Fresh-agent maintenance | Median line delta | Median complexity delta | Input + output tokens | Agent time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hidden resynthesis | 3/3 | 3/3 | -23 | -5 | 1,321,944 | 15.54 min |
| Visible refactor | 3/3 | 3/3 | -31 | -7 | 1,225,799 | 12.12 min |
| Visible rewrite | 3/3 | 3/3 | -45 | -11 | 1,134,371 | 12.64 min |

The result is mixed:

- All three approaches tied on correctness and downstream agent traversability.
- Hidden resynthesis caused no correctness or leakage failures.
- Hidden resynthesis used 16.5% more tokens and took 22.9% longer than visible
  rewriting, without improving the primary endpoint.
- Visible rewriting produced the strongest median line and complexity
  reductions in this fixture.

This is **not evidence against the idea**. With one small fixture and a 100%
pass rate in every arm, the pilot is saturated and cannot distinguish the
approaches. The honest verdict is `insufficient_fixture_blocks`. The hypothesis
now needs harder codebases where the existing implementation may actively
anchor an agent to bad abstractions.

## Build on TERP

TERP is deliberately small, inspectable, and open for extension. Fork it, add a
fixture, challenge the protocol, and publish the result—even if the result is
negative.

Contributions are especially welcome for:

- adversarial legacy modules with locally plausible but globally harmful
  abstractions;
- new debt types, languages, and ecosystems;
- stronger hidden or property-based correctness checks;
- multiple unseen maintenance tasks per fixture;
- alternative agent models and fixed-budget configurations;
- stronger disposable sandboxing and leakage detection; and
- analysis or reporting improvements.

If you believe erasure should help a particular failure mode, the most useful
contribution is to encode that belief as a reproducible fixture and open a pull
request. The goal is not to prove TERP right; it is to find out where, if
anywhere, implementation erasure reliably improves agentic software work.

All benchmark agents are launched through the locally authenticated Codex CLI:

```text
codex exec --ephemeral --ignore-user-config --ignore-rules \
  --sandbox danger-full-access -m gpt-5.6-sol \
  -c model_reasoning_effort="xhigh" -c approval_policy="never" --json ...
```

The harness does not use an API key or a second model runtime. This Windows
installation currently rejects file writes and Python checks under Codex's
`workspace-write` sandbox, so the trusted disposable fixtures run with
`danger-full-access` and `approval_policy="never"`. Agents are constrained by
the fixture prompt and audited JSONL, not by a hardened filesystem boundary.

## Causal comparison

Each fixture is run in three arms:

1. `visible_refactor`: the original implementation is visible and the agent is
   asked to refactor it.
2. `visible_rewrite`: the original implementation is visible, but a fresh agent
   is told to re-derive and replace it.
3. `hidden_resynthesis`: the same rewrite instruction is used, but the original
   implementation is replaced with a signature-only stub before the fresh agent
   starts.

The second and third arms isolate the effect of implementation visibility. Run
order is seeded and randomized to reduce time/backend drift.

At run start, task definitions, seed/stub trees, prompts, hidden oracles, and the
output schema are snapshotted and hashed under that run. Every agent workspace
is then copied from the frozen fixture, stripped of caches and Git history,
initialized as a new repository, and committed before Codex starts. The hidden
arm therefore has no original implementation in local Git history.

## Primary endpoint

The primary endpoint is intention-to-treat pipeline success:

> The generated candidate passes every correctness gate, and a fresh blinded
> Codex agent completes an unseen maintenance challenge within the fixed budget.

A failed generation contributes a failure for every held-out challenge. It is
not removed from the denominator. Static cleanliness metrics are secondary.

The harness records:

- public and hidden verification results;
- immutable-file violations;
- downstream challenge success;
- input, cached-input, and output tokens when exposed by Codex JSONL;
- wall-clock time and event/tool counts;
- file/line/byte, Python AST, branch, import, and nesting metrics;
- the exact prompt, fixture hash, configuration, raw JSONL, final response, and
  Git diff for every run.
- a command/file-change leakage audit; any detected network, parent traversal,
  Git-history access, oracle path access, or out-of-workspace file change fails
  the candidate.

It deliberately does not collapse code quality into one subjective score.

## Quick start

No third-party Python packages are required.

```powershell
git clone https://github.com/MarcusNeufeldt/The-Taelin-Erasure-Resynthesis-Protocol.git
Set-Location "The-Taelin-Erasure-Resynthesis-Protocol"

python -m unittest discover -s tests -v
python -m erasure_bench.cli validate --config benchmark.toml
python -m erasure_bench.cli plan --config benchmark.toml
```

Run one cheap integration case first:

```powershell
python -m erasure_bench.cli run `
  --config benchmark.toml `
  --task parser_backtracker `
  --arm hidden_resynthesis `
  --repetitions 1 `
  --skip-challenges `
  --max-runs 1 `
  --allow-danger-full-access
```

Run the configured pilot:

```powershell
python -m erasure_bench.cli run `
  --config benchmark.toml `
  --allow-danger-full-access
```

Commit the harness and frozen fixture definitions before a real pilot. Every
run records the harness commit plus a content hash so results can be traced back
to the exact experiment definition.

Regenerate a report:

```powershell
python -m erasure_bench.cli report runs\<RUN_ID>
```

## Fixture contract

Each task directory contains:

```text
task.toml
PROMPT.md
seed/                  complete original snapshot copied into visible arms
stub/                  signature-only overlay for hidden resynthesis
verify_hidden.py       harness-side verification, never copied into workspaces
challenges/
  <challenge>.md       unseen downstream maintenance request
  verify_<challenge>.py
```

`task.toml` declares:

- target and metric paths;
- files/directories agents may not change;
- public checks shown to the agent;
- hidden checks run only by the harness;
- held-out challenges and their checks.

Commands are argument arrays, not shell strings. Supported placeholders are
`{python}`, `{workspace}`, and `{task_dir}`.

The benchmark prompt tells agents not to inspect outside their workspace, use
network access, inspect Git history, or modify frozen artifacts. The harness
also hashes immutable paths before and after each run. This is an auditable
local isolation boundary, not a hardened VM security boundary; a confirmatory
study should run fixtures inside disposable OS-level sandboxes.

## Building a real pilot

The included `parser_backtracker` fixture is intentionally small. It validates
the machinery; it is not enough to support a conclusion.

A useful pilot should freeze approximately six deterministic modules from one
ecosystem, ideally 400–1,500 lines each:

- several distinct debt types;
- one clean negative control where erasure should not help;
- strong public tests plus independently authored hidden/property tests;
- two unseen maintenance changes per module;
- three repetitions per arm.

That is 54 generation runs and up to 108 fresh-agent traversability runs. The
report computes paired per-fixture effects and a deterministic fixture-cluster
bootstrap interval. Advance beyond the pilot only if hidden resynthesis gains
at least 10 percentage points on pipeline success, improves at least five of six
fixture blocks, remains within five points of the best visible arm's generation
correctness, and has zero leakage.

## License

TERP is available under the [MIT License](LICENSE). Build on it, test it, and
share what you learn.
