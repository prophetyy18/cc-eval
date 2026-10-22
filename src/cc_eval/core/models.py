"""Pydantic schema contract for cc-eval.

Every artefact that crosses a module boundary is one of these models.  They are
the single source of truth for what a benchmark, an experiment, a run, a trace
and a comparison look like on disk.

Design rules encoded here:

* **No fabricated data.**  Fields that a runtime cannot supply are ``None`` and
  carry an explicit provenance marker (``Usage.source``) rather than a guess.
* **Hidden ground truth is a distinct type.**  ``Oracle`` is never part of a
  ``TaskBundle``; the loader refuses to put it in an agent-visible workspace.
* **Everything is hashable/serialisable** so a run can be reproduced from the
  metadata it records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# Common types
# --------------------------------------------------------------------------- #

Severity = Literal["high", "medium", "low"]
MetricDirection = Literal["higher_is_better", "lower_is_better"]
RunStatus = Literal[
    "success",
    "agent_error",
    "timeout",
    "budget_exhausted",
    "infra_error",
    "aborted",
]


class StrictModel(BaseModel):
    """Base model: reject unknown keys so typos surface instead of silently
    disabling a control."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Benchmark / dataset
# --------------------------------------------------------------------------- #


class GoldenItem(StrictModel):
    """A single ground-truth item.

    Used for both *defects* (things a reviewer must find) and *expected
    entities* (things a classifier must produce).  ``match_keywords`` is the
    human-authored evidence that lets a matcher decide whether an agent finding
    corresponds to this item without a semantic model.
    """

    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: Severity = "medium"
    description: str = ""
    evidence: str = ""
    match_keywords: list[str] = Field(default_factory=list)

    @field_validator("match_keywords")
    @classmethod
    def _normalise_keywords(cls, v: list[str]) -> list[str]:
        return [k.strip().lower() for k in v if k and k.strip()]


class Oracle(StrictModel):
    """Ground truth for one benchmark case.

    **Never materialised into an agent workspace.**  See ADR-001.
    """

    known_defects: list[GoldenItem] = Field(default_factory=list)
    missing_constraints: list[GoldenItem] = Field(default_factory=list)
    expected_entities: list[GoldenItem] = Field(default_factory=list)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    # Marks a deliberate control case: the correct answer is "nothing to
    # report", so an empty oracle is expected rather than an oversight.
    expect_no_findings: bool = False
    notes: str = ""

    @property
    def total_expected(self) -> int:
        return (
            len(self.known_defects)
            + len(self.missing_constraints)
            + len(self.expected_entities)
        )


class TaskBundle(StrictModel):
    """The *agent-visible* half of a benchmark case."""

    case_id: str
    task_type: str
    title: str = ""
    prompt: str = ""
    input_files: dict[str, str] = Field(default_factory=dict)
    expected_output_path: str = "out/result.json"
    # The paths (relative to the workspace root) whose files count as the
    # agent's *output*.  A skill that writes somewhere else - ``docs/intents``
    # for the cc-workflow intent skill, for example - declares it here so the
    # report can show and score it.
    output_paths: list[str] = Field(default_factory=lambda: ["out"])
    # When the *agent* chooses the name of the file it produces (a skill that
    # writes ``docs/intents/INT-001/INTENT.md``), the evaluator scores every
    # file matching this pattern instead of one fixed path.
    output_glob: str = ""
    output_schema: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    evidence: str = ""
    timeout_sec: int = 300
    allowed_tools: list[str] = Field(default_factory=list)


class BenchmarkCase(StrictModel):
    """A benchmark case = visible bundle + hidden oracle."""

    bundle: TaskBundle
    oracle: Oracle
    case_dir: str = ""

    @property
    def id(self) -> str:
        return self.bundle.case_id


class Benchmark(StrictModel):
    """A loaded, versioned dataset."""

    name: str
    version: str
    description: str = ""
    path: str = ""
    content_hash: str = ""
    cases: list[BenchmarkCase] = Field(default_factory=list)

    def case(self, case_id: str) -> BenchmarkCase:
        for c in self.cases:
            if c.id == case_id:
                return c
        raise KeyError(f"unknown case id: {case_id}")

    @property
    def case_ids(self) -> list[str]:
        return [c.id for c in self.cases]


