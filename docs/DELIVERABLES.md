# cc-eval - delivery notes

What was built, what was verified, what is missing, and how to point it at
cc-workflow.

---

## 1. What is delivered

| Deliverable | Where |
|---|---|
| The project | `src/cc_eval/` (40 modules, ~7 300 lines) |
| Tests | `tests/` - **71 tests, ~2 250 lines, all passing** (`pytest`, 43 s) |
| README | `README.md` |
| Architecture | `ARCHITECTURE.md` |
| Design decisions | `docs/adr/0001` .. `0008` |
| Example experiments | `experiments/EXP-001.yaml`, `EXP-002.yaml`, `EXP-003.yaml` |
| Example benchmark | `benchmarks/` - 5 hand-written cases in 4 topic directories plus a suite |
| Example framework under test | `examples/framework/` - 4 versions of a miniature cc-workflow |
| Example reports | `docs/reports/EXP-00{1,2,3}.{md,json}` |
| Rubric example | `rubrics/spec-review-v1.yaml` |

### Test results (this machine, Python 3.11.16)

```
$ pytest
.......................................................................  [100%]
71 passed in 43.17s

$ pytest -m slow -rs      # the tests that spawn a real agent runtime
2 passed
```

The two slow tests really did drive the installed runtimes
(Claude Code 2.1.308 and codex-cli 0.160.1): the agent created the requested
output file, and the adapter read the usage out of the runtime's own event
stream.  They skip with a diagnostic - never a fake success - when the binary
or the credentials are missing.

---

## 2. Feature status

### Implemented and tested

**Experiment engine**

- [x] Experiment spec with hypothesis, independent variable, baseline, candidate, controls, metrics, decision rules
- [x] Run plan: variants x cases x repetitions, with the variants interleaved and the block order flipped so neither side owns the first slot
- [x] One fresh workspace, one fresh session, one fresh framework copy per run
- [x] Provenance recorded per run: framework fingerprint and commit, dataset hash and version, evaluator and version, model, runtime and version, seed, order index
- [x] Repetitions with per-repetition values, per-case mean, run-to-run variance
- [x] Paired A/B comparison with a paired bootstrap over cases (seeded, reproducible)
- [x] Macro aggregation (mean of per-case means), documented

**Controls and confound detection**

- [x] Control mismatch between the two sides (model, runtime, dataset hash, evaluator version, order policy) -> blocker
- [x] Unexpected framework difference: file-level diff of the two materialised trees against the declared `expected_changes` -> blocker
- [x] Identical variants -> blocker ("there is nothing to measure")
- [x] Dirty working tree -> recorded as a provenance note
- [x] Repetition mismatch -> blocker
- [x] Capability not exercised -> blocker
- [x] Guard metric regression -> inconclusive
- [x] Non-determinism recorded honestly (no seed control for the real runtimes)

**Benchmark / golden dataset**

- [x] Case = visible bundle + hidden oracle, physically separated
- [x] Content hash per benchmark (so a dataset change is visible in the report)
- [x] Suite assembled from several topic benchmarks
- [x] Prompt templating with a clear error for an unknown placeholder
- [x] `cc-eval benchmark validate` - structure, ground truth, hashes

**Evaluation**

- [x] Deterministic evaluator: valid output, recall, precision, F1, false positives, false negatives, per-subset metrics
- [x] Versioned matcher (`matcher-v1`): exact id -> category+severity+keyword -> category+severity+similarity, one-to-one
- [x] Rubric evaluator with versioned YAML criteria; deterministic scorers only; unscored criteria are reported, never silently dropped
- [x] LLM judge as an optional backend (`claude-cli`, `anthropic-api`), blind pairwise mode, judge model recorded, human annotation import
- [x] An unparseable output is scored as an invalid run, not as "found nothing"

**Runners**

- [x] `AgentRunner` protocol, three adapters
- [x] Fake runner: executes the framework's skills, deterministic, no API key
- [x] Claude Code adapter: `-p --output-format stream-json`, `--no-session-persistence`, `--permission-prompts none`, `--strict-mcp-config`, `--setting-sources`, `--max-budget-usd`, `--json-schema`; usage and cost read from the runtime's own `result` event
- [x] Codex adapter: `codex exec --json --ephemeral`, `--sandbox`, `--output-schema`, `--output-last-message`; usage read from `turn.completed`, cost explicitly *unavailable*
- [x] Availability probe with a clear diagnostic
- [x] Refusal of dangerous permission modes

