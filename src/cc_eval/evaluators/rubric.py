"""Rubric evaluator.

A rubric is a *pre-declared, versioned* scoring standard.  It lives in a
YAML file so that the criteria used for an experiment are fixed before the
runs happen and can be audited afterwards.

Only deterministic scorers are executed by this evaluator.  Criteria that need
a human or a model are marked ``unscored``: they are listed in the result, they
are excluded from the score (with the weights renormalised over the scored
criteria), and the run is flagged.  They are never silently ignored and never
silently scored.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..core.models import BenchmarkCase, EvaluationResult, RunResult
from .base import empty_result, output_text_for

RUBRIC_VERSION = "rubric-v1"


class Scorer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    keywords: list[str] = Field(default_factory=list)
    pattern: str | None = None
    patterns: list[str] = Field(default_factory=list)
    min_count: int = 1
    max_count: int | None = None
    field: str | None = None


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    weight: float = 1.0
    description: str = ""
    scorer: Scorer


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str = ""
    criteria: list[Criterion] = Field(default_factory=list)
    source_path: str = ""
    content_hash: str = ""

    def hash(self) -> str:
        import hashlib

        payload = json.dumps(
            self.model_dump(mode="json", exclude={"source_path"}), sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_rubric(path: Path | str) -> Rubric:
    """Load a rubric YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"rubric not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rubric = Rubric(**data)
    rubric.source_path = str(p)
    rubric.content_hash = rubric.hash()
    return rubric


class RubricEvaluator:
    """Scores a run against a rubric's deterministic criteria."""

    name = "rubric"

    def __init__(self, rubric: Rubric) -> None:
        self.rubric = rubric
        self.version = f"{RUBRIC_VERSION}+{rubric.name}-{rubric.version}"

    def evaluate(self, case: BenchmarkCase, result: RunResult) -> EvaluationResult:
        wanted = case.bundle.output_glob or case.bundle.expected_output_path
        content = output_text_for(result, wanted)
        text = content or result.output_text or ""
        document: Any = None
        try:
            document = json.loads(content) if content else None
        except json.JSONDecodeError:
            document = None

        scores: list[float] = []
        weights: list[float] = []
        details: dict[str, Any] = {}
        unscored: list[str] = []
        notes: list[str] = []

        for criterion in self.rubric.criteria:
            outcome = _score_criterion(criterion, text, document)
            if outcome is None:
                unscored.append(criterion.id)
                continue
            scores.append(outcome)
            weights.append(criterion.weight)
            details[criterion.id] = {
                "name": criterion.name,
                "score": outcome,
                "weight": criterion.weight,
            }

        if unscored:
            notes.append(
                "criteria not scored by the deterministic rubric evaluator: "
                + ", ".join(unscored)
            )
        if not weights:
            return empty_result(
                case,
                result,
                self.name,
                self.version,
                "the rubric has no deterministically scorable criteria",
            )

        score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        metrics: dict[str, float | None] = {
            "rubric_score": score,
            "rubric_criteria_scored": float(len(weights)),
            "rubric_criteria_unscored": float(len(unscored)),
            "valid_output": 1.0 if document is not None else 0.0,
            "run_ok": 1.0 if result.ok else 0.0,
            "duration_sec": (result.duration_ms / 1000.0) if result.duration_ms is not None else None,
            "total_tokens": float(result.usage.total_tokens) if result.usage.total_tokens is not None else None,
            "cost_usd": result.usage.cost_usd,
        }

        return EvaluationResult(
            run_id=result.run_id,
            experiment_id=result.experiment_id,
            case_id=case.id,
            variant=result.variant,
            repetition=result.repetition,
            evaluator=self.name,
            evaluator_version=self.version,
            metrics=metrics,
            details=details,
            notes=notes,
        )


def _score_criterion(criterion: Criterion, text: str, document: Any) -> float | None:
    """Return 0.0/1.0 for a criterion, or ``None`` when it cannot be scored
    deterministically."""
    scorer = criterion.scorer
    kind = scorer.type

    if kind in {"manual", "llm_judge"}:
        return None

    if kind == "keyword_present":
        if not scorer.keywords:
            return None
        lowered = text.lower()
        return 1.0 if any(k.lower() in lowered for k in scorer.keywords) else 0.0

    if kind == "keyword_absent":
        if not scorer.keywords:
            return None
        lowered = text.lower()
        return 0.0 if any(k.lower() in lowered for k in scorer.keywords) else 1.0

    if kind == "regex":
        if not scorer.pattern:
            return None
        return 1.0 if re.search(scorer.pattern, text, re.IGNORECASE) else 0.0

    if kind == "forbidden_pattern":
        if not scorer.patterns:
            return None
        for pattern in scorer.patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return 0.0
        return 1.0

    if kind == "count_min":
        if not scorer.pattern:
            return None
        found = len(re.findall(scorer.pattern, text, re.IGNORECASE))
        return 1.0 if found >= scorer.min_count else 0.0

    if kind == "count_max":
        if not scorer.pattern or scorer.max_count is None:
            return None
        found = len(re.findall(scorer.pattern, text, re.IGNORECASE))
        return 1.0 if found <= scorer.max_count else 0.0

    if kind == "json_field_present":
        if not scorer.field:
            return None
        return 1.0 if _has_path(document, scorer.field) else 0.0

    return None


def _has_path(document: Any, dotted: str) -> bool:
    node = document
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return node is not None
