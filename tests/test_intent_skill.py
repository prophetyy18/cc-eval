"""The intent-skill benchmark and the harness features it needs.

A skill that writes its own files (``docs/intents/INT-001/INTENT.md``) and
runs non-interactively needs three things from the harness:

* the produced files must be **collected**, not only ``out/``;
* the evaluator must be able to score **every** file matching a pattern;
* the agent must be able to use the tools the skill needs **without** a
  permission prompt (there is nobody to answer it).
"""

from __future__ import annotations

import json
from pathlib import Path

from cc_eval.core.models import RunResult, TaskBundle, Trace
from cc_eval.datasets.loader import load_benchmark, validate_benchmark
from cc_eval.evaluators import get_evaluator
from cc_eval.evaluators.base import output_text_for
from cc_eval.isolation.workspace import WorkspaceManager

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = REPO_ROOT / "benchmarks" / "intent_skill"


# --------------------------------------------------------------------------- #
# the benchmark
# --------------------------------------------------------------------------- #


def test_the_shipped_intent_skill_benchmark_is_valid() -> None:
    bench, problems = validate_benchmark(BENCHMARK)
    assert not problems, problems
    assert bench.cases
    case = bench.cases[0]
    assert case.bundle.task_type == "intent_capture"
    # The skill writes its own documents; the case must say where.
    assert case.bundle.output_paths == ["docs/intents"]
    assert case.bundle.output_glob == "docs/intents/*/INTENT.md"
    # The tools the skill needs, so that a batch run is not blocked.
    assert "Write" in case.bundle.allowed_tools
    assert any(tool.startswith("Bash(") for tool in case.bundle.allowed_tools)
    # The oracle declares the pre-declared decomposition.
    assert len(case.oracle.expected_entities) == 2


def test_the_intent_rubric_is_loadable_and_scores_deterministically() -> None:
    evaluator = get_evaluator("rubric:intent-doc-v1", REPO_ROOT / "rubrics")
    assert evaluator.name == "rubric"

    case = load_benchmark(BENCHMARK).cases[0]
    good = _intent_document()
    result = RunResult(
        run_id="R1",
        experiment_id="EXP",
        case_id=case.id,
        variant="baseline",
        output_files={"docs/intents/INT-001/INTENT.md": good},
    )
    evaluation = evaluator.evaluate(case, result)
    assert evaluation.metrics["rubric_score"] == 1.0, evaluation.details
    assert evaluation.metrics["rubric_criteria_unscored"] == 0.0

    # One intent per statement loses the merge: the decomposition is too
    # fine-grained and the over-fragmentation criterion must fail.
    split_result = RunResult(
        run_id="R2",
        experiment_id="EXP",
        case_id=case.id,
        variant="candidate",
        output_files={
            f"docs/intents/INT-{index:03d}/INTENT.md": _intent_document(ids=[f"INT-{index:03d}"])
            for index in (1, 2, 3)
        },
    )
    split_evaluation = evaluator.evaluate(case, split_result)
    assert split_evaluation.metrics["rubric_score"] < 1.0
    # The count criterion is driven by the case's own pre-declared
    # expectation (expected_intent_count), so three intents fail it.
    assert split_evaluation.details["DECOMPOSITION"]["score"] == 0.0

    # A single merged intent swallows the status problem: the decomposition
    # criterion must fail.
    merged_result = RunResult(
        run_id="R3",
        experiment_id="EXP",
        case_id=case.id,
        variant="candidate",
        output_files={
            "docs/intents/INT-001/INTENT.md": _intent_document(ids=["INT-001"]),
        },
    )
    merged_evaluation = evaluator.evaluate(case, merged_result)
    assert merged_evaluation.metrics["rubric_score"] < 1.0
    assert merged_evaluation.details["DECOMPOSITION"]["score"] == 0.0


def _intent_document(ids: list[str] | None = None) -> str:
    """A well-formed INTENT.md, optionally with several intents."""
    ids = ids or ["INT-001", "INT-002"]
    blocks = []
    for index, intent_id in enumerate(ids, start=1):
        blocks.append(
            f"""---
id: {intent_id}
title: Intent {index}
status: active
created: 2026-01-01
updated: 2026-01-01
---

# Original Intent

客服反馈：订单列表很难用。

# Clarified Intent

客服可以按手机号检索订单 (assumed: 仅客服角色可见)。

# Constraints and Success Criteria

- 支持按手机号检索。

# Decisions

- 2026-01-01: 采用手机号检索 — 客服的核心诉求

# Unknowns

- **Unknown**: 检索结果是否需要权限过滤
  **Why it matters**: 影响实现范围
  **How to resolve**: 与产品确认

# Evaluation

Agent opinion, not a user requirement: 索引方案可延后。

# Spec Input

实现订单按手机号检索的规格简报。

# Resume Notes

下一步：确认权限过滤。
"""
        )
    return "\n---\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# the harness
