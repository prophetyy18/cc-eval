"""Claude Code adapter.

The command line and the event shape were verified against the locally
installed Claude Code **2.1.308** (``claude --help`` and a live
``--output-format stream-json`` probe), not taken from memory:

* ``-p`` / ``--print`` runs non-interactively; the prompt may be piped on stdin.
* ``--output-format stream-json`` emits one JSON object per line:
  ``system/init`` (carries ``skills`` and ``tools``), ``assistant`` messages
  with ``tool_use`` blocks, ``user`` messages with ``tool_result`` blocks and a
  final ``result`` object carrying usage/cost.
* ``--no-session-persistence`` keeps the session off disk (no state bleed).
* ``--permission-prompts none`` makes anything that would prompt a no-op
  instead of hanging a non-interactive run.
* ``--strict-mcp-config`` and ``--setting-sources`` keep user-level MCP and
  settings out of the measurement.

The adapter never invents token counts: what the runtime reports is what is
stored, and what it does not report is recorded as missing.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from ..core.models import RunResult, RuntimeInfo, TaskBundle, Trace, Usage
from ..isolation.subprocess import build_env, run_command
from ..traces.normalize import normalise_claude, parse_jsonl
from .base import BaseRunner, RunConfig

# Permission modes that would remove the safety boundary are refused unless
# the operator explicitly opted in.
_DANGEROUS_MODES = {"bypassPermissions", "dangerously-skip-permissions"}


class ClaudeRunner(BaseRunner):
    """Runs a task with the Claude Code CLI."""

    name = "claude"

    def __init__(self, binary: str = "claude", extra_args: list[str] | None = None) -> None:
        self.binary = binary
        self.extra_args = list(extra_args or [])

    # -- availability ------------------------------------------------------- #
    def available(self) -> tuple[bool, str]:
        path = shutil.which(self.binary)
        if not path:
            return False, f"{self.binary} was not found on PATH"
        result = run_command([path, "--version"], cwd=Path.cwd(), timeout_sec=30)
        if not result.ok:
            return False, f"{self.binary} --version failed: {result.stderr.strip()[:200]}"
        return True, f"{self.binary} {result.stdout.strip()}"

    def version(self) -> str | None:
        path = shutil.which(self.binary)
        if not path:
            return None
        result = run_command([path, "--version"], cwd=Path.cwd(), timeout_sec=30)
        return result.stdout.strip() or None

    # -- command ------------------------------------------------------------ #
    def build_command(
        self,
        task: TaskBundle,
        config: RunConfig,
        workspace: Any,
    ) -> list[str]:
        cmd: list[str] = [shutil.which(self.binary) or self.binary, "-p"]
        cmd += [
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--permission-prompts",
            "none",
        ]
        mode = config.permission_mode
        if mode in _DANGEROUS_MODES:
            if not config.allow_bypass_permissions:
                raise ValueError(
                    f"refusing to run Claude Code with permission mode {mode!r}: "
                    "set controls.runner_args.allow_bypass_permissions explicitly to enable it"
                )
        else:
            cmd += ["--permission-mode", mode]
        cmd += ["--max-turns", str(config.max_turns)]
        cmd += ["--session-id", str(uuid.uuid4())]

        setting_sources = str(config.runner_args.get("setting_sources", "project"))
        if setting_sources:
            cmd += ["--setting-sources", setting_sources]

        if config.model:
            cmd += ["--model", config.model]
        if config.max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(config.max_budget_usd)]
        if task.output_schema:
            cmd += ["--json-schema", json.dumps(task.output_schema, ensure_ascii=False)]

        cmd += self.extra_args
        cmd += list(config.extra_args)
        return cmd

    # -- execution ---------------------------------------------------------- #
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
        run_id = f"{experiment_id or 'EXP'}-{variant or 'base'}-{task.case_id}-r{repetition}"
        result = self._base_result(
            task=task,
            config=config,
            experiment_id=experiment_id,
            variant=variant,
            repetition=repetition,
            run_id=run_id,
        )
        result.started_at = _now()
        try:
            command = self.build_command(task, config, workspace)
        except ValueError as exc:
            result.status = "agent_error"
            result.error = str(exc)
            return result

        env = _env(config)
        exec_result = run_command(
            command,
            cwd=workspace.root,
            timeout_sec=config.timeout_sec,
            env=env,
            stdin_text=task.prompt,
        )
        result.ended_at = _now()
        result.duration_ms = exec_result.duration_ms
        result.exit_code = exec_result.exit_code
        result.runtime = RuntimeInfo(
            name=self.name,
            version=self.version(),
            model=config.model,
            model_reported_by="controls.model",
            command=command,
            seed_supported=False,
            seed_note=(
                "Claude Code exposes no seed control and no determinism guarantee; "
                "identical prompts can still produce different runs."
            ),
        )
        result.provenance.update(
            {
                "runtime_version": result.runtime.version,
                "permission_mode": config.permission_mode,
                "session_persistence": False,
                "cwd": str(workspace.root),
            }
        )

        if exec_result.timed_out:
            result.status = "timeout"
            result.error = f"Claude Code exceeded the {config.timeout_sec}s timeout"

        raw_events = parse_jsonl(exec_result.stdout)
        trace = normalise_claude(
            raw_events,
            run_id=run_id,
            experiment_id=experiment_id,
            case_id=task.case_id,
            variant=variant,
            repetition=repetition,
        )
        trace.raw_path = None
        result.trace = trace
        result.raw_events = raw_events

        final = _find_result_event(raw_events)
        if final:
            result.usage = _usage_from_claude(final)
            result.runtime.model_reported_by = "claude.result.modelUsage"
            reported_models = list((final.get("modelUsage") or {}).keys())
            if reported_models:
                result.runtime.model = reported_models[0]
            result.output_text = str(final.get("result") or "")
            if final.get("is_error"):
                errors = final.get("errors") or []
                message = "; ".join(str(e) for e in errors) or str(final.get("subtype"))
                if "budget" in str(final.get("subtype", "")).lower():
                    result.status = "budget_exhausted"
                elif result.status != "timeout":
                    result.status = "agent_error"
                result.error = message
        elif result.status == "success":
            result.status = "infra_error"
            result.error = (
                "Claude Code produced no result event. stderr: "
                + (exec_result.stderr.strip()[:500] or "<empty>")
            )

        result.output_files = workspace.collect_outputs()
        if exec_result.stderr.strip():
            result.provenance["stderr_tail"] = exec_result.stderr.strip()[-2000:]
        return result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _now() -> str:
    from ..core.models import utcnow_iso

    return utcnow_iso()


def _env(config: RunConfig) -> dict[str, str]:
    """Environment for the Claude Code process.

    By default the credentials are stripped: the agent under test must not
    inherit the operator's API keys.  ``inherit_env`` is the explicit opt-in
    for a local, trusted run.
    """
    import os

    from ..core.config import EvalConfig

    if config.inherit_env:
        env = dict(os.environ)
        env.update(config.env)
        return env
    return build_env(EvalConfig(root=Path(".")), extra=config.env)


def _find_result_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def _usage_from_claude(result_event: dict[str, Any]) -> Usage:
    """Extract usage from a Claude Code ``result`` event.

    The per-model ``modelUsage`` block is authoritative (the top-level
    ``usage`` object reported zeros in the verified 2.1.308 output), so it is
    preferred and the top-level object is only a fallback.
    """
    model_usage = result_event.get("modelUsage") or {}
    if model_usage:
        input_tokens = sum(int(m.get("inputTokens") or 0) for m in model_usage.values())
        output_tokens = sum(int(m.get("outputTokens") or 0) for m in model_usage.values())
        cache_read = sum(int(m.get("cacheReadInputTokens") or 0) for m in model_usage.values())
        cache_creation = sum(
            int(m.get("cacheCreationInputTokens") or 0) for m in model_usage.values()
        )
        thinking = sum(int(m.get("thinkingTokens") or 0) for m in model_usage.values())
        cost = result_event.get("total_cost_usd")
        if cost is None:
            cost = sum(float(m.get("costUSD") or 0.0) for m in model_usage.values())
        return Usage(
            source="runtime",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            reasoning_tokens=thinking,
            total_tokens=input_tokens + output_tokens,
            cost_usd=float(cost) if cost is not None else None,
        )

    usage = result_event.get("usage") or {}
    if not usage:
        return Usage(source="unavailable")
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    details = usage.get("output_tokens_details") or {}
    return Usage(
        source="runtime",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        reasoning_tokens=int(details.get("thinking_tokens") or 0),
        total_tokens=input_tokens + output_tokens,
        cost_usd=(
            float(result_event["total_cost_usd"])
            if result_event.get("total_cost_usd") is not None
            else None
        ),
    )
