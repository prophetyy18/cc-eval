"""Trace storage and retrieval.

Traces are stored per experiment:

    traces/<experiment_id>/<run_id>.json      normalised trace
    traces/<experiment_id>/raw/<run_id>.jsonl  the runtime's own event stream

The raw stream is kept because the normalisation is lossy: a human reviewing
a failure needs the original events, not cc-eval's interpretation of them.

Both files are redacted on write.  Note that redaction is a hygiene measure,
not a guarantee - a raw trace can still contain sensitive content, so traces
must not be uploaded to an external platform or committed to git by default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import Trace


@dataclass
class TraceStore:
    """Reads and writes traces under a single root directory."""

    root: Path

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def experiment_dir(self, experiment_id: str) -> Path:
        return self.root / experiment_id

    def normalised_path(self, experiment_id: str, run_id: str) -> Path:
        return self.experiment_dir(experiment_id) / f"{run_id}.json"

    def raw_path(self, experiment_id: str, run_id: str) -> Path:
        return self.experiment_dir(experiment_id) / "raw" / f"{run_id}.jsonl"

    def write(self, experiment_id: str, run_id: str, trace: Trace) -> tuple[Path, Path | None]:
        """Write the normalised trace and (optionally) the raw stream."""
        directory = self.experiment_dir(experiment_id)
        directory.mkdir(parents=True, exist_ok=True)
        normalised = self.normalised_path(experiment_id, run_id)
        normalised.write_text(
            json.dumps(trace.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        raw: Path | None = None
        raw_file = self.raw_path(experiment_id, run_id)
        if trace.raw_path and Path(trace.raw_path).exists():
            raw = raw_file
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text(Path(trace.raw_path).read_text(encoding="utf-8"), encoding="utf-8")
        return normalised, raw

    def load(self, experiment_id: str, run_id: str) -> Trace | None:
        path = self.normalised_path(experiment_id, run_id)
        if not path.exists():
            return None
        try:
            return Trace(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError):
            return None

    def list_runs(self, experiment_id: str) -> list[str]:
        directory = self.experiment_dir(experiment_id)
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    def search(
        self,
        experiment_id: str | None = None,
        *,
        case_id: str | None = None,
        variant: str | None = None,
    ) -> list[Trace]:
        """Retrieve traces by experiment, case and variant."""
        found: list[Trace] = []
        experiment_ids = (
            [experiment_id] if experiment_id else [d.name for d in sorted(self.root.iterdir()) if d.is_dir()]
        )
        for exp_id in experiment_ids:
            directory = self.experiment_dir(exp_id)
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    trace = Trace(**json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, ValueError):
                    continue
                if case_id and trace.case_id != case_id:
                    continue
                if variant and trace.variant != variant:
                    continue
                found.append(trace)
        return found

    def summary(self, experiment_id: str) -> dict[str, Any]:
        """A compact overview of an experiment's traces."""
        traces = self.search(experiment_id)
        by_case: dict[str, int] = {}
        by_variant: dict[str, int] = {}
        for trace in traces:
            by_case[trace.case_id] = by_case.get(trace.case_id, 0) + 1
            by_variant[trace.variant] = by_variant.get(trace.variant, 0) + 1
        return {
            "experiment_id": experiment_id,
            "trace_count": len(traces),
            "by_case": by_case,
            "by_variant": by_variant,
            "missing_fields": sorted({f for t in traces for f in t.missing_fields}),
        }
