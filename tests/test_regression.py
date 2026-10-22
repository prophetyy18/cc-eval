"""Scenario 4 - regression detection.

When the candidate is worse on some cases, the report must name those cases -
an average improvement is not allowed to hide a regression.
"""

from __future__ import annotations

from pathlib import Path

from cc_eval.comparison.compare import compare
from cc_eval.core.experiment import load_experiment
from cc_eval.core.models import Verdict
from cc_eval.reports import render_markdown

from .conftest import (
    run_experiment,
    tree_diff_for,
    write_benchmark,
    write_experiment,
    write_framework,
)


def test_regressed_cases_are_named_in_the_report(
    tmp_path: Path, workspace_root
) -> None:
    """The candidate finds a defect on one case that the baseline got right."""
    baseline = write_framework(
        tmp_path / "fw" / "baseline",
        detectors=[("alpha", r"alpha")],
        version="baseline",
    )
    # The candidate starts inventing a defect that is not there.
    candidate = write_framework(
        tmp_path / "fw" / "candidate",
        detectors=[("alpha", r"alpha"), ("beta", r"beta")],
        version="candidate",
    )
    benchmark = write_benchmark(
        tmp_path / "bench",
        case_id="CASE-REG",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": "alpha present",
                "match_keywords": ["alpha"],
            }
        ],
        input_text="alpha and beta are both present",
    )
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=["skills/test_skill.py", "skills.manifest.json"],
        primary_metric="precision",
        min_delta=0.0,
    )
    records, _ = run_experiment(spec_path, workspace_root)
    spec = load_experiment(spec_path)
    report = compare(
        spec,
        records,
        tree_diff=tree_diff_for(spec, tmp_path / "diff"),
        dataset_version="v1",
        evaluator="deterministic",
    )

    # The candidate now invents a defect that the baseline did not report.
    candidate_precision = next(
        r.evaluation.metrics["precision"]
        for r in records
        if r.result.variant == "candidate"
    )
    assert candidate_precision == 0.5

    markdown = render_markdown(report, spec)
    assert "CASE-REG" in markdown
    assert "Regressed on a non-primary metric" in markdown
    assert "false_positive_count" in markdown


def test_guard_metric_blocks_an_improved_verdict(tmp_path: Path, workspace_root) -> None:
    """A better average with a worse precision is not an improvement."""
    baseline = write_framework(
        tmp_path / "fw" / "baseline", detectors=[("alpha", r"alpha")], version="baseline"
    )
    candidate = write_framework(
        tmp_path / "fw" / "candidate",
        detectors=[("alpha", r"alpha"), ("beta", r"beta")],
        version="candidate",
    )
    benchmark = write_benchmark(
        tmp_path / "bench",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": "alpha present",
                "match_keywords": ["alpha"],
            }
        ],
        input_text="alpha and beta",
    )
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=["skills/test_skill.py", "skills.manifest.json"],
        primary_metric="recall",
        min_delta=0.0,
        guard_metrics=["precision"],
    )
    records, _ = run_experiment(spec_path, workspace_root)
    spec = load_experiment(spec_path)
    report = compare(
        spec,
        records,
        tree_diff=tree_diff_for(spec, tmp_path / "diff"),
        dataset_version="v1",
        evaluator="deterministic",
    )

    assert report.decision.verdict is Verdict.INCONCLUSIVE
    # The reason names the guard metric that regressed.
    assert "guard metric" in report.decision.reason
    assert "precision" in report.decision.reason


def test_regression_above_the_allowed_count_blocks_the_verdict(
    tmp_path: Path, workspace_root
) -> None:
    """max_regressed_cases = 0 means any regression blocks the verdict."""
    from cc_eval.core.models import EvaluationResult, ExperimentSpec, RunResult, Variant

    spec = ExperimentSpec(
        id="EXP-REG",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 1},
        decision_rules={
            "primary_metric": "recall",
            "min_delta": 0.05,
            "max_regressed_cases": 0,
        },
    )

    def make(variant: str, case: str, score: float):
        return type(
            "R",
            (),
            {
                "result": RunResult(
                    run_id=f"{variant}-{case}",
                    experiment_id="EXP-REG",
                    case_id=case,
                    variant=variant,
                ),
                "evaluation": EvaluationResult(
                    run_id=f"{variant}-{case}",
                    experiment_id="EXP-REG",
                    case_id=case,
                    variant=variant,
                    metrics={"recall": score},
                ),
                "fingerprint": "f",
                "dataset_hash": "h",
                "dataset_version": "v1",
                "evaluator_version": "deterministic-v1",
            },
        )()

    records = [
        make("baseline", "CASE-A", 1.0),
        make("candidate", "CASE-A", 1.0),
        make("baseline", "CASE-B", 1.0),
        make("candidate", "CASE-B", 0.0),
    ]
    report = compare(spec, records)
    regressed = [c for c in report.cases if c.status == "regressed"]
    assert [c.case_id for c in regressed] == ["CASE-B"]
    assert report.decision.verdict is Verdict.INCONCLUSIVE
    assert "regressed" in report.decision.reason
