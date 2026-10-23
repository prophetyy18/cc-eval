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
xdg-open .cc-eval/reports/EXP-001/report.html  # the interactive report
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
| `cc-eval run EXP-001 --runner fake\|claude\|codex` | execute the experiment (writes the HTML report) |
| `cc-eval compare EXP-001` | recompute the comparison from the stored runs |
| `cc-eval report EXP-001 [--format html\|md\|json\|both\|all]` | render the report (HTML by default) |
| `cc-eval render EXP-001 [--format html]` | re-render the report from the stored runs, without running anything |
| `cc-eval serve [--host 0.0.0.0] [--port 30000]` | start the console: author cases, run experiments, watch the results live |
| `cc-eval benchmark validate PATH` | check a dataset's structure, ground truth and hashes |
| `cc-eval registry list` / `show` / `reports` | inspect the recorded history and the reports it links to |
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

`cc-eval run` writes the report automatically:

```
.cc-eval/reports/EXP-001/
    report.html    interactive dashboard - open it in a browser
    report.json    the same data, machine readable
    report.md      the plain-text report
```

**Open the HTML report with any browser - no server, no network, no
dependencies:**

```bash
xdg-open .cc-eval/reports/EXP-001/report.html     # Linux
open .cc-eval/reports/EXP-001/report.html         # macOS
start .cc-eval\reports\EXP-001\report.html        # Windows
```

The page has four tabs:

| Tab | What it answers |
|---|---|
| **Overview** | what is the conclusion, what is the evidence, what does the data *not* support, how did the metrics, the tokens and the cost move |
| **Cases** | which cases improved, which regressed, what changed in them - filter by movement, search by case id / category / tag, expand a case for both outputs, the scoring evidence and the failure reason |
| **Traces** | the execution path of every run, per case and per repetition, with the A/B path difference |
| **Metadata** | runtime, model, commits, fingerprints, config hash, dataset and evaluator versions, controls, decision rules, run ids, environment |

The report never invents a number: it is rendered from the same
`ReportModel` that produces `report.json`, so the two cannot drift.  A
missing value is shown as **n/a**, never as zero, and an experiment that did
not finish is marked **incomplete** instead of being presented as a success.

The Markdown report contains the metric table with the confidence interval,
the **per-case** table (which cases improved, which regressed, which metrics
they regressed on), the run-to-run variation, the cost and time movement, the
confound analysis, and the verdict with the exact rule that produced it.

#### Language

The report is written in **Chinese by default**.  To read it in English:

```bash
cc-eval report EXP-001 --lang en
export CC_EVAL_LANG=en          # for every report from now on
```

The JSON report stays language neutral: it carries the English text plus a
stable message code (`reason.improved`, `confound.control_mismatch`, ...), so
a machine never has to parse prose.  The HTML and the Markdown reports
translate the chrome and the engine's own sentences.

#### Re-rendering without running the experiment

```bash
cc-eval render EXP-001                    # HTML only, from the stored runs
cc-eval render EXP-001 --format all       # HTML + JSON + Markdown
cc-eval report EXP-001 --format html --output /tmp/report.html
```

#### Customising the HTML report

The template is a single Jinja2 file:

```
src/cc_eval/reports/templates/report.html.j2
```

Copy it, edit it and point cc-eval at your copy - no code change needed:

```bash
cp src/cc_eval/reports/templates/report.html.j2 ~/.cc-eval/my-report.html.j2
$EDITOR ~/.cc-eval/my-report.html.j2

cc-eval render EXP-001 --template ~/.cc-eval/my-report.html.j2
```

The same path can be set permanently:

```bash
export CC_EVAL_HTML_TEMPLATE=~/.cc-eval/my-report.html.j2
```

The template receives the `ReportModel` (`report`), the experiment
specification (`spec`), the comparison (`comparison`), the per-case view
models (`case_views`), the metadata (`metadata`), the metric cards with their
inline SVG charts (`metric_cards`) and the movement summary (`movement`).
Agent output is escaped by Jinja2; `{{ value | safe }}` is only for markup
cc-eval itself generated.

