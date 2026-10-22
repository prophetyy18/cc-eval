---
name: intent-router
description: Classify raw user statements into intents, merging statements that describe the same topic.
---

# intent-router

Classify raw user statements into intents, merging statements that describe the same topic.

## Procedure

1. Read every input file under `task/input/`.
2. Merge statements that talk about the same topic into one intent.
3. Write the findings to `out/result.json`.

Output format: `{"findings": [{"id", "category", "severity", "description", "evidence"}]}` written to `out/result.json`.
