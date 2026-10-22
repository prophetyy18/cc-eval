"""Requirement coverage skill - BASELINE version.

The baseline considers a constraint *implemented* when any of its distinctive
keywords appears **anywhere** in the specification document.  That is a naive
whole-document substring check, and it produces false negatives: a constraint
that is mentioned in a non-normative "out of scope" note is mistaken for an
implemented constraint.

The candidate version restricts the search to normative statements.
"""

from __future__ import annotations

import re

REQUIREMENT = re.compile(r"^\s*(?:[-*]\s*)?(C\d{3})\s*[:.\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

STOPWORDS = {
    "the", "a", "an", "is", "are", "be", "to", "of", "and", "for", "with", "by",
    "on", "in", "at", "as", "it", "this", "that", "each", "every", "all", "any",
    "must", "should", "shall", "will", "can", "may", "has", "have", "had",
}


def _split_files(files: dict[str, str]) -> tuple[str, str]:
    """Return ``(requirements_text, document_text)``."""
    requirements = ""
    document = ""
    for name in sorted(files):
        lowered = name.lower()
        if "requirement" in lowered or "constraint" in lowered:
            requirements += files[name] + "\n"
        elif "spec" in lowered or "document" in lowered:
            document += files[name] + "\n"
    if not document:
        # Fall back: everything that is not the requirement list is the document.
        document = "\n".join(
            text for name, text in sorted(files.items())
            if "requirement" not in name.lower() and "constraint" not in name.lower()
        )
    return requirements, document


def _keywords(statement: str) -> list[str]:
    words = re.sub(r"[^a-z0-9 ]", " ", statement.lower()).split()
    content = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return content[-2:] if len(content) >= 2 else content


def check(payload: dict) -> dict:
    """Report constraints that the specification does not implement."""
    files = payload.get("input_files") or {}
    requirements, document = _split_files(files)
    doc_lower = re.sub(r"[^a-z0-9 ]", " ", document.lower())

    findings: list[dict] = []
    for match in REQUIREMENT.finditer(requirements):
        constraint_id = match.group(1).upper()
        statement = match.group(2).strip()
        keywords = _keywords(statement)
        if not keywords:
            continue
        if any(keyword in doc_lower for keyword in keywords):
            # Naive check: the keyword appears somewhere in the document.
            continue
        findings.append(
            {
                "id": constraint_id,
                "category": "missing-constraint",
                "severity": "high",
                "description": f"Constraint {constraint_id} is not implemented: {statement}",
                "evidence": statement,
                "recommendation": "Add the constraint to the specification.",
            }
        )

    return {"findings": findings, "extra": {"matcher": "whole-document-keyword"}}
