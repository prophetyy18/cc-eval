"""The structured report model.

The evaluation engine produces a :class:`ReportModel`: one object that carries
the *whole* result of an experiment - the comparison, the per-case detail, the
raw runs, the traces and the metadata needed to reproduce the measurement.

Renderers (JSON, Markdown, HTML) are pure functions of this model.  They never
recompute a score, never re-derive a verdict and never touch the evaluation
engine: a renderer that changed a number would be lying about the experiment.

The model is also the *replayable* artefact: ``report.json`` is its
serialisation, so an HTML report can be regenerated later without re-running
the evaluation.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field

from ..core.models import (
    CapabilityStatus,
    ComparisonReport,
    ExperimentSpec,
    MatchedItem,
    RuntimeInfo,
    StrictModel,
    TraceEvent,
    Usage,
    utcnow_iso,
)
from ..isolation.redaction import redact_obj

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..core.experiment import RunRecord
    from ..datasets.loader import Benchmark

ReportStatus = Literal["complete", "incomplete", "failed"]

SCHEMA_VERSION = "cc-eval/report/v1"

#: Fields the runtimes may not expose.  A report must say so instead of
#: showing a zero where the truth is "unknown".
_METRIC_LABELS = {
    "recall": "Recall",
    "precision": "Precision",
    "f1": "F1",
    "false_positive_count": "False positives",
    "false_negative_count": "False negatives",
    "valid_output": "Valid output",
    "total_tokens": "Tokens",
    "input_tokens": "Input tokens",
    "output_tokens": "Output tokens",
    "cost_usd": "Cost (USD)",
    "duration_sec": "Duration (s)",
    "rubric_score": "Rubric score",
    "judge_score": "Judge score",
}


def metric_label(metric: str, language: str | None = None) -> str:
    """Human readable name of a metric (shared by every renderer)."""
    from .i18n import metric_label as _label

    return _label(metric, language) if language else _label(metric)


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #


#: The trace events a report shows.  The full trace stays on disk; a report
#: carries the events a reader can act on, and counts the rest so that
#: nothing disappears silently.  (A single Claude Code run produces thousands
#: of ``system`` bookkeeping events - they would dwarf the report.)
REPORT_TRACE_EVENTS = frozenset(
    {
        "session",
        "tool_call",
        "tool_result",
        "agent_output",
        "result",
        "error",
        "reasoning",
    }
)


class ReportRun(StrictModel):
    """One run, with its evaluation and its normalised trace."""

    run_id: str
    case_id: str
    variant: str
    repetition: int = 1
    status: str = "success"
    ok: bool = True
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int | None = None
    usage: Usage = Field(default_factory=Usage)
    runtime: RuntimeInfo = Field(default_factory=lambda: RuntimeInfo(name="unknown"))
    output_text: str = ""
    output_files: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    # -- evaluation ------------------------------------------------------- #
    evaluator: str = ""
    evaluator_version: str = ""
    metrics: dict[str, float | None] = Field(default_factory=dict)
    matched: list[MatchedItem] = Field(default_factory=list)
    unmatched_golden: list[str] = Field(default_factory=list)
    unmatched_predictions: list[str] = Field(default_factory=list)
    evaluation_errors: list[str] = Field(default_factory=list)
    evaluation_notes: list[str] = Field(default_factory=list)
    evaluation_details: dict[str, Any] = Field(default_factory=dict)
    # -- trace ------------------------------------------------------------ #
    trace_present: bool = False
    trace_events: list[TraceEvent] = Field(default_factory=list)
    #: How many events of each type the full trace holds.
    trace_event_counts: dict[str, int] = Field(default_factory=dict)
    #: The event types that were counted but not shown.
    trace_event_types_collapsed: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    capabilities: list[CapabilityStatus] = Field(default_factory=list)
    redacted: bool = True
    # -- provenance -------------------------------------------------------- #
    framework_fingerprint: str = ""
    framework_commit: str = ""
    order_index: int | None = None


class ReportCase(StrictModel):
    """One benchmark case, seen from both sides."""

    case_id: str
    task_type: str = ""
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    status: Literal["improved", "regressed", "unchanged", "unknown"] = "unknown"
    baseline_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None
    baseline_per_repetition: list[float | None] = Field(default_factory=list)
    candidate_per_repetition: list[float | None] = Field(default_factory=list)
    baseline_variance: float | None = None
    candidate_variance: float | None = None
    regressed_metrics: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    runs: list[ReportRun] = Field(default_factory=list)
    #: One entry per repetition: the tool path of both sides and the steps
    #: that only one of them took.
    trace_diff: list[dict[str, Any]] = Field(default_factory=list)


class ReportMetadata(StrictModel):
    """Everything needed to know *how* and *where* the numbers came from."""

    experiment_id: str
    title: str = ""
    hypothesis: str = ""
    independent_variable: str = ""
    framework_change: str = ""
    status: ReportStatus = "complete"
    status_detail: str = ""
    status_detail_code: str = ""
    status_detail_params: dict[str, Any] = Field(default_factory=dict)
    verdict: str = "pending"
    verdict_reason: str = ""
    primary_metric: str = ""
    baseline_variant: str = ""
    candidate_variant: str = ""
    # -- runtime ----------------------------------------------------------- #
    runtime: str = ""
    runtime_version: str = ""
    model: str | None = None
    model_reported_by: str | None = None
    seed_supported: bool = False
    # -- framework --------------------------------------------------------- #
    framework_fingerprints: dict[str, str] = Field(default_factory=dict)
    framework_commits: dict[str, str] = Field(default_factory=dict)
    framework_dirty: dict[str, bool] = Field(default_factory=dict)
    config_hash: str = ""
    cc_eval_commit: str = ""
    # -- data -------------------------------------------------------------- #
    dataset_name: str = ""
    dataset_version: str = ""
    dataset_hash: str = ""
    evaluator: str = ""
    evaluator_version: str = ""
    # -- controls ---------------------------------------------------------- #
    controls: dict[str, Any] = Field(default_factory=dict)
    decision_rules: dict[str, Any] = Field(default_factory=dict)
    repetitions: int = 1
    order_policy: str = ""
    seed: int | None = None
    # -- runs -------------------------------------------------------------- #
    run_ids: list[str] = Field(default_factory=list)
    run_counts: dict[str, int] = Field(default_factory=dict)
    run_statuses: dict[str, int] = Field(default_factory=dict)
    # -- provenance -------------------------------------------------------- #
    environment: dict[str, Any] = Field(default_factory=dict)
    confounds: list[dict[str, Any]] = Field(default_factory=list)
    confound_free: bool = True
    warnings: list[str] = Field(default_factory=list)
    manual_review_cases: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    #: The stable identifiers of the caveats, with their parameters.
    caveat_codes: list[dict[str, Any]] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    redaction_stats: dict[str, int] = Field(default_factory=dict)
    generated_at: str = Field(default_factory=utcnow_iso)


class ReportModel(StrictModel):
    """The complete, renderer-independent result of one experiment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)

    # ``schema`` is the JSON key; the attribute is renamed because pydantic's
    # own ``BaseModel.schema`` helper would be shadowed otherwise.
    schema_: str = Field(default=SCHEMA_VERSION, alias="schema", serialization_alias="schema")
    generated_at: str = Field(default_factory=utcnow_iso)
    status: ReportStatus = "complete"
    experiment: ExperimentSpec
    report: ComparisonReport
    cases: list[ReportCase] = Field(default_factory=list)
    metadata: ReportMetadata = Field(default_factory=lambda: ReportMetadata(experiment_id=""))

    # -- serialisation ------------------------------------------------------ #
    def to_payload(self) -> dict[str, Any]:
        """The JSON payload (identical to ``report.json``).

        ``mode="json"`` turns non-finite floats into ``null`` instead of the
        invalid JSON tokens ``NaN``/``Infinity``.
        """
        return self.model_dump(mode="json", by_alias=True)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, indent=indent, default=str)