---

## The console

`cc-eval serve` starts a local web application over the workspace: it authors
test cases, starts experiments, streams the results as they happen, and shows
the history.

```bash
cc-eval serve                                    # http://127.0.0.1:30000
cc-eval serve --host 0.0.0.0 --port 30000 \
    --allow-write --admin-token "$ADMIN"         # reachable from the network
```

The live run table marks planned and active runs, then adds each completed
run's score, time, token usage, and outputs. Outputs accumulate by case in a
baseline/candidate comparison; refreshing the page restores the active run.
**实验详情** also shows completed runs and their original `INTENT.md` text while
the experiment is still running. These are per-run results, published when a
run ends; the verdict and aggregate metrics appear after the experiment ends.
The final detail page is populated from structured `report.json` and shows
the verdict, metrics, per-case runs, output, traces, and provenance. The
standalone HTML remains available there as an export. Console runs also save
an experiment YAML and a selected-case dataset
snapshot (`.cc-eval/experiments/<ID>.dataset.json`) with the task inputs and
oracle as they were at launch, plus an explicit candidate ID in the registry.
Older experiments without a snapshot still show their recorded results.
For `intent-skill`, the current rubric checks document
structure and the oracle's expected intent count; it does not score the
quality of interactive clarification.

### Candidate branches in the browser

With an admin token and `--allow-write`, open **候选版本**. Point it at the
`cc-workflow` Git checkout and create a candidate. The console pins the
current `main` commit as the baseline, creates a `codex/intent-*` branch and
an isolated worktree under `.cc-eval/candidates/`. Enter an edit instruction
and the repository-relative paths Claude Code may change, one per line. The
default is `.claude/skills/intent/`; a trailing `/` means a directory, while a
path without it names one file. Add another path or choose a broader directory
when a change also needs shared references. The browser also sets the maximum
Agent turns, runtime and budget for each edit (defaults: 100 turns, 30 minutes,
$10). Select a Claude Code permission mode for each edit: `acceptEdits` (the
default) or `bypassPermissions`. Each edit runs in a temporary worktree with
only Read/Edit/Write/Glob/Grep tools and a PreToolUse hook that rejects
Write/Edit paths outside the selected scope. Claude Code treats `.claude/` as
protected, so `acceptEdits` uses editable copies under an unprotected temporary
path and maps changes back to the original paths for review. With
`bypassPermissions`, Claude edits the selected `.claude/` paths directly in the
temporary worktree. The chosen mode is saved with the edit and its experiment
snapshot. Headless `bypassPermissions` requires the Claude Code user setting
`skipDangerousModePermissionPrompt: true` to be accepted beforehand.
After the run, the console checks the Git changes and copies only selected
paths to the candidate branch. Denied or isolated attempts appear next to the
edit controls. Review the changed files and diff against the pinned
baseline, then commit from the browser. The console pushes the candidate
branch to `origin` after each commit; that commit action does not push `main`. If a push fails,
the local commit remains available for experiments and the same button retries
the push without creating another commit. **放弃本轮未提交改动** restores only the
last agent edit's reviewed files to the previous commit, so another edit can
start without changing earlier commits or the remote branch. The file list is
discovered from the actual Git diff, so every changed skill or reference file appears without
naming it in advance. The same diff is saved with the experiment and shown
inside its detail page; bound experiments declare exactly those changed files
as the expected framework change. The
**用此版本配置对比实验** button fills the run form with the baseline and candidate
commit IDs. The run form sets the same Agent turn limit for both variants
(100 by default); this limit is saved in the experiment configuration. The
button stays disabled while changes are uncommitted or outside the
selected scope. Existing untracked files outside the scope can be removed
with the **清理范围外的未跟踪文件** button. Keep the scope limited to the intended
independent variable when comparing the candidate with `main`. This is an
application-level path and import boundary, not an operating-system sandbox.
The candidate page shows the budget ceiling during an edit and Claude Code's
reported cost when it becomes available. Some providers report cost only in
the final result; the console leaves live dollar usage unknown rather than
estimating it from turns or elapsed time.
After testing, use **准备合并到 main** on the candidate page. The console reads
the current `origin/main` and candidate branch, shows the changed files and
whether the latest experiment used the current candidate commit. After you
confirm, local Git creates a merge commit in a temporary worktree, pushes it
to `origin/main`, and fast-forwards the clean local `main`. It never
force-pushes. A conflict or rejected push leaves local `main` untouched;
branch protection may reject direct pushes. The candidate worktree must be
clean and pushed, and the local `main` checkout must be clean. The console
fetches a newer remote `main` when needed and fast-forwards a local `main`
that has not diverged.
If the public tunnel briefly returns 502/503/504, the merge preview retries.
The merge request is never blindly retried: the page rechecks `origin/main`
first. If the remote already contains the candidate but the local checkout is
behind, **同步本地 main** finishes the local fast-forward.

