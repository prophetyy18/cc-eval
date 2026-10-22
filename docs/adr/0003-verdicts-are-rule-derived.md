# ADR 0003 - Verdicts are rule-derived

**Status:** accepted

## Context

"The candidate looked better" is the most common failure mode of framework
evaluation.  It is easy to write, hard to argue with, and impossible to
reproduce: the same numbers can be summarised as "improved" or "inconclusive"
depending on who is reading.

## Decision

The decision rules live in the experiment file, **before** the runs:

```yaml
decision_rules:
  primary_metric: recall
  direction: higher_is_better
  min_delta: 0.05
  min_repetitions: 3
  guard_metrics: [precision]
  max_regressed_cases: 0
  require_confound_free: true
  confidence: {method: paired_bootstrap, alpha: 0.05, iterations: 2000}
```

`apply_decision_rules()` then walks a fixed sequence of gates and returns the
first that fires:

1. a blocking confound -> `invalid`
2. fewer repetitions than required -> `inconclusive`
3. the primary metric moved less than `min_delta` -> `inconclusive`
4. a case regressed that the rules forbid -> `inconclusive`
5. a guard metric moved the wrong way -> `inconclusive`
6. the confidence interval contains zero -> `inconclusive`
7. otherwise -> `improved` or `regressed`

There is no code path that produces a verdict from a subjective impression,
and the rules that produced the verdict are copied into the report.

## Consequences

* An experiment can fail to produce the answer the author wanted.  That is the
  point: `EXP-002` improves one case out of five and is correctly reported as
  `inconclusive` because the bootstrap interval contains zero.
* Changing the rules after seeing the result is possible (they are a file),
  but it invalidates comparability with earlier runs of the same experiment
  id - which is why the report records the rules snapshot.
* Guard metrics exist because the primary metric alone can hide a regression:
  a reviewer that starts inventing defects improves recall and ruins
  precision.  `EXP-003` demonstrates exactly that.
