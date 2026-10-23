"""Scenario 5 - security and isolation.

Three properties are checked:

1. the golden answer never reaches the agent's workspace;
2. secrets do not survive into traces, run records or reports;
3. the isolation primitives really do what they claim - the subprocess
   environment is sanitised and the workspace manager refuses to delete
   anything outside its own root.
"""

from __future__ import annotations

import json
from pathlib import Path

from cc_eval.core.config import EvalConfig
from cc_eval.datasets.loader import load_benchmark, materialise_bundle
from cc_eval.isolation.redaction import redact_text
from cc_eval.isolation.subprocess import build_env
from cc_eval.isolation.workspace import WorkspaceManager

from .conftest import run_experiment, write_benchmark, write_experiment, write_framework

ORACLE_SECRET = "GOLDEN-ANSWER-must-never-reach-the-agent"


def test_oracle_is_not_materialised_into_the_workspace(tmp_path: Path) -> None:
    benchmark_dir = write_benchmark(
        tmp_path / "bench",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": ORACLE_SECRET,
                "match_keywords": ["alpha"],
            }
        ],
    )
    benchmark = load_benchmark(benchmark_dir)
    case = benchmark.cases[0]

    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("RUN-1")
    materialise_bundle(case, workspace)

    for path in workspace.root.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            assert ORACLE_SECRET not in content, f"oracle leaked into {path}"

    # The oracle file itself is not in the workspace either.
    assert not list(workspace.root.rglob("oracle.yaml"))
    assert not list(workspace.root.rglob("oracle*"))


def test_no_artefact_contains_the_oracle_after_a_full_run(
    tmp_path: Path, framework_pair, workspace_root
) -> None:
    baseline_dir, candidate_dir = framework_pair
    benchmark_dir = write_benchmark(
        tmp_path / "bench",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": ORACLE_SECRET,
                "match_keywords": ["alpha"],
            }
        ],
    )
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        benchmark_dir=benchmark_dir,
        repetitions=1,
        expected_changes=["skills/test_skill.py"],
    )
    run_experiment(spec_path, workspace_root)

    leaked = []
    for path in workspace_root.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            if ORACLE_SECRET in content:
                leaked.append(str(path))
    assert not leaked, f"oracle leaked into: {leaked}"


def test_secrets_are_redacted_from_traces_and_records(tmp_path: Path, workspace_root) -> None:
    """A skill that leaks a credential must not leak it into the artefacts."""
    secret = "sk-ant-api03-THISISASECRETKEYVALUE123456"
    baseline = write_framework(tmp_path / "fw" / "baseline", detectors=[("alpha", r"nothing")])
    candidate = write_framework(
        tmp_path / "fw" / "candidate",
        detectors=[("alpha", r"alpha")],
    )
    # The candidate skill leaks a credential into its finding description.
    skill_path = candidate / "skills" / "test_skill.py"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            '"evidence": "pattern matched",',
            f'"evidence": "pattern matched {secret}",',
        ),
        encoding="utf-8",
    )
    benchmark_dir = write_benchmark(
        tmp_path / "bench",
        known_defects=[
            {
                "id": "D001",
                "category": "alpha",
                "severity": "high",
                "description": "alpha present",
                "match_keywords": ["alpha"],
            }
        ],
    )
    spec_path = write_experiment(
        tmp_path / "exp.yaml",
        baseline_dir=baseline,
        candidate_dir=candidate,
        benchmark_dir=benchmark_dir,
        repetitions=1,
        expected_changes=["skills/test_skill.py"],
    )
    run_experiment(spec_path, workspace_root)

    leaked = []
    for path in workspace_root.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            if secret in content:
                leaked.append(str(path))
    assert not leaked, f"secret leaked into: {leaked}"

    # The redaction is visible, not silent deletion.
    run_file = next((workspace_root / "runs").rglob("*.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    serialised = json.dumps(payload)
    assert "REDACTED" in serialised


def test_redaction_covers_common_credential_shapes() -> None:
    samples = [
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
        "export AWS_SECRET_ACCESS_KEY=abcd1234efgh5678",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz.1234567890",
        "password: hunter2000",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ]
    for sample in samples:
        redacted, stats = redact_text(sample)
        assert "REDACTED" in redacted, sample
        assert stats


def test_subprocess_environment_is_sanitised(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    monkeypatch.setenv("SAFE_VARIABLE", "fine")

    cfg = EvalConfig(root=Path("."))
    env = build_env(cfg)

    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env["SAFE_VARIABLE"] == "fine"


def test_explicit_runner_env_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret")
    cfg = EvalConfig(root=Path("."), runner_env={"ANTHROPIC_API_KEY": "explicit"})
    env = build_env(cfg)
    assert env["ANTHROPIC_API_KEY"] == "explicit"


def test_workspace_manager_refuses_to_delete_outside_its_root(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "important.txt").write_text("do not delete", encoding="utf-8")

    try:
        from cc_eval.isolation.workspace import Workspace

        manager.destroy(Workspace(root=outside, run_id="x"))
    except RuntimeError as exc:
        assert "outside managed root" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("the workspace manager deleted a directory outside its root")

    assert (outside / "important.txt").exists()


def test_workspace_manager_refuses_unmanaged_directories(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    rogue = tmp_path / "workspaces" / "not-ours"
    rogue.mkdir()
    (rogue / "file.txt").write_text("data", encoding="utf-8")

    from cc_eval.isolation.workspace import Workspace

    try:
        manager.destroy(Workspace(root=rogue, run_id="x"))
    except RuntimeError as exc:
        assert "not a managed cc-eval workspace" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the workspace manager deleted an unmanaged directory")

    assert (rogue / "file.txt").exists()


def test_workspace_manager_only_cleans_its_own_workspaces(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    manager.create("RUN-1")
    rogue = tmp_path / "workspaces" / "user-data"
    rogue.mkdir()
    (rogue / "file.txt").write_text("data", encoding="utf-8")

    assert manager.cleanup_all() == 1
    assert (rogue / "file.txt").exists()


def test_dangerous_permission_mode_is_refused(tmp_path: Path) -> None:
    """Claude Code's bypass mode must not be reachable by accident."""
    from cc_eval.core.models import TaskBundle
    from cc_eval.runners.base import RunConfig
    from cc_eval.runners.claude import ClaudeRunner

    runner = ClaudeRunner()
    config = RunConfig(permission_mode="bypassPermissions")
    task = TaskBundle(case_id="C1", task_type="t", prompt="p")

    try:
        runner.build_command(task, config, tmp_path)
    except ValueError as exc:
        assert "bypassPermissions" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the dangerous permission mode was accepted")


def test_explicit_bypass_opt_in_selects_the_requested_mode(tmp_path: Path) -> None:
    from cc_eval.core.models import TaskBundle
    from cc_eval.runners.base import RunConfig
    from cc_eval.runners.claude import ClaudeRunner

    task = TaskBundle(case_id="C1", task_type="t", prompt="p")
    command = ClaudeRunner().build_command(
        task, RunConfig(permission_mode="bypassPermissions", allow_bypass_permissions=True), tmp_path,
    )
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"
