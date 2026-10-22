"""The agent runner interface and shared plumbing.

All three adapters (fake, claude, codex) return the same
:class:`~cc_eval.core.models.RunResult` so that the comparison layer never has
to special-case a runtime.  Where a runtime cannot supply a piece of data the
result says so explicitly instead of filling in a plausible-looking number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.models import (
    DeterminismInfo,
    RunConfig,
    RunResult,
    RuntimeInfo,
    TaskBundle,
    Trace,
    Usage,
)

__all__ = [
    "AgentRunner",
    "BaseRunner",
    "RunConfig",
    "trace_capabilities",
]


@runtime_checkable
class AgentRunner(Protocol):
    """Adapter contract for anything that can execute a task."""

    name: str

    def available(self) -> tuple[bool, str]:
        """Return ``(ok, reason)`` describing whether this runner can run here."""
        ...

    def run(
        self,
        task: TaskBundle,
        config: RunConfig,
        workspace: Any,
        *,
        experiment_id: str = "",
        variant: str = "",
        repetition: int = 1,
    ) -> RunResult:
        """Execute ``task`` inside ``workspace`` and return the outcome."""
        ...


class BaseRunner:
    """Common behaviour for the concrete adapters."""

    name = "base"

    def available(self) -> tuple[bool, str]:  # pragma: no cover - overridden
        return False, "not implemented"

    def _base_result(
        self,
        *,
        task: TaskBundle,
        config: RunConfig,
        experiment_id: str,
        variant: str,
        repetition: int,
        run_id: str,
    ) -> RunResult:
        return RunResult(
            run_id=run_id,
            experiment_id=experiment_id,
            case_id=task.case_id,
            variant=variant,
            repetition=repetition,
            runtime=RuntimeInfo(name=self.name, model=config.model),
            determinism=DeterminismInfo(supported=False, seed=config.seed),
        )

    @staticmethod
    def usage_from_none(reason: str) -> Usage:
        return Usage(source="unavailable")


def trace_capabilities(trace: Trace, expected: list[str]) -> Trace:
    """Attach capability verification for the expected list to a trace."""
    from ..traces.normalize import build_capabilities

    available: list[str] = []
    invoked: list[str] = []
    tools: list[str] = []
    for event in trace.events:
        if event.type == "session":
            available.extend(str(s) for s in event.details.get("skills", []) or [])
            tools.extend(str(s) for s in event.details.get("tools", []) or [])
        if event.type == "tool_call" and event.tool == "Skill":
            args = event.details.get("arguments") or {}
            skill = args.get("skill") or args.get("name")
            if skill:
                invoked.append(str(skill))
    trace.capabilities = build_capabilities(
        expected=expected,
        available=available,
        invoked=invoked,
        tools=tools,
        source="trace",
    )
    return trace


def resolve_output_path(workspace: Any, task: TaskBundle) -> Path:
    return workspace.resolve_output(task.expected_output_path)
