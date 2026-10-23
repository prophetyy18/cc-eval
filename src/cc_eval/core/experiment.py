"""Experiment specification, run planning and execution.

This module is the engine of the system:

* it loads and validates an :class:`~cc_eval.core.models.ExperimentSpec`;
* it builds a **run plan** (variants x cases x repetitions) whose ordering
  interleaves the two variants so that neither side systematically benefits
  from running first;
* it executes every planned run in its own isolated workspace with its own
  fresh session;
* it records provenance (framework fingerprint, dataset hash, evaluator
  version, model, runtime) for every single run, so that a later comparison
  can detect confounds instead of assuming them away.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..datasets.loader import Benchmark, materialise_bundle
from ..evaluators.base import Evaluator
from ..isolation.redaction import redact_obj, redact_text
from ..isolation.workspace import WorkspaceManager
from .config import EvalConfig, EvalPaths
from .models import (
    BenchmarkCase,
    Decision,
    EvaluationResult,
    ExperimentSpec,
    RunConfig,
    RunResult,
    Variant,
)
from .snapshot import MaterialisedVariant, TreeDiff, diff_file_maps, materialise


class ExperimentError(RuntimeError):
    """Raised when an experiment cannot be loaded or executed."""


# --------------------------------------------------------------------------- #
# Spec loading
# --------------------------------------------------------------------------- #


def load_experiment(path: Path | str) -> ExperimentSpec:
    """Load an experiment spec from YAML."""
    p = Path(path)
    if not p.exists():
        raise ExperimentError(f"experiment file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    try:
        return ExperimentSpec(**data)
    except Exception as exc:  # noqa: BLE001 - pydantic error message is the useful part
        raise ExperimentError(f"invalid experiment {p}: {exc}") from exc


def save_experiment(spec: ExperimentSpec, path: Path | str) -> Path:
    """Persist an experiment spec as YAML."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = spec.model_dump(mode="json")
    p.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return p


def experiment_path(paths: EvalPaths, experiment_id: str) -> Path:
    return paths.experiments / f"{experiment_id}.yaml"


# --------------------------------------------------------------------------- #
# Run plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedRun:
    """One unit of work: a case, a variant and a repetition."""

    case_id: str
    variant: str
    repetition: int
    order_index: int


@dataclass
class RunPlan:
    runs: list[PlannedRun] = field(default_factory=list)
    order_policy: str = "alternating"

    def __len__(self) -> int:
        return len(self.runs)

    def selected(self, cases: list[str] | None, repetitions: int | None) -> list[PlannedRun]:
        selected = self.runs
        if cases:
            selected = [r for r in selected if r.case_id in cases]
        if repetitions is not None:
            selected = [r for r in selected if r.repetition <= repetitions]
        return selected


def build_run_plan(
    spec: ExperimentSpec,
    case_ids: list[str],
    *,
    repetitions: int | None = None,
) -> RunPlan:
    """Build the ordered list of runs.

    The two variants are interleaved *within* each (repetition, case) block so
    that a slow machine, a warm cache or a transient provider problem cannot
    systematically favour one side.  With an even number of repetitions each
    variant starts first equally often.
    """
    reps = repetitions or spec.controls.repetitions
    policy = spec.controls.order
    rng = random.Random(spec.controls.seed)
    variants = [spec.baseline.name, spec.candidate.name]

    plan = RunPlan(order_policy=policy)
    order_index = 0
    block_index = 0
    for repetition in range(1, reps + 1):
        for case_id in case_ids:
            if policy == "sequential":
                block = list(variants)
            elif policy == "random":
                block = list(variants)
                rng.shuffle(block)
            else:  # alternating
                # Alternate which variant goes first, flipping every block so
                # that neither side owns the first position.
                block = list(variants)
                if block_index % 2 == 1:
                    block.reverse()
            block_index += 1
            for variant_name in block:
                plan.runs.append(
                    PlannedRun(
                        case_id=case_id,
                        variant=variant_name,
                        repetition=repetition,
                        order_index=order_index,
                    )
                )
                order_index += 1
    return plan


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #


@dataclass
class RunRecord:
    """A run plus its evaluation, as stored on disk."""

    result: RunResult
    evaluation: EvaluationResult
    fingerprint: str = ""
    dataset_hash: str = ""
    dataset_version: str = ""
    evaluator_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.model_dump(mode="json"),
            "evaluation": self.evaluation.model_dump(mode="json"),
            "fingerprint": self.fingerprint,
            "dataset_hash": self.dataset_hash,
            "dataset_version": self.dataset_version,
            "evaluator_version": self.evaluator_version,
        }


