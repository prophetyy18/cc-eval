"""Cross-module impact skill - BASELINE version.

The baseline only reports the module that was edited directly.  It does not
follow the dependency graph, so it misses transitive impact.
"""

from __future__ import annotations

import re

DIRECT_EDIT = re.compile(r"^\s*edited\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MODULES = re.compile(r"^\s*-\s*(MOD-[a-z0-9\-]+)\b.*$", re.IGNORECASE | re.MULTILINE)


def _read(payload: dict) -> str:
    files = payload.get("input_files") or {}
    for name in sorted(files):
        if name.endswith(".md"):
            return files[name]
    return "\n".join(files.values())


def analyze(payload: dict) -> dict:
    """Report the modules affected by a change."""
    text = _read(payload)
    findings: list[dict] = []

    edited_match = DIRECT_EDIT.search(text)
    edited: list[str] = []
    if edited_match:
        edited = [part.strip() for part in edited_match.group(1).split(",") if part.strip()]

    for index, module in enumerate(edited, start=1):
        findings.append(
            {
                "id": f"MOD-{index:03d}",
                "category": "module-impact",
                "severity": "medium",
                "description": f"Module {module} is edited directly and must be re-tested.",
                "evidence": f"edited: {module}",
                "recommendation": f"Review the callers of {module}.",
            }
        )

    return {"findings": findings, "extra": {"transitive": False, "modules_seen": len(MODULES.findall(text))}}
