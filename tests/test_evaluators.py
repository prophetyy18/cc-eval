"""Evaluators: matching, deterministic scoring, rubric scoring."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from cc_eval.core.models import (
    BenchmarkCase,
    GoldenItem,
    Oracle,
    RunResult,
    TaskBundle,
    Usage,
)
from cc_eval.evaluators.base import extract_findings
from cc_eval.evaluators.deterministic import DeterministicEvaluator
from cc_eval.evaluators.matching import MATCHER_VERSION, match_findings
from cc_eval.evaluators.rubric import RubricEvaluator, load_rubric


def _case(
    known_defects: list[GoldenItem] | None = None,
    expect_no_findings: bool = False,
) -> BenchmarkCase:
    return BenchmarkCase(
        bundle=TaskBundle(
            case_id="CASE-001",
            task_type="spec_review",
            prompt="p",
            expected_output_path="out/result.json",
        ),
        oracle=Oracle(
            known_defects=known_defects or [],
            expect_no_findings=expect_no_findings,
        ),
    )


def _result(
    findings: list[dict] | None = None,
    *,
    text: str = "",
    files: dict[str, str] | None = None,
):
    payload = dict(files or {})
    if findings is not None and "out/result.json" not in payload:
        payload["out/result.json"] = json.dumps({"findings": findings})
    return RunResult(
        run_id="RUN-1",
        experiment_id="EXP-1",
        case_id="CASE-001",
        variant="baseline",
        output_text=text,
        output_files=payload,
        usage=Usage(source="runtime", input_tokens=10, output_tokens=5, total_tokens=15),
    )


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #


def test_exact_id_match() -> None:
    golden = [GoldenItem(id="F001", category="event", severity="high", description="x")]
    outcome = match_findings(
        golden, [{"id": "F001", "category": "event", "severity": "high", "description": "x"}]
    )
    assert outcome.matched[0].matched_by == "id"
    assert outcome.unmatched_golden == []


def test_keyword_match_requires_category_and_severity() -> None:
    golden = [
        GoldenItem(
            id="F001",
            category="event_contract",
            severity="high",
            description="x",
            match_keywords=["ordering"],
        )
    ]
    # Right keyword, wrong category -> no match.
    outcome = match_findings(
        golden, [{"id": "T1", "category": "other", "severity": "high", "description": "ordering"}]
    )
    assert not outcome.matched
    assert outcome.unmatched_predictions == ["T1"]

    # Right category and severity -> match.
    outcome = match_findings(
        golden,
        [{"id": "T2", "category": "event_contract", "severity": "high", "description": "ordering"}],
    )
    assert outcome.matched[0].matched_by == "keyword"


def test_similarity_match() -> None:
    golden = [
        GoldenItem(
            id="F001",
            category="determinism",
            severity="medium",
            description="the specification depends on the current time",
        )
    ]
    outcome = match_findings(
        golden,
        [
            {
                "id": "T1",
                "category": "determinism",
                "severity": "medium",
                "description": "the specification depends on the current time value",
            }
        ],
    )
    assert outcome.matched
    assert outcome.matched[0].matched_by in {"similarity", "keyword"}


def test_one_to_one_matching() -> None:
    golden = [
        GoldenItem(id="F001", category="a", severity="high", description="x", match_keywords=["k"]),
        GoldenItem(id="F002", category="b", severity="high", description="y", match_keywords=["k"]),
    ]
    outcome = match_findings(
        golden,
        [
            {"id": "T1", "category": "a", "severity": "high", "description": "k"},
            {"id": "T2", "category": "b", "severity": "high", "description": "k"},
        ],
    )
    assert len(outcome.matched) == 2
    assert len({m.predicted_id for m in outcome.matched}) == 2


def test_matcher_version_is_recorded() -> None:
    assert MATCHER_VERSION == "matcher-v1"


# --------------------------------------------------------------------------- #
# deterministic evaluator
# --------------------------------------------------------------------------- #


def test_deterministic_evaluator_scores_recall_and_precision() -> None:
    case = _case(
        [
            GoldenItem(
                id="F001",
                category="alpha",
                severity="high",
                description="alpha",
                match_keywords=["alpha"],
            ),
            GoldenItem(
                id="F002",
                category="beta",
                severity="high",
                description="beta",
                match_keywords=["beta"],
            ),
        ]
    )
    result = _result(
        [{"id": "T1", "category": "alpha", "severity": "high", "description": "alpha"}]
    )
    evaluation = DeterministicEvaluator().evaluate(case, result)
    assert evaluation.metrics["recall"] == 0.5
    assert evaluation.metrics["precision"] == 1.0
    assert evaluation.metrics["false_negative_count"] == 1.0
    assert evaluation.metrics["false_positive_count"] == 0.0
    assert evaluation.unmatched_golden == ["F002"]


def test_unparseable_output_is_an_invalid_run_not_an_empty_one() -> None:
    case = _case([GoldenItem(id="F001", category="alpha", severity="high", description="a")])
    result = _result([], files={"out/result.json": "this is not json"})
    evaluation = DeterministicEvaluator().evaluate(case, result)
    assert evaluation.metrics["valid_output"] == 0.0
    assert evaluation.metrics["recall"] == 0.0
    assert evaluation.errors


def test_empty_ground_truth_gives_full_recall_and_no_false_positive() -> None:
    case = _case([], expect_no_findings=True)
    evaluation = DeterministicEvaluator().evaluate(case, _result([]))
    assert evaluation.metrics["recall"] == 1.0
    assert evaluation.metrics["precision"] == 1.0
    assert evaluation.metrics["false_positive_count"] == 0.0


def test_empty_ground_truth_with_a_finding_is_a_false_positive() -> None:
    case = _case([], expect_no_findings=True)
    evaluation = DeterministicEvaluator().evaluate(
        case, _result([{"id": "T1", "category": "x", "severity": "low", "description": "x"}])
    )
    assert evaluation.metrics["recall"] == 1.0
    assert evaluation.metrics["precision"] == 0.0
    assert evaluation.metrics["false_positive_count"] == 1.0


def test_findings_are_recovered_from_the_final_message() -> None:
    findings, error = extract_findings(
        _result(None, text='Here you go: {"findings": [{"id": "T1"}]}'), "out/result.json"
    )
    assert error is None
    assert findings == [{"id": "T1"}]


def test_missing_output_is_reported_as_an_error() -> None:
    findings, error = extract_findings(_result(None), "out/result.json")
    assert findings == []
    assert error and "neither an output file" in error


# --------------------------------------------------------------------------- #
# rubric evaluator
# --------------------------------------------------------------------------- #


def test_rubric_scores_deterministic_criteria(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.yaml"
    rubric_path.write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "version": "v1",
                "criteria": [
                    {
                        "id": "C1",
                        "name": "cites evidence",
                        "weight": 1.0,
                        "scorer": {"type": "keyword_present", "keywords": ["evidence"]},
                    },
                    {
                        "id": "C2",
                        "name": "no speculation",
                        "weight": 1.0,
                        "scorer": {
                            "type": "forbidden_pattern",
                            "patterns": ["probably", "I think"],
                        },
                    },
                    {
                        "id": "C3",
                        "name": "human judgement",
                        "weight": 1.0,
                        "scorer": {"type": "manual"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rubric = load_rubric(rubric_path)
    evaluator = RubricEvaluator(rubric)
    case = _case()
    evaluation = evaluator.evaluate(
        case, _result([], files={"out/result.json": '{"evidence": "line 3"}'})
    )
    assert evaluation.metrics["rubric_score"] == 1.0
    assert evaluation.metrics["rubric_criteria_scored"] == 2.0
    assert evaluation.metrics["rubric_criteria_unscored"] == 1.0
    # The unscored criterion is reported, never silently ignored.
    assert any("C3" in note for note in evaluation.notes)


def test_rubric_penalises_a_forbidden_pattern(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.yaml"
    rubric_path.write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "version": "v1",
                "criteria": [
                    {
                        "id": "C1",
                        "name": "no speculation",
                        "weight": 1.0,
                        "scorer": {"type": "forbidden_pattern", "patterns": ["probably"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    evaluator = RubricEvaluator(load_rubric(rubric_path))
    evaluation = evaluator.evaluate(
        _case(), _result([], files={"out/result.json": '{"d": "probably wrong"}'})
    )
    assert evaluation.metrics["rubric_score"] == 0.0
