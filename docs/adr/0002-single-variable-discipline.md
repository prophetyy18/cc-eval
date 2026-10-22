# ADR 0002 - Single variable discipline

**Status:** accepted

## Context

The value of an A/B comparison comes from the claim that *one* thing changed.
In practice a framework change drags other changes along: a formatter runs
over the tree, a manifest records a version string, a helper is refactored.
The comparison still produces a number, and the number gets attributed to the
change the author had in mind.

## Decision

An experiment declares which framework files it expects to differ:

```yaml
expected_changes:
  - skills/spec_review.py
```

cc-eval materialises both variants, hashes every file and compares the two
trees.  Any difference **outside** `expected_changes` is reported:

| Situation | Severity | Effect on the verdict |
|---|---|---|
| a file differs that was not declared | blocker | the verdict becomes `invalid` |
| files differ and nothing was declared | warning | reported, verdict may stand |
| the two variants are byte-identical | blocker | there is nothing to measure |

The declaration is a claim, not a guarantee - which is why the comparison
checks the claim against the actual trees instead of trusting the YAML.

## Consequences

* A "clean" experiment has to be maintained: when a framework version bump
  touches the manifest, the declaration is updated.  The failure mode is
  visible (a blocker in the report), not silent.
* `EXP-002` in this repository is a genuine single-variable experiment: only
  `skills/spec_review.py` differs between the two trees.
* The check is cheap (content hashes, no diffing of file contents), so it can
  run on every comparison.
