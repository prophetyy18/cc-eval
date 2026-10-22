"""Scenario 1 - a complete A/B experiment with the fake runner.

The test asserts the whole loop: run -> trace -> evaluation -> comparison ->
report, and that the baseline and the candidate really do produce different
results (so the pipeline is measuring something, not just plumbing).
"""

from __future__ import annotations

import json
from pathlib import Path

from cc_eval.comparison.compare import compare
from cc_eval.core.experiment import load_experiment
from cc_eval.core.models import Verdict
from cc_eval.reports import render_json, render_markdown

from .conftest import run_experiment, write_experiment


def test_full_ab_experiment(tmp_path: Path, framework_pair, benchmark, workspace_root) -> None:
    baseline_dir, candidate_dir = framework_pair
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark,
        repetitions=2,
        expected_changes=["skills/test_skill.py"],
    )

    records, runner = run_experiment(spec_path, workspace_root)
    spec = load_experiment(spec_path)

    # -- both variants were run, on the same cases, the same number of times -- #
    assert len(records) == 4  # 2 variants x 1 case x 2 repetitions
    assert {r.result.variant for r in records} == {"baseline", "candidate"}
    assert {r.result.repetition for r in records} == {1, 2}

    # -- the results actually differ ---------------------------------------- #
    baseline_scores = [r.evaluation.metrics["recall"] for r in records if r.result.variant == "baseline"]
    candidate_scores = [r.evaluation.metrics["recall"] for r in records if r.result.variant == "candidate"]
    assert baseline_scores == [0.0, 0.0]
    assert candidate_scores == [1.0, 1.0]

    # -- traces were written, raw and normalised ---------------------------- #
    traces_dir = workspace_root / "traces" / spec.id
    assert traces_dir.exists()
    normalised = list(traces_dir.glob("*.json"))
    assert len(normalised) == 4
    payload = json.loads(normalised[0].read_text(encoding="utf-8"))
    assert payload["events"], "the normalised trace must contain events"
    assert payload["capabilities"], "the trace must record capability evidence"

    # -- run records were persisted ----------------------------------------- #
    run_records = list((workspace_root / "runs" / spec.id).glob("*.json"))
    assert len(run_records) == 4

    # -- the comparison is produced and derived from the rules --------------- #
    report = compare(
        spec,
        records,
        dataset_version="v1",
        dataset_hash="abc",
        evaluator="deterministic",
    )
    assert report.decision.verdict is Verdict.IMPROVED
    assert report.metrics
    recall = next(m for m in report.metrics if m.metric == "recall")
    assert recall.baseline == 0.0
    assert recall.candidate == 1.0
    assert recall.delta == 1.0

    # -- both report formats render ---------------------------------------- #
    markdown = render_markdown(report, spec)
    # The default report language is Chinese.
    assert "结论: 改善" in markdown
    # ... and the English rendering is one flag away.
    assert "Verdict: IMPROVED" in render_markdown(report, spec, language="en")
    assert "CASE-001" in markdown
    json_payload = json.loads(render_json(report, spec))
    assert json_payload["schema"] == "cc-eval/report/v1"
    assert json_payload["report"]["decision"]["verdict"] == "improved"


def test_run_plan_interleaves_variants(tmp_path: Path) -> None:
    """The two variants must alternate so neither side owns the first slot."""
    from cc_eval.core.experiment import build_run_plan
    from cc_eval.core.models import ExperimentSpec, Variant

    spec = ExperimentSpec(
        id="EXP-T002",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 4, "order": "alternating", "seed": 7},
    )
    plan = build_run_plan(spec, ["A", "B"])
    first_positions = [r.variant for r in plan.runs if r.order_index % 2 == 0]
    assert first_positions.count("baseline") == len(first_positions) / 2
    assert len(plan.runs) == 16  # 2 variants x 2 cases x 4 repetitions


def test_repetitions_are_ordered_and_grouped(tmp_path: Path) -> None:
    from cc_eval.core.experiment import build_run_plan
    from cc_eval.core.models import ExperimentSpec, Variant

    spec = ExperimentSpec(
        id="EXP-T003",
        baseline=Variant(name="baseline", framework={"type": "path", "path": "x"}),
        candidate=Variant(name="candidate", framework={"type": "path", "path": "y"}),
        controls={"repetitions": 3, "order": "sequential", "seed": 7},
    )
    plan = build_run_plan(spec, ["A"])
    assert [r.repetition for r in plan.runs] == [1, 1, 2, 2, 3, 3]
