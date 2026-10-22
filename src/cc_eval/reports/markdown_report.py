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

from typing import Any

from ..core.models import ComparisonReport, ExperimentSpec
from .i18n import Translator, resolve_language

_VERDICT_LABEL = {
    "improved": "IMPROVED",
    "regressed": "REGRESSED",
    "inconclusive": "INCONCLUSIVE",
    "invalid": "INVALID (not attributable)",
    "pending": "PENDING",
}

_VERDICT_LABEL_ZH = {
    "improved": "改善",
    "regressed": "退化",
    "inconclusive": "不确定",
    "invalid": "无效(无法归因)",
    "pending": "待定",
}


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _metric_display(metric: str, language: str = "zh") -> str:
    """The human readable name of a metric (shared with the HTML report)."""
    from .i18n import metric_label

    return metric_label(metric, language)


def _resolve_notes(case: Any, translator: Translator) -> list[str]:
    """The notes of a case, in the reader's language."""
    resolved = []
    for index, note in enumerate(case.notes):
        codes = getattr(case, "note_codes", []) or []
        code = codes[index] if index < len(codes) else None
        if code:
            resolved.append(
                translator.m(code.get("code", ""), **(code.get("params") or {})) or note
            )
        else:
            resolved.append(note)
    return resolved


def render_markdown(
    report: ComparisonReport,
    spec: ExperimentSpec | None = None,
    language: str | None = None,
) -> str:
    """Render the full Markdown report."""
    translator = Translator(resolve_language(language))
    labels = _VERDICT_LABEL_ZH if translator.language == "zh" else _VERDICT_LABEL
    lines: list[str] = []
    decision = report.decision

    # The confound details first: the verdict reason quotes them.
    confound_details = [
        translator.m(c.code, **(c.params or {})) or c.detail for c in report.confounds
    ]
    reason_params = dict(decision.reason_params)
    if decision.reason_code in {"reason.capability_blocked", "reason.confounded"}:
        want_capability = decision.reason_code == "reason.capability_blocked"
        details = [
            detail
            for confound, detail in zip(report.confounds, confound_details)
            if (confound.kind == "capability_unverified") == want_capability
        ]
        if details:
            reason_params["details"] = "; ".join(details)
    reason = translator.m(decision.reason_code, **reason_params) or decision.reason

    lines.append(f"# cc-eval 报告 · {report.experiment_id}")
    lines.append("")
    if spec is not None:
        if spec.title:
            lines.append(f"**{spec.title}**")
            lines.append("")
        lines.append(f"**假设:** {spec.hypothesis.strip()}")
        lines.append("")
        lines.append(f"**自变量:** {spec.independent_variable}")
        lines.append("")
        if spec.framework_change:
            lines.append(f"**被测改动:** {spec.framework_change}")
            lines.append("")

    lines.append(f"**{'结论' if translator.language == 'zh' else 'Verdict'}: {labels.get(decision.verdict.value, decision.verdict.value)}**")
    lines.append("")
    if decision.reason:
        lines.append(f"> {reason}")
        lines.append("")

    # -- provenance ---------------------------------------------------------- #
    lines.append("## 测量条件")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 数据集版本 | {report.dataset_version or 'n/a'} |")
    lines.append(f"| 数据集哈希 | `{report.dataset_hash[:16] or 'n/a'}` |")
    lines.append(f"| 评估器 | {report.evaluator or 'n/a'} |")
    lines.append(f"| 基线变体 | {report.baseline_variant} |")
    lines.append(f"| 候选变体 | {report.candidate_variant} |")
    lines.append(f"| 运行次数 | {report.run_counts.get('baseline', 0)} 基线 / "
                 f"{report.run_counts.get('candidate', 0)} 候选 |")
    for variant, fingerprint in sorted(report.framework_fingerprints.items()):
        lines.append(f"| 框架指纹({variant}) | `{fingerprint[:16]}` |")
    lines.append("")

    # -- metric table -------------------------------------------------------- #
    lines.append("## 指标汇总")
    lines.append("")
    lines.append("| 指标 | 基线 | 候选 | 差值 | 变化率 | 95% 置信区间 | 方向 |")
    lines.append("|---|---:|---:|---:|---:|---|---|")
    for aggregate in report.metrics:
        ci = (
            f"[{_fmt(aggregate.ci_low)}, {_fmt(aggregate.ci_high)}]"
            if aggregate.ci_low is not None
            else "n/a"
        )
        direction = translator.s(aggregate.direction)
        lines.append(
            f"| {_metric_display(aggregate.metric, translator.language)} | {_fmt(aggregate.baseline)} | "
            f"{_fmt(aggregate.candidate)} | {_fmt(aggregate.delta)} | "
            f"{_fmt_pct(aggregate.delta_pct)} | {ci} | {direction} |"
        )
    lines.append("")
    lines.append(
        "_数值为宏平均:每个用例先对其重复执行取平均,再对用例取平均._"
        if translator.language == "zh"
        else "_Values are macro-averages: each case is averaged over its repetitions "
        "first, then the cases are averaged._"
    )
    lines.append("")

    # -- per case ------------------------------------------------------------ #
    lines.append("## 逐用例结果")
    lines.append("")
    lines.append("| 用例 | 基线 | 候选 | 差值 | 状态 | 退化指标 | 备注 |")
    lines.append("|---|---:|---:|---:|---|---|---|")
    for case in report.cases:
        notes = "; ".join(_resolve_notes(case, translator)) if case.notes else ""
        regressed_on = ", ".join(case.regressed_metrics) if case.regressed_metrics else "-"
        lines.append(
            f"| {case.case_id} | {_fmt(case.baseline_score)} | {_fmt(case.candidate_score)} | "
            f"{_fmt(case.delta)} | {translator.s(f'case_status_{case.status}')} | {regressed_on} | {notes} |"
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
    lines.append("### 逐用例变化")
    lines.append("")
    lines.append(f"- **改善({len(improved)}):** {', '.join(improved) if improved else ('_无_' if translator.language == 'zh' else '_none_')}")
    lines.append(f"- **退化({len(regressed)}):** {', '.join(regressed) if regressed else ('_无_' if translator.language == 'zh' else '_none_')}")
    lines.append(f"- **持平({len(unchanged)}):** {', '.join(unchanged) if unchanged else ('_无_' if translator.language == 'zh' else '_none_')}")
    if other_regressions:
        lines.append(
            "- **在非主指标上退化:** " + "; ".join(other_regressions) if translator.language == "zh" else "- **Regressed on a non-primary metric:** " + "; ".join(other_regressions)
        )
    lines.append("")

    # -- variance ------------------------------------------------------------ #
    lines.append("## 运行间波动")
    lines.append("")
    lines.append("| 用例 | 基线(每次重复) | 候选(每次重复) | 基线方差 | 候选方差 |")
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
    lines.append("## 成本与耗时")
    lines.append("")
    lines.append(f"- 成本变化: {_fmt(report.cost_delta_usd, 4)} USD" if translator.language == "zh" else f"- Cost delta: {_fmt(report.cost_delta_usd, 4)} USD")
    lines.append(f"- 耗时变化: {_fmt(report.duration_delta_sec, 2)} 秒" if translator.language == "zh" else f"- Duration delta: {_fmt(report.duration_delta_sec, 2)} seconds")
    cost_metric = next((m for m in report.metrics if m.metric == "cost_usd"), None)
    if cost_metric is not None and cost_metric.baseline is None:
        lines.append(
            "- 成本无法比较:运行时没有报告成本(该值明确显示为 *n/a*,而不是 0)。"
            if translator.language == "zh"
            else "- Cost comparison unavailable: the runtime does not report cost "
            "(the value is explicitly *n/a*, not zero)."
        )
    lines.append("")

    # -- confounds ----------------------------------------------------------- #
    lines.append("## 混淆因素分析")
    lines.append("")
    if not report.confounds:
        lines.append("未检测到混淆因素。" if translator.language == "zh" else "No confounds were detected.")
    else:
        lines.append("| 严重度 | 类型 | 详情 |")
        lines.append("|---|---|---|")
        for confound, detail in zip(report.confounds, confound_details):
            lines.append(
                f"| {translator.s(f'severity_{confound.severity}')} | "
                f"{translator.s(f'confound_{confound.kind}')} | {detail} |"
            )
    lines.append("")

    # -- verdict ------------------------------------------------------------- #
    lines.append("## 结论")
    lines.append("")
    lines.append(f"**{labels.get(decision.verdict.value, decision.verdict.value)}** - {reason}")
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
    lines.append("## 需要人工复核")
    lines.append("")
    if report.manual_review_cases:
        for case_id in report.manual_review_cases:
            lines.append(f"- `{case_id}` - 差异在噪声范围内" if translator.language == "zh" else f"- `{case_id}` - the difference is within noise")
    else:
        lines.append("- 没有用例被标记为模棱两可。")
    lines.append("")
    lines.append(
        "实验通过 **不等于** 应该合并这次框架改动。先看退化的用例、混淆因素表和成本变化。"
        if translator.language == "zh"
        else "An experiment passing does **not** mean the framework change should be merged. "
        "Review the regressed cases, the confound table and the cost movement first."
    )
    lines.append("")

    if report.warnings:
        lines.append("## 警告")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines)
