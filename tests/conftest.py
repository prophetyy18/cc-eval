"""Shared test fixtures.

The tests never touch the developer's real cc-eval workspace: every test
builds its own framework tree, benchmark and workspace under ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cc_eval.core.config import EvalConfig, EvalPaths
from cc_eval.core.experiment import ExperimentRunner, RunRecord
from cc_eval.core.models import ExperimentSpec, Variant
from cc_eval.datasets.loader import load_benchmark
from cc_eval.evaluators import get_evaluator
from cc_eval.runners import get_runner
from cc_eval.runners.base import RunConfig

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #

SKILL_SOURCE = '''\
"""A deterministic test skill."""

import re

DETECTORS = {detectors!r}


def {entrypoint}(payload):
    text = "\\n".join((payload.get("input_files") or {{}}).values())
    findings = []
    for index, (category, pattern) in enumerate(DETECTORS, start=1):
        if re.search(pattern, text, re.IGNORECASE):
            findings.append({{
                "id": "T-{{:03d}}".format(index),
                "category": category,
                "severity": "high",
                "description": "Detected {{}} in the input.".format(category),
                "evidence": "pattern matched",
            }})
    return {{"findings": findings}}
'''


def write_framework(
    root: Path,
    *,
    detectors: list[tuple[str, str]],
    entrypoint: str = "run",
    version: str = "v1",
) -> Path:
    """Create a minimal framework tree with one skill."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    (root / "skills" / "test_skill.py").write_text(
        SKILL_SOURCE.format(detectors=detectors, entrypoint=entrypoint), encoding="utf-8"
    )
    manifest = {
        "name": "test-framework",
        "version": version,
        "skills": [
            {
                "name": "test-skill",
                "module": "skills/test_skill.py",
                "entrypoint": entrypoint,
                "task_types": ["test_task"],
            }
        ],
    }
    (root / "skills.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return root


def write_benchmark(
    root: Path,
    *,
    case_id: str = "CASE-001",
    task_type: str = "test_task",
    known_defects: list[dict] | None = None,
    expect_no_findings: bool = False,
    input_text: str = "the input mentions alpha",
) -> Path:
    """Create a minimal benchmark with one case."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "cases" / case_id / "input").mkdir(parents=True)
    (root / "cases" / case_id / "input" / "input.md").write_text(input_text, encoding="utf-8")
    (root / "cases" / case_id / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": case_id,
                "task_type": task_type,
                "title": "test case",
                "prompt": "Read task/input/input.md and write findings to out/result.json.",
                "expected_output_path": "out/result.json",
                "tags": ["test"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "cases" / case_id / "oracle.yaml").write_text(
        yaml.safe_dump(
            {
                "known_defects": known_defects or [],
                "expect_no_findings": expect_no_findings,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(
            {"name": "test-benchmark", "version": "v1", "cases": [case_id]}, sort_keys=False
        ),
        encoding="utf-8",
    )
    return root


def write_experiment(
    path: Path,
    *,
    experiment_id: str = "EXP-T001",
    baseline_dir: Path,
    candidate_dir: Path,
    benchmark_dir: Path,
    repetitions: int = 2,
    expected_changes: list[str] | None = None,
    primary_metric: str = "recall",
    min_delta: float = 0.05,
    min_repetitions: int = 1,
    guard_metrics: list[str] | None = None,
    baseline_config: dict | None = None,
    candidate_config: dict | None = None,
    evaluator: str = "deterministic-v1",
    rubric_dir: Path | None = None,
) -> Path:
    """Write an experiment specification."""
    spec = ExperimentSpec(
        id=experiment_id,
        title=f"Test experiment {experiment_id}",
        hypothesis="The candidate finds the planted defect and the baseline does not.",
        independent_variable="the test skill implementation",
        baseline=Variant(
            name="baseline",
            framework={"type": "path", "path": str(baseline_dir)},
            config=baseline_config or {},
            expected_capabilities=["test-skill"],
        ),
        candidate=Variant(
            name="candidate",
            framework={"type": "path", "path": str(candidate_dir)},
            config=candidate_config or {},
            expected_capabilities=["test-skill"],
        ),
        controls={
            "runtime": "fake",
            "benchmark": str(benchmark_dir),
            "repetitions": repetitions,
            "evaluator": evaluator,
            "order": "alternating",
            "seed": 1234,
        },
        metrics=[primary_metric],
        expected_changes=expected_changes if expected_changes is not None else [],
        decision_rules={
            "primary_metric": primary_metric,
            "direction": "higher_is_better",
            "min_delta": min_delta,
            "min_repetitions": min_repetitions,
            "guard_metrics": guard_metrics or [],
            "max_regressed_cases": 0,
            "require_confound_free": True,
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def run_experiment(
    spec_path: Path,
    root: Path,
    *,
    runner: str = "fake",
    repetitions: int | None = None,
) -> tuple[list[RunRecord], ExperimentRunner]:
    """Execute an experiment and return the records."""
    from cc_eval.core.experiment import load_experiment

    spec = load_experiment(spec_path)
    paths = EvalPaths(root)
    paths.ensure()
    benchmark = load_benchmark(spec.controls.benchmark)
    evaluator = get_evaluator(spec.controls.evaluator)
    agent = get_runner(runner)
    experiment_runner = ExperimentRunner(
        spec,
        benchmark=benchmark,
        evaluator=evaluator,
        runner=agent,
        paths=paths,
        config=EvalConfig(root=root),
        run_config=RunConfig(runtime=runner, seed=spec.controls.seed),
    )
    execution = experiment_runner.execute(repetitions=repetitions)
    assert not execution.errors, execution.errors
    return execution.records, experiment_runner


def tree_diff_for(spec, workdir: Path):
    """Compute the framework diff between the two variants of a spec."""
    from cc_eval.core.snapshot import diff_file_maps, materialise

    maps = {}
    for variant in (spec.baseline, spec.candidate):
        materialised = materialise(
            variant, workdir / variant.name, clean=True
        )
        maps[variant.name] = materialised.file_map
    return diff_file_maps(maps[spec.baseline.name], maps[spec.candidate.name])


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def framework_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A baseline framework that finds nothing and a candidate that finds one."""
    baseline = write_framework(
        tmp_path / "frameworks" / "baseline", detectors=[("alpha", r"nothing-matches-this")]
    )
    candidate = write_framework(
        tmp_path / "frameworks" / "candidate", detectors=[("alpha", r"alpha")]
    )
    return baseline, candidate


@pytest.fixture()
def benchmark(tmp_path: Path) -> Path:
    return write_benchmark(
        tmp_path / "benchmarks" / "test",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": "the input mentions alpha",
                "match_keywords": ["alpha"],
            }
        ],
    )


@pytest.fixture()
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "cc-eval-root"
    EvalPaths(root).ensure()
    return root
