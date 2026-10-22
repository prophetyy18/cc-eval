"""HTML report rendering.

The HTML report is a **single, self-contained file**: no CDN, no web server, no
build step.  Open ``report.html`` in a browser and it works - offline, from a
USB stick, from an artefact archive.

The renderer is a pure function of the :class:`~cc_eval.reports.model.ReportModel`.
It never recomputes a score and never changes a verdict; it only presents what
the evaluation produced.

Customisation
-------------
The template lives in ``cc_eval/reports/templates/report.html.j2``.  Point the
``CC_EVAL_HTML_TEMPLATE`` environment variable (or the ``--template`` option of
``cc-eval report``) at a copy of it to restyle the report without touching the
code.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .charts import delta_bar, is_rate_metric, metric_mini_chart
from .i18n import Translator, metric_label, resolve_language
from .model import ReportModel, metric_label

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "report.html.j2"
TEMPLATE_ENV_VAR = "CC_EVAL_HTML_TEMPLATE"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _sanitise(value: Any) -> Any:
    """Replace non-finite floats with ``None`` so the payload is valid JSON."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {key: _sanitise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(item) for item in value]
    return value


def json_for_script(payload: Any) -> str:
    """Serialise a payload for embedding in a ``<script>`` block.

    ``<``, ``>``, ``&`` and ``'`` are escaped as unicode escapes, so the JSON
    can never terminate the script element (``</script>``) and the result is
    still valid JSON for ``JSON.parse``.
    """
    text = json.dumps(_sanitise(payload), ensure_ascii=False, allow_nan=False, default=str)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("'", "\\u0027")
    )


def _fmt_number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if value == int(value) and abs(value) < 1_000_000:
        return str(int(value))
    return f"{value:.{digits}f}"


def _fmt_signed(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}"


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def _fmt_metric(value: float | None, metric: str = "") -> str:
    """Format a metric value.

    Rates (recall, precision, ...) always show four decimals so that
    ``1`` and ``0.9833`` are read as the same kind of number; counts show as
    integers.
    """
    if value is None:
        return "n/a"
    if is_rate_metric(metric):
        return f"{value:.4f}"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}"


def _metric_direction_label(direction: str) -> str:
    return "higher is better" if direction == "higher_is_better" else "lower is better"


def _json_compact(value: Any) -> str:
    """Compact JSON for *display* (the surrounding HTML is escaped by Jinja)."""
    return json.dumps(_sanitise(value), ensure_ascii=False, default=str)


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["number"] = _fmt_number
    env.filters["signed"] = _fmt_signed
    env.filters["percent"] = _fmt_percent
    env.filters["metric_label"] = metric_label
    env.filters["metric_number"] = _fmt_metric
    env.filters["json_compact"] = _json_compact
    return env


def _load_template(template: str | Path | None = None) -> Any:
    """Load the report template.

    Precedence: explicit argument > ``CC_EVAL_HTML_TEMPLATE`` > the packaged
    template.
    """
    candidate = template or os.environ.get(TEMPLATE_ENV_VAR)
    if candidate:
        path = Path(candidate)
        if not path.exists():
            raise FileNotFoundError(f"HTML template not found: {path}")
        env = _environment()
        env.loader = FileSystemLoader(str(path.parent))
        return env.get_template(path.name)
    return _environment().get_template(TEMPLATE_NAME)


# --------------------------------------------------------------------------- #
# derived view data
# --------------------------------------------------------------------------- #


