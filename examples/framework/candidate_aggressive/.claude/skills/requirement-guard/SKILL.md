---
name: requirement-guard
description: Identify user requirements that the specification fails to implement.
---

# requirement-guard

Identify user requirements that the specification fails to implement.

## Procedure

1. Read every input file under `task/input/`.
2. Only treat a requirement as implemented when it is stated normatively.
3. Write the findings to `out/result.json`.

Output format: `{"findings": [{"id", "category", "severity", "description", "evidence"}]}` written to `out/result.json`.