# --------------------------------------------------------------------------- #
# builder
# --------------------------------------------------------------------------- #


def build_report_model(
    spec: ExperimentSpec,
    comparison: ComparisonReport,
    records: Sequence[RunRecord],
    *,
    benchmark: Benchmark | None = None,
    dataset_name: str = "",
    execution_errors: Sequence[str] = (),
    execution_warnings: Sequence[str] = (),
) -> ReportModel:
    """Assemble the report model from a comparison and its run records.

    The builder is the only place that knows how a run record maps onto the
    report.  It never changes a score: every number it copies comes either
    from the evaluation or from the comparison.
    """
    case_meta = _case_meta(benchmark)
    runs_by_case: dict[str, list[ReportRun]] = defaultdict(list)
    for record in records:
        runs_by_case[record.result.case_id].append(_run_summary(record))

    comparison_cases = {case.case_id: case for case in comparison.cases}
    case_ids = sorted(set(comparison_cases) | set(runs_by_case))

    cases: list[ReportCase] = []
    for case_id in case_ids:
        comparison_case = comparison_cases.get(case_id)
        meta = case_meta.get(case_id, {})
        runs = sorted(
            runs_by_case.get(case_id, []),
            key=lambda run: (run.variant, run.repetition),
        )
        case = ReportCase(
            case_id=case_id,
            task_type=(comparison_case.task_type if comparison_case else "") or meta.get("task_type", ""),
            title=meta.get("title", ""),
            tags=list(meta.get("tags", [])),
            status=comparison_case.status if comparison_case else "unknown",
            baseline_score=comparison_case.baseline_score if comparison_case else None,
            candidate_score=comparison_case.candidate_score if comparison_case else None,
            delta=comparison_case.delta if comparison_case else None,
            baseline_per_repetition=list(comparison_case.baseline_per_repetition)
            if comparison_case
            else [],
            candidate_per_repetition=list(comparison_case.candidate_per_repetition)
            if comparison_case
            else [],
            baseline_variance=comparison_case.baseline_variance if comparison_case else None,
            candidate_variance=comparison_case.candidate_variance if comparison_case else None,
            regressed_metrics=list(comparison_case.regressed_metrics) if comparison_case else [],
            notes=list(comparison_case.notes) if comparison_case else [],
            runs=runs,
        )
        case.trace_diff = _trace_diff(case)
        cases.append(case)

    metadata = _build_metadata(
        spec,
        comparison,
        records,
        cases=cases,
        benchmark=benchmark,
        dataset_name=dataset_name,
        execution_errors=execution_errors,
        execution_warnings=execution_warnings,
    )

    model = ReportModel(
        schema=SCHEMA_VERSION,
        generated_at=metadata.generated_at,
        status=metadata.status,
        experiment=spec,
        report=comparison,
        cases=cases,
        metadata=metadata,
    )

    # -- redaction ---------------------------------------------------------- #
    # The runs hold agent output; a credential must never reach a report.
    payload, stats = redact_obj(model.model_dump(mode="json"))
    redacted = ReportModel.model_validate(payload)
    redacted.metadata.redaction_stats = stats
    return redacted


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _case_meta(benchmark: Benchmark | None) -> dict[str, dict[str, Any]]:
    if benchmark is None:
        return {}
    meta: dict[str, dict[str, Any]] = {}
    for case in benchmark.cases:
        meta[case.id] = {
            "task_type": case.bundle.task_type,
            "title": case.bundle.title,
            "tags": list(case.bundle.tags),
        }
    return meta


