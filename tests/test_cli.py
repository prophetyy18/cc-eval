"""CLI behaviour.

These tests drive the real Typer application through its runner, so they
cover argument parsing, exit codes and the files the commands write.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

import pytest
from typer.testing import CliRunner

from cc_eval.cli.main import app

from .conftest import write_benchmark, write_experiment, write_framework

runner = CliRunner()


def _invoke(*args: str):
    return runner.invoke(app, list(args))


def test_init_creates_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    result = _invoke("init", "--root", str(root))
    assert result.exit_code == 0, result.output
    assert (root / "config.yaml").exists()
    for directory in ("experiments", "registry", "runs", "traces", "reports", "workspaces"):
        assert (root / directory).is_dir()


def test_experiment_init_writes_a_template(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    result = _invoke(
        "experiment",
        "init",
        "EXP-900",
        "--root",
        str(root),
        "--title",
        "demo",
        "--hypothesis",
        "h",
        "--variable",
        "v",
    )
    assert result.exit_code == 0, result.output
    spec_file = root / "experiments" / "EXP-900.yaml"
    assert spec_file.exists()

    import yaml

    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    assert spec["id"] == "EXP-900"
    assert spec["hypothesis"] == "h"
    assert spec["decision_rules"]["primary_metric"] == "recall"

    # Creating it twice must not silently overwrite.
    again = _invoke("experiment", "init", "EXP-900", "--root", str(root))
    assert again.exit_code == 2


def test_run_compare_report_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))

    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(
        tmp_path / "bench",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": "alpha",
                "match_keywords": ["alpha"],
            }
        ],
    )
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
        expected_changes=["skills/test_skill.py"],
    )

    run_result = _invoke("run", "EXP-CLI", "--runner", "fake", "--root", str(root), "--file", str(spec_file))
    assert run_result.exit_code == 0, run_result.output
    assert "verdict:" in run_result.output

    compare_result = _invoke("compare", "EXP-CLI", "--root", str(root), "--file", str(spec_file))
    assert compare_result.exit_code == 0, compare_result.output
    assert "verdict: improved" in compare_result.output

    report_result = _invoke(
        "report", "EXP-CLI", "--root", str(root), "--file", str(spec_file), "--format", "both"
    )
    assert report_result.exit_code == 0, report_result.output
    assert "cc-eval report" in report_result.output
    assert (root / "reports" / "EXP-T001.md").exists()
    assert (root / "reports" / "EXP-T001.json").exists()

    payload = json.loads((root / "reports" / "EXP-T001.json").read_text(encoding="utf-8"))
    assert payload["report"]["decision"]["verdict"] == "improved"


def test_compare_without_runs_fails_clearly(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(tmp_path / "bench")
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
    )
    result = _invoke("compare", "EXP-CLI", "--root", str(root), "--file", str(spec_file))
    assert result.exit_code == 2
    assert "no runs found" in result.output


def test_unknown_runner_fails_clearly(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(tmp_path / "bench")
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
    )
    result = _invoke("run", "EXP-CLI", "--runner", "nope", "--root", str(root), "--file", str(spec_file))
    assert result.exit_code == 2
    assert "unknown runner" in result.output


def test_benchmark_validate_reports_problems(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    benchmark = tmp_path / "bench"
    benchmark.mkdir()
    (benchmark / "manifest.yaml").write_text("name: x\nversion: v1\ncases: [NOPE]\n", encoding="utf-8")
    result = _invoke("benchmark", "validate", str(benchmark))
    assert result.exit_code == 2
    assert "case directory missing" in result.output


def test_benchmark_validate_accepts_the_shipped_suite() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    result = _invoke("benchmark", "validate", str(repo_root / "benchmarks" / "suite"))
    assert result.exit_code == 0, result.output
    assert "benchmark is valid" in result.output


def test_runner_list_reports_availability() -> None:
    result = _invoke("runner", "list")
    assert result.exit_code == 0, result.output
    assert "fake" in result.output


def test_dry_run_shows_the_plan_without_running(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(tmp_path / "bench")
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=2,
    )
    result = _invoke(
        "run", "EXP-CLI", "--runner", "fake", "--root", str(root), "--file", str(spec_file), "--dry-run"
    )
    assert result.exit_code == 0, result.output
    assert "plan: 4 run(s)" in result.output
    assert not (root / "runs").exists() or not list((root / "runs").glob("**/*.json"))


@pytest.mark.parametrize("command", [["experiment", "list"], ["registry", "list"]])
def test_empty_registries_do_not_crash(tmp_path: Path, command: list[str]) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    result = _invoke(*command, "--root", str(root))
    assert result.exit_code == 0, result.output


def test_rubric_evaluator_is_usable_from_the_cli(tmp_path: Path, monkeypatch) -> None:
    """The rubric evaluator must be reachable, not just importable."""
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(tmp_path / "bench")
    rubric_dir = tmp_path / "rubrics"
    rubric_dir.mkdir()
    (rubric_dir / "rubric-v1.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "version": "v1",
                "criteria": [
                    {
                        "id": "C1",
                        "name": "mentions the finding",
                        "weight": 1.0,
                        "scorer": {"type": "keyword_present", "keywords": ["alpha"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
        evaluator="rubric-v1",
    )
    # The rubric is looked up in ./rubrics relative to the working directory.
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        "run", "EXP-CLI", "--runner", "fake", "--root", str(root), "--file", str(spec_file)
    )
    assert result.exit_code == 0, result.output
    payload = json.loads((root / "reports" / "EXP-T001.json").read_text(encoding="utf-8"))
    assert payload["report"]["evaluator"] == "rubric"


def test_llm_judge_without_a_backend_fails_clearly(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _invoke("init", "--root", str(root))
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(tmp_path / "fw" / "candidate", detectors=[("alpha", r"alpha")])
    benchmark = write_benchmark(tmp_path / "bench")
    spec_file = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark,
        repetitions=1,
        evaluator="llm-judge-v1",
    )
    result = _invoke(
        "run", "EXP-CLI", "--runner", "fake", "--root", str(root), "--file", str(spec_file)
    )
    assert result.exit_code == 2
    assert "judge-backend" in result.output
