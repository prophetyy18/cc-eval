"""Benchmark loading and task materialisation."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "load_benchmark",
    "load_case",
    "materialise_bundle",
    "render_prompt",
    "validate_benchmark",
    "BenchmarkError",
]

_LAZY = {
    "load_benchmark": "cc_eval.datasets.loader.load_benchmark",
    "load_case": "cc_eval.datasets.loader.load_case",
    "materialise_bundle": "cc_eval.datasets.loader.materialise_bundle",
    "render_prompt": "cc_eval.datasets.loader.render_prompt",
    "validate_benchmark": "cc_eval.datasets.loader.validate_benchmark",
    "BenchmarkError": "cc_eval.datasets.loader.BenchmarkError",
}

__getattr__ = lazy_exports(_LAZY)