# --------------------------------------------------------------------------- #
# Experiment specification
# --------------------------------------------------------------------------- #


class FrameworkRef(StrictModel):
    """How to materialise one side of the experiment.

    ``type="path"``  -> copy a directory snapshot (works for any repo state,
                        including a plain working tree).
    ``type="git"``   -> create a detached git worktree at ``ref``.

    Both produce an isolated directory that is never shared between runs.
    """

    type: Literal["path", "git"] = "path"
    repository: Path | None = None
    ref: str | None = None
    path: Path | None = None
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude: list[str] = Field(
        default_factory=lambda: [".git/**", "**/__pycache__/**", "**/.venv/**"]
    )
    # Optional pin for a knowledge base (ccw-knowledge) so retrieval results do
    # not drift between the two sides of the experiment.
    knowledge_snapshot: Path | None = None


class Variant(StrictModel):
    """One side of the A/B comparison."""

    name: str = Field(min_length=1)
    framework: FrameworkRef
    config: dict[str, Any] = Field(default_factory=dict)
    # Capabilities this variant is *supposed* to provide.  These are verified
    # against the trace, never assumed (ADR-007).
    expected_capabilities: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_")
        if not v:
            raise ValueError("variant name must not be empty")
        return v


class Controls(StrictModel):
    """Variables explicitly held constant across both variants."""

    runtime: str = "fake"
    model: str | None = None
    benchmark: Path = Path("benchmarks/spec")
    dataset_version: str | None = None
    evaluator: str = "deterministic-v1"
    repetitions: int = Field(default=1, ge=1)
    max_budget_usd: float | None = None
    timeout_sec: int = Field(default=300, ge=1)
    seed: int = 20240101
    order: Literal["alternating", "sequential", "random"] = "alternating"
    runner_args: dict[str, Any] = Field(default_factory=dict)


class ConfidenceConfig(StrictModel):
    method: Literal["paired_bootstrap", "none"] = "paired_bootstrap"
    alpha: float = Field(default=0.05, ge=0.0, lt=1.0)
    iterations: int = Field(default=2000, ge=100)
    seed: int = 20240101


class DecisionRules(StrictModel):
    """Pre-declared rules that mechanically derive the verdict.

    The rules live in the experiment file *before* the runs happen, so the
    verdict cannot be tuned after seeing the candidate's numbers (ADR-002).
    """

    primary_metric: str = "recall"
    direction: MetricDirection = "higher_is_better"
    min_delta: float = 0.0
    min_repetitions: int = 1
    # A per-case difference smaller than this is treated as "unchanged".
    case_delta_threshold: float = 0.0
    # Metrics that must not get worse.  A guard metric that drops by more than
    # `guard_tolerance` blocks an "improved" verdict - this is how a
    # false-positive regression is caught when the primary metric improves.
    guard_metrics: list[str] = Field(default_factory=list)
    guard_tolerance: float = 0.0
    max_regressed_cases: int = 0
    max_regression_rate: float | None = None
    require_confound_free: bool = True
    require_verified_capabilities: list[str] = Field(default_factory=list)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    notes: str = ""


class ExperimentSpec(StrictModel):
    """The full, human-readable description of one controlled experiment."""

    id: str = Field(min_length=1)
    title: str = ""
    hypothesis: str = ""
    independent_variable: str = ""
    baseline: Variant
    candidate: Variant
    controls: Controls = Field(default_factory=Controls)
    metrics: list[str] = Field(default_factory=list)
    # Globs of framework files expected to differ between the two variants.
    # Anything that differs outside these globs is reported as a confound.
    expected_changes: list[str] = Field(default_factory=list)
    decision_rules: DecisionRules = Field(default_factory=DecisionRules)
    # Provenance of the change under test.
    framework_change: str = ""
    references: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("experiment id must not be empty")
        return v

    def variant(self, name: str) -> Variant:
        for v in (self.baseline, self.candidate):
            if v.name == name:
                return v
        raise KeyError(f"unknown variant: {name}")


