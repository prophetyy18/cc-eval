"""Cross-module impact skill - CANDIDATE version.

The candidate follows the dependency graph: a module is affected not only when
it is edited directly, but also when it depends - directly or transitively -
on the edited module.

This is the independent variable of the cross-module part of the benchmark.
"""

from __future__ import annotations

import re

DIRECT_EDIT = re.compile(r"^\s*edited\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MODULE_LINE = re.compile(r"^\s*-\s*(MOD-[a-z0-9\-]+)\s*(?::|,)?\s*(.*)$", re.IGNORECASE | re.MULTILINE)


def _split_files(files: dict[str, str]) -> tuple[str, str]:
    change = ""
    modules = ""
    for name in sorted(files):
        lowered = name.lower()
        if "module" in lowered:
            modules += files[name] + "\n"
        else:
            change += files[name] + "\n"
    return change, modules


def analyze(payload: dict) -> dict:
    """Report the modules affected by a change, following dependencies."""
    files = payload.get("input_files") or {}
    change, modules_text = _split_files(files)

    dependencies: dict[str, list[str]] = {}
    for match in MODULE_LINE.finditer(modules_text):
        module = match.group(1).upper()
        rest = match.group(2)
        calls = re.findall(r"MOD-[a-z0-9\-]+", rest, re.IGNORECASE)
        dependencies[module] = [c.upper() for c in calls]

    edited_match = DIRECT_EDIT.search(change)
    edited: list[str] = []
    if edited_match:
        edited = [part.strip().upper() for part in edited_match.group(1).split(",") if part.strip()]

    # Depth-first traversal of the dependency graph, deepest-first ordering
    # keeps the directly edited module first in the output.
    affected: list[tuple[str, int]] = []

    def _visit(module: str, depth: int, seen: set[str]) -> None:
        if module in seen:
            return
        seen.add(module)
        affected.append((module, depth))
        for dependency in dependencies.get(module, []):
            _visit(dependency, depth + 1, seen)

    seen: set[str] = set()
    for module in edited:
        _visit(module, 0, seen)

    findings: list[dict] = []
    for index, (module, depth) in enumerate(affected, start=1):
        if depth == 0:
            description = f"Module {module} is edited directly and must be re-tested."
            recommendation = f"Review the callers of {module}."
        else:
            description = (
                f"Module {module} is affected because it is a transitive dependency of "
                "the changed module."
            )
            recommendation = f"Re-test {module} even though it was not edited."
        findings.append(
            {
                "id": f"MOD-{index:03d}",
                "category": "module-impact",
                "severity": "medium",
                "description": description,
                "evidence": f"affected module: {module}",
                "recommendation": recommendation,
            }
        )

    return {
        "findings": findings,
        "extra": {"transitive": True, "modules_seen": len(dependencies)},
    }