def _run_summary(record: RunRecord) -> ReportRun:
    result = record.result
    evaluation = record.evaluation
    trace = result.trace
    provenance = result.provenance or {}
    return ReportRun(
        run_id=result.run_id,
        case_id=result.case_id,
        variant=result.variant,
        repetition=result.repetition,
        status=result.status,
        ok=result.ok,
        started_at=result.started_at,
        ended_at=result.ended_at,
        duration_ms=result.duration_ms,
        usage=result.usage,
        runtime=result.runtime,
        output_text=result.output_text,
        output_files=dict(result.output_files),
        error=result.error,
        evaluator=evaluation.evaluator or str(provenance.get("evaluator", "")),
        evaluator_version=evaluation.evaluator_version or record.evaluator_version,
        metrics=dict(evaluation.metrics),
        matched=list(evaluation.matched),
        unmatched_golden=list(evaluation.unmatched_golden),
        unmatched_predictions=list(evaluation.unmatched_predictions),
        evaluation_errors=list(evaluation.errors),
        evaluation_notes=list(evaluation.notes),
        evaluation_details=dict(evaluation.details),
        trace_present=trace is not None,
        trace_events=[
            event for event in (trace.events if trace else []) if event.type in REPORT_TRACE_EVENTS
        ],
        trace_event_counts=_event_counts(trace.events if trace else []),
        trace_event_types_collapsed=sorted(
            {
                event.type
                for event in (trace.events if trace else [])
                if event.type not in REPORT_TRACE_EVENTS
            }
        ),
        missing_fields=list(trace.missing_fields) if trace else [],
        capabilities=list(trace.capabilities) if trace else [],
        redacted=bool(trace.redacted) if trace else True,
        framework_fingerprint=record.fingerprint or str(provenance.get("framework_fingerprint", "")),
        framework_commit=str(provenance.get("framework_commit", "") or ""),
        order_index=provenance.get("order_index"),
    )


