"""Mechanical derivation of the experiment verdict.

The verdict is *never* a judgement call.  It is the output of the rules that
were declared in the experiment file before the runs happened:

1. any blocking confound -> ``invalid`` (the result is not attributable);
2. too few repetitions -> ``inconclusive``;
3. the primary metric must move by at least ``min_delta`` in the declared
   direction, otherwise -> ``inconclusive``;
4. the confidence interval must exclude zero, otherwise -> ``inconclusive``;
5. more regressed cases than allowed -> ``inconclusive`` (mixed evidence);
6. otherwise -> ``improved`` or ``regressed``.
"""

from __future__ import annotations

from ..core.models import ComparisonReport, Decision, ExperimentSpec, Verdict


def apply_decision_rules(spec: ExperimentSpec, report: ComparisonReport) -> Decision:
    """Derive the verdict for a comparison report."""
    rules = spec.decision_rules
    primary = rules.primary_metric

    decision = Decision(
        verdict=Verdict.INCONCLUSIVE,
        primary_metric=primary,
        rules_snapshot=rules,
    )

    aggregate = next((m for m in report.metrics if m.metric == primary), None)
    if aggregate is None:
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = f"the primary metric {primary!r} was not produced by any run"
        decision.reason_code = "reason.metric_missing"
        decision.reason_params = {"metric": primary}
        return decision

    decision.baseline_value = aggregate.baseline
    decision.candidate_value = aggregate.candidate
    decision.delta = aggregate.delta
    decision.confidence_low = aggregate.ci_low
    decision.confidence_high = aggregate.ci_high

    # -- 1. blocking confounds --------------------------------------------- #
    blockers = [c for c in report.confounds if c.severity == "blocker"]
    capability_blockers = [c for c in blockers if c.kind == "capability_unverified"]
    other_blockers = [c for c in blockers if c.kind != "capability_unverified"]

    if capability_blockers:
        decision.verdict = Verdict.INVALID
        details = "; ".join(c.detail for c in capability_blockers)
        decision.reason = (
            "the framework capability under test could not be shown to have been used: "
            + details
        )
        decision.reason_code = "reason.capability_blocked"
        decision.reason_params = {"details": details}
        return decision

    if other_blockers and rules.require_confound_free:
        decision.verdict = Verdict.INVALID
        details = "; ".join(c.detail for c in other_blockers)
        decision.reason = (
            "confounding variables were detected, so the difference cannot be attributed "
            "to the independent variable: "
            + details
        )
        decision.reason_code = "reason.confounded"
        decision.reason_params = {"details": details}
        return decision

    # -- 2. repetitions ------------------------------------------------------ #
    per_variant_runs = {
        variant: count
        for variant, count in report.run_counts.items()
        if variant in {spec.baseline.name, spec.candidate.name}
    }
    actual_reps = min(per_variant_runs.values()) if per_variant_runs else 0
    if actual_reps < rules.min_repetitions:
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = (
            f"only {actual_reps} repetition(s) per variant were run; "
            f"the rules require at least {rules.min_repetitions}"
        )
        decision.reason_code = "reason.too_few_repetitions"
        decision.reason_params = {"actual": actual_reps, "required": rules.min_repetitions}
        return decision

    # -- 3. effect size ----------------------------------------------------- #
    delta = aggregate.delta
    if delta is None:
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = f"the primary metric {primary!r} is missing on one of the sides"
        decision.reason_code = "reason.delta_missing"
        decision.reason_params = {"metric": primary}
        return decision

    if rules.direction == "higher_is_better":
        improved = delta > 0
        worsened = delta < 0
    else:
        improved = delta < 0
        worsened = delta > 0

    if abs(delta) < rules.min_delta:
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = (
            f"{primary} changed by {delta:+.4f}, which is below the pre-declared "
            f"minimum effect size of {rules.min_delta:+.4f}"
        )
        decision.reason_code = "reason.below_min_delta"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "min_delta": rules.min_delta,
        }
        return decision

    # -- 4. case regressions ------------------------------------------------ #
    regressed = [c for c in report.cases if c.status == "regressed"]
    guard_regressions = [
        (c.case_id, sorted(set(c.regressed_metrics) & set(rules.guard_metrics)))
        for c in report.cases
        if any(m in rules.guard_metrics for m in c.regressed_metrics)
    ]
    regression_rate = len(regressed) / len(report.cases) if report.cases else 0.0
    too_many = len(regressed) > rules.max_regressed_cases
    rate_exceeded = (
        rules.max_regression_rate is not None and regression_rate > rules.max_regression_rate
    )
    if too_many or rate_exceeded:
        decision.verdict = Verdict.INCONCLUSIVE
        rate_clause = (
            f"and a regression rate of {rules.max_regression_rate:.0%}. "
            if rules.max_regression_rate is not None
            else ". "
        )
        decision.reason = (
            f"{primary} moved by {delta:+.4f} but {len(regressed)} case(s) regressed "
            f"({regression_rate:.0%} of cases); the rules allow at most "
            f"{rules.max_regressed_cases} "
            + rate_clause
            + "See the per-case table before accepting this change."
        )
        decision.reason_code = "reason.too_many_regressions"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "count": len(regressed),
            "rate": regression_rate,
            "allowed": rules.max_regressed_cases,
            "rate_clause": rate_clause,
        }
        return decision

    if guard_regressions:
        decision.verdict = Verdict.INCONCLUSIVE
        details = "; ".join(
            f"{case_id} ({', '.join(metrics)})" for case_id, metrics in guard_regressions
        )
        decision.reason = (
            f"{primary} moved by {delta:+.4f} but {len(guard_regressions)} case(s) "
            f"regressed on a guard metric: {details}"
        )
        decision.reason_code = "reason.guard_regression"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "count": len(guard_regressions),
            "details": details,
        }
        return decision

    # -- 5. guard metrics --------------------------------------------------- #
    for guard in rules.guard_metrics:
        guard_aggregate = next((m for m in report.metrics if m.metric == guard), None)
        if guard_aggregate is None or guard_aggregate.delta is None:
            continue
        guard_worse = (
            guard_aggregate.delta > rules.guard_tolerance
            if guard_aggregate.direction == "lower_is_better"
            else guard_aggregate.delta < -rules.guard_tolerance
        )
        if guard_worse:
            decision.verdict = Verdict.INCONCLUSIVE
            decision.reason = (
                f"{primary} changed by {delta:+.4f} but the guard metric {guard} moved by "
                f"{guard_aggregate.delta:+.4f} "
                f"({guard_aggregate.baseline:.4f} -> {guard_aggregate.candidate:.4f}), "
                "which is outside the allowed tolerance. "
                + "A change that improves one metric while degrading another is not an "
                "unambiguous improvement."
            )
            decision.reason_code = "reason.guard_metric"
            decision.reason_params = {
                "metric": primary,
                "delta": delta,
                "guard": guard,
                "guard_delta": guard_aggregate.delta,
                "guard_baseline": guard_aggregate.baseline,
                "guard_candidate": guard_aggregate.candidate,
            }
            return decision

    # -- 6. confidence ------------------------------------------------------ #
    if rules.confidence.method == "paired_bootstrap" and aggregate.significant is False:
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = (
            f"{primary} changed by {delta:+.4f} but the {int(rules.confidence.alpha * 100)}% "
            f"confidence interval [{aggregate.ci_low:+.4f}, {aggregate.ci_high:+.4f}] "
            "includes zero"
        )
        decision.reason_code = "reason.ci_includes_zero"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "alpha": int(rules.confidence.alpha * 100),
            "low": aggregate.ci_low,
            "high": aggregate.ci_high,
        }
        return decision

    # -- 7. verdict --------------------------------------------------------- #
    if improved:
        decision.verdict = Verdict.IMPROVED
        decision.reason = (
            f"{primary} improved by {delta:+.4f} "
            f"({aggregate.baseline:.4f} -> {aggregate.candidate:.4f}) with no blocked "
            "confound and no disallowed case regression"
        )
        decision.reason_code = "reason.improved"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "baseline": aggregate.baseline,
            "candidate": aggregate.candidate,
        }
    elif worsened:
        decision.verdict = Verdict.REGRESSED
        decision.reason = (
            f"{primary} regressed by {delta:+.4f} "
            f"({aggregate.baseline:.4f} -> {aggregate.candidate:.4f})"
        )
        decision.reason_code = "reason.regressed"
        decision.reason_params = {
            "metric": primary,
            "delta": delta,
            "baseline": aggregate.baseline,
            "candidate": aggregate.candidate,
        }
    else:  # pragma: no cover - delta == 0 is caught by the min_delta gate
        decision.verdict = Verdict.INCONCLUSIVE
        decision.reason = f"{primary} did not change"
        decision.reason_code = "reason.no_change"
        decision.reason_params = {"metric": primary}

    if other_blockers:
        details = "; ".join(c.detail for c in other_blockers)
        decision.notes = ("non-blocking confounds were recorded: " + details)
        decision.notes_codes = [
            {"code": "note.non_blocking_confounds", "params": {"details": details}}
        ]
    return decision