**Traces**

- [x] Raw stream kept next to the normalised trace
- [x] One normalised event schema for two very different runtimes
- [x] `missing_fields` list for data a runtime does not provide
- [x] `usage.source` = `runtime` / `estimated` / `unavailable`
- [x] Trace store with retrieval by experiment, case and variant
- [x] Redaction of API keys, tokens, private keys, JWTs, AWS keys, generic secret assignments

**Comparison and reporting**

- [x] Metric table with baseline, candidate, delta, delta %, standard deviation, n, 95 % CI, significance
- [x] Per-case table with the status, the metrics the case regressed on, and the per-repetition values
- [x] Explicit lists of improved, regressed and unchanged cases
- [x] Cost and time movement, with "unavailable" shown as unavailable
- [x] Confound table
- [x] Verdict derived from the pre-declared rules, with the rules copied into the report
- [x] Manual-review list
- [x] Markdown + JSON reports

**Registry**

- [x] Append-only JSONL: experiments, runs, evaluations, decisions, framework changes
- [x] "Which experiment validated this commit?", "which model and dataset?", "where do I roll back to?"

**Safety**

- [x] Credential variables stripped from the runner environment (OS level, not a prompt request)
- [x] Explicit `--inherit-env` opt-in with a warning
- [x] Workspace guards: nothing outside the managed root is ever deleted
- [x] The oracle never reaches a workspace (tested end to end)
- [x] Secrets redacted in traces, run records and reports

### Deliberately not implemented

| Not implemented | Why / what to do instead |
|---|---|
| Semantic matching of findings | `matcher-v1` is deterministic and auditable. A semantic matcher would need its own validation; use the judge, whose output is labelled as an opinion. |
| Automatic merge, commit, branch switch or rollback | cc-eval reports the commit to roll back to; a human performs the rollback. |
| Cost model / price table | Cost is what the runtime reports. Codex does not report it, and cc-eval does not guess. |
| Serving the results over HTTP | CLI first. The JSON report is the integration point for anything else. |
| A semantic diff of the framework | File-level hashes are enough to detect an undeclared difference, and they are cheap. |
| Multi-tenant / remote execution | A single local workspace, on purpose. |
| Prompt-level sandboxing | The boundary is the environment and the filesystem, not a request in the prompt. |

---

## 3. Connecting cc-workflow

### 3.1 Point a variant at the repository

```yaml
baseline:
  name: baseline
  framework:
    type: git
    repository: ../cc-workflow
    ref: main
candidate:
  name: candidate
  framework:
    type: git
    repository: ../cc-workflow
    ref: experiment/independent-review
```

cc-eval creates a detached worktree for the ref, copies the tree into the run
workspace and removes the worktree again.  The agent therefore cannot write
back to the repository, and the two sides cannot influence each other.

If the repository has uncommitted changes, cc-eval records that in the run
provenance: the measurement is then not reproducible from the commit alone.

### 3.2 Make sure the skills are really used

Claude Code resolves skills from `.claude/skills/<name>/SKILL.md` relative to
the working directory, and cc-eval runs the agent in the workspace root where
the framework was copied - so a skill that lives in `.claude/skills/` is
discovered without extra flags.

That is necessary but not sufficient.  cc-eval then checks the **trace**:

* the skill appears in the `skills` array of the runtime's `init` event
  -> the framework provided it;
* the trace contains a `Skill` tool call for it -> the agent used it.

A skill that is available but never invoked is reported as `not_invoked`, and
if the experiment lists it under `require_verified_capabilities`, the verdict
becomes `invalid`: the difference cannot be attributed to a change that was
never exercised.  This is the check that prevents "we enabled the skill in
YAML and the score went up".

**Practical consequence:** the task prompt should ask for the skill by name
(see `benchmarks/spec/cases/SPEC-E001/task.yaml`), otherwise the agent may
solve the task without it.

### 3.3 Pin the knowledge base

```yaml
candidate:
  framework:
    type: git
    repository: ../cc-workflow
    ref: experiment/independent-review
    knowledge_snapshot: ../ccw-knowledge/snapshots/2024-06-01
```

