"""Scenario 2 - control variable checks.

The system must detect a confounder and refuse to attribute the difference to
the single declared variable.  Three kinds of confounder are covered:

* a control that differs between the two sides (the model);
* a framework change that was not declared;
* two variants that are byte-identical (the variable is not present at all).
"""

from __future__ import annotations

from pathlib import Path

from cc_eval.comparison.compare import compare
from cc_eval.core.experiment import load_experiment
from cc_eval.core.models import Verdict
from cc_eval.core.snapshot import diff_file_maps, materialise

from .conftest import run_experiment, tree_diff_for, write_experiment, write_framework


def test_model_mismatch_is_detected(tmp_path: Path, framework_pair, benchmark, workspace_root) -> None:
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=["skills/test_skill.py"],
        # The candidate pins a different model - a classic confounder.
        candidate_config={"model": "some-other-model"},
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

    kinds = {c.kind for c in report.confounds}
    assert "model_mismatch" in kinds
    model_confound = next(c for c in report.confounds if c.kind == "model_mismatch")
    assert model_confound.severity == "blocker"
    assert "model" in model_confound.detail

    # The verdict must not claim an improvement.
    assert report.decision.verdict is Verdict.INVALID
    assert "confound" in report.decision.reason.lower()


def test_unexpected_framework_difference_is_detected(
    tmp_path: Path, framework_pair, benchmark, workspace_root
) -> None:
    baseline_dir, candidate_dir = framework_pair
    # The candidate changes two files but only one is declared.
    (candidate_dir / "skills" / "extra_skill.py").write_text("# extra\n", encoding="utf-8")
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=["skills/test_skill.py"],
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
    confound = next(
        (c for c in report.confounds if c.kind == "unexpected_framework_diff"), None
    )
    assert confound is not None
    assert confound.severity == "blocker"
    assert "skills/extra_skill.py" in confound.detail
    assert report.decision.verdict is Verdict.INVALID


def test_undeclared_framework_difference_is_only_a_warning(
    tmp_path: Path, framework_pair, benchmark, workspace_root
) -> None:
    """Without expected_changes the difference is reported but not fatal."""
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=[],
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
    confound = next(
        (c for c in report.confounds if c.kind == "unexpected_framework_diff"), None
    )
    assert confound is not None
    assert confound.severity == "warning"


def test_identical_variants_are_flagged(tmp_path: Path, framework_pair, benchmark) -> None:
    """If both sides are the same tree, nothing can be measured."""
    baseline_dir, _ = framework_pair
    from cc_eval.core.models import Variant

    materialised_baseline = materialise(
        Variant(name="baseline", framework={"type": "path", "path": str(baseline_dir)}),
        tmp_path / "mat-a",
        clean=True,
    )
    materialised_candidate = materialise(
        Variant(name="candidate", framework={"type": "path", "path": str(baseline_dir)}),
        tmp_path / "mat-b",
        clean=True,
    )
    diff = diff_file_maps(materialised_baseline.file_map, materialised_candidate.file_map)
    assert diff.identical


def test_repetition_mismatch_is_detected(tmp_path: Path, framework_pair, benchmark) -> None:
    """Running one side more often than the other invalidates the pairing."""
    from cc_eval.core.models import EvaluationResult, ExperimentSpec, RunResult, Variant

    spec = ExperimentSpec(
        id="EXP-T010",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 2},
        decision_rules={"primary_metric": "recall"},
    )
    records = []
    for variant, repetitions in (("baseline", 2), ("candidate", 1)):
        for repetition in range(1, repetitions + 1):
            records.append(
                type(
                    "R",
                    (),
                    {
                        "result": RunResult(
                            run_id=f"{variant}-r{repetition}",
                            experiment_id="EXP-T010",
                            case_id="CASE-001",
                            variant=variant,
                            repetition=repetition,
                        ),
                        "evaluation": EvaluationResult(
                            run_id=f"{variant}-r{repetition}",
                            experiment_id="EXP-T010",
                            case_id="CASE-001",
                            variant=variant,
                            repetition=repetition,
                            metrics={"recall": 1.0},
                        ),
                        "fingerprint": "x",
                        "dataset_hash": "y",
                        "dataset_version": "v1",
                        "evaluator_version": "deterministic-v1",
                    },
                )()
            )
    report = compare(spec, records)
    assert any(c.kind == "repetition_mismatch" for c in report.confounds)
    assert report.decision.verdict is Verdict.INVALID


def test_framework_diff_detects_exactly_one_changed_file(
    tmp_path: Path, framework_pair
) -> None:
    """The diff must be precise enough to prove a single-variable experiment."""
    baseline_dir, candidate_dir = framework_pair
    from cc_eval.core.models import Variant

    a = materialise(
        Variant(name="baseline", framework={"type": "path", "path": str(baseline_dir)}),
        tmp_path / "m1",
        clean=True,
    )
    b = materialise(
        Variant(name="candidate", framework={"type": "path", "path": str(candidate_dir)}),
        tmp_path / "m2",
        clean=True,
    )
    diff = diff_file_maps(a.file_map, b.file_map)
    assert diff.changed_files() == ["skills/test_skill.py"]
    assert diff.added == []
    assert diff.removed == []
