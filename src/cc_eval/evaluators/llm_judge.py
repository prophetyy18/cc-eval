"""Optional LLM-as-a-Judge evaluator.

Guard rails implemented here, because a judge is a *measurement instrument*,
not an oracle:

* the judge always uses a **fixed, versioned rubric** (the same rubric file for
  every run of an experiment);
* the judge never sees which side of the comparison it is looking at - the
  baseline/candidate labels are stripped before the prompt is built and the
  order is randomised;
* the judge model and its version are recorded with every score;
* the judge output is stored as a *judgement*, clearly separated from the
  deterministic ground truth, and reports are expected to present it as such;
* human annotations can be imported and take precedence over judge scores.

The judge is optional: without a configured backend the evaluator refuses to
run rather than returning a fabricated score.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..core.models import BenchmarkCase, EvaluationResult, RunResult
from .base import empty_result

JUDGE_VERSION = "llm-judge-v1"


class JudgeBackend(Protocol):
    """A backend that can answer a judging prompt."""

    name: str

    def available(self) -> tuple[bool, str]: ...

    def complete(self, prompt: str) -> tuple[str, dict[str, Any]]:
        """Return ``(text, metadata)``; metadata carries the model identity."""
        ...


class ClaudeCliJudgeBackend:
    """Uses the local Claude Code CLI as the judge backend.

    Requires the same credentials as a normal Claude Code session.
    """

    name = "claude-cli"

    def __init__(self, binary: str = "claude", model: str | None = None) -> None:
        self.binary = binary
        self.model = model

    def available(self) -> tuple[bool, str]:
        import shutil

        path = shutil.which(self.binary)
        if not path:
            return False, f"{self.binary} was not found on PATH"
        return True, f"{self.binary} available"

    def complete(self, prompt: str) -> tuple[str, dict[str, Any]]:
        import shutil

        from ..isolation.subprocess import build_env, run_command

        cmd = [shutil.which(self.binary) or self.binary, "-p", "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["--permission-mode", "dontAsk", "--permission-prompts", "none"]
        cmd += ["--no-session-persistence", "--strict-mcp-config"]
        result = run_command(
            cmd,
            cwd=Path.cwd(),
            timeout_sec=300,
            env=build_env(_default_config()),
            stdin_text=prompt,
        )
        if not result.ok:
            raise RuntimeError(f"judge backend failed: {result.stderr.strip()[:300]}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"judge backend returned non-JSON output: {exc}") from exc
        model_usage = payload.get("modelUsage") or {}
        model = next(iter(model_usage), None) or self.model
        return str(payload.get("result") or ""), {
            "backend": self.name,
            "model": model,
            "model_reported_by": "claude.result.modelUsage",
            "cost_usd": payload.get("total_cost_usd"),
        }


class AnthropicApiJudgeBackend:
    """Uses the Anthropic API directly.  Requires the ``judge`` extra."""

    name = "anthropic-api"

    def __init__(self, model: str = "claude-opus-5-5", api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key

    def available(self) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "the 'anthropic' package is not installed (pip install 'cc-eval[judge]')"
        return True, "anthropic SDK available"

    def complete(self, prompt: str) -> tuple[str, dict[str, Any]]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        message = client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "text", None))
        return text, {"backend": self.name, "model": self.model, "model_reported_by": "api"}


def _default_config():
    from ..core.config import EvalConfig
    from pathlib import Path as _P

    return EvalConfig(root=_P("."))


class HumanAnnotation(BaseModel):
    """A human score for one criterion of one run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    criterion_id: str
    score: float = Field(ge=0.0, le=1.0)
    annotator: str = ""
    note: str = ""


def load_annotations(path: Path | str) -> list[HumanAnnotation]:
    """Import human annotations from a JSONL file."""
    annotations: list[HumanAnnotation] = []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"annotation file not found: {p}")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        annotations.append(HumanAnnotation(**json.loads(line)))
    return annotations


