# ADR 0005 - A capability counts as used only when the trace proves it

**Status:** accepted

## Context

The most dangerous way to run a framework experiment is to mark a skill as
"enabled" in a configuration file and then attribute the result to it.  The
skill may never have been loaded, may have been loaded but not invoked, or may
have been invoked by a different mechanism than the one under test.  The
YAML says nothing about any of this.

## Decision

A variant declares what it is supposed to provide:

```yaml
candidate:
  expected_capabilities: [spec-review, intent-router]
```

cc-eval then looks for **evidence in the trace**:

| Evidence | Status | Meaning |
|---|---|---|
| the runtime listed the capability **and** the run invoked it | `verified` | the capability was exercised |
| the runtime listed it but the run never invoked it | `not_invoked` | the skill was available but unused |
| the runtime does not list it | `contradicted` | the framework did not provide it |
| the runtime exposes no inventory | `unverified` | nothing can be said |

For Claude Code the inventory comes from the `system/init` event (`skills`,
`tools`) and an invocation is a `Skill` tool call in the assistant messages -
both are part of the raw stream that is stored.  For Codex there is no such
inventory, so the capability stays `unverified` unless the invocation itself
is visible.

The statuses are aggregated per variant across that variant's runs, and a
`capability_unverified` blocker makes the verdict `invalid` - a conclusion
cannot be drawn from a change that was never exercised.

## Consequences

* The rule applies to the fake runner too: it publishes the skills it loaded
  and the ones it invoked.
* A `not_invoked` result is the most valuable finding in the whole system -
  it says the framework change could not have caused the observed difference.
* The mechanism is generic: it works for hooks, MCP servers and prompts as
  long as the runtime leaves a trace of them.
