"""Intent routing skill - CANDIDATE version.

Two changes over the baseline:

* statements that talk about the same topic are **merged** into one intent
  instead of producing one intent each;
* the ``access-control`` category exists, so a statement that restricts a
  feature to a group of users is no longer mislabelled as a plain feature.
"""

from __future__ import annotations

import re

STOPWORDS = {
    "the", "a", "an", "is", "are", "be", "to", "of", "and", "for", "with", "by",
    "on", "in", "at", "as", "it", "this", "that", "each", "every", "all", "any",
    "must", "should", "shall", "will", "can", "may", "also", "make", "add", "so",
    "only", "just", "when", "we", "i", "we'd", "please",
}

# Topic table: (topic key, distinctive keywords).  The first match wins.
TOPICS: list[tuple[str, list[str]]] = [
    ("dark-mode", ["dark mode", "dark theme"]),
    ("last-tab", ["last selected tab", "last tab", "remember the tab"]),
    ("access-control", ["administrator", "restrict", "admin only", "permissions"]),
    ("notifications", ["notification", "alert"]),
]

ACCESS_CONTROL = re.compile(
    r"\badministrator\w*\b|\brestrict\w*\b|\badmin only\b|\bonly for\b|\bpermissions?\b",
    re.IGNORECASE,
)
BUG = re.compile(r"\bbug\b|\bbroken\b|\bcrash\w*\b|\bfails?\b|\bnot working\b", re.IGNORECASE)


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


def _topic(statement: str) -> str:
    lowered = statement.lower()
    for key, keywords in TOPICS:
        if any(keyword in lowered for keyword in keywords):
            return key
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", lowered).split() if w not in STOPWORDS]
    return "-".join(words[:3]) or "intent"


def _category(statement: str) -> str:
    if ACCESS_CONTROL.search(statement):
        return "access-control"
    if BUG.search(statement):
        return "bugfix"
    return "feature"


def route(payload: dict) -> dict:
    """Classify and merge raw user statements into intents."""
    files = payload.get("input_files") or {}
    text = "\n".join(files.values()) if files else str(payload.get("prompt", ""))
    statements = _statements(text)

    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for statement in statements:
        key = _topic(statement)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(statement)

    findings: list[dict] = []
    for index, key in enumerate(order, start=1):
        merged = grouped[key]
        description = "; ".join(merged)
        category = "feature"
        for statement in merged:
            candidate_category = _category(statement)
            if candidate_category != "feature":
                category = candidate_category
                break
        findings.append(
            {
                "id": f"ENT-{index:03d}",
                "category": category,
                "severity": "medium",
                "description": description,
                "evidence": merged[0],
                "recommendation": "",
            }
        )

    return {
        "findings": findings,
        "extra": {"categories": ["feature", "bugfix", "access-control"], "merged": True},
    }
