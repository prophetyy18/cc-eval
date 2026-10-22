"""Scenario 3 - repeated runs.

The system must keep the individual results and the aggregate apart: a single
run is one sample, the reported number is the mean over repetitions, and the
spread is reported next to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_eval.comparison.aggregate import collect_series
from cc_eval.comparison.compare import compare
from cc_eval.core.experiment import load_experiment
from cc_eval.core.models import EvaluationResult, ExperimentSpec, RunResult, Variant

from .conftest import run_experiment, write_experiment


def _record(variant: str, repetition: int, score: float):
    return type(
        "R",
        (),
        {
            "result": RunResult(
                run_id=f"{variant}-r{repetition}",
                experiment_id="EXP-R01",
                case_id="CASE-001",
                variant=variant,
                repetition=repetition,
            ),
            "evaluation": EvaluationResult(
                run_id=f"{variant}-r{repetition}",
                experiment_id="EXP-R01",
                case_id="CASE-001",
                variant=variant,
                repetition=repetition,
                metrics={"recall": score},
            ),
            "fingerprint": "same",
            "dataset_hash": "h",
            "dataset_version": "v1",
            "evaluator_version": "deterministic-v1",
        },
    )()


def test_each_repetition_is_recorded_separately(
    tmp_path: Path, framework_pair, benchmark, workspace_root
) -> None:
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=3,
        expected_changes=["skills/test_skill.py"],
    )
    records, _ = run_experiment(spec_path, workspace_root)
    assert len(records) == 6  # 2 variants x 3 repetitions

    per_variant: dict[str, list[int]] = {}
    for record in records:
        per_variant.setdefault(record.result.variant, []).append(record.result.repetition)
    for variant, repetitions in per_variant.items():
        assert sorted(repetitions) == [1, 2, 3], variant


def test_aggregate_is_the_mean_not_the_last_run() -> None:
    spec = ExperimentSpec(
        id="EXP-R01",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 3},
        decision_rules={"primary_metric": "recall"},
    )
    records = [
        _record("baseline", 1, 0.2),
        _record("baseline", 2, 0.4),
        _record("baseline", 3, 0.6),
        _record("candidate", 1, 1.0),
        _record("candidate", 2, 1.0),
        _record("candidate", 3, 1.0),
    ]
    report = compare(spec, records)
    recall = next(m for m in report.metrics if m.metric == "recall")
    assert recall.baseline == pytest.approx(0.4)  # the mean, not the last value (0.6)
    assert recall.baseline_std is not None and recall.baseline_std > 0
    assert recall.baseline_n == 3

    case = report.cases[0]
    assert case.baseline_per_repetition == [0.2, 0.4, 0.6]
    assert case.candidate_per_repetition == [1.0, 1.0, 1.0]
    assert case.baseline_variance is not None and case.baseline_variance > 0
    assert case.candidate_variance == 0.0  # identical values have no spread


def test_high_variance_case_is_flagged_for_manual_review() -> None:
    spec = ExperimentSpec(
        id="EXP-R02",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 4},
        decision_rules={"primary_metric": "recall"},
    )
    records = [
        _record("baseline", 1, 0.0),
        _record("baseline", 2, 1.0),
        _record("baseline", 3, 0.0),
        _record("baseline", 4, 1.0),
        _record("candidate", 1, 0.5),
        _record("candidate", 2, 0.5),
        _record("candidate", 3, 0.5),
        _record("candidate", 4, 0.5),
    ]
    report = compare(spec, records)
    case = report.cases[0]
    assert case.baseline_variance is not None and case.baseline_variance > 0.1
    assert any("variance" in note for note in case.notes)
    assert "CASE-001" in report.manual_review_cases


def test_too_few_repetitions_is_inconclusive(
    tmp_path: Path, framework_pair, benchmark, workspace_root
) -> None:
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=1,
        min_repetitions=3,
        expected_changes=["skills/test_skill.py"],
    )
    records, _ = run_experiment(spec_path, workspace_root)
    spec = load_experiment(spec_path)
    report = compare(spec, records)
    assert report.decision.verdict.value == "inconclusive"
    assert "repetition" in report.decision.reason


def test_series_collects_every_repetition(tmp_path: Path, framework_pair, benchmark, workspace_root) -> None:
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=3,
        expected_changes=["skills/test_skill.py"],
    )
    records, _ = run_experiment(spec_path, workspace_root)
    series = collect_series(records, "candidate", "recall")
    assert series.n_runs == 3
    assert series.n_cases == 1
    assert len(series.by_case["CASE-001"]) == 3
