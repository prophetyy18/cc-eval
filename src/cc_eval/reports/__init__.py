"""Report rendering.

Every renderer is a pure function of the :class:`ReportModel`:

    report model  ->  report.json  (machine readable, replayable)
                 ->  report.html  (interactive, self-contained)
                 ->  report.md    (optional, plain text)

The model is built by the evaluation layer; the renderers never recompute a
score or re-derive a verdict.
"""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "ReportModel",
    "build_payload",
    "build_report_model",
    "load_model_json",
    "metric_label",
    "render_html",
    "render_json",
    "render_markdown",
    "write_html",
    "write_json",
    "write_model_json",
]

_LAZY = {
    "render_markdown": "cc_eval.reports.markdown_report.render_markdown",
    "render_json": "cc_eval.reports.json_report.render_json",
    "write_json": "cc_eval.reports.json_report.write_json",
    "build_payload": "cc_eval.reports.json_report.build_payload",
    "ReportModel": "cc_eval.reports.model.ReportModel",
    "build_report_model": "cc_eval.reports.model.build_report_model",
    "metric_label": "cc_eval.reports.model.metric_label",
    "render_html": "cc_eval.reports.html_report.render_html",
    "write_html": "cc_eval.reports.html_report.write_html",
    "write_model_json": "cc_eval.reports.html_report.write_model_json",
    "load_model_json": "cc_eval.reports.html_report.load_model_json",
}

__getattr__ = lazy_exports(_LAZY)
