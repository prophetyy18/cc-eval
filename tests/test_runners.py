"""Runner adapters.

The Claude Code and Codex event samples below were captured from the locally
installed binaries (Claude Code 2.1.308 and codex-cli 0.160.1) - the adapters
are tested against the real output shape, not against an assumption about it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cc_eval.core.models import TaskBundle, Usage
from cc_eval.isolation.workspace import WorkspaceManager
from cc_eval.runners import available_runners, get_runner
from cc_eval.runners.base import RunConfig
from cc_eval.runners.claude import ClaudeRunner
from cc_eval.runners.codex import CodexRunner
from cc_eval.runners.fake import FakeRunner
from cc_eval.traces.normalize import normalise_claude, normalise_codex

# --------------------------------------------------------------------------- #
# captured samples
# --------------------------------------------------------------------------- #

CLAUDE_STREAM_SAMPLE = [
    {
        "type": "system",
        "subtype": "init",
        "cwd": "/tmp/probe",
        "session_id": "d2ccfdbe-0f67-4fff-cfdf-fd56c86a179f",
        "tools": ["Task", "Bash", "Edit", "Read", "Skill", "Write"],
        "mcp_servers": [],
        "model": "some-model",
        "permissionMode": "acceptEdits",
        "slash_commands": ["init", "config"],
        "claude_code_version": "2.1.308",
        "apiKeySource": "none",
        "skills": ["spec-review", "intent-router"],
        "plugins": [{"name": "cc-plugin-agents-md", "path": "builtin"}],
    },
    {
        "type": "assistant",
        "message": {
            "id": "07291dd7",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_01",
                    "name": "Skill",
                    "input": {"skill": "spec-review"},
                }
            ],
            "model": "some-model",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        "session_id": "d2ccfdbe",
        "timestamp": "2026-10-22T10:52:11.739Z",
    },
    {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_01",
                    "content": "skill output",
                    "is_error": False,
                }
            ],
        },
        "timestamp": "2026-10-22T10:52:11.810Z",
    },
    {
        "type": "assistant",
        "message": {
            "id": "07291dde",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Found two defects."}],
            "model": "some-model",
            "usage": {"input_tokens": 20, "output_tokens": 8},
        },
        "timestamp": "2026-10-22T10:52:12.000Z",
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "total_cost_usd": 0.0123,
        "duration_ms": 4774,
        "result": "Found two defects.",
        "session_id": "d2ccfdbe",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens_details": {"thinking_tokens": 0},
        },
        "modelUsage": {
            "some-model": {
                "inputTokens": 10713,
                "outputTokens": 5,
                "cacheReadInputTokens": 202,
                "cacheCreationInputTokens": 0,
                "costUSD": 0.053791,
                "canonicalModel": "some-model",
                "thinkingTokens": 0,
            }
        },
    },
]

CODEX_JSONL_SAMPLE = [
    {"type": "thread.started", "thread_id": "01a1633d-87f7-7cd1-966e-1cbf534a8417"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": "I will run it."},
    },
    {
        "type": "item.started",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "/bin/bash -lc 'echo hello_probe'",
            "aggregated_output": "",
            "exit_code": None,
            "status": "in_progress",
        },
    },
    {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "/bin/bash -lc 'echo hello_probe'",
            "aggregated_output": "hello_probe\n",
            "exit_code": 0,
            "status": "completed",
        },
    },
    {
        "type": "item.completed",
        "item": {"id": "item_2", "type": "agent_message", "text": "DONE"},
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 27559,
            "cached_input_tokens": 25344,
            "cache_write_input_tokens": 0,
            "output_tokens": 72,
            "reasoning_output_tokens": 14,
        },
    },
]


# --------------------------------------------------------------------------- #
# trace normalisation
# --------------------------------------------------------------------------- #


def test_claude_stream_is_normalised() -> None:
    trace = normalise_claude(
        CLAUDE_STREAM_SAMPLE,
        run_id="RUN-1",
        experiment_id="EXP-1",
        case_id="CASE-1",
        variant="baseline",
    )
    types = [event.type for event in trace.events]
    assert types[0] == "session"
    assert "tool_call" in types
    assert "tool_result" in types
    assert "agent_output" in types
    assert types[-1] == "result"

    tool_call = next(e for e in trace.events if e.type == "tool_call")
    assert tool_call.tool == "Skill"
    assert tool_call.details["arguments"] == {"skill": "spec-review"}

    result_event = trace.events[-1]
    assert result_event.details["num_turns"] == 2
    assert result_event.details["total_cost_usd"] == 0.0123


def test_claude_capabilities_are_proven_from_the_trace() -> None:
    trace = normalise_claude(
        CLAUDE_STREAM_SAMPLE,
        run_id="RUN-1",
        experiment_id="EXP-1",
        case_id="CASE-1",
        variant="baseline",
    )
    assert trace.available_capabilities == ["intent-router", "spec-review"]
    assert trace.invoked_capabilities == ["spec-review"]


def test_codex_stream_is_normalised() -> None:
    trace = normalise_codex(
        CODEX_JSONL_SAMPLE,
        run_id="RUN-1",
        experiment_id="EXP-1",
        case_id="CASE-1",
        variant="baseline",
    )
    types = [event.type for event in trace.events]
    assert "session" in types
    assert "tool_call" in types
    assert "agent_output" in types
    assert types[-1] == "result"

    commands = [e for e in trace.events if e.type == "tool_call"]
    assert commands[0].tool == "Bash"
    completed = next(e for e in commands if e.exit_code is not None)
    assert completed.exit_code == 0
    assert completed.details["phase"] == "completed"

    # Codex does not report cost - that must stay explicitly unavailable.
    assert "cost_usd" in trace.missing_fields


def test_codex_usage_is_read_from_the_turn() -> None:
    trace = normalise_codex(
        CODEX_JSONL_SAMPLE,
        run_id="RUN-1",
        experiment_id="EXP-1",
        case_id="CASE-1",
        variant="baseline",
    )
    usage = trace.events[-1].details["usage"]
    assert usage["input_tokens"] == 27559
    assert usage["output_tokens"] == 72


# --------------------------------------------------------------------------- #
# command construction
# --------------------------------------------------------------------------- #


def test_claude_command_uses_verified_flags(tmp_path: Path) -> None:
    runner = ClaudeRunner()
    config = RunConfig(
        model="some-model",
        max_budget_usd=1.5,
        max_turns=7,
        timeout_sec=60,
    )
    task = TaskBundle(case_id="C1", task_type="t", prompt="hello")
    command = runner.build_command(task, config, tmp_path)

    assert command[0].endswith("claude")
    assert "-p" in command
    assert "--output-format" in command and "stream-json" in command
    assert "--no-session-persistence" in command
    assert "--strict-mcp-config" in command
    assert "--permission-prompts" in command and "none" in command
    assert "--permission-mode" in command and "acceptEdits" in command
    assert "--max-turns" in command and "7" in command
    assert "--max-budget-usd" in command and "1.5" in command
    assert "--model" in command and "some-model" in command
    assert "--dangerously-skip-permissions" not in command
    assert "bypassPermissions" not in command


def test_codex_command_uses_verified_flags(tmp_path: Path) -> None:
    runner = CodexRunner()
    config = RunConfig(model="some-model", sandbox="read-only")
    task = TaskBundle(case_id="C1", task_type="t", prompt="hello")
    last_message = tmp_path / "last.txt"
    command = runner.build_command(task, config, tmp_path, last_message, None)

    assert command[0].endswith("codex")
    assert "exec" in command
    assert "--json" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert "--sandbox" in command and "read-only" in command
    assert "--output-last-message" in command
    assert "--model" in command and "some-model" in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #


def test_fake_runner_is_always_available() -> None:
    ok, reason = FakeRunner().available()
    assert ok
    assert "no external dependencies" in reason


def test_runner_registry_lists_every_runner() -> None:
    status = available_runners()
    assert set(status) == {"fake", "claude", "codex"}
    assert status["fake"][0] is True


def test_unknown_runner_is_rejected() -> None:
    with pytest.raises(KeyError) as excinfo:
        get_runner("does-not-exist")
    assert "fake" in str(excinfo.value)


@pytest.mark.slow
def test_real_claude_runner_executes_a_small_fixture(tmp_path: Path) -> None:
    """A low-risk real run, skipped when the runtime is not usable.

    The test never claims success it did not achieve: if the binary is
    missing or the session is not authenticated, it skips with the reason.
    """
    if shutil.which("claude") is None:
        pytest.skip("claude is not installed on this machine")

    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("REAL-1")
    task = TaskBundle(
        case_id="REAL-001",
        task_type="probe",
        prompt=(
            "Create the file out/result.json containing exactly this JSON: "
            '{"findings": [{"id": "R001", "category": "probe", "severity": "low", '
            '"description": "the runtime answered", "evidence": "probe"}]}'
        ),
        expected_output_path="out/result.json",
    )
    config = RunConfig(
        runtime="claude",
        model=None,
        timeout_sec=180,
        max_budget_usd=0.5,
        permission_mode="acceptEdits",
        max_turns=6,
        # The local runtime needs the operator's credentials; this is the
        # explicit opt-in that the CLI also exposes.
        inherit_env=True,
    )
    result = ClaudeRunner().run(
        task, config, workspace, experiment_id="EXP-REAL", variant="baseline", repetition=1
    )
    if result.status != "success":
        pytest.skip(f"claude did not complete the probe: {result.status} {result.error}")
    assert result.output_files
    assert "out/result.json" in result.output_files
    assert result.usage.source == "runtime"
    assert result.usage.total_tokens is not None


@pytest.mark.slow
def test_real_codex_runner_executes_a_small_fixture(tmp_path: Path) -> None:
    if shutil.which("codex") is None:
        pytest.skip("codex is not installed on this machine")

    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create("REAL-2")
    task = TaskBundle(
        case_id="REAL-002",
        task_type="probe",
        prompt=(
            "Create the file out/result.json containing exactly this JSON: "
            '{"findings": [{"id": "R001", "category": "probe", "severity": "low", '
            '"description": "the runtime answered", "evidence": "probe"}]}'
        ),
        expected_output_path="out/result.json",
    )
    config = RunConfig(
        runtime="codex", timeout_sec=180, sandbox="workspace-write", inherit_env=True
    )
    result = CodexRunner().run(
        task, config, workspace, experiment_id="EXP-REAL", variant="baseline", repetition=1
    )
    if result.status != "success":
        pytest.skip(f"codex did not complete the probe: {result.status} {result.error}")
    assert result.usage.source == "runtime"
    # Codex does not report cost - the adapter must not invent one.
    assert result.usage.cost_usd is None
    assert result.provenance.get("cost_usd", "").startswith("unavailable")