class LLMJudgeEvaluator:
    """Scores agent output against a rubric with a language model."""

    name = "llm_judge"

    def __init__(
        self,
        rubric_text: str,
        backend: JudgeBackend,
        rubric_name: str = "ad-hoc",
        rubric_version: str = "v1",
        max_score: float = 1.0,
    ) -> None:
        self.rubric_text = rubric_text
        self.backend = backend
        self.rubric_name = rubric_name
        self.rubric_version = rubric_version
        self.max_score = max_score
        self.version = f"{JUDGE_VERSION}+{rubric_name}-{rubric_version}"

    # -- single output ------------------------------------------------------ #
    def evaluate(self, case: BenchmarkCase, result: RunResult) -> EvaluationResult:
        content = result.output_files.get(case.bundle.expected_output_path, "")
        text = content or result.output_text or ""
        if not text.strip():
            return empty_result(case, result, self.name, self.version, "the run produced no output")

        prompt = self._build_prompt(text, case)
        try:
            raw, meta = self.backend.complete(prompt)
        except Exception as exc:  # noqa: BLE001 - recorded, never faked
            evaluation = empty_result(case, result, self.name, self.version, f"judge failed: {exc}")
            evaluation.metrics = {"judge_score": None}
            return evaluation

        scores = _parse_judge_output(raw)
        mean = sum(scores.values()) / len(scores) if scores else None
        evaluation = EvaluationResult(
            run_id=result.run_id,
            experiment_id=result.experiment_id,
            case_id=case.id,
            variant=result.variant,
            repetition=result.repetition,
            evaluator=self.name,
            evaluator_version=self.version,
            metrics={
                "judge_score": mean,
                "judge_criteria_scored": float(len(scores)),
                "run_ok": 1.0 if result.ok else 0.0,
            },
            notes=[
                "LLM judge scores are judgements, not ground truth",
                f"judge backend: {meta.get('backend')} model: {meta.get('model')}",
            ],
        )
        evaluation.details = {
            "criteria": scores,
            "judge_metadata": meta,
            "raw": raw[:4000],
        }
        return evaluation

    # -- blind pairwise ----------------------------------------------------- #
    def judge_pair(
        self,
        baseline: RunResult,
        candidate: RunResult,
        case: BenchmarkCase,
        *,
        seed: int = 20240101,
    ) -> dict[str, Any]:
        """Blind A/B judgement of two runs.

        Neither the judge nor the prompt mentions which run is the baseline.
        The presentation order is randomised with a fixed seed so the result
        is reproducible.
        """
        b_text = baseline.output_files.get(case.bundle.expected_output_path) or baseline.output_text
        c_text = candidate.output_files.get(case.bundle.expected_output_path) or candidate.output_text
        rng = random.Random(seed)
        order = ["A", "B"]
        rng.shuffle(order)
        first, second = (b_text, c_text) if order == ["A", "B"] else (c_text, b_text)

        prompt = self._build_pair_prompt(first, second, case)
        try:
            raw, meta = self.backend.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"judge failed: {exc}", "metadata": {}}

        preference = _parse_preference(raw)
        winner = None
        if preference in {"A", "B"}:
            shown = order.index(preference)
            winner = "baseline" if shown == 0 else "candidate"
        return {
            "winner": winner,
            "raw_preference": preference,
            "raw": raw[:4000],
            "metadata": meta,
            "presentation_order": order,
        }

    # -- prompts ------------------------------------------------------------ #
    def _build_prompt(self, text: str, case: BenchmarkCase) -> str:
        return (
            "You are grading an AI agent's output against a fixed rubric.\n"
            "Reply with JSON only: {\"scores\": {\"<criterion id>\": <0..1>}, "
            "\"reason\": \"<one sentence>\"}\n\n"
            f"RUBRIC (version {self.rubric_version}):\n{self.rubric_text}\n\n"
            f"TASK TYPE: {case.task_type}\n\n"
            f"AGENT OUTPUT:\n{text[:12000]}\n"
        )

    def _build_pair_prompt(self, first: str, second: str, case: BenchmarkCase) -> str:
        return (
            "Compare two AI agent outputs against the rubric below.\n"
            "The outputs are anonymous: do not guess which system produced which.\n"
            "Reply with JSON only: {\"preferred\": \"A\" | \"B\" | \"tie\", "
            "\"scores\": {\"A\": <0..1>, \"B\": <0..1>}, \"reason\": \"<one sentence>\"}\n\n"
            f"RUBRIC (version {self.rubric_version}):\n{self.rubric_text}\n\n"
            f"TASK TYPE: {case.task_type}\n\n"
            f"OUTPUT A:\n{first[:8000]}\n\n"
            f"OUTPUT B:\n{second[:8000]}\n"
        )


def _parse_judge_output(raw: str) -> dict[str, float]:
    """Extract criterion scores from a judge reply (robust to prose)."""
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    scores = payload.get("scores") if isinstance(payload, dict) else None
    if not isinstance(scores, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, value in scores.items():
        try:
            parsed[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return parsed


def _parse_preference(raw: str) -> str | None:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        preference = payload.get("preferred")
        if isinstance(preference, str) and preference.strip().upper() in {"A", "B", "TIE"}:
            return preference.strip().upper()
    return None
