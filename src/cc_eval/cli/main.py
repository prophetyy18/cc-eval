"""The ``cc-eval`` command line interface.

Design principles:

* **CLI first.**  Every capability is reachable from the terminal; there is no
  web UI and no background service.
* **Honest failures.**  If a runner is missing credentials or a binary, the
  command says exactly what is missing instead of pretending to have run.
* **Safe by default.**  The fake runner is the default, dangerous permission
  modes are refused, and nothing in the CLI ever modifies the framework under
  test (no commits, no branch merges, no rollbacks).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import typer
import yaml

from ..comparison.compare import compare
from ..core.config import EvalConfig, EvalPaths, load_config, resolve_root, save_config
from ..core.experiment import (
    ExperimentError,
    ExperimentRunner,
    RunRecord,
    build_run_plan,
    describe_experiment,
    experiment_path,
    load_experiment,
    save_experiment,
)
from ..core.models import ComparisonReport, Decision, ExperimentSpec, Variant
from ..core.registry import Registry
from ..datasets.loader import Benchmark, BenchmarkError, load_benchmark, validate_benchmark
from ..evaluators import get_evaluator
from ..reports import (
    ReportModel,
    build_report_model,
    render_markdown,
    write_html,
    write_model_json,
)
from ..runners import available_runners, get_runner
from ..runners.base import RunConfig
from ..traces.store import TraceStore

app = typer.Typer(
    name="cc-eval",
    help="Controlled experiments for agent frameworks: run, compare and report.",
    no_args_is_help=True,
    add_completion=False,
)
experiment_app = typer.Typer(help="Create and inspect experiments.", no_args_is_help=True)
benchmark_app = typer.Typer(help="Inspect and validate benchmarks.", no_args_is_help=True)
registry_app = typer.Typer(help="Inspect the experiment registry.", no_args_is_help=True)
runner_app = typer.Typer(help="Inspect the available agent runners.", no_args_is_help=True)

app.add_typer(experiment_app, name="experiment")
app.add_typer(benchmark_app, name="benchmark")
app.add_typer(registry_app, name="registry")
app.add_typer(runner_app, name="runner")

RootOption = typer.Option(
    None,
    "--root",
    "-r",
    help="cc-eval workspace root (default: $CC_EVAL_HOME or ./.cc-eval).",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _seed_example_experiments(paths: EvalPaths) -> list[str]:
    """Copy the shipped example experiments into the workspace.

    The examples reference paths relative to the repository root, so they are
    only usable when cc-eval runs from that root - which is where the
    repository is meant to be used.
    """
    source = Path("experiments")
    if not source.is_dir():
        return []
    copied: list[str] = []
    for path in sorted(source.glob("*.yaml")):
        target = paths.experiments / path.name
        if target.exists():
            continue
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(path.stem)
    return copied


def _paths(root: Optional[str]) -> EvalPaths:
    return EvalPaths(resolve_root(root))


def _load_spec(paths: EvalPaths, experiment_id: str, file: Optional[str]) -> tuple[ExperimentSpec, Path]:
    spec_path = Path(file) if file else experiment_path(paths, experiment_id)
    try:
        spec = load_experiment(spec_path)
    except ExperimentError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    if spec.id != experiment_id:
        # The spec file owns the id: every artefact is stored under it.
        typer.secho(
            f"note: the experiment id in {spec_path} is {spec.id!r}; artefacts are stored "
            f"under {spec.id!r}, not {experiment_id!r}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return spec, spec_path


def _resolve_benchmark(spec: ExperimentSpec, spec_path: Path, paths: EvalPaths) -> Path:
    """Find the benchmark directory referenced by the experiment."""
    raw = Path(spec.controls.benchmark)
    candidates = [raw]
    if not raw.is_absolute():
        candidates += [
            Path.cwd() / raw,
            spec_path.parent / raw,
            spec_path.parent.parent / raw,
            paths.root.parent / raw,
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    typer.secho(
        f"benchmark not found: {spec.controls.benchmark} (looked in "
        + ", ".join(str(c) for c in candidates)
        + ")",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _load_records(paths: EvalPaths, experiment_id: str) -> list[RunRecord]:
    """Load persisted run records for an experiment."""
    from ..core.models import EvaluationResult, RunResult

    run_dir = paths.runs / experiment_id
    records: list[RunRecord] = []
    if not run_dir.exists():
        return records
    for path in sorted(run_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.append(
                RunRecord(
                    result=RunResult(**payload["result"]),
                    evaluation=EvaluationResult(**payload["evaluation"]),
                    fingerprint=payload.get("fingerprint", ""),
                    dataset_hash=payload.get("dataset_hash", ""),
                    dataset_version=payload.get("dataset_version", ""),
                    evaluator_version=payload.get("evaluator_version", ""),
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            typer.secho(f"skipping unreadable run record {path.name}: {exc}", fg=typer.colors.YELLOW, err=True)
    return records


def _build_evaluator(
    spec: ExperimentSpec,
    *,
    judge_backend: str | None = None,
    judge_model: str | None = None,
):
    """Build the evaluator named by the experiment.

    The llm-judge evaluator is optional and must be configured explicitly: it
    needs a backend, and without one cc-eval refuses to run rather than
    inventing a score.
    """
    if spec.controls.evaluator.startswith("llm-judge"):
        if not judge_backend:
            raise ValueError(
                "the llm-judge evaluator needs a backend: pass --judge-backend "
                "claude-cli or --judge-backend anthropic-api"
            )
        from ..evaluators.llm_judge import (
            AnthropicApiJudgeBackend,
            ClaudeCliJudgeBackend,
            LLMJudgeEvaluator,
        )

        backend = (
            ClaudeCliJudgeBackend(model=judge_model)
            if judge_backend == "claude-cli"
            else AnthropicApiJudgeBackend(model=judge_model or "claude-opus-5-5")
        )
        ok, reason = backend.available()
        if not ok:
            raise ValueError(f"judge backend {judge_backend!r} is not available: {reason}")
        rubric_text = _judge_rubric_text(spec)
        return LLMJudgeEvaluator(
            rubric_text,
            backend,
            rubric_name="experiment-rubric",
            rubric_version=spec.id,
        )
    return get_evaluator(spec.controls.evaluator, Path("rubrics"))


def _judge_rubric_text(spec: ExperimentSpec) -> str:
    """The fixed rubric the judge must use for this experiment."""
    path = Path("rubrics") / f"{spec.decision_rules.primary_metric}-v1.yaml"
    if not path.exists():
        path = Path("rubrics") / "spec-review-v1.yaml"
    if path.exists():
        import yaml as _yaml

        data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        lines = [f"Rubric: {data.get('name', path.stem)} {data.get('version', '')}"]
        for criterion in data.get("criteria") or []:
            lines.append(
                f"- {criterion.get('id')}: {criterion.get('name')} "
                f"({criterion.get('description', '').strip()})"
            )
        return "\n".join(lines)
    return (
        "Score the agent output on whether the user requirement was correctly "
        "implemented, whether the reasoning is complete, and whether every "
        "claim is supported by the input."
    )


def _build_comparison(
    spec: ExperimentSpec,
    records: list[RunRecord],
    paths: EvalPaths,
    tree_diff=None,
) -> ComparisonReport:
    dataset_version = records[0].dataset_version if records else ""
    dataset_hash = records[0].dataset_hash if records else ""
    evaluator = records[0].evaluation.evaluator if records else spec.controls.evaluator
    report = compare(
        spec,
        records,
        tree_diff=tree_diff,
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        evaluator=evaluator,
    )
    return report


def _maybe_load_benchmark(
    spec: ExperimentSpec, spec_path: Path, paths: EvalPaths
) -> Benchmark | None:
    """Load the benchmark of an experiment, if it is still reachable.

    The benchmark carries the case metadata (category, title, tags) that the
    report shows.  A report must still be renderable when the benchmark is
    gone, so this never fails: the fields are simply reported as unknown.
    """
    raw = Path(spec.controls.benchmark)
    candidates = [raw]
    if not raw.is_absolute():
        candidates += [
            Path.cwd() / raw,
            spec_path.parent / raw,
            spec_path.parent.parent / raw,
            paths.root.parent / raw,
        ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return load_benchmark(candidate)
        except BenchmarkError:
            continue
    return None


def _build_model(
    spec: ExperimentSpec,
    records: list[RunRecord],
    paths: EvalPaths,
    *,
    tree_diff=None,
    benchmark: Benchmark | None = None,
    execution_errors: list[str] | None = None,
    execution_warnings: list[str] | None = None,
) -> ReportModel:
    """Build the report model: the single input of every renderer."""
    comparison = _build_comparison(spec, records, paths, tree_diff=tree_diff)
    return build_report_model(
        spec,
        comparison,
        records,
        benchmark=benchmark,
        dataset_name=benchmark.name if benchmark is not None else "",
        execution_errors=execution_errors or [],
        execution_warnings=execution_warnings or [],
    )


#: The file extensions of the report formats.
_FORMAT_SUFFIX = {"html": ".html", "json": ".json", "md": ".md"}

#: The formats the CLI accepts and the files each of them produces.
_FORMATS = {
    "html": ["html"],
    "md": ["md"],
    "json": ["json"],
    "both": ["md", "json"],
    "all": ["html", "md", "json"],
}


def _write_report(
    model: ReportModel,
    fmt: str,
    target: Path,
    *,
    template: str | None = None,
) -> Path:
    """Write one report file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "html":
        write_html(model, target, template=template)
    elif fmt == "json":
        write_model_json(model, target)
    elif fmt == "md":
        target.write_text(render_markdown(model.report, model.experiment), encoding="utf-8")
    else:  # pragma: no cover - guarded by the CLI
        raise ValueError(f"unknown report format: {fmt}")
    return target


