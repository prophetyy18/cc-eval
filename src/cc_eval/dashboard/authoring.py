"""Authoring: turning a form into a case on disk.

The console is an **editor**, not a second source of truth.  What the user
fills in becomes a normal cc-eval case directory:

    benchmarks/<type>/cases/<ID>/
        input/<file>      the text the user typed
        task.yaml         generated from the test type
        oracle.yaml       optional, only when ground truth was declared

That keeps the properties the rest of the system depends on: the dataset has a
content hash, a case is diffable in git, and "declared before the run" is a
property of a file rather than of a database row.

Every path that comes from the network is validated here.  The console is
reachable from the internet; a case id or a benchmark name that escaped its
directory would be an arbitrary file write.
"""

from __future__ import annotations

import re
from datetime import UTC
from pathlib import Path
from typing import Any

import yaml

from .types import TestType

#: A case id is a file name; it may not contain a path separator.
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The keys of a form value.
_SAFE_TAG_RE = re.compile(r"^[A-Za-z0-9_.\-一-鿿]{1,48}$")


class AuthoringError(ValueError):
    """Raised when a form payload cannot be turned into a case."""


def repo_root() -> Path:
    """The cc-eval checkout that holds the benchmarks."""
    return Path(__file__).resolve().parents[3]


def benchmark_dir(test_type: TestType) -> Path:
    """The directory that holds the cases of a test type."""
    root = repo_root()
    target = (root / test_type.benchmark).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise AuthoringError(f"benchmark path escapes the repository: {test_type.benchmark}")
    return target


def _safe_case_id(case_id: str) -> str:
    if not CASE_ID_RE.match(case_id or ""):
        raise AuthoringError(f"invalid case id: {case_id!r}")
    return case_id


def _safe_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        tag = str(tag).strip()
        if not tag:
            continue
        if not _SAFE_TAG_RE.match(tag):
            raise AuthoringError(f"invalid tag: {tag!r}")
        out.append(tag)
    return out


def list_cases(test_type: TestType) -> list[dict[str, Any]]:
    """The cases of a test type, as the console shows them."""
    directory = benchmark_dir(test_type) / "cases"
    cases: list[dict[str, Any]] = []
    if not directory.exists():
        return cases
    for case_dir in sorted(directory.iterdir()):
        if not case_dir.is_dir():
            continue
        task_file = case_dir / "task.yaml"
        if not task_file.exists():
            continue
        data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        cases.append(
            {
                "case_id": str(data.get("case_id", case_dir.name)),
                "title": str(data.get("title", "")),
                "tags": list(data.get("tags") or []),
                "task_type": str(data.get("task_type", "")),
                "has_oracle": (case_dir / "oracle.yaml").exists(),
                "values": read_values(test_type, case_dir),
                "updated_at": _timestamp(task_file),
            }
        )
    return cases


def _timestamp(path: Path) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(
        timespec="seconds"
    )


def read_values(test_type: TestType, case_dir: Path) -> dict[str, Any]:
    """Read a case back into the values of the form."""
    values: dict[str, Any] = {}
    task_file = case_dir / "task.yaml"
    data = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
    for form_field in test_type.form:
        if form_field.type == "tags":
            values[form_field.key] = list(data.get("tags") or [])
            continue
        relative = test_type.input_files.get(form_field.key)
        if not relative:
            values[form_field.key] = data.get(form_field.key, form_field.default)
            continue
        target = case_dir / "input" / relative
        values[form_field.key] = (
            target.read_text(encoding="utf-8") if target.exists() else ""
        )
    return values


def next_case_id(test_type: TestType) -> str:
    """The next free case id of a test type."""
    directory = benchmark_dir(test_type) / "cases"
    used: set[int] = set()
    if directory.exists():
        for case_dir in directory.iterdir():
            match = re.match(rf"^{re.escape(test_type.case_prefix)}(\d+)$", case_dir.name)
            if match:
                used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    return f"{test_type.case_prefix}{number:03d}"


def write_case(
    test_type: TestType,
    values: dict[str, Any],
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a case from a form payload.

    Returns the written case as the console shows it.
    """
    case_id = _safe_case_id(case_id) if case_id else next_case_id(test_type)
    case_dir = (benchmark_dir(test_type) / "cases" / case_id).resolve()
    root = benchmark_dir(test_type).resolve()
    if not str(case_dir).startswith(str(root)):
        raise AuthoringError(f"case path escapes the benchmark: {case_id}")

    task: dict[str, Any] = {
        "case_id": case_id,
        "task_type": test_type.task_type,
        "title": str(values.get("title") or "").strip(),
        "tags": _safe_tags(values.get("tags") or []),
        "prompt": test_type.prompt_template,
        "allowed_tools": list(test_type.allowed_tools),
        "output_paths": list(test_type.output_paths),
    }
    if test_type.outputs and any(o.count_pattern for o in test_type.outputs):
        task["output_glob"] = test_type.outputs[0].collect

    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for form_field in test_type.form:
        relative = test_type.input_files.get(form_field.key)
        if not relative:
            continue
        value = values.get(form_field.key)
        if value is None:
            value = form_field.default if form_field.default is not None else ""
        if form_field.type == "tags":
            continue
        if form_field.required and not str(value).strip():
            raise AuthoringError(f"{form_field.label} 不能为空")
        target = (input_dir / relative).resolve()
        if not str(target).startswith(str(input_dir.resolve())):
            raise AuthoringError(f"input path escapes the case: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(value), encoding="utf-8")

    (case_dir / "task.yaml").write_text(
        yaml.safe_dump(task, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    _register_case(test_type, case_id)
    return {
        "case_id": case_id,
        "title": task["title"],
        "tags": task["tags"],
        "task_type": task["task_type"],
        "has_oracle": (case_dir / "oracle.yaml").exists(),
        "values": read_values(test_type, case_dir),
        "updated_at": _timestamp(case_dir / "task.yaml"),
    }


def _register_case(test_type: TestType, case_id: str) -> None:
    """Add the case to the benchmark manifest, so the loader sees it."""
    manifest_file = benchmark_dir(test_type) / "manifest.yaml"
    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    cases = list(manifest.get("cases") or [])
    if case_id not in cases:
        cases.append(case_id)
        manifest["cases"] = cases
        manifest_file.write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )


def delete_case(test_type: TestType, case_id: str) -> bool:
    """Remove a case directory and its manifest entry."""
    case_id = _safe_case_id(case_id)
    case_dir = (benchmark_dir(test_type) / "cases" / case_id).resolve()
    root = benchmark_dir(test_type).resolve()
    if not str(case_dir).startswith(str(root)) or not case_dir.exists():
        return False
    for path in sorted(case_dir.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    case_dir.rmdir()

    manifest_file = root / "manifest.yaml"
    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    cases = [c for c in (manifest.get("cases") or []) if c != case_id]
    manifest["cases"] = cases
    manifest_file.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return True
