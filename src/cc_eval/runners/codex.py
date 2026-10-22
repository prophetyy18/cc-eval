"""Codex CLI adapter.

The command line and the event shape were verified against the locally
installed **codex-cli 0.160.1**:

* ``codex exec --json`` runs non-interactively and emits JSONL events:
  ``thread.started``, ``turn.started``, ``item.started`` / ``item.completed``
  (item types ``agent_message``, ``command_execution``, ``file_change``, ...)
  and ``turn.completed``.
* ``turn.completed`` carries token usage but **no cost information** - so the
  adapter reports ``cost_usd = None`` with ``usage.source = "runtime"`` for the
  tokens and leaves cost explicitly unavailable rather than inventing it.
* ``--ephemeral`` keeps session files off disk.
* ``--output-schema`` requests a structured final response.
* ``--sandbox`` selects the command sandbox policy.

Codex has no equivalent of Claude Code's "skills" inventory, so capability
verification for Codex relies on what the trace shows (commands executed,
files changed) and anything that cannot be evidenced is reported as
``unverified`` (ADR-007).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..core.models import RunResult, RuntimeInfo, TaskBundle, Usage
from ..isolation.subprocess import build_env, run_command
from ..traces.normalize import normalise_codex, parse_jsonl
from .base import BaseRunner, RunConfig


class CodexRunner(BaseRunner):
    """Runs a task with the Codex CLI."""

    name = "codex"

    def __init__(self, binary: str = "codex", extra_args: list[str] | None = None) -> None:
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
        last_message_path: Path,
        schema_path: Path | None,
    ) -> list[str]:
        cmd: list[str] = [shutil.which(self.binary) or self.binary, "exec", "--json"]
        cmd += ["--ephemeral", "--skip-git-repo-check"]
        cmd += ["-C", str(workspace.root)]
        cmd += ["--sandbox", config.sandbox]
        cmd += ["--color", "never"]
        cmd += ["--output-last-message", str(last_message_path)]
        if config.model:
            cmd += ["--model", config.model]
        if schema_path is not None:
            cmd += ["--output-schema", str(schema_path)]
        if config.runner_args.get("ignore_user_config"):
            cmd.append("--ignore-user-config")
        cmd += self.extra_args
        cmd += list(config.extra_args)
        # The prompt is passed as the positional argument: `codex exec` reads
        # it from stdin only when no prompt is given, and a piped stdin would
        # otherwise be appended to the instructions.
        cmd.append(task.prompt)
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

        meta_dir = Path(workspace.meta_dir)
        meta_dir.mkdir(parents=True, exist_ok=True)
        last_message_path = meta_dir / "codex_last_message.txt"
        schema_path: Path | None = None
        if task.output_schema:
            schema_path = meta_dir / "codex_output_schema.json"
            schema_path.write_text(
                json.dumps(task.output_schema, ensure_ascii=False), encoding="utf-8"
            )

        command = self.build_command(task, config, workspace, last_message_path, schema_path)
        env = _env(config)
        exec_result = run_command(
            command,
            cwd=workspace.root,
            timeout_sec=config.timeout_sec,
            env=env,
            stdin_text=None,
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
                "Codex exposes no seed control; identical prompts can produce "
                "different runs."
            ),
        )
        result.provenance.update(
            {"runtime_version": result.runtime.version, "sandbox": config.sandbox, "cwd": str(workspace.root)}
        )

        if exec_result.timed_out:
            result.status = "timeout"
            result.error = f"Codex exceeded the {config.timeout_sec}s timeout"

        raw_events = parse_jsonl(exec_result.stdout)
        trace = normalise_codex(
            raw_events,
            run_id=run_id,
            experiment_id=experiment_id,
            case_id=task.case_id,
            variant=variant,
            repetition=repetition,
        )
        result.trace = trace
        result.raw_events = raw_events

        turn = _find_turn_completed(raw_events)
        if turn:
            usage = turn.get("usage") or {}
            result.usage = Usage(
                source="runtime",
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cached_input_tokens") or 0),
                cache_creation_tokens=int(usage.get("cache_write_input_tokens") or 0),
                reasoning_tokens=int(usage.get("reasoning_output_tokens") or 0),
                total_tokens=int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0),
                cost_usd=None,  # Codex does not report cost.
            )
            result.provenance["cost_usd"] = "unavailable: codex exec does not report cost"
        else:
            result.usage = Usage(source="unavailable")

        if last_message_path.exists():
            result.output_text = last_message_path.read_text(encoding="utf-8", errors="replace")
        else:
            messages = [
                str((event.get("item") or {}).get("text", ""))
                for event in raw_events
                if event.get("type") == "item.completed"
                and (event.get("item") or {}).get("type") == "agent_message"
            ]
            result.output_text = messages[-1] if messages else ""

        failed = _find_turn_failed(raw_events)
        if failed:
            result.status = "agent_error"
            result.error = json.dumps(failed.get("error") or failed, ensure_ascii=False)[:500]
        elif exec_result.exit_code not in (0, None) and result.status == "success":
            result.status = "agent_error"
            result.error = (
                f"codex exec exited with {exec_result.exit_code}: "
                f"{exec_result.stderr.strip()[:400]}"
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
    """Environment for the Codex process (credentials stripped by default)."""
    import os

    from ..core.config import EvalConfig

    if config.inherit_env:
        env = dict(os.environ)
        env.update(config.env)
        return env
    return build_env(EvalConfig(root=Path(".")), extra=config.env)


def _find_turn_completed(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == "turn.completed":
            return event
    return None


def _find_turn_failed(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") in {"turn.failed", "error"}:
            return event
    return None
