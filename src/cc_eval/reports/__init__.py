"""Report rendering."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "build_payload",
    "render_json",
    "render_markdown",
    "write_json",
]

_LAZY = {
    "render_markdown": "cc_eval.reports.markdown_report.render_markdown",
    "render_json": "cc_eval.reports.json_report.render_json",
    "write_json": "cc_eval.reports.json_report.write_json",
    "build_payload": "cc_eval.reports.json_report.build_payload",
}

__getattr__ = lazy_exports(_LAZY)
