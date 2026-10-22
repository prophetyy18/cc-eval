"""Spec review skill - CANDIDATE version.

Adds two detectors to the baseline skill:

* ``determinism``        - the specification depends on the wall clock;
* ``state_consistency``  - concurrent writes are not serialised.

This is the independent variable of experiment ``EXP-001``.
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
WALL_CLOCK = re.compile(
    r"\bcurrent time\b|\bwall[- ]clock\b|\breal[- ]time clock\b|\bsystem time\b|\bDate\.now\b",
    re.IGNORECASE,
)
CONCURRENCY = re.compile(r"\bconcurrently\b|\bsimultaneously\b|\bin parallel\b", re.IGNORECASE)
SERIALISATION = re.compile(
    r"\block\w*\b|\bserialis\w+\b|\bserializ\w+\b|\batomic\w*\b|\boptimistic\b|\bversion check\b",
    re.IGNORECASE,
)


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

    if WALL_CLOCK.search(spec):
        findings.append(
            {
                "id": "SR-003",
                "category": "determinism",
                "severity": "medium",
                "description": (
                    "The specification reads the current wall-clock time, so a replay of the "
                    "same input is not deterministic and the result cannot be reproduced."
                ),
                "evidence": _evidence(spec, WALL_CLOCK),
                "recommendation": "Inject the clock and persist the resolved time in the event.",
            }
        )

    if CONCURRENCY.search(spec) and not SERIALISATION.search(spec):
        findings.append(
            {
                "id": "SR-004",
                "category": "state_consistency",
                "severity": "medium",
                "description": (
                    "Concurrent updates to the same purchase are not serialised, so the "
                    "resulting state after two conflicting writes is not defined."
                ),
                "evidence": _evidence(spec, CONCURRENCY),
                "recommendation": "Serialise the writes with a lock or an optimistic version check.",
            }
        )

    return {
        "findings": findings,
        "extra": {
            "detectors": [
                "event_contract",
                "error_contract",
                "determinism",
                "state_consistency",
            ]
        },
    }
