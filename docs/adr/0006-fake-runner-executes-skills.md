# ADR 0006 - The fake runner executes the framework's skills

**Status:** accepted

## Context

The system needs a runner that works without an API key: for the test suite,
for CI, and for anybody who wants to see the pipeline work before spending
money.  The tempting implementation returns a canned answer, but then the
experiment is a simulation of the *harness* and tells you nothing about the
*framework* - the thing under test is exactly what a stub would fake.

## Decision

The fake runner is an agent simulator that executes the framework under test:

1. it reads `skills.manifest.json` from the materialised framework;
2. it selects the skills whose `task_types` match the case;
3. it imports the skill module and calls its entry point with the case
   payload;
4. it merges the findings and writes them to the expected output path.

The skills are ordinary Python modules in the framework tree.  Changing a
skill implementation changes the results - the difference comes from the
framework content, not from a label attached to the experiment.

## Consequences

* The whole pipeline (trace, evaluation, comparison, report) runs offline, so
  it can be a CI gate on every framework change.
* The fake runner is deterministic: identical framework content plus identical
  case gives identical output.  Repetitions are therefore a no-op for it, and
  the report shows zero variance - which is honest, and is why the run-to-run
  variation machinery is exercised with real runners and with synthetic
  records in the tests.
* The example framework under `examples/framework/` is a miniature
  cc-workflow: four skills, three versions, one clean specification as a
  false-positive control.
