# ADR 0004 - One trace format, missing data recorded instead of invented

**Status:** accepted

## Context

Claude Code and Codex emit completely different event vocabularies.  A report
that mixes them is unreadable, and a report that pretends the two are the same
invents facts: Codex does not report cost, Claude Code does not report a
duration per tool call.

## Decision

Two artefacts per run:

* the **raw** event stream, exactly as the runtime emitted it
  (`traces/<EXP>/raw/<run_id>.jsonl`);
* the **normalised** trace (`traces/<EXP>/<run_id>.json`) with one event
  schema: `session`, `tool_call`, `tool_result`, `agent_output`, `reasoning`,
  `result`.

Normalisation is lossy on purpose.  Fields a runtime cannot supply stay
`None`, and the trace lists them under `missing_fields` (`cost_usd`,
`tool_duration_ms`, ...).  Token usage carries a `source` field with three
values:

| `usage.source` | Meaning |
|---|---|
| `runtime` | the runtime reported it |
| `estimated` | cc-eval computed it from a documented formula |
| `unavailable` | the runtime does not expose it - the value is `None` |

## Consequences

* A metric that is `n/a` for one runtime and a number for another is a real
  difference in what can be measured, and the report says so instead of
  showing a fake zero.
* The raw stream is kept because the normalisation is a lossy interpretation.
  It is also where sensitive content can hide, so it is redacted on write and
  must not be published by default.
* The event shapes in `traces/normalize.py` were written against the observed
  output of Claude Code 2.1.308 and codex-cli 0.160.1, not from memory.
