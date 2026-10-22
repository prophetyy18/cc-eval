"""Spec review skill - BASELINE version.

This is the "before" implementation of the review skill.  It looks for two
classes of defect only:

* events that are declared without any ordering rule;
* a specification that never says what happens on failure.

The candidate version adds two more detectors (determinism and concurrent
state).  The difference between the two implementations is exactly the
independent variable of the experiment ``EXP-001``.

The heuristics are intentionally simple and inspectable: they are a stand-in
for a real skill, not a claim about how a language model would review a spec.
"""

from __future__ import annotations

import re

ORDERING_RULE = re.compile(
    r"\bordering\b|\bsequenc\w+\b|\bprecedence\b|\bin order\b|\bfirst[^\n.]{0,40}\bthen\b",
    re.IGNORECASE,
)
FAILURE_RULE = re.compile(
    r"error handling|on error|failure mode|failure behaviour|failure behavior",
    re.IGNORECASE,
)
EVENT_WORD = re.compile(r"\bevent\w*\b", re.IGNORECASE)


def _spec_text(payload: dict) -> str:
    files = payload.get("input_files") or {}
    for name in sorted(files):
        if name.endswith(".md"):
            return files[name]
    return "\n".join(files.values())


def _evidence(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - 40)
    return text[start : match.end() + 40].strip().replace("\n", " ")


def review(payload: dict) -> dict:
    """Return the defects found in the specification."""
    spec = _spec_text(payload)
    findings: list[dict] = []

    if EVENT_WORD.search(spec) and not ORDERING_RULE.search(spec):
        findings.append(
            {
                "id": "SR-001",
                "category": "event_contract",
                "severity": "high",
                "description": (
                    "Events are declared but no ordering or consistency rule is given, "
                    "so two consumers may process the same events in a different order."
                ),
                "evidence": _evidence(spec, EVENT_WORD),
                "recommendation": "State the ordering and consistency rules for the event stream.",
            }
        )

    if not FAILURE_RULE.search(spec):
        findings.append(
            {
                "id": "SR-002",
                "category": "error_contract",
                "severity": "high",
                "description": (
                    "The specification never defines the error handling or the failure "
                    "behaviour of the purchase pipeline."
                ),
                "evidence": "",
                "recommendation": "Define what happens when a step fails and how it is retried.",
            }
        )

    return {"findings": findings, "extra": {"detectors": ["event_contract", "error_contract"]}}
