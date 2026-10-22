"""Intent routing skill - BASELINE version.

The baseline classifies every user statement on its own and only knows the
``feature`` and ``bugfix`` categories.  It therefore produces one intent per
statement, misses overlapping statements that should be merged, and
mislabels access-control requirements.
"""

from __future__ import annotations

import re

STATEMENT = re.compile(r"^\s*(?:[-*]\s*)?(\d+[.)]\s*)?(.+?)\s*$", re.MULTILINE)


def _statements(text: str) -> list[str]:
    statements: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*\d.)\s]+", "", line).strip()
        if line:
            statements.append(line)
    return statements


def _category(statement: str) -> str:
    lowered = statement.lower()
    if re.search(r"\bbug\b|\bbroken\b|\bcrash\w*\b|\bfails?\b", lowered):
        return "bugfix"
    return "feature"


def route(payload: dict) -> dict:
    """Classify raw user statements into intents."""
    files = payload.get("input_files") or {}
    text = "\n".join(files.values()) if files else str(payload.get("prompt", ""))
    findings: list[dict] = []
    for index, statement in enumerate(_statements(text), start=1):
        findings.append(
            {
                "id": f"ENT-{index:03d}",
                "category": _category(statement),
                "severity": "medium",
                "description": statement,
                "evidence": statement,
                "recommendation": "",
            }
        )
    return {
        "findings": findings,
        "extra": {"categories": ["feature", "bugfix"], "merged": False},
    }
