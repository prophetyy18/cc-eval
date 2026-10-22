"""Requirement coverage skill - CANDIDATE version.

The candidate only accepts a constraint as implemented when its keyword
appears in a **normative** statement of the specification (a line that says
what the system must do).  A constraint that is merely mentioned - for example
in an "out of scope" note - is reported as missing.
"""

from __future__ import annotations

import re

REQUIREMENT = re.compile(r"^\s*(?:[-*]\s*)?(C\d{3})\s*[:.\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

NORMATIVE = re.compile(
    r"\bmust\b|\bshall\b|\brequires?\b|\benforces?\b|\bis limited to\b|\bguarantee\w*\b|"
    r"\balways\b|\bnever\b|\bmay not\b|\bmust not\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "the", "a", "an", "is", "are", "be", "to", "of", "and", "for", "with", "by",
    "on", "in", "at", "as", "it", "this", "that", "each", "every", "all", "any",
    "must", "should", "shall", "will", "can", "may", "has", "have", "had",
}


def _split_files(files: dict[str, str]) -> tuple[str, str]:
    requirements = ""
    document = ""
    for name in sorted(files):
        lowered = name.lower()
        if "requirement" in lowered or "constraint" in lowered:
            requirements += files[name] + "\n"
        elif "spec" in lowered or "document" in lowered:
            document += files[name] + "\n"
    if not document:
        document = "\n".join(
            text for name, text in sorted(files.items())
            if "requirement" not in name.lower() and "constraint" not in name.lower()
        )
    return requirements, document


def _keywords(statement: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", statement.lower()).split()
    content = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return content[-2:] if len(content) >= 2 else content


def _normative_lines(document: str) -> list[str]:
    lines = []
    for line in document.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if NORMATIVE.search(stripped):
            lines.append(re.sub(r"[^a-z0-9 ]", " ", stripped.lower()))
    return lines


def check(payload: dict) -> dict:
    """Report constraints that the specification does not implement normatively."""
    files = payload.get("input_files") or {}
    requirements, document = _split_files(files)
    normative = _normative_lines(document)
    whole = re.sub(r"[^a-z0-9 ]", " ", document.lower())

    findings: list[dict] = []
    for match in REQUIREMENT.finditer(requirements):
        constraint_id = match.group(1).upper()
        statement = match.group(2).strip()
        keywords = _keywords(statement)
        if not keywords:
            continue
        in_normative = any(
            any(keyword in line for keyword in keywords) for line in normative
        )
        if in_normative:
            continue
        mentioned_only = any(keyword in whole for keyword in keywords)
        if mentioned_only:
            evidence = "the constraint is only mentioned in a non-normative part of the document"
        else:
            evidence = "the constraint does not appear in the specification"
        findings.append(
            {
                "id": constraint_id,
                "category": "missing-constraint",
                "severity": "high",
                "description": (
                    f"Constraint {constraint_id} is not implemented normatively: {statement}"
                ),
                "evidence": evidence,
                "recommendation": "Add the constraint to the specification as a normative rule.",
            }
        )

    return {"findings": findings, "extra": {"matcher": "normative-statement"}}
