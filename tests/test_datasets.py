"""Benchmark loading, versioning and the suite mechanism."""

from __future__ import annotations

from pathlib import Path

from cc_eval.datasets.loader import (
    BenchmarkError,
    load_benchmark,
    materialise_bundle,
    render_prompt,
    validate_benchmark,
)
from cc_eval.isolation.workspace import WorkspaceManager

from .conftest import write_benchmark


def test_benchmark_loads_with_a_content_hash(tmp_path: Path) -> None:
    benchmark = load_benchmark(write_benchmark(tmp_path / "bench"))
    assert benchmark.name == "test-benchmark"
    assert benchmark.version == "v1"
    assert benchmark.case_ids == ["CASE-001"]
    assert len(benchmark.content_hash) == 64


def test_content_hash_changes_when_a_case_changes(tmp_path: Path) -> None:
    directory = write_benchmark(tmp_path / "bench")
    before = load_benchmark(directory).content_hash
    (directory / "cases" / "CASE-001" / "input" / "input.md").write_text(
        "different content", encoding="utf-8"
    )
    after = load_benchmark(directory).content_hash
    assert before != after


def test_missing_case_directory_is_an_error(tmp_path: Path) -> None:
    directory = write_benchmark(tmp_path / "bench")
    (directory / "cases" / "CASE-001" / "task.yaml").unlink()
    try:
        load_benchmark(directory)
    except BenchmarkError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a missing task file must raise")


def test_shipped_suite_is_valid_and_covers_every_task_type() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    benchmark, problems = validate_benchmark(repo_root / "benchmarks" / "suite")
    assert problems == [], problems
    assert len(benchmark.cases) == 5
    task_types = {case.bundle.task_type for case in benchmark.cases}
    assert task_types == {
        "intent_classification",
        "spec_review",
        "cross_module",
        "requirement_coverage",
    }


def test_prompt_placeholders_are_rendered(tmp_path: Path) -> None:
    directory = tmp_path / "bench"
    case_dir = directory / "cases" / "CASE-001"
    case_dir.mkdir(parents=True)
    (case_dir / "task.yaml").write_text(
        "case_id: CASE-001\ntask_type: t\nprompt: 'case {case_id} type {task_type}'\n",
        encoding="utf-8",
    )
    (case_dir / "oracle.yaml").write_text("known_defects: []\n", encoding="utf-8")
    (directory / "manifest.yaml").write_text(
        "name: x\nversion: v1\ncases: [CASE-001]\n", encoding="utf-8"
    )
    benchmark = load_benchmark(directory)
    assert render_prompt(benchmark.cases[0].bundle) == "case CASE-001 type t"


def test_unknown_placeholder_is_reported(tmp_path: Path) -> None:
    directory = tmp_path / "bench"
    case_dir = directory / "cases" / "CASE-001"
    case_dir.mkdir(parents=True)
    (case_dir / "task.yaml").write_text(
        "case_id: CASE-001\ntask_type: t\nprompt: '{nope}'\n", encoding="utf-8"
    )
    (case_dir / "oracle.yaml").write_text("known_defects: []\n", encoding="utf-8")
    (directory / "manifest.yaml").write_text(
        "name: x\nversion: v1\ncases: [CASE-001]\n", encoding="utf-8"
    )
    benchmark = load_benchmark(directory)
    try:
        render_prompt(benchmark.cases[0].bundle)
    except BenchmarkError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unknown placeholder must raise")


def test_materialised_bundle_contains_the_task_but_not_the_oracle(tmp_path: Path) -> None:
    benchmark = load_benchmark(write_benchmark(tmp_path / "bench"))
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("RUN-1")
    materialise_bundle(benchmark.cases[0], workspace)

    assert workspace.prompt_file.exists()
    assert (workspace.input_dir / "input.md").exists()
    assert not list(workspace.root.rglob("oracle.yaml"))
    # The expected output location is prepared for the agent.
    assert workspace.resolve_output("out/result.json").parent.exists()
