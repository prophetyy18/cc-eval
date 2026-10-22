---
name: spec-review
description: Review a specification document and report defects (missing event ordering, undefined failure behaviour, wall-clock dependence, unsynchronised concurrent writes).
---

# spec-review

Review a specification document and report defects (missing event ordering, undefined failure behaviour, wall-clock dependence, unsynchronised concurrent writes).

## Procedure

1. Read every input file under `task/input/`.
2. Report each defect as a finding with `id`, `category`, `severity`, `description` and `evidence`.
3. Write the findings to `out/result.json`.

Output format: `{"findings": [{"id", "category", "severity", "description", "evidence"}]}` written to `out/result.json`.
