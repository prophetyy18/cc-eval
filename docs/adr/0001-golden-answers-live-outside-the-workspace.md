# ADR 0001 - Golden answers live outside the workspace

**Status:** accepted

## Context

A benchmark case has two halves: the task the agent sees, and the ground
truth that decides whether the answer was right.  If the ground truth is in
the same directory as the task, then either the agent can read it, or every
run needs a filtering step that might be wrong.  A benchmark that leaks its
answers measures nothing.

## Decision

A case is a directory with three parts:

```
cases/SPEC-E001/
  task.yaml      # agent visible: id, task type, prompt, output contract
  input/         # agent visible: the files the task is about
  oracle.yaml    # hidden: known defects, constraints, expected entities
```

`materialise_bundle()` writes **only** `task.yaml` and `input/` into the run
workspace.  The oracle is never copied, and the loader additionally refuses to
treat a case as valid if its oracle declares no ground truth without saying so
explicitly (`expect_no_findings: true`, used by the false-positive control
case).

## Consequences

* The evaluator and the agent receive different objects; there is no code path
  that hands the oracle to a runner.
* A test asserts that a marker string from the oracle appears nowhere in the
  workspace **and** nowhere in the artefacts cc-eval writes afterwards.
* Adding an oracle field means deciding whether the agent may see it.  The
  default answer is no.
