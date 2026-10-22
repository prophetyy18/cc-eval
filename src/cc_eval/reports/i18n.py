"""Localisation of the reports.

The reports are the part of cc-eval a human reads, so they speak the reader's
language.  Two kinds of text are translated:

* **UI chrome** - labels, headings, help text.  Lives in :data:`UI`.
* **Engine messages** - the sentences the comparison layer derives from the
  decision rules ("recall improved by +0.5167 ...").  The engine emits a
  stable *code* plus its parameters next to the English text; the renderer
  looks the code up in :data:`MESSAGES`.

The JSON report stays language neutral: it carries the English text and the
codes, so a machine never has to parse prose.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "zh"
LANGUAGES = ("zh", "en")


# --------------------------------------------------------------------------- #
# UI chrome
# --------------------------------------------------------------------------- #

UI: dict[str, dict[str, str]] = {
    # -- tabs -------------------------------------------------------------- #
    "tab_overview": {"en": "Overview", "zh": "总览"},
    "tab_cases": {"en": "Cases", "zh": "用例"},
    "tab_traces": {"en": "Traces", "zh": "轨迹"},
    "tab_metadata": {"en": "Metadata", "zh": "元数据"},
    # -- top bar ----------------------------------------------------------- #
    "complete": {"en": "complete", "zh": "已完成"},
    "incomplete": {"en": "incomplete", "zh": "未完成"},
    "failed": {"en": "failed", "zh": "失败"},
    "generated": {"en": "generated", "zh": "生成于"},
    "runtime": {"en": "runtime", "zh": "运行时"},
    # -- verdicts ---------------------------------------------------------- #
    "verdict_improved": {"en": "improved", "zh": "改善"},
    "verdict_regressed": {"en": "regressed", "zh": "退化"},
    "verdict_inconclusive": {"en": "inconclusive", "zh": "不确定"},
    "verdict_invalid": {"en": "invalid", "zh": "无效"},
    "verdict_pending": {"en": "pending", "zh": "待定"},
    # -- overview ---------------------------------------------------------- #
    "conclusion": {"en": "Conclusion", "zh": "结论"},
    "decided_at": {"en": "decided", "zh": "判定于"},
    "experiment": {"en": "Experiment", "zh": "实验"},
    "hypothesis": {"en": "Hypothesis", "zh": "假设"},
    "independent_variable": {"en": "Independent variable", "zh": "自变量"},
    "change_under_test": {"en": "Change under test", "zh": "被测改动"},
    "baseline": {"en": "Baseline", "zh": "基线"},
    "candidate": {"en": "Candidate", "zh": "候选"},
    "dataset": {"en": "Dataset", "zh": "数据集"},
    "evaluator": {"en": "Evaluator", "zh": "评估器"},
    "references": {"en": "References", "zh": "参考"},
    "primary_metric": {"en": "Primary metric", "zh": "主指标"},
    "confidence": {"en": "Confidence", "zh": "置信度"},
    "cases_count": {"en": "Cases", "zh": "用例"},
    "attribution": {"en": "Attribution", "zh": "归因"},
    "runs": {"en": "Runs", "zh": "运行次数"},
    "evidence_note": {
        "en": "The verdict was derived from the decision rules declared before the runs.",
        "zh": "结论由运行前声明的判定规则机械推导得出。",
    },
    "what_this_does_not_support": {
        "en": "What this data does not support",
        "zh": "这份数据支撑不了什么",
    },
    "cost_delta": {"en": "Cost delta", "zh": "成本变化"},
    "significant": {"en": "significant", "zh": "显著"},
    "not_significant": {"en": "not significant", "zh": "不显著"},
    "no_interval": {"en": "no interval available", "zh": "没有置信区间"},
    "confounds_count": {"en": "confound(s)", "zh": "个混淆因素"},
    "blocking_count": {"en": "blocking", "zh": "个是阻断性的"},
    "usd": {"en": "USD", "zh": "美元"},
    "seconds": {"en": "seconds", "zh": "秒"},
    "not_reported_by_runtime": {"en": "not reported by the runtime", "zh": "运行时没有报告"},
    "duration_delta": {"en": "Duration delta", "zh": "耗时变化"},
    "metric_ab": {"en": "Metric A/B", "zh": "指标 A/B 对比"},
    "metric_table": {"en": "Metric table", "zh": "指标明细表"},
    "movement_by_case": {"en": "Movement by case", "zh": "逐用例变化"},
    "confounds": {"en": "Confounds", "zh": "混淆因素"},
    "needs_human_review": {"en": "Needs human review", "zh": "需要人工复核"},
    "warnings": {"en": "Warnings", "zh": "警告"},
    "cases_improved": {"en": "Cases improved", "zh": "改善的用例"},
    "cases_regressed": {"en": "Cases regressed", "zh": "退化的用例"},
    "improved": {"en": "Improved", "zh": "改善"},
    "regressed": {"en": "Regressed", "zh": "退化"},
    "unchanged": {"en": "Unchanged", "zh": "持平"},
    "undetermined": {"en": "Undetermined", "zh": "无法判定"},
    "regressed_other_metric": {
        "en": "Regressed on another metric",
        "zh": "在其他指标上退化",
    },
    "no_blocking_confound": {
        "en": "no blocking confound",
        "zh": "没有阻断性混淆因素",
    },
    "of_cases": {"en": "of {n} cases", "zh": "共 {n} 个用例"},
    "unchanged_undetermined": {
        "en": "{unchanged} unchanged · {undetermined} undetermined",
        "zh": "{unchanged} 个持平 · {undetermined} 个无法判定",
    },
    "delta_note": {"en": "Δ", "zh": "Δ"},
    "higher_is_better": {"en": "higher is better", "zh": "越大越好"},
    "lower_is_better": {"en": "lower is better", "zh": "越小越好"},
    "direction": {"en": "Direction", "zh": "方向"},
    "metric": {"en": "Metric", "zh": "指标"},
    "detail": {"en": "Detail", "zh": "详情"},
    "where": {"en": "Where", "zh": "位置"},
    "kind": {"en": "Kind", "zh": "类型"},
    "severity": {"en": "Severity", "zh": "严重度"},
    "no_confound": {
        "en": "No blocking confound was detected.",
        "zh": "未检测到阻断性混淆因素。",
    },
    "no_metric": {"en": "No metric was measured.", "zh": "没有测到任何指标。"},
    "no_case": {"en": "No case was evaluated.", "zh": "没有评估任何用例。"},
    "macro_average_note": {
        "en": (
            "Values are macro-averages: each case is averaged over its repetitions "
            "first, then the cases are averaged. “n/a” means the runtime did not "
            "report the value - it never means zero."
        ),
        "zh": (
            "数值为宏平均:先对每个用例的多次重复取平均,再对用例取平均。"
            "“n/a” 表示运行时没有报告该值,绝不表示 0。"
        ),
    },
    "within_noise": {
        "en": "the difference is within noise; manual review recommended",
        "zh": "差异在噪声范围内,建议人工复核",
    },
    "partial_banner": {"en": "This report is partial", "zh": "这份报告是残缺的"},
    "failed_banner": {
        "en": "This experiment did not complete",
        "zh": "这次实验没有跑完",
    },
    "partial_warning": {
        "en": (
            "The verdict below was derived from the runs that did complete and must "
            "not be read as a successful experiment."
        ),
        "zh": "下面的结论只由跑完的那部分运行推导得出,不能当作一次成功的实验。",
    },
    # -- cases ------------------------------------------------------------- #
    "search_placeholder": {
        "en": "Search case id, category or tag…",
        "zh": "搜索用例 ID、类别或标签…",
    },
    "all": {"en": "All", "zh": "全部"},
    "case": {"en": "Case", "zh": "用例"},
    "category": {"en": "Category", "zh": "类别"},
    "tags": {"en": "Tags", "zh": "标签"},
    "movement": {"en": "Movement", "zh": "变化"},
    "reps": {"en": "Reps", "zh": "重复数"},
    "assessment": {"en": "Assessment", "zh": "评估说明"},
    "scoring_evidence": {"en": "Scoring evidence", "zh": "评分依据"},
    "repetition_statistics": {"en": "Repetition statistics", "zh": "重复统计"},
    "scores_per_repetition": {"en": "Scores per repetition", "zh": "每次重复的得分"},
    "variance": {"en": "Variance", "zh": "方差"},
    "side": {"en": "Side", "zh": "一侧"},
    "agent_output": {"en": "Agent output", "zh": "Agent 输出"},
    "output_file": {"en": "Output file", "zh": "产出文件"},
    "no_run_for_side": {
        "en": "No run was recorded for this side.",
        "zh": "这一侧没有记录到任何运行。",
    },
    "not_reported_by_runtime": {
        "en": "Not reported by the runtime: {fields}. Those values are shown as n/a, never as zero.",
        "zh": "运行时没有提供: {fields}。这些值显示为 n/a,绝不显示为 0。",
    },
    "denied_tool_calls": {
        "en": "The runtime denied {n} tool call(s).",
        "zh": "运行时拒绝了 {n} 次工具调用。",
    },
    "denied_explanation": {
        "en": (
            "The agent was not allowed to perform them - that is not the same as the "
            "agent choosing not to, and it can explain a missing output."
        ),
        "zh": (
            "agent 是被禁止执行,而不是自己选择不做 —— 这两者不同,而且它常常正是产出缺失的原因。"
        ),
    },
    # -- traces ------------------------------------------------------------ #
    "run": {"en": "Run", "zh": "运行"},
    "repetition": {"en": "repetition", "zh": "重复"},
    "trace_available": {"en": "trace available", "zh": "有轨迹"},
    "no_trace": {"en": "no trace", "zh": "无轨迹"},
    "redacted": {"en": "redacted", "zh": "已脱敏"},
    "events_collapsed": {"en": "{types} events collapsed", "zh": "{types} 类事件已折叠"},
    "no_trace_events": {
        "en": "No trace event was recorded for this run.",
        "zh": "这次运行没有记录到任何轨迹事件。",
    },
    "no_trace_at_all": {"en": "No trace was recorded.", "zh": "没有记录到任何轨迹。"},
    "capability_evidence": {"en": "Capability evidence", "zh": "能力证据"},
    "ab_execution_path": {"en": "A/B execution path", "zh": "A/B 执行路径"},
    "ab_execution_path_hint": {
        "en": "Where the two variants took a different path through the case.",
        "zh": "两个变体在这个用例上走了哪条不同的路径。",
    },
    "identical_path": {"en": "identical path", "zh": "路径相同"},
    "paths_differ": {"en": "paths differ", "zh": "路径不同"},
    "only_here": {"en": "← only here", "zh": "← 仅此处"},
    # -- metadata ---------------------------------------------------------- #
    "runtime_section": {"en": "Runtime", "zh": "运行时"},
    "model": {"en": "Model", "zh": "模型"},
    "seed_control": {"en": "Seed control", "zh": "随机种子控制"},
    "seed_supported": {"en": "supported", "zh": "支持"},
    "seed_not_supported": {
        "en": "not supported — runs are not bit-reproducible",
        "zh": "不支持 —— 运行结果无法逐位复现",
    },
    "seed": {"en": "Seed", "zh": "随机种子"},
    "order_policy": {"en": "Order policy", "zh": "执行顺序策略"},
    "repetitions": {"en": "Repetitions", "zh": "重复次数"},
    "run_status": {"en": "Run status", "zh": "运行状态"},
    "framework": {"en": "Framework", "zh": "框架"},
    "cc_eval_commit": {"en": "cc-eval commit", "zh": "cc-eval 版本"},
    "config_hash": {"en": "Config hash", "zh": "配置哈希"},
    "fingerprint": {"en": "Fingerprint", "zh": "指纹"},
    "commit": {"en": "Commit", "zh": "提交"},
    "working_tree": {"en": "Working tree", "zh": "工作区状态"},
    "clean": {"en": "clean", "zh": "干净"},
    "dirty": {"en": "dirty (uncommitted changes present)", "zh": "有改动(存在未提交的变更)"},
    "expected_changes": {"en": "Expected changes", "zh": "预期变更"},
    "none_declared": {"en": "none declared", "zh": "未声明"},
    "data_and_evaluation": {"en": "Data & evaluation", "zh": "数据与评估"},
    "dataset_version": {"en": "Dataset version", "zh": "数据集版本"},
    "dataset_hash": {"en": "Dataset hash", "zh": "数据集哈希"},
    "evaluator_version": {"en": "Evaluator version", "zh": "评估器版本"},
    "missing_data": {"en": "Missing data", "zh": "缺失数据"},
    "none_reported": {"en": "none reported", "zh": "无"},
    "redactions": {"en": "Redactions", "zh": "脱敏记录"},
    "none": {"en": "none", "zh": "无"},
    "controls": {"en": "Controls", "zh": "控制变量"},
    "decision_rules": {"en": "Decision rules", "zh": "判定规则"},
    "decision_rules_hint": {
        "en": "Declared in the experiment file before the runs.",
        "zh": "在实验文件中、运行之前就已声明。",
    },
    "environment": {"en": "Environment", "zh": "环境"},
    "run_ids": {"en": "Run ids", "zh": "运行 ID"},
    "no_run_recorded": {"en": "no run was recorded", "zh": "没有记录到任何运行"},
    "self_contained_note": {
        "en": "this file is self-contained and needs no network access",
        "zh": "本文件自包含,无需网络访问",
    },
}


# --------------------------------------------------------------------------- #
# engine messages
# --------------------------------------------------------------------------- #

MESSAGES: dict[str, dict[str, str]] = {
    # -- decision reasons -------------------------------------------------- #
    "reason.metric_missing": {
        "en": "the primary metric {metric!r} was not produced by any run",
        "zh": "没有任何一次运行产出主指标 {metric!r}",
    },
    "reason.capability_blocked": {
        "en": (
            "the framework capability under test could not be shown to have been used: {details}"
        ),
        "zh": "无法证明被测框架能力真的被使用过:{details}",
    },
    "reason.confounded": {
        "en": (
            "confounding variables were detected, so the difference cannot be attributed "
            "to the independent variable: {details}"
        ),
        "zh": "检测到混淆变量,差异无法归因于自变量:{details}",
    },
    "reason.too_few_repetitions": {
        "en": (
            "only {actual} repetition(s) per variant were run; the rules require at "
            "least {required}"
        ),
        "zh": "每个变体只跑了 {actual} 次;规则要求至少 {required} 次",
    },
    "reason.delta_missing": {
        "en": "the primary metric {metric!r} is missing on one of the sides",
        "zh": "主指标 {metric!r} 在其中一侧缺失",
    },
    "reason.below_min_delta": {
        "en": (
            "{metric} changed by {delta:+.4f}, which is below the pre-declared "
            "minimum effect size of {min_delta:+.4f}"
        ),
        "zh": "{metric} 变化 {delta:+.4f},低于事先声明的最小效应量 {min_delta:+.4f}",
    },
    "reason.too_many_regressions": {
        "en": (
            "{metric} moved by {delta:+.4f} but {count} case(s) regressed "
            "({rate:.0%} of cases); the rules allow at most {allowed}"
            "{rate_clause}. See the per-case table before accepting this change."
        ),
        "zh": (
            "{metric} 变动 {delta:+.4f},但有 {count} 个用例退化(占 {rate:.0%});"
            "规则最多允许 {allowed} 个{rate_clause}。接受这次改动前请先看逐用例明细。"
        ),
    },
    "reason.guard_regression": {
        "en": (
            "{metric} moved by {delta:+.4f} but {count} case(s) regressed on a guard "
            "metric: {details}"
        ),
        "zh": "{metric} 变动 {delta:+.4f},但有 {count} 个用例在守护指标上退化:{details}",
    },
    "reason.guard_metric": {
        "en": (
            "{metric} changed by {delta:+.4f} but the guard metric {guard} moved by "
            "{guard_delta:+.4f} ({guard_baseline:.4f} -> {guard_candidate:.4f}), which is "
            "outside the allowed tolerance. A change that improves one metric while "
            "degrading another is not an unambiguous improvement."
        ),
        "zh": (
            "{metric} 变化 {delta:+.4f},但守护指标 {guard} 变动 {guard_delta:+.4f}"
            "({guard_baseline:.4f} → {guard_candidate:.4f}),超出允许容差。"
            "一个指标变好、另一个指标变差的改动,不是无歧义的改善。"
        ),
    },
    "reason.ci_includes_zero": {
        "en": (
            "{metric} changed by {delta:+.4f} but the {alpha}% confidence interval "
            "[{low:+.4f}, {high:+.4f}] includes zero"
        ),
        "zh": "{metric} 变化 {delta:+.4f},但 {alpha}% 置信区间 [{low:+.4f}, {high:+.4f}] 包含 0",
    },
    "reason.improved": {
        "en": (
            "{metric} improved by {delta:+.4f} ({baseline:.4f} -> {candidate:.4f}) with no "
            "blocked confound and no disallowed case regression"
        ),
        "zh": (
            "{metric} 改善 {delta:+.4f}({baseline:.4f} → {candidate:.4f}),"
            "没有阻断性混淆因素,也没有不被允许的用例退化"
        ),
    },
    "reason.regressed": {
        "en": "{metric} regressed by {delta:+.4f} ({baseline:.4f} -> {candidate:.4f})",
        "zh": "{metric} 退化 {delta:+.4f}({baseline:.4f} → {candidate:.4f})",
    },
    "reason.no_change": {"en": "{metric} did not change", "zh": "{metric} 没有变化"},
    "note.non_blocking_confounds": {
        "en": "non-blocking confounds were recorded: {details}",
        "zh": "记录到非阻断性混淆因素:{details}",
    },
    # -- status ------------------------------------------------------------- #
    "status.no_runs": {
        "en": (
            "no run was recorded for this experiment; the report contains no measurements"
            "{errors_clause}"
        ),
        "zh": "这次实验没有记录到任何运行,报告里没有任何测量值{errors_clause}",
    },
    "status.failed_runs": {
        "en": (
            "{failed} of {total} run(s) did not complete successfully (statuses: {statuses}); "
            "the numbers below cover the successful runs only"
        ),
        "zh": (
            "{total} 次运行中有 {failed} 次没有成功完成(状态:{statuses});"
            "下面的数字只覆盖成功的那些运行"
        ),
    },
    "status.execution_errors": {
        "en": (
            "{errors} run(s) failed before they could be recorded; the numbers below are partial"
        ),
        "zh": "有 {errors} 次运行在记录之前就失败了,下面的数字是不完整的",
    },
    "status.complete": {
        "en": "every planned run completed successfully",
        "zh": "所有计划中的运行都成功完成",
    },
    # -- caveats ------------------------------------------------------------ #
    "caveat.partial": {
        "en": (
            "This report is PARTIAL: not every planned run completed. "
            "The verdict below applies to the runs that did complete."
        ),
        "zh": "这份报告是残缺的:并非所有计划中的运行都完成了。下面的结论只适用于跑完的那部分。",
    },
    "caveat.failed_runs": {
        "en": "{count} run(s) failed; their cases may be missing from the averages.",
        "zh": "有 {count} 次运行失败,对应的用例可能没有进入平均值。",
    },
    "caveat.few_repetitions": {
        "en": (
            "Only {repetitions} repetition(s) per case: the per-case numbers are a single "
            "sample and cannot show run-to-run variation."
        ),
        "zh": "每个用例只重复了 {repetitions} 次:逐用例的数字是单次采样,无法体现运行间波动。",
    },
    "caveat.few_cases": {
        "en": (
            "Only {count} case(s): the confidence interval is too wide to support a "
            "confident conclusion."
        ),
        "zh": "只有 {count} 个用例:置信区间太宽,支撑不了有把握的结论。",
    },
    "caveat.blocking_confounds": {
        "en": (
            "{count} blocking confound(s) were detected; the difference cannot be attributed "
            "to the independent variable."
        ),
        "zh": "检测到 {count} 个阻断性混淆因素,差异无法归因于自变量。",
    },
    "caveat.missing_data": {
        "en": (
            "Some data is not available from the runtime ({fields}); those cells are shown as "
            "n/a, never as zero."
        ),
        "zh": "部分数据运行时没有提供({fields});这些格子显示为 n/a,绝不显示为 0。",
    },
    "caveat.manual_review": {
        "en": "{count} case(s) differ only within noise and need a human review.",
        "zh": "有 {count} 个用例的差异只在噪声范围内,需要人工复核。",
    },
    # -- case notes -------------------------------------------------------- #
    "case.regressed_metric": {
        "en": "regressed on {metrics} while the primary metric held",
        "zh": "主指标持平,但在 {metrics} 上退化",
    },
    "case.high_variance": {
        "en": "high {label} variance across repetitions ({variance:.3f})",
        "zh": "{label}在多次重复间方差过大({variance:.3f})",
    },
    "case.within_noise": {
        "en": "the difference is within noise; manual review recommended",
        "zh": "差异在噪声范围内,建议人工复核",
    },
    # -- confounds --------------------------------------------------------- #
    "confound.control_mismatch": {
        "en": (
            "{field} differs between the variants: baseline={baseline} candidate="
            "{candidate}. The measured difference cannot be attributed to the "
            "independent variable."
        ),
        "zh": (
            "{field} 在两个变体之间不一致:基线={baseline},候选={candidate}。"
            "测到的差异无法归因于自变量。"
        ),
    },
    "confound.identical_frameworks": {
        "en": (
            "the baseline and candidate frameworks are byte-identical and their "
            "configurations are equal: the independent variable is not present in "
            "either side, so the experiment cannot measure anything"
        ),
        "zh": (
            "基线与候选框架逐字节相同、配置也相同:自变量在两侧都不存在,"
            "这次实验测不到任何东西"
        ),
    },
    "confound.unexpected_diff": {
        "en": (
            "the two variants differ in {count} file(s) but no expected_changes were "
            "declared, so it cannot be shown that the independent variable is the only "
            "difference"
        ),
        "zh": (
            "两个变体有 {count} 个文件不同,但没有声明 expected_changes,"
            "因此无法证明自变量是唯一的差异来源"
        ),
    },
    "confound.unexpected_files": {
        "en": (
            "{count} file(s) differ outside the declared expected_changes: {files}. "
            "These changes may explain the observed difference."
        ),
        "zh": "有 {count} 个文件在声明的 expected_changes 之外发生变化:{files}。这些改动可能解释观察到的差异。",
    },
    "confound.capability_unverified": {
        "en": (
            "capability {name!r} of variant {variant!r} is {status}; {extra}"
        ),
        "zh": "变体 {variant!r} 的能力 {name!r} 状态为 {status};{extra}",
    },
    "confound.capability_required": {
        "en": (
            "the experiment declares this capability as required, so the result must "
            "not be attributed to it"
        ),
        "zh": "实验声明该能力为必需,因此结果不能归因于它",
    },
    "confound.capability_optional": {
        "en": (
            "no evidence was found that the framework change was actually exercised by "
            "the agent"
        ),
        "zh": "没有证据表明 agent 真的行使了这次框架改动",
    },
    "confound.no_evidence": {
        "en": (
            "variant {variant!r} declares capabilities {capabilities} but the runtime "
            "produced no capability evidence at all"
        ),
        "zh": "变体 {variant!r} 声明了能力 {capabilities},但运行时完全没有产出能力证据",
    },
    "confound.repetition_mismatch": {
        "en": (
            "the variants were not run the same number of times: baseline={baseline} "
            "candidate={candidate}"
        ),
        "zh": "两个变体的运行次数不一致:基线={baseline},候选={candidate}",
    },
}


# --------------------------------------------------------------------------- #
# metric names
# --------------------------------------------------------------------------- #

METRIC_LABELS: dict[str, dict[str, str]] = {
    "recall": {"en": "Recall", "zh": "召回率"},
    "precision": {"en": "Precision", "zh": "精确率"},
    "f1": {"en": "F1", "zh": "F1"},
    "false_positive_count": {"en": "False positives", "zh": "误报数"},
    "false_negative_count": {"en": "False negatives", "zh": "漏报数"},
    "valid_output": {"en": "Valid output", "zh": "输出格式有效"},
    "total_tokens": {"en": "Tokens", "zh": "Token 数"},
    "input_tokens": {"en": "Input tokens", "zh": "输入 Token"},
    "output_tokens": {"en": "Output tokens", "zh": "输出 Token"},
    "cost_usd": {"en": "Cost (USD)", "zh": "成本(美元)"},
    "duration_sec": {"en": "Duration (s)", "zh": "耗时(秒)"},
    "rubric_score": {"en": "Rubric score", "zh": "评分标准得分"},
    "judge_score": {"en": "Judge score", "zh": "评审模型得分"},
    "run_ok": {"en": "Run ok", "zh": "运行成功"},
    "finding_count": {"en": "Finding count", "zh": "发现数"},
    "rubric_criteria_scored": {"en": "Rubric criteria scored", "zh": "已评标准数"},
    "rubric_criteria_unscored": {"en": "Rubric criteria unscored", "zh": "未评标准数"},
}


def metric_label(metric: str, language: str = DEFAULT_LANGUAGE) -> str:
    """The human readable name of a metric."""
    entry = METRIC_LABELS.get(metric)
    if entry is None:
        return metric.replace("_", " ")
    return entry.get(language) or entry.get("en") or metric


# --------------------------------------------------------------------------- #
# translator
# --------------------------------------------------------------------------- #


class Translator:
    """Resolves UI strings and engine messages for one language."""

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE

    def s(self, key: str, **params: Any) -> str:
        """A UI string."""
        entry = UI.get(key)
        if entry is None:
            return key
        text = entry.get(self.language) or entry.get("en") or key
        return text.format(**params) if params else text

    def m(self, code: str, **params: Any) -> str | None:
        """An engine message, or ``None`` when the code is unknown."""
        entry = MESSAGES.get(code)
        if entry is None:
            return None
        text = entry.get(self.language) or entry.get("en") or code
        try:
            return text.format(**params)
        except (KeyError, IndexError):  # pragma: no cover - defensive
            return text

    def ui(self) -> dict[str, str]:
        """Every UI string, resolved - handed to the template as ``L``."""
        return {key: self.s(key) for key in UI}


def resolve_language(explicit: str | None = None) -> str:
    """The language to render in: explicit > environment > default."""
    import os

    candidate = explicit or os.environ.get("CC_EVAL_LANG", "")
    return candidate if candidate in LANGUAGES else DEFAULT_LANGUAGE
