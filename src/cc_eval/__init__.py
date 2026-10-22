"""cc-eval: a controlled-experiment evaluation system for Agent frameworks.

The package is intentionally layered so that the *experiment engine* stays
decoupled from the framework under test:

    core/        experiment specs, registry, variant materialization
    runners/     AgentRunner adapters (fake / claude / codex)
    datasets/    benchmark loading + task bundle materialization
    evaluators/  deterministic / rubric / llm-judge scoring
    traces/      raw + normalized execution traces
    comparison/  aggregation, paired stats, rule-derived verdicts
    reports/     JSON + Markdown rendering
    isolation/   workspace guards, subprocess budgets, redaction
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