class Verdict(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"
    PENDING = "pending"


class Decision(StrictModel):
    """The recorded outcome of an experiment.  Written by ``cc-eval compare``."""

    verdict: Verdict = Verdict.PENDING
    reason: str = ""
    #: A stable identifier for the reason (``reason.metric_missing`` ...) so
    #: a report can render it in the reader's language.  ``reason`` stays the
    #: canonical English text.
    reason_code: str = ""
    reason_params: dict[str, Any] = Field(default_factory=dict)
    #: The stable identifiers of the notes, with their parameters.
    notes_codes: list[dict[str, Any]] = Field(default_factory=list)
    primary_metric: str = ""
    baseline_value: float | None = None
    candidate_value: float | None = None
    delta: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    rules_snapshot: DecisionRules | None = None
    decided_at: str = Field(default_factory=utcnow_iso)
    decided_by: str = "cc-eval"
    accepted: bool = False
    notes: str = ""


# --------------------------------------------------------------------------- #
# Traces
# --------------------------------------------------------------------------- #


class TraceEvent(StrictModel):
    """A normalised execution event.

    Normalisation is *best effort and lossy by design*: fields a runtime
    cannot supply stay ``None`` and are listed in ``Trace.missing_fields``
    rather than being invented.
    """

    seq: int
    ts: str | None = None
    type: str
    tool: str | None = None
    name: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None
    input_digest: str | None = None
    output_digest: str | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None
    error: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityStatus(StrictModel):
    """Whether a framework capability was actually exercised.

    ``verified``     -> evidence found in the trace / materialised framework
    ``not_invoked``  -> capability present in the framework but never used
    ``contradicted`` -> capability absent although the variant claims it
    ``unverified``   -> the runtime exposes no evidence either way
    """

    name: str
    status: Literal["verified", "not_invoked", "contradicted", "unverified"]
    evidence: str = ""
    source: str = ""


class Trace(StrictModel):
    """Normalised trace for one run."""

    run_id: str
    experiment_id: str
    case_id: str
    variant: str
    repetition: int = 1
    events: list[TraceEvent] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    # The runtime's own capability inventory: which capabilities were offered
    # and which were actually used.  These are the evidence behind the
    # per-capability verdict; a YAML declaration is not evidence.
    available_capabilities: list[str] = Field(default_factory=list)
    invoked_capabilities: list[str] = Field(default_factory=list)
    capabilities: list[CapabilityStatus] = Field(default_factory=list)
    raw_path: str | None = None
    redacted: bool = True
    redaction_stats: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Run configuration
# --------------------------------------------------------------------------- #


class RunConfig(StrictModel):
    """Everything a runner needs that is not case-specific."""

    runtime: str = "fake"
    model: str | None = None
    timeout_sec: int = 300
    max_budget_usd: float | None = None
    seed: int = 20240101
    repetitions: int = 1
    # Claude Code permission mode.  ``bypassPermissions`` is refused unless the
    # operator explicitly opted in.
    permission_mode: str = "acceptEdits"
    max_turns: int = 40
    # Codex sandbox policy.
    sandbox: str = "workspace-write"
    allow_bypass_permissions: bool = False
    # Free-form extra arguments (e.g. ``--add-dir``).
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # Opt-in: give the agent the host environment, including credentials.
    # Off by default - see the security notes in the README.
    inherit_env: bool = False
    runner_args: dict[str, Any] = Field(default_factory=dict)
    # A JSON schema the agent output must satisfy.
    output_schema: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Run results
# --------------------------------------------------------------------------- #


class Usage(StrictModel):
    """Token / cost accounting.

    ``source`` is the honesty guarantee:

    ``runtime``     -> reported by the agent runtime
    ``estimated``   -> derived by cc-eval from a documented formula
    ``unavailable`` -> the runtime does not expose it; the value is ``None``
    """

    source: Literal["runtime", "estimated", "unavailable"] = "unavailable"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def is_known(self) -> bool:
        return self.source != "unavailable"


class RuntimeInfo(StrictModel):
    """Everything needed to know *which* agent produced a result."""

    name: str
    version: str | None = None
    model: str | None = None
    model_reported_by: str | None = None
    command: list[str] = Field(default_factory=list)
    seed_supported: bool = False
    seed_note: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class DeterminismInfo(StrictModel):
    supported: bool = False
    seed: int | None = None
    note: str = "Agent runtimes do not expose seed control; results are not bit-reproducible."


class RunResult(StrictModel):
    """The outcome of a single agent invocation."""

    run_id: str
    experiment_id: str
    case_id: str
    variant: str
    repetition: int = 1
    status: RunStatus = "success"
    exit_code: int | None = None
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int | None = None
    output_text: str = ""
    output_files: dict[str, str] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    runtime: RuntimeInfo = Field(default_factory=lambda: RuntimeInfo(name="unknown"))
    determinism: DeterminismInfo = Field(default_factory=DeterminismInfo)
    trace: Trace | None = None
    # The runtime's own event stream, kept verbatim for manual audit.
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    trace_path: str | None = None
    raw_trace_path: str | None = None
    error: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "success"


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


class MatchedItem(StrictModel):
    golden_id: str
    predicted_id: str | None = None
    matched_by: str = ""
    confidence: float = 1.0


class EvaluationResult(StrictModel):
    """Per-run evaluation output."""

    run_id: str
    experiment_id: str
    case_id: str
    variant: str
    repetition: int = 1
    evaluator: str = ""
    evaluator_version: str = ""
    metrics: dict[str, float | None] = Field(default_factory=dict)
    matched: list[MatchedItem] = Field(default_factory=list)
    unmatched_golden: list[str] = Field(default_factory=list)
    unmatched_predictions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Free-form evaluator payload (per-criterion rubric scores, judge metadata,
    # raw judge output, ...).  Kept separate from `metrics` so that scalar
    # metrics stay comparable across evaluators.
    details: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


class MetricAggregate(StrictModel):
    metric: str
    baseline: float | None = None
    candidate: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    baseline_std: float | None = None
    candidate_std: float | None = None
    baseline_n: int = 0
    candidate_n: int = 0
    direction: MetricDirection = "higher_is_better"
    ci_low: float | None = None
    ci_high: float | None = None
    significant: bool | None = None


class CaseComparison(StrictModel):
    case_id: str
    task_type: str = ""
    baseline_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None
    status: Literal["improved", "regressed", "unchanged", "unknown"] = "unknown"
    baseline_per_repetition: list[float | None] = Field(default_factory=list)
    candidate_per_repetition: list[float | None] = Field(default_factory=list)
    baseline_variance: float | None = None
    candidate_variance: float | None = None
    # Metrics on which this case got worse, even when the primary metric did
    # not.  A case that only regresses on a guard metric must still be visible.
    regressed_metrics: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: The stable identifiers of the notes, with their parameters.
    note_codes: list[dict[str, Any]] = Field(default_factory=list)


class Confound(StrictModel):
    """A detected threat to attribution."""

    kind: Literal[
        "control_mismatch",
        "unexpected_framework_diff",
        "dataset_mismatch",
        "evaluator_mismatch",
        "runtime_mismatch",
        "model_mismatch",
        "capability_unverified",
        "repetition_mismatch",
        "other",
    ]
    severity: Literal["blocker", "warning", "info"]
    detail: str
    where: str = ""
    #: The stable identifier of the message (``confound.control_mismatch`` ...)
    #: and the parameters it was built from, so a report can localise it.
    code: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def blocks_attribution(self) -> bool:
        return self.severity == "blocker"


class ComparisonReport(StrictModel):
    """The complete, reproducible result of comparing two variants."""

    experiment_id: str
    generated_at: str = Field(default_factory=utcnow_iso)
    baseline_variant: str = ""
    candidate_variant: str = ""
    metrics: list[MetricAggregate] = Field(default_factory=list)
    cases: list[CaseComparison] = Field(default_factory=list)
    confounds: list[Confound] = Field(default_factory=list)
    decision: Decision = Field(default_factory=Decision)
    run_counts: dict[str, int] = Field(default_factory=dict)
    dataset_version: str = ""
    dataset_hash: str = ""
    evaluator: str = ""
    framework_fingerprints: dict[str, str] = Field(default_factory=dict)
    cost_delta_usd: float | None = None
    duration_delta_sec: float | None = None
    manual_review_cases: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
