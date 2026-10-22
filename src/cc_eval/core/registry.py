"""The append-only experiment registry.

The registry answers the questions the framework author actually asks months
later:

* *Why was this skill added?*  -> the experiment's hypothesis + framework change
* *Which experiment validated it?* -> ``framework_changes.jsonl``
* *Which model and dataset were used?* -> the run records
* *What improved, what regressed, what did it cost?* -> the decision record
* *Where do I roll back to?* -> the recorded framework commit

Files (all JSONL, append-only, human readable):

    registry/index.jsonl               one line per experiment
    registry/framework_changes.jsonl   framework commit -> experiments
    registry/<EXP-ID>/runs.jsonl       one line per run
    registry/<EXP-ID>/results.jsonl    one line per evaluation
    registry/<EXP-ID>/decisions.jsonl  one line per decision
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Decision, EvaluationResult, RunResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


@dataclass
class Registry:
    """Filesystem-backed registry."""

    root: Path

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------- #
    @property
    def index(self) -> Path:
        return self.root / "index.jsonl"

    @property
    def framework_changes(self) -> Path:
        return self.root / "framework_changes.jsonl"

    def experiment_dir(self, experiment_id: str) -> Path:
        return self.root / experiment_id

    # -- writes ------------------------------------------------------------- #
    def register_experiment(
        self,
        experiment_id: str,
        *,
        title: str = "",
        hypothesis: str = "",
        framework_change: str = "",
        baseline_commit: str | None = None,
        candidate_commit: str | None = None,
        dataset: str = "",
        dataset_version: str = "",
        status: str = "created",
        reports: dict[str, str] | None = None,
    ) -> None:
        """Add or update the index entry for an experiment.

        ``reports`` links the experiment to the report files that were
        generated for it (the HTML report, the JSON report, ...).  It is what
        makes the history of an experiment navigable later.
        """
        existing = _read_jsonl(self.index)
        previous = next(
            (e for e in existing if e.get("experiment_id") == experiment_id),
            None,
        )
        entries = [e for e in existing if e.get("experiment_id") != experiment_id]
        known_reports: dict[str, str] = dict((previous or {}).get("reports") or {})
        known_reports.update(reports or {})
        entries.append(
            {
                "experiment_id": experiment_id,
                "title": title,
                "hypothesis": hypothesis,
                "framework_change": framework_change,
                "baseline_commit": baseline_commit,
                "candidate_commit": candidate_commit,
                "dataset": dataset,
                "dataset_version": dataset_version,
                "status": status,
                "reports": known_reports,
                "updated_at": _now(),
            }
        )
        self._rewrite(self.index, entries)

    def link_framework_change(
        self,
        *,
        repository: str,
        commit: str,
        change: str,
        experiment_id: str,
        notes: str = "",
    ) -> None:
        """Record that a framework commit is what an experiment tests."""
        entries = _read_jsonl(self.framework_changes)
        for entry in entries:
            if entry.get("commit") == commit and entry.get("repository") == repository:
                experiments = set(entry.get("experiments") or [])
                experiments.add(experiment_id)
                entry["experiments"] = sorted(experiments)
                break
        else:
            entries.append(
                {
                    "repository": repository,
                    "commit": commit,
                    "change": change,
                    "experiments": [experiment_id],
                    "notes": notes,
                    "recorded_at": _now(),
                }
            )
        self._rewrite(self.framework_changes, entries)

    def append_run(self, experiment_id: str, result: RunResult) -> None:
        _append_jsonl(
            self.experiment_dir(experiment_id) / "runs.jsonl",
            {
                "recorded_at": _now(),
                "run": result.model_dump(mode="json"),
            },
        )

    def append_evaluation(self, experiment_id: str, evaluation: EvaluationResult) -> None:
        _append_jsonl(
            self.experiment_dir(experiment_id) / "results.jsonl",
            {
                "recorded_at": _now(),
                "evaluation": evaluation.model_dump(mode="json"),
            },
        )

    def append_decision(self, experiment_id: str, decision: Decision) -> None:
        _append_jsonl(
            self.experiment_dir(experiment_id) / "decisions.jsonl",
            {
                "recorded_at": _now(),
                "decision": decision.model_dump(mode="json"),
            },
        )

    # -- reads -------------------------------------------------------------- #
    def list_experiments(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.index)

    def list_reports(self) -> list[dict[str, Any]]:
        """The generated reports of every experiment, newest entry last.

        This is the link between the registry and the report files: it is what
        a future history view (or a web UI) would walk to compare one
        experiment with the next.
        """
        reports: list[dict[str, Any]] = []
        for entry in _read_jsonl(self.index):
            experiment_id = str(entry.get("experiment_id", ""))
            decision = self.latest_decision(experiment_id)
            reports.append(
                {
                    "experiment_id": experiment_id,
                    "title": entry.get("title", ""),
                    "status": entry.get("status", ""),
                    "dataset_version": entry.get("dataset_version", ""),
                    "verdict": decision.verdict.value if decision else "",
                    "reports": dict(entry.get("reports") or {}),
                    "updated_at": entry.get("updated_at", ""),
                }
            )
        return reports

    def reports_for(self, experiment_id: str) -> dict[str, str]:
        """The report files recorded for one experiment."""
        for entry in _read_jsonl(self.index):
            if entry.get("experiment_id") == experiment_id:
                return dict(entry.get("reports") or {})
        return {}

    def history(self, experiment_id: str) -> dict[str, Any]:
        directory = self.experiment_dir(experiment_id)
        return {
            "runs": _read_jsonl(directory / "runs.jsonl"),
            "results": _read_jsonl(directory / "results.jsonl"),
            "decisions": _read_jsonl(directory / "decisions.jsonl"),
        }

    def experiments_for_commit(self, commit: str) -> list[str]:
        out: list[str] = []
        for entry in _read_jsonl(self.framework_changes):
            if entry.get("commit") == commit:
                out.extend(entry.get("experiments") or [])
        return sorted(set(out))

    def latest_decision(self, experiment_id: str) -> Decision | None:
        decisions = _read_jsonl(self.experiment_dir(experiment_id) / "decisions.jsonl")
        if not decisions:
            return None
        try:
            return Decision(**decisions[-1]["decision"])
        except (KeyError, ValueError):
            return None

    # -- internals ---------------------------------------------------------- #
    @staticmethod
    def _rewrite(path: Path, entries: Iterable[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