### The console writes ordinary case files

What you type in the form becomes a normal cc-eval case:

```
benchmarks/<type>/cases/<ID>/
├── input/<file>     the text you typed
├── task.yaml        generated from the test type
└── oracle.yaml      hidden answer, edited separately from the agent prompt
```

The console is an **editor**, not a second source of truth.  A case stays a
file: it has a content hash, it is diffable in git, and the dataset hash
travels with every run - so you can always tell whether two runs used the
same questions.

### Tokens

| Token | Grants | How to set |
|---|---|---|
| viewer | read the dashboard, the runs, the reports | `--token` or `CC_EVAL_DASHBOARD_TOKEN` |
| admin | author cases, start runs | `--admin-token` or `CC_EVAL_DASHBOARD_ADMIN_TOKEN` |

* Binding to a **public** interface without a token **refuses to start**.
* `--allow-write` without an admin token **refuses to start**.
* The session cookie is signed, `HttpOnly` and `SameSite=Strict`, and expires
  after 12 hours.  Failed logins are rate limited.
* A run request may only take its framework from an allowed root
  (`--allow-framework-root`, defaults to your home directory), so a caller
  cannot point the agent at an arbitrary directory.

### Exposing it to the internet

The console can read your run records (agent output, traces), write case
files and start real agent runs.  If you bind it to `0.0.0.0`:

* **use HTTPS** - either `--tls-cert`/`--tls-key` directly, or put a reverse
  proxy (Caddy, nginx) in front of it;
* **use a long random admin token** and rotate it;
* remember that the redaction in cc-eval covers credentials, not business
  content: your prompts, code fragments and generated documents are visible
  to anyone who can authenticate.

### Adding a kind of test

A new kind of test is a registration, not a code change:

```python
from cc_eval.dashboard.types import Field, OutputSpec, TestType, register

register(TestType(
    name="intent-skill",
    title="Intent 拆解",
    benchmark=Path("benchmarks/intent_skill"),
    form=[Field("request", "Intent 输入", "textarea", required=True)],
    outputs=[OutputSpec("intent_doc", "INTENT.md",
                        collect="docs/intents/*/INTENT.md",
                        view="markdown",
                        count_pattern=r"(?m)^id:\s*INT-\d{3}",
                        count_label="intent 数量")],
    prompt_template="...",
    input_files={"request": "request.md"},
    output_paths=["docs/intents"],
    allowed_tools=["Read", "Write", "Bash(mkdir:*)"],
    case_prefix="INT-S",
    task_type="intent_capture",
))
```

The form drives the authoring UI, the output spec drives what the console
collects and shows, and the runner stays generic.

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
                  reports/templates/report.html.j2   the HTML report template
                  dashboard/         the console: authoring, live runs, the API
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