@dataclass
class ExecutionResult:
    """Everything produced by one ``cc-eval run`` invocation."""

    experiment_id: str
    records: list[RunRecord] = field(default_factory=list)
    materialised: dict[str, MaterialisedVariant] = field(default_factory=dict)
    tree_diff: TreeDiff | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ExperimentRunner:
    """Executes an experiment plan."""

    def __init__(
        self,
        spec: ExperimentSpec,
        *,
        benchmark: Benchmark,
        evaluator: Evaluator,
        runner: Any,
        paths: EvalPaths,
        config: EvalConfig,
        run_config: RunConfig,
    ) -> None:
        self.spec = spec
        self.benchmark = benchmark
        self.evaluator = evaluator
        self.runner = runner
        self.paths = paths
        self.config = config
        self.run_config = run_config
        self.workspaces = WorkspaceManager(paths.workspaces)

    # -- helpers ------------------------------------------------------------ #
    def _variant(self, name: str) -> Variant:
        return self.spec.variant(name)

    def _run_config_for(self, variant: Variant) -> RunConfig:
        """Merge the variant's own configuration into the run configuration.

        A variant may legitimately change the *agent-visible* configuration of
        the framework (that is often the independent variable).  Anything that
        changes the *measurement* conditions (model, budget, timeout) is
        allowed but is recorded so the comparison can flag it as a confound.
        """
        merged = self.run_config.model_copy(deep=True)
        variant_config = variant.config or {}
        for key in ("model", "max_budget_usd", "timeout_sec", "max_turns", "permission_mode", "sandbox"):
            if key in variant_config:
                setattr(merged, key, variant_config[key])
        if "runner_args" in variant_config:
            merged.runner_args = {**merged.runner_args, **variant_config["runner_args"]}
        if "extra_args" in variant_config:
            merged.extra_args = list(variant_config.get("extra_args") or [])
        if "env" in variant_config:
            merged.env = {**merged.env, **variant_config["env"]}
        return merged

    def _write_trace(self, result: RunResult) -> tuple[str | None, str | None]:
        """Persist the raw and normalised traces.  Returns their paths."""
        trace_dir = self.paths.traces / result.experiment_id
        raw_dir = trace_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        trace_dir.mkdir(parents=True, exist_ok=True)

        raw_path = raw_dir / f"{result.run_id}.jsonl"
        if result.raw_events:
            with raw_path.open("w", encoding="utf-8") as handle:
                for event in result.raw_events:
                    handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        elif self.config.keep_raw_traces:
            raw_path.write_text("", encoding="utf-8")

        normalised_path: str | None = None
        if result.trace is not None:
            trace = result.trace
            trace.raw_path = str(raw_path) if raw_path.exists() else None
            payload = trace.model_dump(mode="json")
            payload, stats = redact_obj(payload)
            trace.redaction_stats = stats
            normalised_path = str(trace_dir / f"{result.run_id}.json")
            Path(normalised_path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        return normalised_path, (str(raw_path) if raw_path.exists() else None)

    # -- main entry point --------------------------------------------------- #
    def execute(
        self,
        *,
        cases: list[str] | None = None,
        repetitions: int | None = None,
        dry_run: bool = False,
        on_run: Callable[[RunRecord], None] | None = None,
        on_run_start: Callable[[PlannedRun], None] | None = None,
    ) -> ExecutionResult:
        spec = self.spec
        case_ids = cases or list(self.benchmark.case_ids)
        plan = build_run_plan(spec, case_ids, repetitions=repetitions)
        execution = ExecutionResult(experiment_id=spec.id)

        if dry_run:
            return execution

        # Materialise each variant once to learn its fingerprint and the diff
        # between the two sides.  The actual run workspaces get their own fresh
        # copy so the agent cannot modify the framework.
        fingerprint_dirs: dict[str, Path] = {}
        for variant in (spec.baseline, spec.candidate):
            scratch = self.paths.workspaces / f"_probe-{variant.name}"
            materialised = materialise(
                variant,
                scratch,
                worktree_root=self.paths.workspaces / "_worktrees",
                clean=True,
            )
            execution.materialised[variant.name] = materialised
            fingerprint_dirs[variant.name] = scratch

        if spec.baseline.name in execution.materialised and spec.candidate.name in execution.materialised:
            execution.tree_diff = diff_file_maps(
                execution.materialised[spec.baseline.name].file_map,
                execution.materialised[spec.candidate.name].file_map,
            )

        for planned in plan.runs:
            if on_run_start is not None:
                on_run_start(planned)
            case = self.benchmark.case(planned.case_id)
            variant = self._variant(planned.variant)
            run_config = self._run_config_for(variant)

            workspace = self.workspaces.create(
                f"{spec.id}-{planned.variant}-{planned.case_id}-r{planned.repetition}"
            )
            try:
                materialised = materialise(
                    variant,
                    workspace.root,
                    worktree_root=self.paths.workspaces / "_worktrees",
                )
                materialise_bundle(case, workspace)

                result = self.runner.run(
                    case.bundle,
                    run_config,
                    workspace,
                    experiment_id=spec.id,
                    variant=planned.variant,
                    repetition=planned.repetition,
                )
                result.provenance.update(
                    {
                        "order_index": planned.order_index,
                        "framework_fingerprint": materialised.fingerprint,
                        "framework_commit": materialised.commit,
                        "framework_dirty": materialised.dirty,
                        "dataset_hash": self.benchmark.content_hash,
                        "dataset_version": self.benchmark.version,
                        "evaluator": getattr(self.evaluator, "name", "unknown"),
                        "evaluator_version": getattr(self.evaluator, "version", "unknown"),
                        "model": result.runtime.model,
                        "runtime": result.runtime.name,
                        "runtime_version": result.runtime.version,
                        "seed": run_config.seed,
                        "order_policy": plan.order_policy,
                    }
                )

                _verify_capabilities(result, variant)
                normalised_path, raw_path = self._write_trace(result)
                result.trace_path = normalised_path
                result.raw_trace_path = raw_path

                evaluation = self.evaluator.evaluate(case, result)
                record = RunRecord(
                    result=result,
                    evaluation=evaluation,
                    fingerprint=materialised.fingerprint,
                    dataset_hash=self.benchmark.content_hash,
                    dataset_version=self.benchmark.version,
                    evaluator_version=getattr(self.evaluator, "version", "unknown"),
                )
                execution.records.append(record)
                self._persist_run(record)
                if on_run is not None:
                    on_run(record)
            except Exception as exc:  # noqa: BLE001 - one bad run must not kill the batch
                execution.errors.append(
                    f"run {planned.variant}/{planned.case_id}/r{planned.repetition} failed: {exc}"
                )
            finally:
                self.workspaces.destroy(workspace)

        # Remove the probe copies used for fingerprinting.
        for path in fingerprint_dirs.values():
            import shutil

            shutil.rmtree(path, ignore_errors=True)

        return execution

    # -- persistence -------------------------------------------------------- #
    def _persist_run(self, record: RunRecord) -> Path:
        run_dir = self.paths.runs / self.spec.id
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / f"{record.result.run_id}.json"
        payload = record.as_dict()
        payload, _ = redact_obj(payload)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        return target


# --------------------------------------------------------------------------- #
# helpers used by the CLI
# --------------------------------------------------------------------------- #


def _verify_capabilities(result: RunResult, variant: Variant) -> None:
    """Decide, from the trace evidence, whether the variant's declared
    capabilities were really exercised.

    A capability listed in the experiment file is a *claim*; the trace is the
    *evidence*.  The result is stored on the trace so that the comparison can
    qualify its conclusion (ADR-007).
    """
    if result.trace is None:
        return
    from ..traces.normalize import build_capabilities

    tools: list[str] = []
    for event in result.trace.events:
        if event.type == "tool_call" and event.tool:
            tools.append(event.tool)
    result.trace.capabilities = build_capabilities(
        expected=variant.expected_capabilities,
        available=result.trace.available_capabilities,
        invoked=result.trace.invoked_capabilities,
        tools=tools,
        source=f"{result.runtime.name}.trace",
    )


def describe_experiment(spec: ExperimentSpec) -> str:
    """A short human-readable summary of an experiment."""
    lines = [
        f"{spec.id}: {spec.title}",
        f"  hypothesis : {spec.hypothesis.strip()}",
        f"  variable   : {spec.independent_variable}",
        f"  baseline   : {spec.baseline.name} ({spec.baseline.framework.type})",
        f"  candidate  : {spec.candidate.name} ({spec.candidate.framework.type})",
        f"  controls   : runtime={spec.controls.runtime} model={spec.controls.model} "
        f"reps={spec.controls.repetitions} benchmark={spec.controls.benchmark}",
        f"  metric     : {spec.decision_rules.primary_metric} "
        f"({spec.decision_rules.direction}), min_delta={spec.decision_rules.min_delta}",
    ]
    return "\n".join(lines)


def redact_run_result(result: RunResult) -> RunResult:
    """Redact a run result in place (used before writing reports)."""
    result.output_text = redact_text(result.output_text)[0]
    result.output_files = {k: redact_text(v)[0] for k, v in result.output_files.items()}
    return result


def load_case(benchmark: Benchmark, case_id: str) -> BenchmarkCase:
    return benchmark.case(case_id)


def default_decision() -> Decision:
    return Decision()