def _write_reports(
    model: ReportModel,
    formats: list[str],
    paths: EvalPaths,
    *,
    output: str | None = None,
    template: str | None = None,
) -> dict[str, Path]:
    """Write the requested report formats.

    Without ``--output`` the reports go to ``<root>/reports/<EXP-ID>/`` (the
    bundle: ``report.html``, ``report.json``, ``report.md``) and the legacy
    flat files ``<root>/reports/<EXP-ID>.json`` / ``.md`` are kept in sync.
    """
    written: dict[str, Path] = {}
    bundle = paths.reports / model.metadata.experiment_id
    for fmt in formats:
        suffix = _FORMAT_SUFFIX[fmt]
        if output is not None:
            target = Path(output) if len(formats) == 1 else Path(output).with_suffix(suffix)
        else:
            target = bundle / f"report{suffix}"
        written[fmt] = _write_report(model, fmt, target, template=template)
        if output is None and fmt in {"json", "md"}:
            legacy = paths.reports / f"{model.metadata.experiment_id}{suffix}"
            written[f"legacy-{fmt}"] = _write_report(model, fmt, legacy, template=template)
    return written


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


@app.command("init")
def init(
    root: Optional[str] = RootOption,
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
    with_examples: bool = typer.Option(
        True, "--with-examples/--no-examples", help="Copy the shipped example experiments."
    ),
) -> None:
    """Create the cc-eval workspace (directories + config)."""
    paths = _paths(root)
    paths.ensure()
    cfg_path = paths.config_file
    if cfg_path.exists() and not force:
        typer.echo(f"cc-eval workspace already initialised at {paths.root}")
    else:
        save_config(EvalConfig(root=paths.root))
        typer.echo(f"initialised cc-eval workspace at {paths.root}")

    copied = _seed_example_experiments(paths)
    if copied:
        typer.echo(f"copied {len(copied)} example experiment(s): {', '.join(copied)}")

    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. create a benchmark:      benchmarks/<name>/manifest.yaml")
    typer.echo("  2. create an experiment:    cc-eval experiment init EXP-001")
    typer.echo("  3. run it:                  cc-eval run EXP-001 --runner fake")
    typer.echo("  4. compare and report:      cc-eval compare EXP-001")
    typer.echo("                              cc-eval report EXP-001")


