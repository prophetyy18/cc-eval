"""JSON report rendering.

The JSON report is the machine-readable half of the deliverable: it contains
the full comparison plus the metadata needed to reproduce it (dataset hash,
evaluator version, framework fingerprints, decision rules snapshot).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.models import ComparisonReport, ExperimentSpec


def build_payload(
    report: ComparisonReport,
    spec: ExperimentSpec | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the JSON report payload."""
    payload: dict[str, Any] = {
        "schema": "cc-eval/report/v1",
        "report": report.model_dump(mode="json"),
    }
    if spec is not None:
        payload["experiment"] = {
            "id": spec.id,
            "title": spec.title,
            "hypothesis": spec.hypothesis,
            "independent_variable": spec.independent_variable,
            "framework_change": spec.framework_change,
            "baseline": spec.baseline.model_dump(mode="json"),
            "candidate": spec.candidate.model_dump(mode="json"),
            "controls": spec.controls.model_dump(mode="json"),
            "expected_changes": spec.expected_changes,
            "decision_rules": spec.decision_rules.model_dump(mode="json"),
            "references": spec.references,
        }
    if extra:
        payload.update(extra)
    return payload


def render_json(
    report: ComparisonReport,
    spec: ExperimentSpec | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    return json.dumps(build_payload(report, spec, extra), ensure_ascii=False, indent=2, default=str)


def write_json(
    report: ComparisonReport,
    path: Path | str,
    spec: ExperimentSpec | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_json(report, spec, extra), encoding="utf-8")
    return target
