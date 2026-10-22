"""Comparison, aggregation and decision logic."""

from __future__ import annotations

from .aggregate import MetricSeries, case_metric_values, collect_series, metric_names
from .compare import compare
from .decision import apply_decision_rules
from .stats import mean, paired_bootstrap_ci, relative_delta, stdev

__all__ = [
    "MetricSeries",
    "apply_decision_rules",
    "case_metric_values",
    "collect_series",
    "compare",
    "mean",
    "metric_names",
    "paired_bootstrap_ci",
    "relative_delta",
    "stdev",
]