@experiment_app.command("init")
def experiment_init(
    experiment_id: str = typer.Argument(..., help="Experiment id, e.g. EXP-001."),
    root: Optional[str] = RootOption,
    title: str = typer.Option("", "--title", help="Short experiment title."),
    hypothesis: str = typer.Option("", "--hypothesis", help="What you expect to change."),
    variable: str = typer.Option("", "--variable", help="The single independent variable."),
    baseline_path: str = typer.Option("examples/framework/baseline", "--baseline-path"),
    candidate_path: str = typer.Option("examples/framework/candidate", "--candidate-path"),
    benchmark: str = typer.Option("benchmarks/spec", "--benchmark", help="Benchmark directory."),
    repetitions: int = typer.Option(3, "--repetitions", "-n"),
    runtime: str = typer.Option("fake", "--runtime", help="Runner to use (fake, claude, codex)."),
    metric: str = typer.Option("recall", "--metric", help="Primary metric."),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Create an experiment specification file."""
    paths = _paths(root)
    paths.ensure()
    target = experiment_path(paths, experiment_id)
    if target.exists() and not overwrite:
        typer.secho(f"{target} already exists (use --overwrite)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    spec = ExperimentSpec(
        id=experiment_id,
        title=title or f"Experiment {experiment_id}",
        hypothesis=hypothesis or "TODO: state what you expect to improve and why.",
        independent_variable=variable or "TODO: name the single variable that changes.",
        baseline=Variant(
            name="baseline",
            framework={"type": "path", "path": baseline_path},
            config={},
        ),
        candidate=Variant(
            name="candidate",
            framework={"type": "path", "path": candidate_path},
            config={},
        ),
        controls={
            "runtime": runtime,
            "benchmark": benchmark,
            "repetitions": repetitions,
            "evaluator": "deterministic-v1",
            "order": "alternating",
            "seed": 20240101,
        },
        metrics=[metric],
        expected_changes=[],
        decision_rules={
            "primary_metric": metric,
            "direction": "higher_is_better",
            "min_delta": 0.05,
            "min_repetitions": repetitions,
            "max_regressed_cases": 0,
            "require_confound_free": True,
            "confidence": {"method": "paired_bootstrap", "alpha": 0.05, "iterations": 2000},
        },
    )
    save_experiment(spec, target)
    typer.echo(f"wrote {target}")
    typer.echo("")
    typer.echo(describe_experiment(spec))
    typer.echo("")
    typer.echo("Edit the file to fill in the hypothesis, the independent variable and")
    typer.echo("the expected_changes before running anything.")


@experiment_app.command("list")
def experiment_list(root: Optional[str] = RootOption) -> None:
    """List the experiments in the registry."""
    paths = _paths(root)
    registry = Registry(paths.registry)
    entries = registry.list_experiments()
    if not entries:
        typer.echo("no experiments registered yet")
        return
    for entry in entries:
        decision = registry.latest_decision(entry["experiment_id"])
        verdict = decision.verdict.value if decision else "-"
        typer.echo(
            f"{entry['experiment_id']:<12} {verdict:<14} {entry.get('title', '')}"
        )


@experiment_app.command("show")
def experiment_show(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file", help="Path to the experiment YAML."),
) -> None:
    """Show an experiment specification."""
    paths = _paths(root)
    spec, spec_path = _load_spec(paths, experiment_id, file)
    typer.echo(f"# {spec_path}")
    typer.echo(describe_experiment(spec))
    typer.echo("")
    typer.echo(yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True))


@experiment_app.command("accept")
def experiment_accept(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    note: str = typer.Option("", "--note"),
    file: Optional[str] = typer.Option(None, "--file"),
) -> None:
    """Record that a human accepted the experiment's outcome.

    This only writes to the registry.  It never commits, merges or rolls back
    anything in the framework repository.
    """
    paths = _paths(root)
    spec, _ = _load_spec(paths, experiment_id, file)
    registry = Registry(paths.registry)
    decision = registry.latest_decision(spec.id)
    if decision is None:
        typer.secho("no decision has been recorded yet - run 'cc-eval compare' first", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    decision.accepted = True
    decision.notes = note or decision.notes
    registry.append_decision(spec.id, decision)
    typer.echo(f"recorded acceptance of {spec.id} ({decision.verdict.value})")
    typer.echo("nothing in the framework repository was modified")


@experiment_app.command("rollback")
def experiment_rollback(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file"),
) -> None:
    """Show which framework commit to roll back to.

    This is a *read-only* helper.  cc-eval never performs the rollback.
    """
    paths = _paths(root)
    spec, _ = _load_spec(paths, experiment_id, file)
    baseline = spec.baseline.framework
    typer.echo(f"Experiment {experiment_id}")
    typer.echo("")
    typer.echo(f"baseline framework : {baseline.type} {baseline.ref or baseline.path}")
    typer.echo(f"candidate framework: {spec.candidate.framework.type} "
               f"{spec.candidate.framework.ref or spec.candidate.framework.path}")
    typer.echo("")
    typer.echo("To roll back, review the baseline commit yourself and run the git")
    typer.echo("commands you are comfortable with. cc-eval does not modify git state.")


@app.command("run")
def run(
    experiment_id: str = typer.Argument(...),
    runner: str = typer.Option("fake", "--runner", help="fake, claude or codex."),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file", help="Path to the experiment YAML."),
    cases: Optional[list[str]] = typer.Option(None, "--case", help="Run only these case ids."),
    repetitions: Optional[int] = typer.Option(None, "--repetitions", "-n"),
    model: Optional[str] = typer.Option(None, "--model", help="Override the pinned model."),
    budget: Optional[float] = typer.Option(None, "--max-budget-usd"),
    timeout: Optional[int] = typer.Option(None, "--timeout"),
    inherit_env: bool = typer.Option(
        False,
        "--inherit-env",
        help=(
            "Give the agent the host environment, including API credentials. "
            "Required for the real runners unless the credentials are passed "
            "explicitly with --env."
        ),
    ),
    env_vars: Optional[list[str]] = typer.Option(
        None, "--env", help="Extra environment variable KEY=VALUE for the runtime."
    ),
    judge_backend: Optional[str] = typer.Option(
        None,
        "--judge-backend",
        help="Judge backend for the llm-judge evaluator: claude-cli or anthropic-api.",
    ),
    judge_model: Optional[str] = typer.Option(
        None, "--judge-model", help="Model the judge should use."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan without running."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Execute an experiment: run every case for both variants."""
    paths = _paths(root)
    cfg = load_config(paths.root)
    spec, spec_path = _load_spec(paths, experiment_id, file)

    try:
        benchmark = load_benchmark(_resolve_benchmark(spec, spec_path, paths))
    except BenchmarkError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if spec.controls.dataset_version and benchmark.version != spec.controls.dataset_version:
        typer.secho(
            f"warning: the experiment pins dataset version {spec.controls.dataset_version} "
            f"but the benchmark is {benchmark.version}",
            fg=typer.colors.YELLOW,
            err=True,
        )

    try:
        evaluator = _build_evaluator(
            spec, judge_backend=judge_backend, judge_model=judge_model
        )
    except (ValueError, FileNotFoundError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    try:
        agent = get_runner(runner)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    ok, reason = agent.available()
    if not ok:
        typer.secho(f"runner {runner!r} is not available: {reason}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    extra_env: dict[str, str] = {}
    for item in env_vars or []:
        if "=" not in item:
            typer.secho(f"invalid --env value {item!r}; expected KEY=VALUE", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        key, _, value = item.partition("=")
        extra_env[key.strip()] = value

    run_config = RunConfig(
        runtime=runner,
        model=model or spec.controls.model,
        timeout_sec=timeout or spec.controls.timeout_sec,
        max_budget_usd=budget if budget is not None else spec.controls.max_budget_usd,
        seed=spec.controls.seed,
        repetitions=repetitions or spec.controls.repetitions,
        runner_args=dict(spec.controls.runner_args),
        inherit_env=inherit_env,
        env=extra_env,
    )
    if inherit_env:
        typer.secho(
            "warning: the agent inherits the host environment, including API "
            "credentials. Only do this with a framework you trust.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    plan = build_run_plan(spec, list(benchmark.case_ids), repetitions=repetitions)
    if dry_run:
        typer.echo(f"plan: {len(plan)} run(s)")
        for planned in plan.runs:
            typer.echo(
                f"  #{planned.order_index:<4} {planned.variant:<10} {planned.case_id} "
                f"repetition {planned.repetition}"
            )
        return

    runner_instance = ExperimentRunner(
        spec,
        benchmark=benchmark,
        evaluator=evaluator,
        runner=agent,
        paths=paths,
        config=cfg,
        run_config=run_config,
    )

    registry = Registry(paths.registry)
    registry.register_experiment(
        spec.id,
        title=spec.title,
        hypothesis=spec.hypothesis,
        framework_change=spec.framework_change,
        baseline_commit=spec.baseline.framework.ref,
        candidate_commit=spec.candidate.framework.ref,
        dataset=benchmark.name,
        dataset_version=benchmark.version,
        status="running",
    )

    if not quiet:
        typer.echo(f"running {len(plan)} run(s) with the {runner} runner...")

    def _on_run(record: RunRecord) -> None:
        registry.append_run(spec.id, record.result)
        registry.append_evaluation(spec.id, record.evaluation)
        if not quiet:
            metrics = ", ".join(
                f"{k}={v:.3f}" for k, v in record.evaluation.metrics.items() if isinstance(v, float)
            )
            typer.echo(
                f"  {record.result.variant:<10} {record.result.case_id:<12} "
                f"r{record.result.repetition}  {record.result.status:<10} {metrics}"
            )

    execution = runner_instance.execute(
        cases=list(cases) if cases else None,
        repetitions=repetitions,
        on_run=_on_run,
    )

    for warning in execution.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)
    for error in execution.errors:
        typer.secho(f"error: {error}", fg=typer.colors.RED, err=True)

    # Build and persist the comparison so that `compare` and `report` can work
    # from the stored data.
    records = [record for record in execution.records]
    model = _build_model(
        spec,
        records,
        paths,
        tree_diff=execution.tree_diff,
        benchmark=benchmark,
        execution_errors=execution.errors,
        execution_warnings=execution.warnings,
    )
    report = model.report
    written = _write_reports(model, ["html", "json", "md"], paths)
    registry.append_decision(spec.id, report.decision)
    registry.register_experiment(
        spec.id,
        title=spec.title,
        hypothesis=spec.hypothesis,
        framework_change=spec.framework_change,
        baseline_commit=spec.baseline.framework.ref,
        candidate_commit=spec.candidate.framework.ref,
        dataset=benchmark.name,
        dataset_version=benchmark.version,
        status="completed" if model.status == "complete" else "partial",
        reports={fmt: str(path) for fmt, path in written.items()},
    )

    typer.echo("")
    typer.echo(f"verdict: {report.decision.verdict.value} - {report.decision.reason}")
    typer.echo(f"html report: {written['html']}")
    typer.echo(f"json report: {written['json']}")
    typer.echo(f"markdown:    {written['md']}")
    if model.status != "complete":
        typer.secho(
            f"warning: the experiment did not complete ({model.status}); the report is "
            "marked as partial and must not be read as a successful experiment.",
            fg=typer.colors.YELLOW,
            err=True,
        )


@app.command("compare")
def compare_command(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write the JSON report here."),
    show_table: bool = typer.Option(True, "--table/--no-table"),
) -> None:
    """Compare the baseline and the candidate of an experiment."""
    paths = _paths(root)
    spec, spec_path = _load_spec(paths, experiment_id, file)
    records = _load_records(paths, spec.id)
    if not records:
        typer.secho(
            f"no runs found for {spec.id} - run 'cc-eval run {spec.id}' first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tree_diff = _tree_diff_from_records(spec, paths)
    model = _build_model(spec, records, paths, tree_diff=tree_diff)
    report = model.report

    report_path = Path(output) if output else paths.reports / f"{spec.id}.json"
    write_model_json(model, report_path)
    registry = Registry(paths.registry)
    registry.append_decision(experiment_id, report.decision)

    if show_table:
        typer.echo("")
        typer.echo(f"verdict: {report.decision.verdict.value}")
        typer.echo(f"reason : {report.decision.reason}")
        typer.echo("")
        headers = ("metric", "baseline", "candidate", "delta", "ci")
        typer.echo(f"{headers[0]:<24}{headers[1]:>12}{headers[2]:>12}{headers[3]:>12}   {headers[4]}")
        for aggregate in report.metrics:
            ci = (
                f"[{aggregate.ci_low:+.3f}, {aggregate.ci_high:+.3f}]"
                if aggregate.ci_low is not None
                else "n/a"
            )
            typer.echo(
                f"{aggregate.metric:<24}"
                f"{_fmt(aggregate.baseline):>12}"
                f"{_fmt(aggregate.candidate):>12}"
                f"{_fmt(aggregate.delta):>12}   {ci}"
            )
        typer.echo("")
        typer.echo("per-case:")
        for case in report.cases:
            typer.echo(
                f"  {case.case_id:<16}{_fmt(case.baseline_score):>10}"
                f"{_fmt(case.candidate_score):>10}{_fmt(case.delta):>10}   {case.status}"
            )
        if report.confounds:
            typer.echo("")
            typer.echo("confounds:")
            for confound in report.confounds:
                typer.echo(f"  [{confound.severity}] {confound.kind}: {confound.detail}")

    typer.echo("")
    typer.echo(f"json report: {report_path}")


@app.command("report")
def report_command(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path."),
    fmt: str = typer.Option(
        "all", "--format", "-f", help="html, md, json, both (md+json) or all (default)."
    ),
    template: Optional[str] = typer.Option(
        None, "--template", help="Custom HTML template (Jinja2) to render the report with."
    ),
) -> None:
    """Render the experiment report.

    The HTML report is the default output; it is a self-contained file that
    opens in any browser and needs no network access.
    """
    paths = _paths(root)
    spec, spec_path = _load_spec(paths, experiment_id, file)
    records = _load_records(paths, spec.id)
    if not records:
        typer.secho(
            f"no runs found for {spec.id} - run 'cc-eval run {spec.id}' first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    formats = _FORMATS.get(fmt)
    if formats is None:
        typer.secho(
            f"unknown format {fmt!r}; use one of: {', '.join(sorted(_FORMATS))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tree_diff = _tree_diff_from_records(spec, paths)
    benchmark = _maybe_load_benchmark(spec, spec_path, paths)
    model = _build_model(
        spec, records, paths, tree_diff=tree_diff, benchmark=benchmark
    )
    written = _write_reports(model, formats, paths, output=output, template=template)

    if "md" in formats:
        typer.echo(render_markdown(model.report, model.experiment))
        typer.echo("")
    for name in formats:
        typer.echo(f"{name} report: {written[name]}")
    if "html" in formats:
        typer.echo("")
        typer.echo(f"open it with: xdg-open {written['html']}")


@app.command("render")
def render_command(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
    file: Optional[str] = typer.Option(None, "--file"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path."),
    fmt: str = typer.Option(
        "html", "--format", "-f", help="html, md, json, both (md+json) or all (default)."
    ),
    template: Optional[str] = typer.Option(
        None, "--template", help="Custom HTML template (Jinja2) to render the report with."
    ),
) -> None:
    """(Re)render the reports from the stored runs - without running anything.

    The evaluation is not repeated: the stored run records and traces are
    rendered again, which is how the HTML template can be iterated on.
    """
    paths = _paths(root)
    spec, spec_path = _load_spec(paths, experiment_id, file)
    records = _load_records(paths, spec.id)
    if not records:
        typer.secho(
            f"no runs found for {spec.id} - run 'cc-eval run {spec.id}' first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    formats = _FORMATS.get(fmt)
    if formats is None:
        typer.secho(
            f"unknown format {fmt!r}; use one of: {', '.join(sorted(_FORMATS))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tree_diff = _tree_diff_from_records(spec, paths)
    benchmark = _maybe_load_benchmark(spec, spec_path, paths)
    model = _build_model(
        spec, records, paths, tree_diff=tree_diff, benchmark=benchmark
    )
    written = _write_reports(model, formats, paths, output=output, template=template)

    registry = Registry(paths.registry)
    registry.register_experiment(
        spec.id,
        title=spec.title,
        hypothesis=spec.hypothesis,
        framework_change=spec.framework_change,
        baseline_commit=spec.baseline.framework.ref,
        candidate_commit=spec.candidate.framework.ref,
        dataset=model.metadata.dataset_name,
        dataset_version=model.metadata.dataset_version,
        status="completed" if model.status == "complete" else "partial",
        reports={fmt: str(path) for fmt, path in written.items()},
    )

    for name in formats:
        typer.echo(f"{name} report: {written[name]}")
    typer.echo("no experiment was executed: the stored runs were rendered again")


@benchmark_app.command("validate")
def benchmark_validate(
    path: str = typer.Argument(..., help="Benchmark directory."),
) -> None:
    """Validate a benchmark directory (structure, ground truth, hashes)."""
    try:
        bench, problems = validate_benchmark(path)
    except BenchmarkError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"benchmark : {bench.name} {bench.version}")
    typer.echo(f"path     : {bench.path}")
    typer.echo(f"hash     : {bench.content_hash[:16]}")
    typer.echo(f"cases    : {len(bench.cases)}")
    for case in bench.cases:
        expected = case.oracle.total_expected
        typer.echo(
            f"  - {case.id:<16} type={case.bundle.task_type:<24} "
            f"ground-truth items={expected} tags={','.join(case.bundle.tags) or '-'}"
        )
    if problems:
        typer.echo("")
        typer.secho("problems:", fg=typer.colors.YELLOW, err=True)
        for problem in problems:
            typer.secho(f"  - {problem}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
    typer.echo("")
    typer.echo("benchmark is valid")


@registry_app.command("list")
def registry_list(root: Optional[str] = RootOption) -> None:
    """List the registry entries."""
    paths = _paths(root)
    registry = Registry(paths.registry)
    entries = registry.list_experiments()
    if not entries:
        typer.echo("the registry is empty")
        return
    for entry in entries:
        typer.echo(f"{entry['experiment_id']:<12} {entry.get('status', '?'):<10} {entry.get('title', '')}")


@registry_app.command("reports")
def registry_reports(
    root: Optional[str] = RootOption,
) -> None:
    """List the generated reports of every experiment."""
    paths = _paths(root)
    registry = Registry(paths.registry)
    entries = registry.list_reports()
    if not entries:
        typer.echo("no report has been generated yet")
        return
    for entry in entries:
        typer.echo(
            f"{entry['experiment_id']:<12} {entry.get('verdict', '-'):<14} "
            f"{entry.get('title', '')}"
        )
        for fmt, path in sorted((entry.get("reports") or {}).items()):
            typer.echo(f"    {fmt:<14} {path}")


@registry_app.command("show")
def registry_show(
    experiment_id: str = typer.Argument(...),
    root: Optional[str] = RootOption,
) -> None:
    """Show the recorded history of an experiment."""
    paths = _paths(root)
    registry = Registry(paths.registry)
    history = registry.history(experiment_id)
    if not any(history.values()):
        typer.secho(f"no history for {experiment_id}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"runs      : {len(history['runs'])}")
    typer.echo(f"evaluations: {len(history['results'])}")
    typer.echo(f"decisions : {len(history['decisions'])}")
    for entry in history["decisions"]:
        decision = entry.get("decision", {})
        typer.echo("")
        typer.echo(f"  {entry.get('recorded_at')}  verdict={decision.get('verdict')}  "
                   f"accepted={decision.get('accepted')}")
        typer.echo(f"  {decision.get('reason', '')}")


@runner_app.command("list")
def runner_list() -> None:
    """Show which agent runners are available on this machine."""
    status = available_runners()
    for name, (ok, reason) in status.items():
        marker = "ok " if ok else "no "
        colour = typer.colors.GREEN if ok else typer.colors.YELLOW
        typer.secho(f"[{marker}] {name:<8} {reason}", fg=colour)


# --------------------------------------------------------------------------- #
# misc helpers
# --------------------------------------------------------------------------- #


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _tree_diff_from_records(spec: ExperimentSpec, paths: EvalPaths):
    """Recompute the framework diff between the two variants."""
    from ..core.snapshot import diff_file_maps, materialise

    scratch = paths.workspaces / "_diff"
    try:
        maps = {}
        for variant in (spec.baseline, spec.candidate):
            target = scratch / variant.name
            materialised = materialise(
                variant, target, worktree_root=paths.workspaces / "_worktrees", clean=True
            )
            maps[variant.name] = materialised.file_map
        return diff_file_maps(maps[spec.baseline.name], maps[spec.candidate.name])
    except Exception:  # noqa: BLE001 - the diff is a diagnostic, never fatal
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> None:  # pragma: no cover - console entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
