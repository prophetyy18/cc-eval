"""Evaluator registry."""

from __future__ import annotations

from pathlib import Path

from .base import Evaluator
from .deterministic import DeterministicEvaluator
from .rubric import RubricEvaluator, load_rubric


def get_evaluator(spec: str, rubric_dir: Path | str | None = None) -> Evaluator:
    """Build an evaluator from its spec string.

    Supported specs:

    ``deterministic-v1``   rule based scoring against the case oracle
    ``rubric-v1``          the rubric named ``rubric-v1.yaml`` (or ``rubric:<name>``)
    ``llm-judge-v1``       requires an explicit judge backend; see the CLI
    """
    normalized = spec.strip()
    if normalized in {"deterministic", "deterministic-v1"}:
        return DeterministicEvaluator()
    if normalized == "rubric-v1" or normalized.startswith("rubric:"):
        name = "rubric-v1" if normalized == "rubric-v1" else normalized.split(":", 1)[1]
        directory = Path(rubric_dir) if rubric_dir else Path("rubrics")
        candidate = directory / f"{name}.yaml"
        if not candidate.exists():
            raise FileNotFoundError(f"rubric file not found: {candidate}")
        return RubricEvaluator(load_rubric(candidate))
    if normalized.startswith("llm-judge"):
        raise ValueError(
            "the llm-judge evaluator needs an explicit judge backend; "
            "use 'cc-eval run --judge-backend claude-cli|anthropic-api' or the python API"
        )
    raise ValueError(f"unknown evaluator spec: {spec!r}")
