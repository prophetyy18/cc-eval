"""Aggregation of per-run evaluations into variant-level statistics.

Aggregation policy (macro average):

* every metric is first averaged **within a case** over its repetitions;
* the variant-level value is then the **mean over cases**.

Macro-averaging keeps a hard case from being drowned out by many easy ones
and makes the value comparable to a per-case table in the report.  The number
of repetitions behind every number is reported alongside it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..core.experiment import RunRecord
from ..core.models import EvaluationResult
from .stats import mean, stdev


@dataclass
class MetricSeries:
    """All values of one metric for one variant, grouped by case."""

    by_case: dict[str, list[float | None]] = field(default_factory=dict)
    per_case_means: dict[str, float | None] = field(default_factory=dict)
    macro_mean: float | None = None
    pooled_std: float | None = None
    n_cases: int = 0
    n_runs: int = 0


def collect_series(
    records: list[RunRecord],
    variant: str,
    metric: str,
) -> MetricSeries:
    """Collect one metric for one variant across all its runs."""
    series = MetricSeries()
    for record in records:
        if record.result.variant != variant:
            continue
        value = record.evaluation.metrics.get(metric)
        series.by_case.setdefault(record.result.case_id, []).append(value)
        if value is not None:
            series.n_runs += 1

    for case_id, values in series.by_case.items():
        series.per_case_means[case_id] = mean(values)
    case_means = [v for v in series.per_case_means.values() if v is not None]
    series.macro_mean = mean(case_means)
    # The spread that matters at this level is the run-to-run variation, so the
    # per-case standard deviations over repetitions are averaged.
    per_case_stds = [stdev([v for v in values if v is not None]) for values in series.by_case.values()]
    series.pooled_std = mean([s for s in per_case_stds if s is not None])
    series.n_cases = len(case_means)
    return series


def metric_names(records: list[RunRecord]) -> list[str]:
    """Every metric name that appears in any evaluation, in a stable order."""
    names: set[str] = set()
    for record in records:
        names.update(record.evaluation.metrics.keys())
    return sorted(names)


def case_metric_values(
    records: list[RunRecord],
    variant: str,
    metric: str,
) -> dict[str, list[float | None]]:
    """Per-repetition values of a metric, keyed by case id."""
    out: dict[str, list[float | None]] = defaultdict(list)
    for record in sorted(
        (r for r in records if r.result.variant == variant),
        key=lambda r: (r.result.case_id, r.result.repetition),
    ):
        out[record.result.case_id].append(record.evaluation.metrics.get(metric))
    return dict(out)


def evaluations_by_case(records: list[RunRecord]) -> dict[str, list[EvaluationResult]]:
    out: dict[str, list[EvaluationResult]] = defaultdict(list)
    for record in records:
        out[record.result.case_id].append(record.evaluation)
    return dict(out)
