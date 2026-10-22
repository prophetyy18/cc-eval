"""Benchmark loading and agent-visible task bundle materialisation.

A benchmark directory looks like this::

    benchmarks/spec/
      manifest.yaml
      cases/
        SPEC-E001/
          task.yaml        # agent-visible description of the task
          oracle.yaml      # ground truth  (NEVER copied into a workspace)
          input/spec.md    # files the agent is allowed to see

The separation between ``task.yaml``/``input/`` and ``oracle.yaml`` is the
mechanism that keeps golden answers away from the agent under test
(ADR-001).  :func:`materialise_bundle` only ever writes the visible half.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..core.models import Benchmark, BenchmarkCase, Oracle, TaskBundle
from ..isolation.workspace import Workspace

_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".so", ".pyc"}


class BenchmarkError(RuntimeError):
    """Raised when a benchmark directory is malformed."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise BenchmarkError(f"missing file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - surfaced as user error
        raise BenchmarkError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkError(f"expected a mapping at the top level of {path}")
    return data


def _hash_tree(root: Path) -> str:
    """Content hash of a directory tree (paths + contents)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def load_case(case_dir: Path) -> BenchmarkCase:
    """Load one case (visible bundle + hidden oracle)."""
    task_data = _read_yaml(case_dir / "task.yaml")
    oracle_data: dict[str, Any] = {}
    oracle_path = case_dir / "oracle.yaml"
    if oracle_path.exists():
        oracle_data = _read_yaml(oracle_path)

    input_files: dict[str, str] = {}
    input_dir = case_dir / "input"
    if input_dir.exists():
        for path in sorted(input_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in _BINARY_SUFFIXES:
                continue
            rel = str(path.relative_to(input_dir)).replace("\\", "/")
            input_files[rel] = path.read_text(encoding="utf-8", errors="replace")

    task_data.setdefault("case_id", case_dir.name)
    if "input_files" in task_data:
        # Inline inputs are merged with on-disk ones (on-disk wins).
        inline = task_data.pop("input_files") or {}
        merged = {str(k): str(v) for k, v in inline.items()}
        merged.update(input_files)
        input_files = merged
    else:
        task_data["input_files"] = input_files

    try:
        bundle = TaskBundle(**task_data)
        oracle = Oracle(**oracle_data)
    except ValidationError as exc:
        raise BenchmarkError(f"invalid case {case_dir.name}: {exc}") from exc

    return BenchmarkCase(bundle=bundle, oracle=oracle, case_dir=str(case_dir))


def load_benchmark(path: Path | str) -> Benchmark:
    """Load a benchmark directory and compute its content hash."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise BenchmarkError(f"benchmark not found: {root}")
    manifest = _read_yaml(root / "manifest.yaml")

    cases_dir = root / "cases"
    case_ids = manifest.get("cases")
    if not case_ids:
        raise BenchmarkError(f"{root}/manifest.yaml does not list any cases")

    cases: list[BenchmarkCase] = []
    for case_id in case_ids:
        entry = str(case_id)
        # A manifest may either name a case directory under ``cases/`` or give
        # a path relative to the benchmark root (which lets one benchmark
        # assemble a suite out of several others).
        if "/" in entry or entry.startswith("."):
            case_dir = (root / entry).resolve()
        else:
            case_dir = cases_dir / entry
        if not case_dir.exists():
            raise BenchmarkError(f"case directory missing: {case_dir}")
        cases.append(load_case(case_dir))

    return Benchmark(
        name=str(manifest.get("name", root.name)),
        version=str(manifest.get("version", "0")),
        description=str(manifest.get("description", "")),
        path=str(root),
        content_hash=_hash_tree(root),
        cases=cases,
    )


def validate_benchmark(path: Path | str) -> tuple[Benchmark, list[str]]:
    """Load a benchmark and return human-readable problems (if any)."""
    problems: list[str] = []
    bench = load_benchmark(path)
    seen: set[str] = set()
    for case in bench.cases:
        if case.id in seen:
            problems.append(f"duplicate case id: {case.id}")
        seen.add(case.id)
        if not case.bundle.prompt.strip():
            problems.append(f"{case.id}: empty prompt")
        if (
            case.oracle.total_expected == 0
            and not case.oracle.expected_output
            and not case.oracle.expect_no_findings
        ):
            problems.append(
                f"{case.id}: oracle declares no ground truth "
                "(set expect_no_findings: true if an empty answer is intended)"
            )
        for gold in case.oracle.known_defects:
            if not gold.match_keywords and not gold.evidence:
                problems.append(
                    f"{case.id}/{gold.id}: no match_keywords or evidence "
                    "(the matcher cannot verify this item)"
                )
    return bench, problems


def render_prompt(bundle: TaskBundle) -> str:
    """Render the prompt template for a case."""
    context = {
        "case_id": bundle.case_id,
        "task_type": bundle.task_type,
        "title": bundle.title,
        "input_files": "\n".join(f"- task/input/{name}" for name in sorted(bundle.input_files)),
        "expected_output_path": bundle.expected_output_path,
    }
    try:
        return bundle.prompt.format(**context)
    except KeyError as exc:
        raise BenchmarkError(
            f"case {bundle.case_id}: prompt references unknown placeholder {exc}"
        ) from exc


def materialise_bundle(case: BenchmarkCase, workspace: Workspace) -> Path:
    """Write the agent-visible part of a case into the workspace.

    Only ``task/`` is written.  The oracle is never touched.
    """
    task_dir = workspace.task_dir
    input_dir = workspace.input_dir
    input_dir.mkdir(parents=True, exist_ok=True)

    for rel, content in case.bundle.input_files.items():
        target = (input_dir / rel).resolve()
        if not str(target).startswith(str(input_dir.resolve())):
            raise BenchmarkError(f"input path escapes task dir: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    prompt = render_prompt(case.bundle)
    workspace.prompt_file.write_text(prompt, encoding="utf-8")

    # The expected output location must exist before the agent runs.
    out_path = workspace.resolve_output(case.bundle.expected_output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    return workspace.prompt_file
