# ADR 0008 - The registry is append-only files

**Status:** accepted

## Context

The registry has to answer, months later: why was this skill added, which
experiment validated it, which model and dataset were used, which cases
improved, what did it cost, and where do we roll back to.  It also has to
survive being read with `cat` and `grep`, and it must not lose history when a
process is killed halfway.

## Decision

Plain files under the cc-eval workspace:

```
registry/
  index.jsonl               one line per experiment
  framework_changes.jsonl   framework commit -> the experiments that test it
  <EXP-ID>/runs.jsonl       one line per run
  <EXP-ID>/results.jsonl    one line per evaluation
  <EXP-ID>/decisions.jsonl  one line per decision
```

Every record carries a timestamp and is only ever appended.  The decision
records carry the rules that produced them, so a verdict can be re-derived
from the stored numbers.

## Consequences

* The history is greppable and diffable, and a crashed run cannot corrupt
  previous records.
* The store is deliberately boring: the shape of the records is a flat JSON
  object with the run, the evaluation and the provenance.  An MLflow
  exporter is a small adapter over this data rather than a rewrite of the
  engine.
* The registry answers the "where do I roll back to" question by storing the
  framework commit with each variant.  cc-eval reports that commit; it never
  performs the rollback itself.
