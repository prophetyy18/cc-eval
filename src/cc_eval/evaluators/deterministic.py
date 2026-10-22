"""Deterministic evaluator.

Everything this evaluator reports is computed by rule, not by a model:

* did the agent produce a parseable, schema-shaped document?
* which of the known defects did it report (recall)?
* how many of its findings were not defects (precision)?
* did it report the requirements that were missing?

The evaluator is deliberately strict: an unparseable output is scored as an
invalid run, not as a run that found nothing.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.models import BenchmarkCase, EvaluationResult, GoldenItem, RunResult
from .base import empty_result, extract_findings
from .matching import MATCHER_VERSION, match_findings, normalise_category

EVALUATOR_VERSION = "deterministic-v1"


class DeterministicEvaluator:
    """Rule-based scoring of agent findings against the case oracle."""

    name = "deterministic"
    version = EVALUATOR_VERSION

    def evaluate(self, case: BenchmarkCase, result: RunResult) -> EvaluationResult:
        findings, error = extract_findings(result, case.bundle.expected_output_path)
        if error:
            evaluation = empty_result(case, result, self.name, self.version, error)
            evaluation.metrics = {
                "valid_output": 0.0,
                "recall": 0.0,
                "precision": 0.0,
                "f1": 0.0,
                "false_positive_count": None,
                "false_negative_count": None,
                "finding_count": 0.0,
                "run_ok": 1.0 if result.ok else 0.0,
            }
            return evaluation

        golden = _expected_items(case)
        outcome = match_findings(golden, findings, matcher_version=MATCHER_VERSION)

        tp = len(outcome.matched)
        fp = len(outcome.false_positives)
        fn = len(outcome.false_negatives)
        # With no ground-truth items there is nothing to miss, so recall is 1.
        # Precision still drops to 0 when the agent invented findings.
        recall = tp / len(golden) if golden else 1.0
        precision = tp / (tp + fp) if (tp + fp) else (1.0 if not golden else None)
        f1 = (
            (2 * precision * recall) / (precision + recall)
            if precision is not None and recall is not None and (precision + recall) > 0
            else 0.0
        )

        metrics: dict[str, float | None] = {
            "valid_output": 1.0,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "false_positive_count": float(fp),
            "false_negative_count": float(fn),
            "finding_count": float(len(findings)),
            "run_ok": 1.0 if result.ok else 0.0,
            "duration_sec": (result.duration_ms / 1000.0) if result.duration_ms is not None else None,
            "total_tokens": float(result.usage.total_tokens) if result.usage.total_tokens is not None else None,
            "cost_usd": result.usage.cost_usd,
        }

        _add_subset_metrics(metrics, case, outcome, golden)

        notes: list[str] = []
        if result.usage.source == "unavailable":
            notes.append("token usage was not reported by the runtime")
        if result.usage.source == "estimated":
            notes.append("token usage is an cc-eval estimate, not a runtime measurement")

        evaluation = EvaluationResult(
            run_id=result.run_id,
            experiment_id=result.experiment_id,
            case_id=case.id,
            variant=result.variant,
            repetition=result.repetition,
            evaluator=self.name,
            evaluator_version=f"{self.version}+{MATCHER_VERSION}",
            metrics=metrics,
            matched=outcome.matched,
            unmatched_golden=outcome.unmatched_golden,
            unmatched_predictions=outcome.unmatched_predictions,
            notes=notes,
        )
        return evaluation


def _expected_items(case: BenchmarkCase) -> list[GoldenItem]:
    """All ground-truth items the agent is expected to produce."""
    return [
        *case.oracle.known_defects,
        *case.oracle.missing_constraints,
        *case.oracle.expected_entities,
    ]


def _add_subset_metrics(
    metrics: dict[str, float | None],
    case: BenchmarkCase,
    outcome: Any,
    golden: list[GoldenItem],
) -> None:
    """Add per-subset recall/precision without breaking the global one-to-one match."""
    subsets = [
        ("constraint", case.oracle.missing_constraints),
        ("entity", case.oracle.expected_entities),
        ("defect", case.oracle.known_defects),
    ]
    matched_ids = {m.golden_id for m in outcome.matched}
    for prefix, items in subsets:
        if not items:
            continue
        subset_ids = {i.id for i in items}
        matched = len(subset_ids & matched_ids)
        categories = {normalise_category(i.category) for i in items}
        fp = sum(
            1
            for p in outcome.false_positives
            if normalise_category(str(p.get("category", ""))) in categories
        )
        metrics[f"{prefix}_recall"] = matched / len(items)
        denominator = matched + fp
        metrics[f"{prefix}_precision"] = (matched / denominator) if denominator else None


def summarise_output(result: RunResult, expected_path: str) -> dict[str, Any] | None:
    """Helper used by the CLI to show what the agent actually produced."""
    content = result.output_files.get(expected_path)
    if content is None:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None