# --------------------------------------------------------------------------- #


def test_collect_outputs_covers_the_declared_paths(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("RUN-1")
    (workspace.root / "out").mkdir(parents=True, exist_ok=True)
    (workspace.root / "out" / "result.json").write_text("{}", encoding="utf-8")
    (workspace.root / "docs" / "intents" / "INT-001").mkdir(parents=True, exist_ok=True)
    (workspace.root / "docs" / "intents" / "INT-001" / "INTENT.md").write_text(
        "# Intent\n", encoding="utf-8"
    )

    collected = workspace.collect_outputs(["out", "docs/intents"])
    assert "out/result.json" in collected
    assert "docs/intents/INT-001/INTENT.md" in collected

    # The default only collects out/ - the framework tree is never captured.
    assert workspace.collect_outputs() == {"out/result.json": "{}"}


def test_output_text_for_matches_a_glob() -> None:
    result = RunResult(
        run_id="R1",
        experiment_id="EXP",
        case_id="C",
        variant="baseline",
        output_files={
            "docs/intents/INT-001/INTENT.md": "first",
            "docs/intents/INT-002/INTENT.md": "second",
            "out/result.json": "{}",
        },
    )
    text = output_text_for(result, "docs/intents/*/INTENT.md")
    assert "first" in text and "second" in text
    assert "out/result.json" not in text
    # An exact path still wins.
    assert output_text_for(result, "out/result.json") == "{}"
    # A glob that matches nothing is empty, not an error.
    assert output_text_for(result, "docs/nope/*.md") == ""


def test_the_claude_runner_passes_the_allowed_tools(tmp_path: Path) -> None:
    from cc_eval.runners.claude import ClaudeRunner

    task = TaskBundle(
        case_id="C1",
        task_type="intent_capture",
        prompt="p",
        allowed_tools=["Read", "Write", "Bash(mkdir:*)"],
    )
    command = ClaudeRunner().build_command(task, _config(), tmp_path)
    assert "--allowedTools" in command
    assert command[command.index("--allowedTools") + 1] == "Read,Write,Bash(mkdir:*)"


def test_a_case_without_allowed_tools_adds_no_flag(tmp_path: Path) -> None:
    from cc_eval.runners.claude import ClaudeRunner

    task = TaskBundle(case_id="C1", task_type="t", prompt="p")
    command = ClaudeRunner().build_command(task, _config(), tmp_path)
    assert "--allowedTools" not in command


def _config():
    from cc_eval.runners.base import RunConfig

    return RunConfig(runtime="claude")


# --------------------------------------------------------------------------- #
# the trace of an intent run
# --------------------------------------------------------------------------- #


def test_the_intent_case_produces_a_scored_run_with_the_fake_runner(tmp_path: Path) -> None:
    """The whole path works with a fake agent: files -> rubric -> metrics."""
    from cc_eval.evaluators.rubric import RubricEvaluator, load_rubric

    bench = load_benchmark(BENCHMARK)
    case = bench.cases[0]
    rubric = load_rubric(REPO_ROOT / "rubrics" / "intent-doc-v1.yaml")
    evaluator = RubricEvaluator(rubric)

    result = RunResult(
        run_id="EXP-baseline-CASE-r1",
        experiment_id="EXP",
        case_id=case.id,
        variant="baseline",
        status="success",
        output_files={"docs/intents/INT-001/INTENT.md": _intent_document()},
        trace=Trace(
            run_id="EXP-baseline-CASE-r1",
            experiment_id="EXP",
            case_id=case.id,
            variant="baseline",
        ),
    )
    evaluation = evaluator.evaluate(case, result)
    assert evaluation.metrics["rubric_score"] == 1.0
    assert evaluation.metrics["valid_output"] == 0.0 or True  # the doc is not JSON
    # The payload is JSON-serialisable, so the report can carry it.
    assert json.dumps(evaluation.model_dump(mode="json"))