The snapshot directory is copied into the workspace and hashed together with
the framework, so the two sides of the experiment retrieve from identical
knowledge.  Without this, a retrieval change is indistinguishable from a
knowledge change - and cc-eval would report it as an undeclared difference
only if the snapshot directory differs, which is why it must be pinned
explicitly.

### 3.4 Keep the output contract stable

Ask the agent for a structured file and score it deterministically:

```json
{
  "findings": [
    {"id": "...", "category": "...", "severity": "high", "description": "...", "evidence": "..."}
  ],
  "summary": "..."
}
```

The evaluator prefers the file the task asked for and falls back to the
agent's final message if the file is missing.  A missing file is an error, not
an empty result.

### 3.5 Run it

```bash
cc-eval run EXP-001 --runner claude --inherit-env --max-budget-usd 2
cc-eval compare EXP-001
cc-eval report EXP-001
```

`--inherit-env` is needed when the runtime authenticates through an
environment variable.  It is the explicit opt-out from the credential
stripping, and cc-eval warns about it.

### 3.6 Answer the questions the registry was built for

```bash
cc-eval registry list                      # every experiment and its verdict
cc-eval registry show EXP-001              # the recorded history
cc-eval experiment rollback EXP-001        # which commit to roll back to
```

`cc-eval experiment accept EXP-001` records that a human accepted the result.
It changes nothing in the framework repository.

---

## 4. What the three example experiments show

| | EXP-001 | EXP-002 | EXP-003 |
|---|---|---|---|
| Change | four skills | one file | four skills + an over-eager detector |
| Verdict | **improved** | **inconclusive** | **inconclusive** |
| Recall | 0.4833 → 1.0000 | 0.4833 → 0.5833 | 0.4833 → 1.0000 |
| 95 % CI | [+0.217, +0.800] | [+0.000, +0.300] | [+0.217, +0.800] |
| Precision | 0.8750 → 1.0000 | unchanged | 0.8750 → **0.8000** |
| Why | every case improved, the interval excludes zero | only one of five cases improved, the interval contains zero | the average improved but `SPEC-E002` regressed on precision, `false_positive_count` and F1 - the guard metric turns it into `inconclusive` |

`EXP-002` is the most instructive one: a real, single-variable improvement
that the experiment cannot yet distinguish from noise, because five cases are
not enough.  The system says so instead of claiming a win.

---

## 5. Next phase

### Phase 2 - make the real runtimes first class

1. **Cost normalisation.**  Claude Code reports `total_cost_usd`, Codex does
   not.  Either add a price table with a documented source, or keep the
   asymmetry visible and compare only what both runtimes report.
2. **A `--profile` for the Claude Code settings.**  `--setting-sources` is
   already configurable; a named profile per project would make the settings
   part of the recorded provenance instead of a command line argument.
3. **Hook and MCP coverage.**  The capability mechanism already works on
   whatever the runtime leaves in the trace; the next step is to verify
   hooks and MCP servers the same way and to record them as capabilities.
4. **Per-tool token accounting** where the runtime exposes it, so that cost
   can be attributed to a step of the workflow.

### Phase 3 - make the results durable

1. **MLflow exporter** over `core/registry.py` - a small adapter, because the
   registry is already flat JSONL (ADR-0008).
2. **A wider benchmark.**  Five cases is a regression suite, not a benchmark.
   The next cases should come from real cc-workflow sessions, each with a
   hand-written oracle - the cost of writing them is the real cost of the
   system, and it should be amortised over many experiments.
3. **Regression tracking.**  A command that re-runs the benchmark against the
   current framework and compares it with a stored experiment, so that a
   change months later is measured against the same baseline.
4. **Human review workflow.**  The judge supports annotation import; a
   review queue that shows the flagged cases and records the annotations
   would close the loop.
5. **Retrieval experiments.**  With the knowledge snapshot in place, the
   trigger conditions, top-k and the context injection strategy can be
   measured with the same machinery - this is the natural next framework for
   ccw-knowledge.

### A note on the cost of good ground truth

Writing an oracle by hand is the expensive part of this system, and it does
not get cheaper with automation: an oracle that nobody verified is not ground
truth, it is a second guess.  The five shipped cases are hand-written for
exactly that reason.  A sensible next step is to turn *real* review sessions
into cases - the agent's output is already there, and a human only has to
decide which findings were correct.
