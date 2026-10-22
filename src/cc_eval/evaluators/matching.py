"""Deterministic matching of agent findings against ground truth.

Matcher ``matcher-v1`` (versioned - the version is recorded in every
:class:`~cc_eval.core.models.EvaluationResult` so a historical score can be
re-interpreted later):

A predicted finding matches a golden item when **any** of the following holds:

1. the predicted ``id`` equals the golden ``id``                -> ``id``
2. the category and severity are equal *and* at least one of the
   human-authored ``match_keywords`` appears in the predicted text -> ``keyword``
3. the category and severity are equal *and* the token-level Jaccard
   similarity of the descriptions is >= 0.5                      -> ``similarity``

Everything else is a false positive.  The rules are deliberately conservative
and human-auditable; no semantic model is involved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.models import GoldenItem, MatchedItem

MATCHER_VERSION = "matcher-v1"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Fields of a predicted finding that participate in text matching.
_TEXT_FIELDS = ("id", "description", "evidence", "recommendation", "title", "summary")


def normalise_category(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def finding_text(finding: dict) -> str:
    parts = []
    for field in _TEXT_FIELDS:
        value = finding.get(field)
        if isinstance(value, str):
            parts.append(value)
    return " \n".join(parts)


@dataclass
class MatchOutcome:
    matched: list[MatchedItem]
    unmatched_golden: list[str]
    unmatched_predictions: list[str]
    false_positives: list[dict]
    false_negatives: list[GoldenItem]


def match_findings(
    golden: list[GoldenItem],
    predictions: list[dict],
    *,
    matcher_version: str = MATCHER_VERSION,
) -> MatchOutcome:
    """Greedy one-to-one matching of predictions against golden items."""
    matched: list[MatchedItem] = []
    used_predictions: set[int] = set()
    matched_golden: set[str] = set()

    # Pass 1: exact id match (strongest signal).
    for gold in golden:
        for index, prediction in enumerate(predictions):
            if index in used_predictions:
                continue
            if str(prediction.get("id", "")).strip() == gold.id:
                matched.append(
                    MatchedItem(
                        golden_id=gold.id,
                        predicted_id=str(prediction.get("id")),
                        matched_by="id",
                        confidence=1.0,
                    )
                )
                used_predictions.add(index)
                matched_golden.add(gold.id)
                break

    # Pass 2: category + severity + keyword.
    for gold in golden:
        if gold.id in matched_golden:
            continue
        for index, prediction in enumerate(predictions):
            if index in used_predictions:
                continue
            if not _same_category_and_severity(gold, prediction):
                continue
            text = finding_text(prediction).lower()
            if any(keyword in text for keyword in gold.match_keywords):
                matched.append(
                    MatchedItem(
                        golden_id=gold.id,
                        predicted_id=str(prediction.get("id") or ""),
                        matched_by="keyword",
                        confidence=0.8,
                    )
                )
                used_predictions.add(index)
                matched_golden.add(gold.id)
                break

    # Pass 3: category + severity + description similarity.
    for gold in golden:
        if gold.id in matched_golden:
            continue
        best_index = -1
        best_score = 0.0
        for index, prediction in enumerate(predictions):
            if index in used_predictions:
                continue
            if not _same_category_and_severity(gold, prediction):
                continue
            score = jaccard(gold.description, str(prediction.get("description", "")))
            if score > best_score:
                best_score = score
                best_index = index
        if best_index >= 0 and best_score >= 0.5:
            matched.append(
                MatchedItem(
                    golden_id=gold.id,
                    predicted_id=str(predictions[best_index].get("id") or ""),
                    matched_by="similarity",
                    confidence=round(best_score, 3),
                )
            )
            used_predictions.add(best_index)
            matched_golden.add(gold.id)

    false_positives = [
        {"id": str(p.get("id") or ""), "description": str(p.get("description") or "")[:200]}
        for i, p in enumerate(predictions)
        if i not in used_predictions
    ]
    false_negatives = [g for g in golden if g.id not in matched_golden]

    return MatchOutcome(
        matched=matched,
        unmatched_golden=[g.id for g in false_negatives],
        unmatched_predictions=[fp["id"] for fp in false_positives],
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def _same_category_and_severity(gold: GoldenItem, prediction: dict) -> bool:
    pred_category = normalise_category(str(prediction.get("category", "")))
    gold_category = normalise_category(gold.category)
    if pred_category != gold_category:
        return False
    pred_severity = str(prediction.get("severity", "")).strip().lower()
    return pred_severity == gold.severity.strip().lower()
