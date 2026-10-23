"""Running experiments from the console.

The console does **not** have its own idea of how to run an experiment: it
builds the same :class:`~cc_eval.core.models.ExperimentSpec` the CLI builds and
hands it to the same :class:`~cc_eval.core.experiment.ExperimentRunner`.  There
is exactly one execution path in the system.

What the console adds is a *stream*: every planned run is announced before it
starts, and every finished run is published with what it produced, so the
browser can show the experiment as it happens.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.config import EvalConfig, EvalPaths
from ..core.experiment import ExperimentRunner, build_run_plan, experiment_path, save_experiment
from ..core.models import ExperimentSpec, Variant
from ..core.registry import Registry
from ..datasets.loader import load_benchmark
from ..evaluators import get_evaluator
from ..runners import get_runner
from ..runners.base import RunConfig
from .authoring import benchmark_dir
from .types import TestType
from .types import get as get_test_type


class RunRequestError(ValueError):
    """Raised when a run request from the network is not acceptable."""


@dataclass
class RunHandle:
    """One experiment running (or finished) in the console."""

    id: str
    test_type: str
    experiment_id: str = ""
    benchmark: str = ""
    dataset_hash: str = ""
    evaluator: str = ""
    case_ids: list[str] = field(default_factory=list)
    status: str = "running"  # running | finished | failed
    error: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(event)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:  # pragma: no cover - a slow reader must not block
                pass


class RunManager:
    """Starts experiments and keeps their live state."""

    def __init__(
        self,
        paths: EvalPaths,
        *,
        allowed_framework_roots: list[Path] | None = None,
        runner_env: dict[str, str] | None = None,
    ) -> None:
        self.paths = paths
        self.allowed_framework_roots = [
            Path(root).expanduser().resolve() for root in (allowed_framework_roots or [])
        ]
        self.runner_env = runner_env or {}
        self._handles: dict[str, RunHandle] = {}
        self._counter = 0
        self._lock = threading.Lock()

    # -- public API -------------------------------------------------------- #
    def handles(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": handle.id,
                    "experiment_id": handle.experiment_id,
                    "test_type": handle.test_type,
                    "status": handle.status,
                    "error": handle.error,
                    "runs": len(handle.results),
                }
                for handle in self._handles.values()
            ]

    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._handles.get(run_id)

    def subscribe(self, run_id: str) -> queue.Queue | None:
        handle = self.get(run_id)
        if handle is None:
            return None
        subscriber: queue.Queue = queue.Queue(maxsize=1000)
        with handle.lock:
            # A late subscriber gets the backlog, not just what happens next:
            # the browser may connect after the first runs already finished.
            for event in handle.events:
                try:
                    subscriber.put_nowait(event)
                except queue.Full:  # pragma: no cover - defensive
                    break
            handle.subscribers.append(subscriber)
        return subscriber

    def start(self, request: dict[str, Any]) -> RunHandle:
        """Start an experiment described by a request payload."""
        spec, test_type, case_ids, plan, benchmark = self._prepare(request)
        # Every console submission needs distinct run IDs and artefact paths.
        suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        spec = spec.model_copy(update={"id": f"{spec.id}-{suffix}"})
        # Freeze the exact input used by this run before the editable benchmark changes.
        save_experiment(spec, experiment_path(self.paths, spec.id))
        snapshot = {
            "schema": "cc-eval/dataset-snapshot/v1",
            "benchmark": benchmark.name,
            "version": benchmark.version,
            "content_hash": benchmark.content_hash,
            "case_ids": case_ids,
            "cases": [case.model_dump(mode="json") for case in benchmark.cases if case.id in case_ids],
            "candidate": request.get("candidate_snapshot"),
        }
        snapshot_path = self.paths.experiments / f"{spec.id}.dataset.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        Registry(self.paths.registry).register_experiment(
            spec.id, title=spec.title, hypothesis=spec.hypothesis,
            framework_change=spec.framework_change,
            baseline_commit=spec.baseline.framework.ref,
            candidate_commit=spec.candidate.framework.ref,
            dataset=benchmark.name, dataset_version=benchmark.version,
            dataset_hash=benchmark.content_hash,
            candidate_id=str(request.get("candidate_id") or ""), status="running",
        )
        with self._lock:
            self._counter += 1
            run_id = f"CONSOLE-{self._counter:03d}-{spec.id}"
        handle = RunHandle(
            id=run_id,
            test_type=test_type.name,
            experiment_id=spec.id,
            benchmark=str(test_type.benchmark),
            dataset_hash=benchmark.content_hash,
            evaluator=test_type.evaluator,
            case_ids=case_ids,
        )
        with self._lock:
            self._handles[run_id] = handle

        for planned in plan.runs:
            handle.publish(
                {
                    "type": "run_planned",
                    "run_id": f"{spec.id}-{planned.variant}-{planned.case_id}-r{planned.repetition}",
                    "variant": planned.variant,
                    "case_id": planned.case_id,
                    "repetition": planned.repetition,
                }
            )

        thread = threading.Thread(
            target=self._execute,
            args=(handle, spec, test_type, case_ids, request, benchmark),
            name=f"cc-eval-console-{run_id}",
            daemon=True,
        )
        thread.start()
        return handle

    # -- internals --------------------------------------------------------- #
    def _prepare(
        self, request: dict[str, Any]
    ) -> tuple[ExperimentSpec, TestType, list[str], Any, Any]:
        name = str(request.get("test_type") or "")
        try:
            test_type = get_test_type(name)
        except KeyError as exc:
            raise RunRequestError(str(exc)) from exc

        benchmark = load_benchmark(benchmark_dir(test_type))
        selected = request.get("case_ids")
        if selected is not None and (not isinstance(selected, list) or not selected):
            raise RunRequestError("请至少选择一个用例；省略 case_ids 才会运行整个 benchmark")
        case_ids = [str(c) for c in (selected or [])]
        if selected is None:
            case_ids = list(benchmark.case_ids)
        unknown = [c for c in case_ids if c not in benchmark.case_ids]
        if unknown:
            raise RunRequestError(f"unknown case id(s): {', '.join(unknown)}")
        if test_type.evaluator.startswith("rubric:"):
            missing = [c.id for c in benchmark.cases if c.id in case_ids
                       and not (c.oracle.expected_output or c.oracle.total_expected
                                or c.oracle.expect_no_findings)]
            if missing:
                raise RunRequestError("用例缺少 oracle.yaml: " + ", ".join(missing))

        baseline = self._variant("baseline", request.get("baseline"))
        candidate = self._variant("candidate", request.get("candidate"))
        repetitions = max(1, int(request.get("repetitions") or 1))
        max_turns = request.get("max_turns", 100)
        if type(max_turns) is not int or not 1 <= max_turns <= 200:
            raise RunRequestError("每次运行的最大 Agent 轮数须为 1–200 的整数")
        runner = str(request.get("runner") or test_type.runner)
        model = request.get("model") or None

        spec = ExperimentSpec(
            id=f"EXP-CONSOLE-{name.upper()}",
            title=f"console: {test_type.title}",
            hypothesis="Ad-hoc run started from the cc-eval console.",
            independent_variable="the framework difference declared in the run request",
            baseline=baseline,
            candidate=candidate,
            controls={
                "runtime": runner,
                "model": model,
                "benchmark": str(benchmark_dir(test_type)),
                "evaluator": test_type.evaluator,
                "repetitions": repetitions,
                "max_turns": max_turns,
                "timeout_sec": int(request.get("timeout_sec") or 600),
                "seed": int(request.get("seed") or 20240101),
                "order": "alternating",
                "runner_args": {"setting_sources": "user,project"},
            },
            metrics=[],
            expected_changes=(
                list((request.get("candidate_snapshot") or {}).get("comparison_files") or [])
                if request.get("candidate_id") else list(test_type.expected_changes)
            ),
            decision_rules={
                "primary_metric": test_type.primary_metric,
                "direction": "higher_is_better",
                "min_repetitions": repetitions,
            },
            framework_change=str(request.get("framework_change") or ""),
            notes=str(request.get("candidate_id") or ""),
        )
        plan = build_run_plan(spec, case_ids, repetitions=repetitions)
        return spec, test_type, case_ids, plan, benchmark

    def _variant(self, name: str, payload: Any) -> Variant:
        if not isinstance(payload, dict):
            raise RunRequestError(f"{name}: missing framework")
        kind = str(payload.get("type") or "path")
        framework: dict[str, Any] = {"type": kind}
        if kind == "git":
            repository = Path(str(payload.get("repository") or "")).expanduser()
            framework["repository"] = repository
            framework["ref"] = str(payload.get("ref") or "HEAD")
        else:
            framework["path"] = Path(str(payload.get("path") or "")).expanduser()
        self._check_framework_path(framework)
        return Variant(name=name, framework=framework, config={})

    def _check_framework_path(self, framework: dict[str, Any]) -> None:
        """A framework path from the network must stay in an allowed root.

        The runner copies the framework into the workspace and the agent runs
        there.  A path outside the allowed roots would let a caller point the
        agent at an arbitrary directory.
        """
        if not self.allowed_framework_roots:
            return
        raw = framework.get("repository") or framework.get("path")
        if raw is None:
            return
        target = Path(str(raw)).expanduser().resolve()
        for root in self.allowed_framework_roots:
            if str(target) == str(root) or str(target).startswith(str(root) + "/"):
                return
        raise RunRequestError(
            f"framework path is outside the allowed roots: {target} "
            f"(allowed: {', '.join(str(r) for r in self.allowed_framework_roots)})"
        )

    def _execute(
        self,
        handle: RunHandle,
        spec: ExperimentSpec,
        test_type: TestType,
        case_ids: list[str],
        request: dict[str, Any],
        benchmark: Any,
    ) -> None:
        try:
            evaluator = get_evaluator(test_type.evaluator, _rubrics_dir())
            agent = get_runner(str(request.get("runner") or test_type.runner))
            run_config = RunConfig(
                runtime=spec.controls.runtime,
                model=spec.controls.model,
                timeout_sec=spec.controls.timeout_sec,
                max_turns=spec.controls.max_turns,
                max_budget_usd=request.get("max_budget_usd"),
                seed=spec.controls.seed,
                repetitions=spec.controls.repetitions,
                inherit_env=False,
                env=dict(self.runner_env),
                runner_args=dict(spec.controls.runner_args),
            )
            runner = ExperimentRunner(
                spec,
                benchmark=benchmark,
                evaluator=evaluator,
                runner=agent,
                paths=self.paths,
                config=EvalConfig(root=self.paths.root),
                run_config=run_config,
            )
            execution = runner.execute(
                cases=case_ids,
                on_run=self._on_run(handle, test_type),
                on_run_start=self._on_run_start(handle, spec.id),
            )
            # Reuse the CLI's comparison/report pipeline so browser runs are
            # reviewable from History after the live stream has ended.
            from ..cli.main import _build_model, _write_reports

            model = _build_model(
                spec, execution.records, self.paths, tree_diff=execution.tree_diff,
                benchmark=benchmark, execution_errors=execution.errors,
                execution_warnings=execution.warnings,
            )
            written = _write_reports(model, ["html", "json", "md"], self.paths)
            registry = Registry(self.paths.registry)
            for record in execution.records:
                registry.append_run(spec.id, record.result)
                registry.append_evaluation(spec.id, record.evaluation)
            registry.append_decision(spec.id, model.report.decision)
            registry.register_experiment(
                spec.id, title=spec.title, hypothesis=spec.hypothesis,
                framework_change=spec.framework_change,
                baseline_commit=spec.baseline.framework.ref,
                candidate_commit=spec.candidate.framework.ref,
                dataset=benchmark.name, dataset_version=benchmark.version,
                dataset_hash=benchmark.content_hash,
                candidate_id=str(request.get("candidate_id") or ""),
                status="completed" if model.status == "complete" else "partial",
                reports={fmt: str(path) for fmt, path in written.items()},
            )
            handle.status = "finished"
            handle.publish(
                {
                    "type": "experiment_finished",
                    "runs": len(execution.records),
                    "errors": list(execution.errors),
                    "warnings": list(execution.warnings),
                    "experiment_id": spec.id,
                    "report_url": f"/reports/{spec.id}/report.html",
                }
            )
        except Exception as exc:  # noqa: BLE001 - the console must not die
            handle.status = "failed"
            handle.error = f"{type(exc).__name__}: {exc}"
            Registry(self.paths.registry).register_experiment(
                spec.id, title=spec.title, hypothesis=spec.hypothesis,
                framework_change=spec.framework_change,
                baseline_commit=spec.baseline.framework.ref,
                candidate_commit=spec.candidate.framework.ref,
                dataset=benchmark.name, dataset_version=benchmark.version,
                dataset_hash=benchmark.content_hash,
                candidate_id=str(request.get("candidate_id") or ""), status="failed",
                error=handle.error,
            )
            handle.publish({"type": "experiment_failed", "experiment_id": spec.id, "error": handle.error})

    @staticmethod
    def _on_run_start(handle: RunHandle, experiment_id: str):
        def _callback(planned: Any) -> None:
            handle.publish({
                "type": "run_started",
                "run_id": f"{experiment_id}-{planned.variant}-{planned.case_id}-r{planned.repetition}",
                "variant": planned.variant,
                "case_id": planned.case_id,
                "repetition": planned.repetition,
            })
        return _callback

    def _on_run(self, handle: RunHandle, test_type: TestType):
        def _callback(record: Any) -> None:
            result = record.result
            evaluation = record.evaluation
            outputs = dict(result.output_files or {})
            counts: dict[str, Any] = {}
            for spec in test_type.outputs:
                if not spec.count_pattern:
                    continue
                text = "\n".join(outputs.values())
                counts[spec.key] = len(re.findall(spec.count_pattern, text))
            tools = [
                event.name or event.tool or event.type
                for event in (result.trace.events if result.trace else [])
                if event.type == "tool_call"
            ]
            with handle.lock:
                handle.results.append({
                    "run_id": result.run_id,
                    "variant": result.variant,
                    "case_id": result.case_id,
                    "repetition": result.repetition,
                    "status": result.status,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    "usage": result.usage.model_dump(mode="json"),
                    "outputs": outputs,
                    "counts": counts,
                    "tools": tools,
                    "evaluation": evaluation.model_dump(mode="json"),
                })
            handle.publish(
                {
                    "type": "run_finished",
                    "run_id": result.run_id,
                    "variant": result.variant,
                    "case_id": result.case_id,
                    "repetition": result.repetition,
                    "status": result.status,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                    "usage": result.usage.model_dump(mode="json"),
                    "outputs": outputs,
                    "counts": counts,
                    "tools": tools,
                    "evaluation": evaluation.model_dump(mode="json"),
                }
            )

        return _callback


def _rubrics_dir() -> Path:
    from .authoring import repo_root

    return repo_root() / "rubrics"
