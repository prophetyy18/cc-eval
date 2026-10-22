# cc-eval

**A controlled-experiment system for agent frameworks.**

cc-eval answers one question with data instead of opinion:

> *Did this change to the framework (a skill, a prompt, a hook, a retrieval
> strategy) actually make the agent better - and what did it cost?*

It runs the same benchmark against two versions of a framework under
controlled conditions, scores the results against hidden ground truth, and
reports what improved, what regressed, what it cost, and how much the
conclusion can be trusted.

It is built for frameworks like **cc-workflow** (Claude Code skills, prompts,
hooks) and **ccw-knowledge** (retrieval), and it does not depend on either of
them.

---

## Why

When a framework evolves - a new intent classification rule, a spec review
prompt, a fresh-context review step, a different retrieval top-k - it is
usually impossible to tell afterwards whether the change helped.  The
evidence is anecdotal: one run, one task, an impression.

cc-eval turns that into an experiment:

| Scientific concept | cc-eval |
|---|---|
| controlled variables | `controls:` in the experiment file, checked against the observed runs |
| independent variable | one framework difference, declared in `expected_changes` |
| baseline / treatment | `baseline:` / `candidate:` variants |
| repeated measurement | `repetitions:` |
| ground truth | the hidden `oracle.yaml` of a benchmark case |
| statistical test | paired bootstrap over cases, with a pre-declared confidence level |
| significance | the pre-declared decision rules |
| replication package | the `.cc-eval/` workspace: runs, traces, registry, reports |

---

## Install

Requires **Python 3.11+**.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

If you use conda:

```bash
conda create -n cc-eval python=3.11 -y
conda activate cc-eval
pip install -e ".[dev]"
```

---

## Quickstart

```bash
cc-eval init                                   # create the workspace
cc-eval benchmark validate benchmarks/suite    # check the dataset
cc-eval run EXP-001 --runner fake --file experiments/EXP-001.yaml
cc-eval compare EXP-001 --file experiments/EXP-001.yaml
cc-eval report EXP-001 --file experiments/EXP-001.yaml
```

The three example experiments in `experiments/` run against the miniature
framework in `examples/framework/`:

| Experiment | Question | Result |
|---|---|---|
| `EXP-001` | Does the candidate skill set find more real defects? | **improved** - recall 0.48 → 1.00 |
| `EXP-002` | Does *one* added detector help? (strict single variable) | **inconclusive** - the effect is real but the interval contains zero |
| `EXP-003` | Does a more aggressive detector hurt? | **inconclusive** - recall improved but precision regressed on the clean case |

The third one is the interesting one: the average got better while a
specification review started inventing defects.  The report names the case.

---

## Commands

| Command | What it does |
|---|---|
| `cc-eval init` | create `.cc-eval/` (config, experiments, registry, runs, traces, reports, workspaces) |
| `cc-eval experiment init EXP-001` | write an experiment template |
| `cc-eval experiment list` / `show` | inspect experiments and their verdicts |
| `cc-eval experiment accept EXP-001` | record a human acceptance in the registry (changes nothing else) |
| `cc-eval experiment rollback EXP-001` | show which framework commit to roll back to (never touches git) |
| `cc-eval run EXP-001 --runner fake\|claude\|codex` | execute the experiment |
| `cc-eval compare EXP-001` | recompute the comparison from the stored runs |
| `cc-eval report EXP-001 [--format md\|json\|both]` | render the report |
| `cc-eval benchmark validate PATH` | check a dataset's structure, ground truth and hashes |
| `cc-eval registry list` / `show` | inspect the recorded history |
| `cc-eval runner list` | probe which runtimes are usable on this machine |

Useful `run` options: `--case ID` (run a subset), `--repetitions N`,
`--model`, `--max-budget-usd`, `--timeout`, `--dry-run` (show the plan),
`--inherit-env` (see *Security*), `--judge-backend`.

---

## The pieces

### A benchmark

A benchmark is a directory of cases.  Each case has an **agent-visible** half
and a **hidden** half:

```
benchmarks/suite/
  manifest.yaml
  cases/
    SPEC-E001/
      task.yaml       # id, task type, prompt, output contract
      input/spec.md   # the files the task is about
      oracle.yaml     # known defects          <- never given to the agent
```

The oracle is physically separated from the task, and the loader refuses to
materialise it into a workspace.

### An experiment

```yaml
id: EXP-001
title: Candidate skill set
hypothesis: >
  The candidate skill set finds more real defects than the baseline skill set
  without introducing false positives on the clean specification.
independent_variable: The implementation of the four framework skills.

baseline:
  name: baseline
  framework: {type: path, path: examples/framework/baseline}
  expected_capabilities: [spec-review, intent-router, cross-module-impact, requirement-guard]
candidate:
  name: candidate
  framework: {type: path, path: examples/framework/candidate}
  expected_capabilities: [spec-review, intent-router, cross-module-impact, requirement-guard]

controls:
  runtime: fake
  benchmark: benchmarks/suite
  dataset_version: v1
  evaluator: deterministic-v1
  repetitions: 3
  order: alternating
  seed: 20240101

expected_changes:
  - skills/manifest.json
  - skills/spec_review.py
  # ... anything that differs outside this list is reported as a confound

decision_rules:
  primary_metric: recall
  direction: higher_is_better
  min_delta: 0.05
  min_repetitions: 3
  guard_metrics: [precision]      # a better average with worse precision is not an improvement
  max_regressed_cases: 0
  require_confound_free: true
  confidence: {method: paired_bootstrap, alpha: 0.05, iterations: 2000}
```

