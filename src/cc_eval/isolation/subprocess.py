"""Subprocess execution with budgets, timeouts and a sanitised environment.

This is the *real* isolation boundary for credentials: the environment handed
to an agent process is built from scratch, and blocked variables (API keys and
friends) are removed unless the operator explicitly opts in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps the import graph acyclic at runtime
    from ..core.config import EvalConfig


@dataclass
class CommandResult:
    """Outcome of a subprocess execution."""

    command: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    started_at: float = 0.0
    ended_at: float = 0.0
    cwd: str = ""
    env_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def combined(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)


def build_env(
    cfg: "EvalConfig",
    extra: dict[str, str] | None = None,
    inherit_base: bool = True,
) -> dict[str, str]:
    """Build a sanitised environment for an agent subprocess.

    * The inherited environment is filtered through ``blocked_env_vars``.
    * ``runner_env`` from the config is applied afterwards (explicit opt-in).
    * ``HOME``/``TMPDIR`` are kept: several runtimes need them, and the agent
      runs inside a sandbox directory, not with a hidden credential store.
    """
    env: dict[str, str] = {}
    if inherit_base:
        blocked = set(cfg.blocked_env_vars)
        for key, value in os.environ.items():
            if key in blocked:
                continue
            env[key] = value
    env.update(cfg.runner_env)
    if extra:
        env.update(extra)
    return env


def run_command(
    command: list[str],
    cwd: Path | str,
    *,
    timeout_sec: int = 300,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    max_output_bytes: int = 8 * 1024 * 1024,
) -> CommandResult:
    """Run ``command`` in ``cwd`` with a timeout, capturing stdout/stderr."""
    cwd = Path(cwd)
    started = time.time()
    stdout_bytes = b""
    stderr_bytes = b""
    timed_out = False
    exit_code: int | None = None

    try:
        proc = subprocess.run(  # noqa: S603 - command is constructed by the runner adapters
            command,
            cwd=str(cwd),
            env=env,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        exit_code = proc.returncode
        stdout_bytes = proc.stdout or b""
        stderr_bytes = proc.stderr or b""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_bytes = exc.stdout or b""
        stderr_bytes = exc.stderr or b""
        exit_code = None
    except FileNotFoundError as exc:
        stderr_bytes = str(exc).encode("utf-8")
        exit_code = 127

    ended = time.time()

    def _decode(data: bytes) -> str:
        if len(data) > max_output_bytes:
            data = data[:max_output_bytes] + b"\n...[truncated]"
        return data.decode("utf-8", errors="replace")

    return CommandResult(
        command=list(command),
        exit_code=exit_code,
        stdout=_decode(stdout_bytes),
        stderr=_decode(stderr_bytes),
        duration_ms=int((ended - started) * 1000),
        timed_out=timed_out,
        started_at=started,
        ended_at=ended,
        cwd=str(cwd),
        env_keys=sorted(env.keys()) if env else [],
    )


def which(binary: str) -> str | None:
    return shutil.which(binary)
