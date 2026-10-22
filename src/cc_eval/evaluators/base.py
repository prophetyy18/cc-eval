"""Evaluator interface and shared helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

from ..core.models import BenchmarkCase, EvaluationResult, RunResult

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@runtime_checkable
class Evaluator(Protocol):
    """Scores one run against one case."""

    name: str
    version: str

    def evaluate(self, case: BenchmarkCase, result: RunResult) -> EvaluationResult:
        ...


def extract_findings(result: RunResult, expected_path: str) -> tuple[list[dict], str | None]:
    """Pull the agent's structured findings out of a run.

    Order of preference:

    1. the file the task asked the agent to write;
    2. the agent's final message, if it contains a JSON document.

    Returns ``(findings, error)``.  ``error`` is set when no usable document
    could be found - the caller then scores the run as invalid rather than
    silently treating it as "no findings".
    """
    content = result.output_files.get(expected_path)
    if content is None:
        for key, value in result.output_files.items():
            if key.endswith(expected_path):
                content = value
                break

    document: Any = None
    if content is not None:
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            return [], f"output file {expected_path} is not valid JSON: {exc}"

    if document is None:
        text = (result.output_text or "").strip()
        if not text:
            return [], "the agent produced neither an output file nor a final message"
        candidate = _first_json_object(text)
        if candidate is None:
            return [], "no JSON object could be recovered from the agent's final message"
        document = candidate

    if isinstance(document, list):
        document = {"findings": document}
    if not isinstance(document, dict):
        return [], f"agent output must be a JSON object, got {type(document).__name__}"

    findings = document.get("findings")
    if findings is None:
        findings = document.get("items") or document.get("results") or []
    if not isinstance(findings, list):
        return [], "'findings' must be a list"
    cleaned = [f for f in findings if isinstance(f, dict)]
    return cleaned, None


def _first_json_object(text: str) -> dict | None:
    """Best-effort recovery of a JSON object from free text."""
    blocks = _JSON_BLOCK.findall(text)
    for block in blocks:
        try:
            parsed = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def empty_result(
    case: BenchmarkCase,
    result: RunResult,
    evaluator: str,
    version: str,
    error: str | None = None,
) -> EvaluationResult:
    """An evaluation result for a run that could not be scored."""
    return EvaluationResult(
        run_id=result.run_id,
        experiment_id=result.experiment_id,
        case_id=case.id,
        variant=result.variant,
        repetition=result.repetition,
        evaluator=evaluator,
        evaluator_version=version,
        metrics={},
        errors=[error] if error else [],
    )