def _metric_cards(report: ReportModel, language: str | None = None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for aggregate in report.report.metrics:
        cards.append(
            {
                "metric": aggregate.metric,
                "label": metric_label(aggregate.metric),
                "baseline": aggregate.baseline,
                "candidate": aggregate.candidate,
                "delta": aggregate.delta,
                "delta_pct": aggregate.delta_pct,
                "ci_low": aggregate.ci_low,
                "ci_high": aggregate.ci_high,
                "significant": aggregate.significant,
                "direction": aggregate.direction,
                "direction_key": aggregate.direction,
                "direction_label": _metric_direction_label(aggregate.direction),
                "baseline_n": aggregate.baseline_n,
                "candidate_n": aggregate.candidate_n,
                "baseline_std": aggregate.baseline_std,
                "candidate_std": aggregate.candidate_std,
                "svg": metric_mini_chart(
                    aggregate.metric,
                    aggregate.baseline,
                    aggregate.candidate,
                    direction=aggregate.direction,
                    language=language,
                ),
            }
        )
    return cards


def _movement(report: ReportModel) -> dict[str, Any]:
    groups: dict[str, list[str]] = {
        "improved": [],
        "regressed": [],
        "unchanged": [],
        "unknown": [],
    }
    guard_regressed: list[str] = []
    for case in report.report.cases:
        groups.setdefault(case.status, []).append(case.case_id)
        if case.regressed_metrics:
            # A case can hold the primary metric steady and still regress on
            # another tracked metric.  It must not disappear from the summary.
            guard_regressed.append(case.case_id)
    groups["guard"] = guard_regressed
    return {
        "groups": groups,
        "counts": {key: len(value) for key, value in groups.items()},
        "total": len(report.report.cases),
    }


def _totals(report: ReportModel) -> list[dict[str, Any]]:
    """The volume story (tokens), taken from the metric table.

    Cost and duration have their own tiles because they need a note when the
    runtime does not report them.
    """
    wanted = ["total_tokens"]
    found: list[dict[str, Any]] = []
    for aggregate in report.report.metrics:
        if aggregate.metric not in wanted:
            continue
        found.append(
            {
                "metric": aggregate.metric,
                "label": metric_label(aggregate.metric),
                "baseline": aggregate.baseline,
                "candidate": aggregate.candidate,
                "delta": aggregate.delta,
                "delta_pct": aggregate.delta_pct,
            }
        )
    return found


def _primary_metric_card(
    report: ReportModel, language: str | None = None
) -> dict[str, Any] | None:
    primary = report.metadata.primary_metric
    for card in _metric_cards(report, language):
        if card["metric"] == primary:
            return card
    return None


def _trace_diff_rows(report: ReportModel) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(report.cases):
        for diff in case.trace_diff:
            rows.append(
                {
                    "case_id": case.case_id,
                    "case_index": index,
                    "repetition": diff["repetition"],
                    "baseline_steps": diff["baseline_steps"],
                    "candidate_steps": diff["candidate_steps"],
                    "only_in_baseline": diff["only_in_baseline"],
                    "only_in_candidate": diff["only_in_candidate"],
                    "identical": diff["identical"],
                }
            )
    return rows


def build_context(report: ReportModel, language: str | None = None) -> dict[str, Any]:
    """Everything the template needs, derived from the report model.

    The UI chrome and the engine's own sentences are rendered in the
    requested language.  The JSON report stays language neutral - it carries
    the English text plus the stable message codes.
    """
    translator = Translator(resolve_language(language))
    max_abs = 0.0
    for case in report.cases:
        if case.delta is not None:
            max_abs = max(max_abs, abs(case.delta))

    case_views: list[dict[str, Any]] = []
    for index, case in enumerate(report.cases):
        view = case.model_dump(mode="json")
        view["index"] = index
        view["delta_svg"] = delta_bar(case.delta, max_abs, case.status)
        for run in view["runs"]:
            # The metric names and the run status are shown to the reader.
            run["metrics_labeled"] = [
                (metric_label(metric, translator.language), value)
                for metric, value in (run.get("metrics") or {}).items()
            ]
            run["status_label"] = translator.s(f"run_status_{run.get('status', 'success')}")
            # A denied tool call is the difference between "the agent decided
            # not to write" and "the agent was not allowed to write".
            denials: list[Any] = []
            for event in run.get("trace_events") or []:
                details = event.get("details") or {}
                denials.extend(details.get("permission_denials") or [])
            run["permission_denials"] = denials
        case_views.append(view)

    # -- the engine's own sentences, in the reader's language ---------------- #
    decision = report.report.decision

    # The confound details come first: the verdict reason quotes them.
    confounds = []
    for confound in report.report.confounds:
        detail = translator.m(confound.code, **(confound.params or {})) or confound.detail
        confounds.append(
            {
                "severity": confound.severity,
                "kind": confound.kind,
                "detail": detail,
                "where": confound.where,
            }
        )

    notes = []
    for index, note in enumerate(decision.notes):
        code = decision.notes_codes[index] if index < len(decision.notes_codes) else None
        if code:
            params = dict(code.get("params") or {})
            if code.get("code") == "note.non_blocking_confounds":
                blocking = [
                    resolved["detail"]
                    for confound, resolved in zip(report.report.confounds, confounds)
                    if confound.severity != "blocker"
                ]
                if blocking:
                    params["details"] = "; ".join(blocking)
            notes.append(translator.m(code.get("code", ""), **params) or note)
        else:
            notes.append(note)

    # The reason embeds the confound details; they are resolved above so the
    # whole sentence is in one language.
    reason_params = dict(decision.reason_params)
    if decision.reason_code in {"reason.capability_blocked", "reason.confounded"}:
        want_capability = decision.reason_code == "reason.capability_blocked"
        resolved_details = [
            resolved["detail"]
            for confound, resolved in zip(report.report.confounds, confounds)
            if (confound.kind == "capability_unverified") == want_capability
        ]
        if resolved_details:
            reason_params["details"] = "; ".join(resolved_details)
    reason = translator.m(decision.reason_code, **reason_params) or decision.reason

    caveats = []
    for index, caveat in enumerate(report.metadata.caveats):
        code = (
            report.metadata.caveat_codes[index]
            if index < len(report.metadata.caveat_codes)
            else None
        )
        if code:
            caveats.append(
                translator.m(code.get("code", ""), **(code.get("params") or {})) or caveat
            )
        else:
            caveats.append(caveat)

    for view in case_views:
        resolved = []
        for index, note in enumerate(view.get("notes") or []):
            code = (
                (view.get("note_codes") or [])[index]
                if index < len(view.get("note_codes") or [])
                else None
            )
            if code:
                resolved.append(
                    translator.m(code.get("code", ""), **(code.get("params") or {})) or note
                )
            else:
                resolved.append(note)
        view["notes_resolved"] = resolved

    metadata = report.metadata
    # The environment block carries a short note that the engine wrote in
    # English; the reader sees it in their own language.
    environment = dict(metadata.environment or {})
    if "note" in environment:
        environment["note"] = translator.s("env_note_value")
    status_detail = (
        translator.m(metadata.status_detail_code, **(metadata.status_detail_params or {}))
        or metadata.status_detail
    )

    # Trace events are raw runtime records; their type and status get a
    # readable label, the raw value stays in the tooltip/details.
    event_type_labels = {
        "session": translator.s("event_session"),
        "init": translator.s("event_init"),
        "tool_call": translator.s("event_tool_call"),
        "tool_result": translator.s("event_tool_result"),
        "agent_output": translator.s("event_agent_output"),
        "result": translator.s("event_result"),
        "error": translator.s("event_error"),
        "reasoning": translator.s("event_reasoning"),
    }
    event_status_labels = {
        "success": translator.s("status_success"),
        "error": translator.s("status_error"),
        "initiated": translator.s("status_initiated"),
        "denied": translator.s("status_denied"),
    }

    verdict = decision.verdict.value
    return {
        "report": report,
        "spec": report.experiment,
        "comparison": report.report,
        "cases": report.cases,
        "case_views": case_views,
        "metadata": report.metadata,
        "environment": environment,
        "event_type_labels": event_type_labels,
        "event_status_labels": event_status_labels,
        "L": translator.ui(),
        "language": translator.language,
        "decision": {
            "verdict": verdict,
            "label": translator.s(f"verdict_{verdict}"),
            "reason": reason,
            "notes": notes,
            "decided_at": decision.decided_at,
        },
        "confounds": confounds,
        "caveats": caveats,
        "status_detail": status_detail,
        "metric_cards": _metric_cards(report, translator.language),
        "primary_card": _primary_metric_card(report, translator.language),
        "movement": _movement(report),
        "totals": _totals(report),
        "trace_diff_rows": _trace_diff_rows(report),
        "max_abs_delta": max_abs,
        "data_json": json_for_script(report.to_payload()),
        "generator": "cc-eval",
    }


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def render_html(
    report: ReportModel,
    *,
    template: str | Path | None = None,
    language: str | None = None,
) -> str:
    """Render the interactive HTML report."""
    if not isinstance(report, ReportModel):  # pragma: no cover - defensive
        raise TypeError("render_html expects a ReportModel")
    env_template = _load_template(template)
    return env_template.render(**build_context(report, language))


def write_html(
    report: ReportModel,
    path: Path | str,
    *,
    template: str | Path | None = None,
    language: str | None = None,
) -> Path:
    """Write the HTML report to disk and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(report, template=template, language=language), encoding="utf-8")
    return target


def write_model_json(report: ReportModel, path: Path | str) -> Path:
    """Write the report model as JSON (the machine-readable twin of the HTML)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json(), encoding="utf-8")
    return target


def load_model_json(path: Path | str) -> ReportModel:
    """Load a report model from a ``report.json`` file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReportModel.model_validate(payload)