def _event_counts(events: list[TraceEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    return dict(sorted(counts.items()))


def _step_label(event: TraceEvent) -> str:
    """A comparable label for one trace event ("tool_call:Skill")."""
    parts = [event.type]
    if event.name:
        parts.append(str(event.name))
    elif event.tool:
        parts.append(str(event.tool))
    return ":".join(parts)


def _trace_diff(case: ReportCase) -> list[dict[str, Any]]:
    """Compare the execution path of the two variants, repetition by repetition."""
    baseline_runs = [run for run in case.runs if run.variant == "baseline"]
    candidate_runs = [run for run in case.runs if run.variant == "candidate"]
    repetitions = sorted({run.repetition for run in baseline_runs + candidate_runs})

    diffs: list[dict[str, Any]] = []
    for repetition in repetitions:
        baseline = next((r for r in baseline_runs if r.repetition == repetition), None)
        candidate = next((r for r in candidate_runs if r.repetition == repetition), None)
        baseline_steps = [_step_label(event) for event in baseline.trace_events] if baseline else []
        candidate_steps = [_step_label(event) for event in candidate.trace_events] if candidate else []
        baseline_set = set(baseline_steps)
        candidate_set = set(candidate_steps)
        diffs.append(
            {
                "repetition": repetition,
                "baseline_run_id": baseline.run_id if baseline else "",
                "candidate_run_id": candidate.run_id if candidate else "",
                "baseline_steps": baseline_steps,
                "candidate_steps": candidate_steps,
                "only_in_baseline": sorted(baseline_set - candidate_set),
                "only_in_candidate": sorted(candidate_set - baseline_set),
                "identical": baseline_steps == candidate_steps,
            }
        )
    return diffs


def _config_hash(spec: ExperimentSpec) -> str:
    """A stable hash of everything that is *supposed* to be held constant."""
    payload = {
        "controls": spec.controls.model_dump(mode="json"),
        "decision_rules": spec.decision_rules.model_dump(mode="json"),
        "baseline_config": spec.baseline.config,
        "candidate_config": spec.candidate.config,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _cc_eval_commit() -> str:
    """The commit of the cc-eval checkout that produced the report (best effort)."""
    try:
        from ..core.snapshot import repo_state

        commit, _dirty, _files = repo_state(Path(__file__).resolve().parents[3])
        return (commit or "")[:16]
    except Exception:  # noqa: BLE001 - provenance is optional, never fatal
        return ""


def _build_metadata(
    spec: ExperimentSpec,
    comparison: ComparisonReport,
    records: Sequence[RunRecord],
    *,
    cases: list[ReportCase],
    benchmark: Benchmark | None,
    dataset_name: str,
    execution_errors: Sequence[str],
    execution_warnings: Sequence[str],
) -> ReportMetadata:
    baseline_name = spec.baseline.name
    candidate_name = spec.candidate.name

    run_statuses: dict[str, int] = defaultdict(int)
    framework_commits: dict[str, str] = {}
    framework_dirty: dict[str, bool] = {}
    runtime_name = ""
    runtime_version = ""
    model: str | None = None
    model_reported_by: str | None = None
    seed_supported = False
    evaluator_name = comparison.evaluator or spec.controls.evaluator
    evaluator_version = ""
    missing_data: set[str] = set()

    for record in records:
        result = record.result
        run_statuses[result.status] += 1
        framework_commits.setdefault(result.variant, str(result.provenance.get("framework_commit", "") or ""))
        if "framework_dirty" in result.provenance:
            framework_dirty.setdefault(result.variant, bool(result.provenance.get("framework_dirty")))
        if not runtime_name:
            runtime_name = result.runtime.name
            runtime_version = result.runtime.version or ""
            model = result.runtime.model
            model_reported_by = result.runtime.model_reported_by
            seed_supported = result.runtime.seed_supported
        if not evaluator_version:
            evaluator_version = record.evaluator_version or result.provenance.get(
                "evaluator_version", ""
            )
        if result.usage.source == "unavailable":
            missing_data.add("token usage")
        if result.trace is not None:
            missing_data.update(result.trace.missing_fields)

    status, status_detail, status_code, status_params = _status_of(records, execution_errors)
    failed_runs = sum(count for status_name, count in run_statuses.items() if status_name != "success")

    metadata = ReportMetadata(
        experiment_id=spec.id,
        title=spec.title,
        hypothesis=spec.hypothesis,
        independent_variable=spec.independent_variable,
        framework_change=spec.framework_change,
        status=status,
        status_detail=status_detail,
        status_detail_code=status_code,
        status_detail_params=status_params,
        verdict=comparison.decision.verdict.value,
        verdict_reason=comparison.decision.reason,
        primary_metric=spec.decision_rules.primary_metric,
        baseline_variant=baseline_name,
        candidate_variant=candidate_name,
        runtime=runtime_name,
        runtime_version=runtime_version,
        model=model,
        model_reported_by=model_reported_by,
        seed_supported=seed_supported,
        framework_fingerprints=dict(comparison.framework_fingerprints),
        framework_commits=framework_commits,
        framework_dirty=framework_dirty,
        config_hash=_config_hash(spec),
        cc_eval_commit=_cc_eval_commit(),
        dataset_name=dataset_name or (benchmark.name if benchmark else ""),
        dataset_version=comparison.dataset_version,
        dataset_hash=comparison.dataset_hash,
        evaluator=evaluator_name,
        evaluator_version=str(evaluator_version),
        controls=spec.controls.model_dump(mode="json"),
        decision_rules=spec.decision_rules.model_dump(mode="json"),
        repetitions=spec.controls.repetitions,
        order_policy=spec.controls.order,
        seed=spec.controls.seed,
        run_ids=[record.result.run_id for record in records],
        run_counts=dict(comparison.run_counts),
        run_statuses=dict(run_statuses),
        environment={
            "python": sys.version.split()[0],
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cc_eval_version": _cc_eval_version(),
            "generated_at": utcnow_iso(),
            "note": "the host name is deliberately not recorded",
        },
        confounds=[confound.model_dump(mode="json") for confound in comparison.confounds],
        confound_free=not any(c.blocks_attribution for c in comparison.confounds),
        warnings=list(comparison.warnings) + list(execution_warnings),
        manual_review_cases=list(comparison.manual_review_cases),
        missing_data=sorted(missing_data),
    )

    metadata.caveats = _caveats(
        metadata,
        spec=spec,
        comparison=comparison,
        cases=cases,
        failed_runs=failed_runs,
    )
    return metadata


def _cc_eval_version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:  # noqa: BLE001 - defensive
        return "unknown"


def _status_of(
    records: Sequence[RunRecord], execution_errors: Sequence[str]
) -> tuple[ReportStatus, str, str, dict[str, Any]]:
    """Decide whether the experiment completed, partially ran or failed."""
    if not records:
        detail = (
            "no run was recorded for this experiment; the report contains no measurements"
        )
        params: dict[str, Any] = {}
        if execution_errors:
            detail += f"; {len(execution_errors)} execution error(s): {execution_errors[0]}"
            params = {"errors": len(execution_errors), "first": execution_errors[0]}
        return "failed", detail, "status.no_runs", params
    failed = [r for r in records if not r.result.ok]
    if failed:
        statuses = ", ".join(sorted({r.result.status for r in failed}))
        detail = (
            f"{len(failed)} of {len(records)} run(s) did not complete successfully "
            f"(statuses: {statuses}); "
            "the numbers below cover the successful runs only"
        )
        return (
            "incomplete",
            detail,
            "status.failed_runs",
            {"failed": len(failed), "total": len(records), "statuses": statuses},
        )
    if execution_errors:
        detail = (
            f"{len(execution_errors)} run(s) failed before they could be recorded; "
            "the numbers below are partial"
        )
        return (
            "incomplete",
            detail,
            "status.execution_errors",
            {"errors": len(execution_errors)},
        )
    return (
        "complete",
        "every planned run completed successfully",
        "status.complete",
        {},
    )


def _caveats(
    metadata: ReportMetadata,
    *,
    spec: ExperimentSpec,
    comparison: ComparisonReport,
    cases: list[ReportCase],
    failed_runs: int,
) -> list[str]:
    """Honest warnings about how much the numbers can support."""
    caveats: list[str] = []
    codes: list[dict[str, Any]] = []

    def _caveat(text: str, code: str, **params: Any) -> None:
        caveats.append(text)
        codes.append({"code": code, "params": params})

    if metadata.status != "complete":
        _caveat(
            "This report is PARTIAL: not every planned run completed. "
            "The verdict below applies to the runs that did complete.",
            "caveat.partial",
        )
    if failed_runs:
        _caveat(
            f"{failed_runs} run(s) failed; their cases may be missing from the averages.",
            "caveat.failed_runs",
            count=failed_runs,
        )
    if metadata.repetitions < 2:
        _caveat(
            f"Only {metadata.repetitions} repetition(s) per case: the per-case numbers are a "
            "single sample and cannot show run-to-run variation.",
            "caveat.few_repetitions",
            repetitions=metadata.repetitions,
        )
    if len(cases) < 3:
        _caveat(
            f"Only {len(cases)} case(s): the confidence interval is too wide to support a "
            "confident conclusion.",
            "caveat.few_cases",
            count=len(cases),
        )
    if not metadata.confound_free:
        blockers = [c for c in metadata.confounds if c.get("severity") == "blocker"]
        _caveat(
            f"{len(blockers)} blocking confound(s) were detected; the difference cannot be "
            "attributed to the independent variable.",
            "caveat.blocking_confounds",
            count=len(blockers),
        )
    if metadata.missing_data:
        _caveat(
            "Some data is not available from the runtime ("
            + ", ".join(metadata.missing_data[:6])
            + "); those cells are shown as n/a, never as zero.",
            "caveat.missing_data",
            fields=", ".join(metadata.missing_data[:6]),
        )
    if comparison.manual_review_cases:
        _caveat(
            f"{len(comparison.manual_review_cases)} case(s) differ only within noise and need "
            "a human review.",
            "caveat.manual_review",
            count=len(comparison.manual_review_cases),
        )
    metadata.caveat_codes = codes
    return caveats
