"""Markdown report rendering.

The report deliberately does **not** stop at an average.  It shows:

* the metric table with the confidence interval;
* which cases improved and which regressed (individually, by name);
* the cost and time movement;
* the confound analysis - what may invalidate the conclusion;
* the verdict and the exact rule that produced it;
* what a human still has to look at.
"""

from __future__ import annotations

from ..core.models import ComparisonReport, ExperimentSpec

_VERDICT_LABEL = {
    "improved": "IMPROVED",
    "regressed": "REGRESSED",
    "inconclusive": "INCONCLUSIVE",
    "invalid": "INVALID (not attributable)",
    "pending": "PENDING",
}


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _metric_display(metric: str) -> str:
    return {
        "recall": "Recall",
        "precision": "Precision",
        "f1": "F1",
        "false_positive_count": "False positives",
        "false_negative_count": "False negatives",
        "valid_output": "Valid output",
        "total_tokens": "Tokens",
        "cost_usd": "Cost (USD)",
        "duration_sec": "Duration (s)",
        "rubric_score": "Rubric score",
        "judge_score": "Judge score (opinion)",
    }.get(metric, metric)


def render_markdown(
    report: ComparisonReport,
    spec: ExperimentSpec | None = None,
) -> str:
    """Render the full Markdown report."""
    lines: list[str] = []
    decision = report.decision

    lines.append(f"# cc-eval report - {report.experiment_id}")
    lines.append("")
    if spec is not None:
        if spec.title:
            lines.append(f"**{spec.title}**")
            lines.append("")
        lines.append(f"**Hypothesis:** {spec.hypothesis.strip()}")
        lines.append("")
        lines.append(f"**Independent variable:** {spec.independent_variable}")
        lines.append("")
        if spec.framework_change:
            lines.append(f"**Framework change under test:** {spec.framework_change}")
            lines.append("")

    lines.append(f"**Verdict: {_VERDICT_LABEL.get(decision.verdict.value, decision.verdict.value)}**")
    lines.append("")
    if decision.reason:
        lines.append(f"> {decision.reason}")
        lines.append("")

    # -- provenance ---------------------------------------------------------- #
    lines.append("## Measurement conditions")
    lines.append("")
    lines.append("| Item | Value |")
    lines.append("|---|---|")
    lines.append(f"| Dataset version | {report.dataset_version or 'n/a'} |")
    lines.append(f"| Dataset hash | `{report.dataset_hash[:16] or 'n/a'}` |")
    lines.append(f"| Evaluator | {report.evaluator or 'n/a'} |")
    lines.append(f"| Baseline variant | {report.baseline_variant} |")
    lines.append(f"| Candidate variant | {report.candidate_variant} |")
    lines.append(f"| Runs | {report.run_counts.get('baseline', 0)} baseline / "
                 f"{report.run_counts.get('candidate', 0)} candidate |")
    for variant, fingerprint in sorted(report.framework_fingerprints.items()):
        lines.append(f"| Framework fingerprint ({variant}) | `{fingerprint[:16]}` |")
    lines.append("")

    # -- metric table -------------------------------------------------------- #
    lines.append("## Metric summary")
    lines.append("")
    lines.append("| Metric | Baseline | Candidate | Delta | Delta % | 95% CI | Direction |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for aggregate in report.metrics:
        ci = (
            f"[{_fmt(aggregate.ci_low)}, {_fmt(aggregate.ci_high)}]"
            if aggregate.ci_low is not None
            else "n/a"
        )
        direction = "higher is better" if aggregate.direction == "higher_is_better" else "lower is better"
        lines.append(
            f"| {_metric_display(aggregate.metric)} | {_fmt(aggregate.baseline)} | "
            f"{_fmt(aggregate.candidate)} | {_fmt(aggregate.delta)} | "
            f"{_fmt_pct(aggregate.delta_pct)} | {ci} | {direction} |"
        )
    lines.append("")
    lines.append(
        "_Values are macro-averages: each case is averaged over its repetitions first, "
        "then the cases are averaged._"
    )
    lines.append("")

    # -- per case ------------------------------------------------------------ #
    lines.append("## Per-case results")
    lines.append("")
    lines.append("| Case | Baseline | Candidate | Delta | Status | Regressed on | Notes |")
    lines.append("|---|---:|---:|---:|---|---|---|")
    for case in report.cases:
        notes = "; ".join(case.notes) if case.notes else ""
        regressed_on = ", ".join(case.regressed_metrics) if case.regressed_metrics else "-"
        lines.append(
            f"| {case.case_id} | {_fmt(case.baseline_score)} | {_fmt(case.candidate_score)} | "
            f"{_fmt(case.delta)} | {case.status} | {regressed_on} | {notes} |"
        )
    lines.append("")

    improved = [c.case_id for c in report.cases if c.status == "improved"]
    regressed = [c.case_id for c in report.cases if c.status == "regressed"]
    unchanged = [c.case_id for c in report.cases if c.status == "unchanged"]
    other_regressions = [
        f"{c.case_id} ({', '.join(c.regressed_metrics)})"
        for c in report.cases
        if c.regressed_metrics
    ]
    lines.append("### Movement by case")
    lines.append("")
    lines.append(f"- **Improved ({len(improved)}):** {', '.join(improved) if improved else '_none_'}")
    lines.append(f"- **Regressed ({len(regressed)}):** {', '.join(regressed) if regressed else '_none_'}")
    lines.append(f"- **Unchanged ({len(unchanged)}):** {', '.join(unchanged) if unchanged else '_none_'}")
    if other_regressions:
        lines.append(
            "- **Regressed on a non-primary metric:** " + "; ".join(other_regressions)
        )
    lines.append("")

    # -- variance ------------------------------------------------------------ #
    lines.append("## Run-to-run variation")
    lines.append("")
    lines.append("| Case | Baseline per repetition | Candidate per repetition | Baseline variance | Candidate variance |")
    lines.append("|---|---|---|---:|---:|")
    for case in report.cases:
        b = ", ".join(_fmt(v) for v in case.baseline_per_repetition) or "n/a"
        c = ", ".join(_fmt(v) for v in case.candidate_per_repetition) or "n/a"
        lines.append(
            f"| {case.case_id} | {b} | {c} | {_fmt(case.baseline_variance)} | "
            f"{_fmt(case.candidate_variance)} |"
        )
    lines.append("")

    # -- cost ---------------------------------------------------------------- #
    lines.append("## Cost and time")
    lines.append("")
    lines.append(f"- Cost delta: {_fmt(report.cost_delta_usd, 4)} USD")
    lines.append(f"- Duration delta: {_fmt(report.duration_delta_sec, 2)} seconds")
    cost_metric = next((m for m in report.metrics if m.metric == "cost_usd"), None)
    if cost_metric is not None and cost_metric.baseline is None:
        lines.append(
            "- Cost comparison unavailable: the runtime does not report cost "
            "(the value is explicitly *n/a*, not zero)."
        )
    lines.append("")

    # -- confounds ----------------------------------------------------------- #
    lines.append("## Confound analysis")
    lines.append("")
    if not report.confounds:
        lines.append("No confounds were detected.")
    else:
        lines.append("| Severity | Kind | Detail |")
        lines.append("|---|---|---|")
        for confound in report.confounds:
            lines.append(f"| {confound.severity} | {confound.kind} | {confound.detail} |")
    lines.append("")

    # -- verdict ------------------------------------------------------------- #
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{_VERDICT_LABEL.get(decision.verdict.value, decision.verdict.value)}** - {decision.reason}")
    lines.append("")
    if spec is not None:
        lines.append(
            "The verdict was derived from the decision rules declared in the experiment "
            "file *before* the runs:"
        )
        lines.append("")
        lines.append("```yaml")
        lines.append(
            "\n".join(
                f"{key}: {value}"
                for key, value in spec.decision_rules.model_dump(mode="json").items()
            )
        )
        lines.append("```")
        lines.append("")
    if decision.notes:
        lines.append(f"> {decision.notes}")
        lines.append("")

    # -- manual review ------------------------------------------------------- #
    lines.append("## Needs human review")
    lines.append("")
    if report.manual_review_cases:
        for case_id in report.manual_review_cases:
            lines.append(f"- `{case_id}` - the difference is within noise")
    else:
        lines.append("- No case was flagged as ambiguous.")
    lines.append("")
    lines.append(
        "An experiment passing does **not** mean the framework change should be merged. "
        "Review the regressed cases, the confound table and the cost movement first."
    )
    lines.append("")

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)
