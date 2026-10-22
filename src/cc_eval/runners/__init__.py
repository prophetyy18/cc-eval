"""Agent runner adapters."""

from __future__ import annotations

from .._lazy import lazy_exports

__all__ = [
    "AgentRunner",
    "BaseRunner",
    "ClaudeRunner",
    "CodexRunner",
    "FakeRunner",
    "RunConfig",
    "available_runners",
    "get_runner",
]

_LAZY = {
    "AgentRunner": "cc_eval.runners.base.AgentRunner",
    "BaseRunner": "cc_eval.runners.base.BaseRunner",
    "RunConfig": "cc_eval.runners.base.RunConfig",
    "FakeRunner": "cc_eval.runners.fake.FakeRunner",
    "ClaudeRunner": "cc_eval.runners.claude.ClaudeRunner",
    "CodexRunner": "cc_eval.runners.codex.CodexRunner",
    "get_runner": "cc_eval.runners.registry.get_runner",
    "available_runners": "cc_eval.runners.registry.available_runners",
}

__getattr__ = lazy_exports(_LAZY)
