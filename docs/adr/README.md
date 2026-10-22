# Architecture decision records

Each ADR records one decision, its context and its consequences.  They are
short on purpose: the point is to make the *reasoning* recoverable, not to
restate the code.

| ADR | Decision |
|---|---|
| [0001](0001-golden-answers-live-outside-the-workspace.md) | The ground truth is physically separated from the agent-visible task |
| [0002](0002-single-variable-discipline.md) | A framework difference that was not declared is a confound, not a detail |
| [0003](0003-verdicts-are-rule-derived.md) | The verdict is computed from rules declared before the runs |
| [0004](0004-trace-normalisation.md) | One trace format, with missing data recorded instead of invented |
| [0005](0005-capability-verification.md) | A capability counts as used only when the trace proves it |
| [0006](0006-fake-runner-executes-skills.md) | The fake runner executes the framework's skills instead of returning canned answers |
| [0007](0007-isolation-boundaries.md) | Environment sanitisation is access control; a prompt request is not |
| [0008](0008-file-based-registry.md) | The registry is append-only files that can be exported to MLflow later |
