"""Global cc-eval configuration and on-disk layout.

cc-eval keeps a single workspace root that holds everything it produces:

    <root>/
      config.yaml          global settings
      experiments/         experiment specs (YAML)
      registry/            append-only JSONL registry
      runs/                run metadata (JSON)
      traces/              raw + normalised traces
      reports/             generated reports
      workspaces/          scratch dirs for run workspaces (cleaned per run)

The root defaults to ``./.cc-eval`` in the current project and can be
overridden with ``CC_EVAL_HOME``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_ROOT = ".cc-eval"


class EvalConfig(BaseModel):
    """Persisted global settings."""

    model_config = ConfigDict(extra="forbid")

    root: Path = Path(DEFAULT_ROOT)
    # Never expose real credentials to an agent under test by default.
    redact_secrets: bool = True
    # Default ceiling for a single run when the experiment does not set one.
    default_timeout_sec: int = 300
    # Keep raw traces on disk (they may contain sensitive content).
    keep_raw_traces: bool = True
    # Environment variables that are never forwarded into an agent workspace.
    blocked_env_vars: list[str] = Field(
        default_factory=lambda: [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "OPENAI_API_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "CODEX_API_KEY",
        ]
    )
    # Extra environment variables forwarded to real runtimes (e.g. a custom
    # base URL).  Empty by default: real runners get an explicit opt-in.
    runner_env: dict[str, str] = Field(default_factory=dict)


class EvalPaths:
    """Resolved directory layout for a given root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    # -- directories ------------------------------------------------------- #
    @property
    def experiments(self) -> Path:
        return self.root / "experiments"

    @property
    def registry(self) -> Path:
        return self.root / "registry"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def traces(self) -> Path:
        return self.root / "traces"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def workspaces(self) -> Path:
        return self.root / "workspaces"

    @property
    def config_file(self) -> Path:
        return self.root / "config.yaml"

    def all_dirs(self) -> list[Path]:
        return [
            self.root,
            self.experiments,
            self.registry,
            self.runs,
            self.traces,
            self.reports,
            self.workspaces,
        ]

    def ensure(self) -> None:
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)


def resolve_root(explicit: Path | str | None = None) -> Path:
    """Resolve the workspace root.

    Order: explicit argument > ``CC_EVAL_HOME`` > ``./.cc-eval``.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("CC_EVAL_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(DEFAULT_ROOT).expanduser().resolve()


def load_config(root: Path | str | None = None) -> EvalConfig:
    """Load the global config, falling back to defaults."""
    resolved = resolve_root(root)
    cfg_file = resolved / "config.yaml"
    if not cfg_file.exists():
        return EvalConfig(root=resolved)
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
    data.pop("root", None)  # root is implied by where the file lives
    cfg = EvalConfig(**data)
    cfg.root = resolved
    return cfg


def save_config(cfg: EvalConfig) -> Path:
    cfg.root.mkdir(parents=True, exist_ok=True)
    payload = cfg.model_dump(mode="json")
    payload.pop("root", None)
    target = cfg.root / "config.yaml"
    target.write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    return target
