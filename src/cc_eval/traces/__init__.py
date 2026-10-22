"""Trace normalisation and storage."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "TraceStore",
    "build_capabilities",
    "normalise_claude",
    "normalise_codex",
    "parse_jsonl",
]

_LAZY = {
    "normalise_claude": "cc_eval.traces.normalize.normalise_claude",
    "normalise_codex": "cc_eval.traces.normalize.normalise_codex",
    "parse_jsonl": "cc_eval.traces.normalize.parse_jsonl",
    "build_capabilities": "cc_eval.traces.normalize.build_capabilities",
    "TraceStore": "cc_eval.traces.store.TraceStore",
}

__getattr__ = lazy_exports(_LAZY)
