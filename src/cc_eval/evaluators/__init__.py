"""Evaluators: deterministic matching, rubrics and the optional LLM judge."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "AnthropicApiJudgeBackend",
    "ClaudeCliJudgeBackend",
    "DeterministicEvaluator",
    "Evaluator",
    "HumanAnnotation",
    "JudgeBackend",
    "LLMJudgeEvaluator",
    "MATCHER_VERSION",
    "Rubric",
    "RubricEvaluator",
    "extract_findings",
    "get_evaluator",
    "load_annotations",
    "load_rubric",
    "match_findings",
]

_LAZY = {
    "Evaluator": "cc_eval.evaluators.base.Evaluator",
    "extract_findings": "cc_eval.evaluators.base.extract_findings",
    "DeterministicEvaluator": "cc_eval.evaluators.deterministic.DeterministicEvaluator",
    "Rubric": "cc_eval.evaluators.rubric.Rubric",
    "RubricEvaluator": "cc_eval.evaluators.rubric.RubricEvaluator",
    "load_rubric": "cc_eval.evaluators.rubric.load_rubric",
    "LLMJudgeEvaluator": "cc_eval.evaluators.llm_judge.LLMJudgeEvaluator",
    "JudgeBackend": "cc_eval.evaluators.llm_judge.JudgeBackend",
    "ClaudeCliJudgeBackend": "cc_eval.evaluators.llm_judge.ClaudeCliJudgeBackend",
    "AnthropicApiJudgeBackend": "cc_eval.evaluators.llm_judge.AnthropicApiJudgeBackend",
    "HumanAnnotation": "cc_eval.evaluators.llm_judge.HumanAnnotation",
    "load_annotations": "cc_eval.evaluators.llm_judge.load_annotations",
    "MATCHER_VERSION": "cc_eval.evaluators.matching.MATCHER_VERSION",
    "match_findings": "cc_eval.evaluators.matching.match_findings",
    "get_evaluator": "cc_eval.evaluators.registry.get_evaluator",
}

__getattr__ = lazy_exports(_LAZY)
