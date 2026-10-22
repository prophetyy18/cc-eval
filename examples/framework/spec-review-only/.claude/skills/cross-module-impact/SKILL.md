---
name: cross-module-impact
description: Identify every module affected by a change, including transitive dependencies.
---

# cross-module-impact

Identify every module affected by a change, including transitive dependencies.

## Procedure

1. Read every input file under `task/input/`.
2. Follow the dependency graph to the transitive closure.
3. Write the findings to `out/result.json`.

Output format: `{"findings": [{"id", "category", "severity", "description", "evidence"}]}` written to `out/result.json`.