A `git` variant (`framework: {type: git, repository: ../cc-workflow, ref: main}`)
materialises a detached worktree instead of a directory copy.

### The report

The Markdown report contains the metric table with the confidence interval,
the **per-case** table (which cases improved, which regressed, which metrics
they regressed on), the run-to-run variation, the cost and time movement, the
confound analysis, and the verdict with the exact rule that produced it.

---

## Security

The runner executes real tools and shell commands, so the boundaries matter.

* **Credentials are stripped by default.**  `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN` and friends are
  removed from the environment handed to a real runtime.  This is OS-level
  access control, not a request in the prompt.  If the runtime on your
  machine authenticates through an environment variable, a real run fails
  with a clear diagnostic until you opt in.
* **`--inherit-env` is the explicit opt-out** for a local, trusted run.  The
  CLI prints a warning when it is used.
* **Dangerous permission modes are refused.**  `bypassPermissions` and
  `--dangerously-skip-permissions` raise an error unless
  `allow_bypass_permissions` is set explicitly.
* **Every run gets a fresh workspace**, and the workspace manager refuses to
  delete anything outside its own root or without its sentinel file.
* **Secrets that reach the artefacts are redacted** on write, and the report
  records how many redactions happened.
* **Traces can be sensitive.**  They are redacted, but redaction is hygiene,
  not a guarantee - do not publish raw traces or commit them to git.

---

## Connecting cc-workflow

cc-eval does not depend on cc-workflow's internals; it needs a directory it
can materialise and a way to observe what happened.

**1. Point a variant at the repository.**

```yaml
baseline:
  framework: {type: git, repository: ../cc-workflow, ref: main}
candidate:
  framework: {type: git, repository: ../cc-workflow, ref: experiment/independent-review}
```

The tree is copied into the run workspace, so the agent can never write back
to the repository.  A dirty working tree is recorded as a provenance note -
the experiment is not reproducible from the commit alone.

**2. Make the skills discoverable.**  Claude Code resolves skills from
`.claude/skills/<name>/SKILL.md` relative to the working directory, and
cc-eval runs the agent in the workspace root where the framework was copied.
A skill is only *verified* as used when the trace shows it in the runtime's
skill inventory **and** an invocation of it - a YAML flag proves nothing.

**3. Pin the knowledge base.**  When ccw-knowledge is enabled, set
`framework.knowledge_snapshot: ../ccw-knowledge/snapshots/2024-06-01` so that
retrieval results cannot drift between the two sides of the experiment.

**4. Keep the task contract stable.**  Ask the agent for a structured output
file (`out/result.json`) and score it deterministically - see
`benchmarks/spec/cases/SPEC-E001/task.yaml` for the prompt shape.

**5. Run it.**

```bash
cc-eval run EXP-001 --runner claude --inherit-env --max-budget-usd 2
cc-eval report EXP-001
```

See `docs/DELIVERABLES.md` for the full integration guide.

---

## Development

```bash
pip install -e ".[dev]"
pytest                    # the whole suite, no API key needed
pytest -m "not slow"      # skip the tests that spawn a real agent runtime
pytest -m slow -rs        # run them, and show why they were skipped
```

The fake runner is the backbone of the suite: it executes the framework's
skills locally, so the whole pipeline is exercised offline.

Layout:

```
src/cc_eval/       core/ runners/ evaluators/ datasets/ traces/ comparison/ reports/ isolation/ cli/
benchmarks/        intent/ spec/ cross_module/ requirements/ suite/
experiments/       EXP-001.yaml EXP-002.yaml EXP-003.yaml
examples/framework/ baseline/ candidate/ candidate_aggressive/ spec-review-only/
docs/adr/          the decisions behind the design
```

---

## Limitations

* Agent runs are **not** bit-reproducible.  cc-eval records the seed support
  of each runtime and shows the run-to-run variation; it does not promise
  determinism it cannot deliver.
* The default matcher (`matcher-v1`) is rule based: exact id, then
  category + severity + keyword, then category + severity + description
  similarity.  A finding that needs semantic understanding is not matched, and
  the LLM judge is an explicit, optional fallback - its output is labelled as
  a judgement, never as truth.
* The shipped benchmark is small (five hand-written cases).  It is a
  regression suite for the framework, not a statistically powered benchmark.
  With few cases the bootstrap interval is wide, and the reports say so.
* `EXP-002` shows what that means: a real improvement that the experiment
  cannot yet distinguish from noise.

## License

MIT
