"""Core experiment engine: schemas, registry, materialisation, provenance.

The re-exports are lazy to keep the import graph acyclic."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "Benchmark",
    "BenchmarkCase",
    "ComparisonReport",
    "Decision",
    "EvaluationResult",
    "ExperimentSpec",
    "RunConfig",
    "RunResult",
    "Trace",
    "TraceEvent",
    "CapabilityStatus",
    "Verdict",
    "Oracle",
    "GoldenItem",
    "TaskBundle",
    "Usage",
    "MetricAggregate",
    "CaseComparison",
    "Confound",
    "DecisionRules",
    "Controls",
    "Variant",
    "FrameworkRef",
]

_LAZY = {
    "Benchmark": "cc_eval.core.models.Benchmark",
    "BenchmarkCase": "cc_eval.core.models.BenchmarkCase",
    "ComparisonReport": "cc_eval.core.models.ComparisonReport",
    "Decision": "cc_eval.core.models.Decision",
    "EvaluationResult": "cc_eval.core.models.EvaluationResult",
    "ExperimentSpec": "cc_eval.core.models.ExperimentSpec",
    "RunConfig": "cc_eval.core.models.RunConfig",
    "RunResult": "cc_eval.core.models.RunResult",
    "Trace": "cc_eval.core.models.Trace",
    "TraceEvent": "cc_eval.core.models.TraceEvent",
    "CapabilityStatus": "cc_eval.core.models.CapabilityStatus",
    "Verdict": "cc_eval.core.models.Verdict",
    "Oracle": "cc_eval.core.models.Oracle",
    "GoldenItem": "cc_eval.core.models.GoldenItem",
    "TaskBundle": "cc_eval.core.models.TaskBundle",
    "Usage": "cc_eval.core.models.Usage",
    "MetricAggregate": "cc_eval.core.models.MetricAggregate",
    "CaseComparison": "cc_eval.core.models.CaseComparison",
    "Confound": "cc_eval.core.models.Confound",
    "DecisionRules": "cc_eval.core.models.DecisionRules",
    "Controls": "cc_eval.core.models.Controls",
    "Variant": "cc_eval.core.models.Variant",
    "FrameworkRef": "cc_eval.core.models.FrameworkRef",
}

__getattr__ = lazy_exports(_LAZY)
