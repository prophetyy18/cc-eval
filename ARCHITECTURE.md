# cc-eval architecture

cc-eval is a **controlled experiment harness** for agent frameworks.  It does
not own the framework under test, it does not modify it, and it never merges
anything into it.  Its only job is to make a claim about a framework change
*falsifiable*: run the same task against two versions of the framework under
identical conditions, measure the outcome against ground truth, and report
what changed - including the cases where the claim does not hold.

```
                       cc-eval workspace (.cc-eval/)
  ┌──────────┐         ┌──────────────────────────────────────────┐
  │benchmark │────────▶│  experiment runner                       │
  │ (cases + │  tasks  │   variant × case × repetition             │
  │  oracle) │         │   ├─ materialise framework  (git | path) │
  └──────────┘         │   ├─ materialise task bundle (no oracle)  │
                         │   ├─ run agent            (fake|claude| │
                         │   │                        codex)        │
                         │   └─ evaluate             (deterministic│
                         │                        rubric|judge)   │
                         └───────────────┬──────────────────────────┘
                                         │ traces + run records
                                         ▼
                         ┌──────────────────────────────────────────┐
                         │  comparison                              │
                         │   ├─ per-case and macro aggregates       │
                         │   ├─ confound analysis                    │
                         │   └─ rule-derived verdict                 │
                         └───────────────┬──────────────────────────┘
                                         ▼
                          report (Markdown + JSON) + registry
```

## Layers

| Package | Responsibility | Depends on |
|---|---|---|
| `core/models.py` | the schema contract - every artefact that crosses a module boundary | nothing |
| `core/snapshot.py` | materialise a variant (git worktree or directory copy), fingerprint it, diff two variants | `core.models` |
| `core/experiment.py` | load a spec, build the run plan, execute it, persist the results | datasets, evaluators, isolation |
| `core/registry.py` | append-only experiment / run / decision history | `core.models` |
| `core/config.py` | workspace layout and global settings | nothing |
| `datasets/` | load a benchmark, materialise the agent-visible task bundle | `core.models`, `isolation` |
| `runners/` | the `AgentRunner` adapters | `core.models`, `isolation`, `traces` |
| `evaluators/` | deterministic matching, rubrics, the optional judge | `core.models` |
| `traces/` | normalise runtime events, store raw and normalised traces | `core.models` |
| `comparison/` | aggregation, statistics, confound detection, verdict | `core.models`, `core.experiment` |
| `reports/` | render the JSON and Markdown reports | `core.models` |
| `isolation/` | workspace guards, subprocess budgets, redaction | `core.config` |
| `cli/` | the Typer application | everything |

The dependency direction is strictly downwards, and the package `__init__`
modules use lazy re-exports (`cc_eval/_lazy.py`) so that importing one package
never drags in a cycle.

## The three things the design protects

**1. The ground truth stays hidden.**  `oracle.yaml` lives next to `task.yaml`
in the benchmark, but only `task.yaml` and `input/` are materialised into the
run workspace.  The evaluator reads the oracle from the benchmark, the runner
never sees it.  (ADR-0001)

**2. The comparison is a controlled comparison.**  Each run gets a fresh
workspace, a fresh session and its own copy of the framework.  Every run
records its provenance - framework fingerprint, dataset hash, evaluator
version, model, runtime version, seed, order index - so that the comparison
can *detect* a difference in the conditions instead of assuming there is none.
The framework trees are compared file by file, and any difference outside the
declared `expected_changes` is reported as a confound.  (ADR-0002)

**3. The verdict is computed, not judged.**  The decision rules are declared
before the runs and applied mechanically by `apply_decision_rules()`.  There
is no code path that produces `improved` from an impression.  (ADR-0003)

## The run plan

```
for repetition in 1..N:
    for case in cases:
        for variant in [baseline, candidate]   # order alternates
            workspace := fresh workspace
            materialise(variant.framework) -> workspace
            materialise(case.task)       -> workspace/task
            run(agent, case, workspace)   -> RunResult + Trace
            evaluate(case, result)        -> EvaluationResult
            persist(run record, trace, evaluation)
```

The variants alternate within each `(repetition, case)` block, and the block
order flips from block to block, so neither variant systematically occupies
the first slot.  With an even number of repetitions each variant goes first
exactly half of the time.

## Aggregation

* Within a case, the metric is averaged over its repetitions.
* Across cases, the value is the **mean of the per-case means** (macro
  average), so a hard case is not drowned out by many easy ones.
* The confidence interval is a paired bootstrap over cases: the unit the
  experiment generalises over is the case, not the individual run.
* The spread reported next to a metric is the mean of the per-case standard
  deviations over repetitions.

## Confound detection

| Check | Source | Effect |
|---|---|---|
| control mismatch | provenance of the two sides (model, runtime, dataset hash, evaluator version) | blocker |
| unexpected framework diff | file-level diff of the two materialised trees vs `expected_changes` | blocker |
| identical variants | file-level diff | blocker - there is nothing to measure |
| capability unverified | trace evidence vs `expected_capabilities` | blocker |
| repetition mismatch | repetition counts per variant | blocker |
| guard metric regression | metric deltas | inconclusive |

## Determinism

The fake runner is deterministic.  Claude Code and Codex are not: they expose
no seed control, and cc-eval records that fact in
`RunResult.determinism.seed_note`.  Repetitions exist to measure the spread of
that non-determinism, and the report shows it next to every metric.

## Extension points

| To add | Do this |
|---|---|
| a new agent runtime | implement `AgentRunner` in `runners/` and register it in `runners/registry.py` |
| a new scoring method | implement `Evaluator` in `evaluators/` and register it in `evaluators/registry.py` |
| a new dataset format | add a loader in `datasets/` that yields `BenchmarkCase` objects |
| an experiment tracker | write an adapter over `core/registry.py` (MLflow, W&B, ...) |
| a knowledge base | set `framework.knowledge_snapshot` to pin a directory; it is copied and hashed with the framework |

## What is deliberately not here

* No web UI, no background service, no database.
* No automatic merge, commit, branch switch or rollback of the framework.
* No promise of bit-reproducible agent runs.
* No semantic matcher: findings are matched with the deterministic
  `matcher-v1` rules, and anything that needs a model is an explicit,
  optional judge whose output is labelled as a judgement.
