"""Isolated workspace management for agent runs.

Every run gets a brand-new directory tree.  Nothing is shared between runs:
not the framework checkout, not the task input, not the agent's output, not any
state the runtime may have written.

Layout of a run workspace::

    <workspace>/
      <framework files at the root>   # materialised variant (skills, config)
      task/
        TASK.md                       # rendered prompt
        input/...                     # task input files
      out/                            # agent output (expected_output_path)
      .cc-eval/                       # cc-eval internal metadata

The framework sits at the workspace *root* on purpose: Claude Code discovers
``.claude/skills/<name>/SKILL.md`` relative to the working directory, so the
skills under test are genuinely reachable without extra flags.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .redaction import redact_text

# Guard: never delete anything that is not inside a managed workspaces root.
_SENTINEL = ".cc-eval-workspace"


@dataclass(frozen=True)
class Workspace:
    """A single run workspace."""

    root: Path
    run_id: str

    @property
    def task_dir(self) -> Path:
        return self.root / "task"

    @property
    def input_dir(self) -> Path:
        return self.task_dir / "input"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def meta_dir(self) -> Path:
        return self.root / ".cc-eval"

    @property
    def prompt_file(self) -> Path:
        return self.task_dir / "TASK.md"

    def resolve_output(self, relative: str) -> Path:
        """Resolve an output path, refusing to escape the workspace."""
        target = (self.root / relative).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"output path escapes workspace: {relative}")
        return target

    def read_output(self, relative: str) -> str | None:
        target = self.resolve_output(relative)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8", errors="replace")

    def collect_outputs(self, subdir: str = "out") -> dict[str, str]:
        """Collect every produced file under ``subdir`` (redacted)."""
        base = self.root / subdir
        if not base.exists():
            return {}
        collected: dict[str, str] = {}
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.root))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            collected[rel] = redact_text(text)[0]
        return collected


class WorkspaceManager:
    """Creates and disposes run workspaces inside a managed root."""

    def __init__(self, workspaces_root: Path | str) -> None:
        self.root = Path(workspaces_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _assert_inside(self, path: Path) -> None:
        resolved = path.resolve()
        if not str(resolved).startswith(str(self.root)):
            raise RuntimeError(
                f"refusing to operate on {resolved}: outside managed root {self.root}"
            )

    def create(self, run_id: str) -> Workspace:
        path = self.root / run_id
        self._assert_inside(path)
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        (path / ".cc-eval-workspace").write_text(run_id, encoding="utf-8")
        ws = Workspace(root=path, run_id=run_id)
        for d in (ws.task_dir, ws.input_dir, ws.out_dir, ws.meta_dir):
            d.mkdir(parents=True, exist_ok=True)
        return ws

    def destroy(self, workspace: Workspace) -> None:
        """Remove a workspace.  Refuses to touch anything outside the root."""
        self._assert_inside(workspace.root)
        if not (workspace.root / _SENTINEL).exists():
            raise RuntimeError(
                f"refusing to remove {workspace.root}: not a managed cc-eval workspace"
            )
        shutil.rmtree(workspace.root, ignore_errors=True)

    def cleanup_all(self) -> int:
        """Remove every managed workspace.  Returns the number removed."""
        removed = 0
        if not self.root.exists():
            return 0
        for child in sorted(self.root.iterdir()):
            if child.is_dir() and (child / _SENTINEL).exists():
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed
