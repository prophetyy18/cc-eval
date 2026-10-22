"""Comparison of two variants and the confound analysis that qualifies it.

The comparison is deliberately not just "a number went up".  It produces:

* the metric table (baseline, candidate, delta, spread, confidence interval);
* the per-case table (which cases improved, which regressed, which are noisy);
* a **confound report** - the conditions under which the delta may not be
  attributable to the independent variable;
* a verdict derived mechanically from the pre-declared decision rules.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from pathlib import Path

from ..core.experiment import RunRecord
from ..core.models import (
    CaseComparison,
    ComparisonReport,
    Confound,
    Decision,
    ExperimentSpec,
    MetricAggregate,
)
from .aggregate import case_metric_values, collect_series, metric_names
from .decision import apply_decision_rules
from .stats import mean, paired_bootstrap_ci, relative_delta, stdev

# Metrics where a smaller number is better.
_LOWER_IS_BETTER = {"cost_usd", "duration_sec", "total_tokens", "false_positive_count",
                    "false_negative_count", "execution_time_sec"}

# Metrics that describe the *quality* of the result.  Only these are used for
# the per-case "regressed on" column; cost and volume metrics (tokens, number
# of findings) are reported globally instead, because finding more defects is
# not a regression and spending more tokens is a cost, not a quality loss.
_QUALITY_METRICS = {
    "recall",
    "precision",
    "f1",
    "false_positive_count",
    "false_negative_count",
    "rubric_score",
    "judge_score",
    "valid_output",
}


def _is_quality_metric(metric: str) -> bool:
    if metric in _QUALITY_METRICS:
        return True
    # Subset metrics such as defect_recall, entity_precision, ...
    return metric.split("_", 1)[0] in {"defect", "entity", "constraint"}

# Provenance fields that must be identical on both sides of the comparison.
_CONTROL_FIELDS = {
    "runtime": "runtime_mismatch",
    "runtime_version": "control_mismatch",
    "model": "model_mismatch",
    "dataset_hash": "dataset_mismatch",
    "dataset_version": "dataset_mismatch",
    "evaluator_version": "evaluator_mismatch",
    "order_policy": "control_mismatch",
}


def compare(
    spec: ExperimentSpec,
    records: list[RunRecord],
    *,
    tree_diff=None,
    dataset_version: str = "",
    dataset_hash: str = "",
    evaluator: str = "",
) -> ComparisonReport:
    """Build the comparison report for an experiment."""
    baseline_name = spec.baseline.name
    candidate_name = spec.candidate.name
    primary = spec.decision_rules.primary_metric

    report = ComparisonReport(
        experiment_id=spec.id,
        baseline_variant=baseline_name,
        candidate_variant=candidate_name,
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        evaluator=evaluator,
    )

    if not records:
        report.decision = Decision(
            verdict="inconclusive",
            reason="no runs were recorded for this experiment",
            primary_metric=primary,
        )
        report.warnings.append("the experiment has no runs")
        return report

    # -- metric table ------------------------------------------------------- #
    for metric in metric_names(records):
        baseline_series = collect_series(records, baseline_name, metric)
        candidate_series = collect_series(records, candidate_name, metric)
        direction = spec.decision_rules.direction if metric == primary else (
            "lower_is_better" if metric in _LOWER_IS_BETTER else "higher_is_better"
        )

        baseline_value = baseline_series.macro_mean
        candidate_value = candidate_series.macro_mean
        delta = None
        if baseline_value is not None and candidate_value is not None:
            delta = candidate_value - baseline_value

        ci_low = ci_high = None
        significant: bool | None = None
        confidence = spec.decision_rules.confidence
        if confidence.method == "paired_bootstrap":
            pairs_baseline = [
                baseline_series.per_case_means.get(case_id)
                for case_id in sorted(baseline_series.per_case_means)
            ]
            pairs_candidate = [
                candidate_series.per_case_means.get(case_id)
                for case_id in sorted(baseline_series.per_case_means)
            ]
            ci_low, ci_high = paired_bootstrap_ci(
                pairs_baseline,
                pairs_candidate,
                alpha=confidence.alpha,
                iterations=confidence.iterations,
                seed=confidence.seed,
            )
            if ci_low is not None and delta is not None:
                significant = (ci_low > 0 and delta > 0) or (ci_high < 0 and delta < 0)

        report.metrics.append(
            MetricAggregate(
                metric=metric,
                baseline=baseline_value,
                candidate=candidate_value,
                delta=delta,
                delta_pct=relative_delta(baseline_value, candidate_value),
                baseline_std=baseline_series.pooled_std,
                candidate_std=candidate_series.pooled_std,
                baseline_n=baseline_series.n_runs,
                candidate_n=candidate_series.n_runs,
                direction=direction,
                ci_low=ci_low,
                ci_high=ci_high,
                significant=significant,
            )
        )

    # -- per-case table ----------------------------------------------------- #
    baseline_values = case_metric_values(records, baseline_name, primary)
    candidate_values = case_metric_values(records, candidate_name, primary)
    threshold = spec.decision_rules.case_delta_threshold
    for case_id in sorted(set(baseline_values) | set(candidate_values)):
        b_series = baseline_values.get(case_id, [])
        c_series = candidate_values.get(case_id, [])
        b_value = mean(b_series)
        c_value = mean(c_series)
        delta = None
        if b_value is not None and c_value is not None:
            delta = c_value - b_value
        status = "unknown"
        if delta is not None:
            if delta > threshold:
                status = "improved"
            elif delta < -threshold:
                status = "regressed"
            else:
                status = "unchanged"

        # A case can hold the primary metric steady and still regress on
        # another tracked metric (a new false positive, for example).
        regressed_metrics: list[str] = []
        for metric in metric_names(records):
            if metric == primary or not _is_quality_metric(metric):
                continue
            b_case = mean(case_metric_values(records, baseline_name, metric).get(case_id, []))
            c_case = mean(case_metric_values(records, candidate_name, metric).get(case_id, []))
            if b_case is None or c_case is None:
                continue
            lower_is_better = metric in _LOWER_IS_BETTER
            worse = c_case > b_case if lower_is_better else c_case < b_case
            if worse and abs(c_case - b_case) > threshold:
                regressed_metrics.append(metric)

        notes: list[str] = []
        if regressed_metrics:
            notes.append(
                "regressed on " + ", ".join(sorted(regressed_metrics)) + " while the primary metric held"
            )
        b_var = stdev([v for v in b_series if v is not None])
        c_var = stdev([v for v in c_series if v is not None])
        for label, variance in (("baseline", b_var), ("candidate", c_var)):
            if variance is not None and variance > 0.1:
                notes.append(f"high {label} variance across repetitions ({variance:.3f})")
        if b_value is not None and c_value is not None and abs(delta or 0.0) < 0.05:
            notes.append("the difference is within noise; manual review recommended")

        report.cases.append(
            CaseComparison(
                case_id=case_id,
                baseline_score=b_value,
                candidate_score=c_value,
                delta=delta,
                status=status,
                baseline_per_repetition=b_series,
                candidate_per_repetition=c_series,
                baseline_variance=b_var,
                candidate_variance=c_var,
                regressed_metrics=sorted(regressed_metrics),
                notes=notes,
            )
        )

    # -- cost / duration deltas -------------------------------------------- #
    report.cost_delta_usd = _metric_delta(report, "cost_usd")
    report.duration_delta_sec = _metric_delta(report, "duration_sec")

    # -- confounds ---------------------------------------------------------- #
    report.confounds.extend(_detect_control_confounds(spec, records))
    report.confounds.extend(_detect_framework_confounds(spec, tree_diff))
    report.confounds.extend(_detect_capability_confounds(spec, records))
    report.confounds.extend(_detect_repetition_confounds(spec, records))

    report.run_counts = {
        "baseline": sum(1 for r in records if r.result.variant == baseline_name),
        "candidate": sum(1 for r in records if r.result.variant == candidate_name),
        "total": len(records),
    }
    report.framework_fingerprints = {
        r.result.variant: r.fingerprint for r in records if r.fingerprint
    }

    report.manual_review_cases = [
        case.case_id
        for case in report.cases
        if any("manual review" in note for note in case.notes)
    ]

    # -- verdict ------------------------------------------------------------ #
    report.decision = apply_decision_rules(spec, report)

    return report


def _metric_delta(report: ComparisonReport, metric: str) -> float | None:
    for aggregate in report.metrics:
        if aggregate.metric == metric and aggregate.delta is not None:
            return aggregate.delta
    return None


# --------------------------------------------------------------------------- #
# confound detection
# --------------------------------------------------------------------------- #


def _observed_values(records: list[RunRecord], variant: str, field: str) -> set[str]:
    values: set[str] = set()
    for record in records:
        if record.result.variant != variant:
            continue
        value = record.result.provenance.get(field)
        if value is not None:
            values.add(str(value))
    return values


def _detect_control_confounds(spec: ExperimentSpec, records: list[RunRecord]) -> list[Confound]:
    confounds: list[Confound] = []
    for field, kind in _CONTROL_FIELDS.items():
        baseline_values = _observed_values(records, spec.baseline.name, field)
        candidate_values = _observed_values(records, spec.candidate.name, field)
        if not baseline_values or not candidate_values:
            continue
        if baseline_values != candidate_values:
            confounds.append(
                Confound(
                    kind=kind,  # type: ignore[arg-type]
                    severity="blocker",
                    detail=(
                        f"{field} differs between the variants: "
                        f"baseline={sorted(baseline_values)} candidate={sorted(candidate_values)}. "
                        "The measured difference cannot be attributed to the independent variable."
                    ),
                    where=field,
                )
            )
    return confounds


def _detect_framework_confounds(
    spec: ExperimentSpec, tree_diff
) -> list[Confound]:
    if tree_diff is None:
        return []
    confounds: list[Confound] = []
    changed = tree_diff.changed_files()
    if not changed:
        same_config = spec.baseline.config == spec.candidate.config
        if same_config:
            confounds.append(
                Confound(
                    kind="other",
                    severity="blocker",
                    detail=(
                        "the baseline and candidate frameworks are byte-identical and their "
                        "configurations are equal: the independent variable is not present in "
                        "either side, so the experiment cannot measure anything"
                    ),
                )
            )
        return confounds

    expected = list(spec.expected_changes)
    if not expected:
        confounds.append(
            Confound(
                kind="unexpected_framework_diff",
                severity="warning",
                detail=(
                    f"the two variants differ in {len(changed)} file(s) but no "
                    "expected_changes were declared, so it cannot be shown that the "
                    "independent variable is the only difference"
                ),
                where=", ".join(changed[:10]),
            )
        )
        return confounds

    unexpected = [
        path for path in changed if not any(_matches(path, pattern) for pattern in expected)
    ]
    if unexpected:
        confounds.append(
            Confound(
                kind="unexpected_framework_diff",
                severity="blocker",
                detail=(
                    f"{len(unexpected)} file(s) differ outside the declared expected_changes: "
                    + ", ".join(unexpected[:10])
                    + ". These changes may explain the observed difference."
                ),
                where=", ".join(unexpected[:10]),
            )
        )
    return confounds


def _matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if fnmatch.fnmatch(path, pattern):
        return True
    if pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"):
        return True
    if "**/" in pattern:
        return fnmatch.fnmatch(path, pattern.replace("**/", "", 1))
    return False


def _detect_capability_confounds(spec: ExperimentSpec, records: list[RunRecord]) -> list[Confound]:
    """Check that the capabilities under test were actually exercised.

    A capability is judged at the **variant** level: a skill that only applies
    to one case type is not invoked in the runs of the other case types, so
    the statuses of all runs of a variant are combined.  The capability counts
    as verified when at least one run provides positive evidence that it was
    used.
    """
    confounds: list[Confound] = []
    required = set(spec.decision_rules.require_verified_capabilities)

    # variant -> capability -> worst status observed across that variant's runs
    observed: dict[str, dict[str, str]] = defaultdict(dict)
    evidence: dict[tuple[str, str], str] = {}
    for record in records:
        trace = record.result.trace
        if trace is None:
            continue
        for capability in trace.capabilities:
            key = (record.result.variant, capability.name)
            current = observed[record.result.variant].get(capability.name)
            if current == "verified":
                # Positive evidence from an earlier run is never downgraded.
                continue
            if capability.status == "verified":
                observed[record.result.variant][capability.name] = "verified"
                evidence[key] = capability.evidence
                continue
            if current is None or _severity_rank(capability.status) > _severity_rank(current):
                observed[record.result.variant][capability.name] = capability.status
                evidence[key] = capability.evidence

    for variant in (spec.baseline, spec.candidate):
        statuses = observed.get(variant.name, {})
        declared = list(variant.expected_capabilities)
        for name in declared:
            status = statuses.get(name, "unverified")
            if status == "verified":
                continue
            required_here = name in required
            confounds.append(
                Confound(
                    kind="capability_unverified",
                    severity="blocker" if required_here else "warning",
                    detail=(
                        f"capability {name!r} of variant {variant.name!r} is {status}; "
                        + (
                            "the experiment declares this capability as required, so the "
                            "result must not be attributed to it"
                            if required_here
                            else "no evidence was found that the framework change was "
                            "actually exercised by the agent"
                        )
                        + (
                            f" ({evidence.get((variant.name, name), '')})"
                            if evidence.get((variant.name, name))
                            else ""
                        )
                    ),
                    where=f"{variant.name}:{name}",
                )
            )
        if declared and not statuses:
            confounds.append(
                Confound(
                    kind="capability_unverified",
                    severity="warning",
                    detail=(
                        f"variant {variant.name!r} declares capabilities "
                        f"{declared} but the runtime produced no capability evidence at all"
                    ),
                    where=variant.name,
                )
            )
    return confounds


def _severity_rank(status: str) -> int:
    return {"contradicted": 3, "not_invoked": 2, "unverified": 1}.get(status, 0)


def _detect_repetition_confounds(spec: ExperimentSpec, records: list[RunRecord]) -> list[Confound]:
    confounds: list[Confound] = []
    counts: dict[str, set[int]] = defaultdict(set)
    for record in records:
        counts[record.result.variant].add(record.result.repetition)
    if len(counts) < 2:
        return confounds
    baseline_reps = counts.get(spec.baseline.name, set())
    candidate_reps = counts.get(spec.candidate.name, set())
    if baseline_reps != candidate_reps:
        confounds.append(
            Confound(
                kind="repetition_mismatch",
                severity="blocker",
                detail=(
                    f"the variants were not run the same number of times: "
                    f"baseline={sorted(baseline_reps)} candidate={sorted(candidate_reps)}"
                ),
            )
        )
    return confounds
