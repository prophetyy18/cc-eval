"""Candidate branches used by the web console."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cc_eval.core.models import RunResult, Usage
from cc_eval.dashboard.candidates import CandidateError, CandidateManager


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture()
def framework(tmp_path: Path) -> Path:
    repo = tmp_path / "workflow"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    skill = repo / ".claude" / "skills" / "intent" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("original\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
    return repo


def test_candidate_branch_review_and_commit(tmp_path: Path, framework: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "better clarification")
    assert candidate["baseline_commit"] == _git(framework, "rev-parse", "main")
    assert candidate["branch"].startswith("codex/intent-better-clarification-")
    assert not candidate["ready_for_experiment"]
    assert (framework / ".claude/skills/intent/SKILL.md").read_text() == "original\n"

    worktree = Path(candidate["worktree"])
    (worktree / ".claude/skills/intent/SKILL.md").write_text("clarify first\n", encoding="utf-8")
    reviewed = manager.detail(candidate["id"])
    assert reviewed["changed_files"] == [".claude/skills/intent/SKILL.md"]
    assert "clarify first" in reviewed["diff"]
    assert not reviewed["ready_for_experiment"]

    committed = manager.commit(candidate["id"])
    assert committed["ready_for_experiment"]
    assert "clarify first" in committed["diff"]
    assert committed["candidate_commit"] != committed["baseline_commit"]
    assert _git(framework, "rev-parse", "main") == candidate["baseline_commit"]
    assert _git(framework, "rev-parse", committed["branch"]) == committed["candidate_commit"]
    assert committed["push_status"] == "pushed"
    assert committed["pushed_commit"] == committed["candidate_commit"]
    assert _git(remote, "rev-parse", f"refs/heads/{committed['branch']}") == committed["candidate_commit"]
    assert subprocess.run(
        ["git", "-C", str(remote), "show-ref", "--verify", "--quiet", "refs/heads/main"],
        check=False,
    ).returncode != 0
    assert manager.list()[0]["id"] == candidate["id"]

    (worktree / ".claude/skills/intent/SKILL.md").write_text("clarify and confirm\n", encoding="utf-8")
    updated = manager.commit(candidate["id"])
    assert updated["push_status"] == "pushed"
    assert updated["candidate_commit"] != committed["candidate_commit"]
    assert _git(remote, "rev-parse", f"refs/heads/{updated['branch']}") == updated["candidate_commit"]


def test_publish_preview_checks_current_remote_head_and_dirty_tree(
    tmp_path: Path, framework: Path, monkeypatch,
) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    _git(framework, "push", "origin", "main")
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "publish")
    skill = Path(candidate["worktree"]) / ".claude/skills/intent/SKILL.md"
    skill.write_text("new text\n", encoding="utf-8")
    committed = manager.commit(candidate["id"])
    _git(framework, "remote", "set-url", "origin", "git@github.com:example/workflow.git")
    original_run = subprocess.run
    remote_head = [committed["candidate_commit"]]

    def fake_run(args, **kwargs):
        if "ls-remote" in args:
            return subprocess.CompletedProcess(args, 0, (
                f"{candidate['baseline_commit']}\trefs/heads/main\n"
                f"{remote_head[0]}\trefs/heads/{candidate['branch']}\n"
            ), "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    preview = manager.publish_preview(candidate["id"])
    assert preview["ready"] is True
    assert preview["ahead"] == 1 and preview["behind"] == 0
    assert preview["changed_files"] == [".claude/skills/intent/SKILL.md"]
    assert preview["local_main_commit"] == candidate["baseline_commit"]

    remote_head[0] = candidate["baseline_commit"]
    assert "尚未把当前提交推送" in " ".join(manager.publish_preview(candidate["id"])["reasons"])
    remote_head[0] = committed["candidate_commit"]
    skill.write_text("uncommitted\n", encoding="utf-8")
    assert "未提交" in " ".join(manager.publish_preview(candidate["id"])["reasons"])


def test_publish_preview_requires_origin(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "local only")
    with pytest.raises(CandidateError, match="origin"):
        manager.publish_preview(candidate["id"])


def test_merge_to_main_pushes_and_updates_clean_local_main(tmp_path: Path, framework: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    _git(framework, "push", "origin", "main")
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "merge")
    Path(candidate["worktree"], ".claude/skills/intent/SKILL.md").write_text("merged\n")
    committed = manager.commit(candidate["id"])
    preview = manager.publish_preview(candidate["id"])
    assert preview["ready"] is True
    with pytest.raises(CandidateError, match="已变化"):
        manager.merge_to_main(candidate["id"], "0" * 40, committed["candidate_commit"])
    assert _git(framework, "rev-parse", "main") == candidate["baseline_commit"]

    merged = manager.merge_to_main(
        candidate["id"], preview["main_commit"], preview["candidate_commit"],
    )
    assert merged["remote_pushed"] and merged["local_main_updated"]
    assert _git(framework, "rev-parse", "main") == merged["merged_commit"]
    assert _git(remote, "rev-parse", "main") == merged["merged_commit"]
    assert _git(framework, "rev-list", "--parents", "-n", "1", "main").split()[1:] == [
        candidate["baseline_commit"], committed["candidate_commit"],
    ]
    assert (framework / ".claude/skills/intent/SKILL.md").read_text() == "merged\n"
    assert not list((tmp_path / "eval" / "candidates" / candidate["id"]).glob("merge-*"))

    # A lost HTTP response after the remote push can leave local main behind.
    _git(framework, "reset", "--hard", candidate["baseline_commit"])
    recovery = manager.publish_preview(candidate["id"])
    assert recovery["already_merged"] is True
    assert recovery["local_main_behind"] is True
    assert recovery["local_main_contains_candidate"] is False
    assert manager.sync_local_main(candidate["id"])["local_main_updated"] is True
    assert _git(framework, "rev-parse", "main") == merged["merged_commit"]


def test_merge_fetches_new_remote_main_and_fast_forwards_local_main(
    tmp_path: Path, framework: Path,
) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    _git(framework, "push", "origin", "main")
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "remote ahead")
    Path(candidate["worktree"], ".claude/skills/intent/SKILL.md").write_text("candidate\n")
    manager.commit(candidate["id"])

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    (other / "note.txt").write_text("new main commit\n")
    _git(other, "add", "note.txt")
    _git(other, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "main ahead")
    _git(other, "push", "origin", "main")

    preview = manager.publish_preview(candidate["id"])
    assert preview["ready"] is True
    assert preview["local_main_behind"] is True
    assert preview["ahead"] == 1 and preview["behind"] == 1
    merged = manager.merge_to_main(candidate["id"], preview["main_commit"], preview["candidate_commit"])
    assert merged["local_main_updated"] is True
    assert _git(framework, "rev-parse", "main") == _git(remote, "rev-parse", "main")
    assert (framework / "note.txt").read_text() == "new main commit\n"
    assert (framework / ".claude/skills/intent/SKILL.md").read_text() == "candidate\n"


def test_merge_failure_leaves_main_unchanged(tmp_path: Path, framework: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    _git(framework, "push", "origin", "main")
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "blocked")
    Path(candidate["worktree"], ".claude/skills/intent/SKILL.md").write_text("candidate\n")
    manager.commit(candidate["id"])
    before = _git(framework, "rev-parse", "main")

    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    preview = manager.publish_preview(candidate["id"])
    with pytest.raises(CandidateError, match="推送 main 失败"):
        manager.merge_to_main(candidate["id"], preview["main_commit"], preview["candidate_commit"])
    assert _git(framework, "rev-parse", "main") == before
    assert _git(remote, "rev-parse", "main") == before
    assert not list((tmp_path / "eval" / "candidates" / candidate["id"]).glob("merge-*"))


def test_merge_conflict_leaves_main_unchanged(tmp_path: Path, framework: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    _git(framework, "push", "origin", "main")
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "conflict")
    Path(candidate["worktree"], ".claude/skills/intent/SKILL.md").write_text("candidate\n")
    manager.commit(candidate["id"])
    (framework / ".claude/skills/intent/SKILL.md").write_text("main\n")
    _git(framework, "add", ".")
    _git(framework, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "main change")
    _git(framework, "push", "origin", "main")
    before = _git(framework, "rev-parse", "main")
    preview = manager.publish_preview(candidate["id"])
    assert preview["ready"] is True and preview["behind"] == 1
    with pytest.raises(CandidateError, match="SKILL.md"):
        manager.merge_to_main(candidate["id"], preview["main_commit"], preview["candidate_commit"])
    assert _git(framework, "rev-parse", "main") == before
    assert _git(remote, "rev-parse", "main") == before


def test_candidate_push_failure_keeps_commit_and_can_retry(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "retry push")
    Path(candidate["worktree"], ".claude/skills/intent/SKILL.md").write_text(
        "revised\n", encoding="utf-8",
    )

    committed = manager.commit(candidate["id"])
    assert committed["ready_for_experiment"]
    assert committed["push_status"] == "failed"
    assert "未配置 origin" in committed["push_error"]
    commit_id = committed["candidate_commit"]

    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    pushed = manager.commit(candidate["id"])
    assert pushed["candidate_commit"] == commit_id
    assert pushed["push_status"] == "pushed"
    assert pushed["push_error"] == ""
    assert _git(remote, "rev-parse", f"refs/heads/{pushed['branch']}") == commit_id


def test_discard_current_edit_restores_previous_pushed_commit(
    tmp_path: Path, framework: Path, monkeypatch,
) -> None:
    from cc_eval.dashboard import candidates

    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    _git(framework, "remote", "add", "origin", str(remote))
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "discard review")
    worktree = Path(candidate["worktree"])
    skill = worktree / ".claude/skills/intent/SKILL.md"
    skill.write_text("accepted\n", encoding="utf-8")
    committed = manager.commit(candidate["id"])
    previous_commit = committed["candidate_commit"]

    def fake_run(self, task, config, workspace, **kwargs):
        (workspace.root / ".claude/skills/intent/SKILL.md").write_text(
            "discard me\n", encoding="utf-8",
        )
        reference = workspace.root / ".claude/skills/intent/references/clarification.md"
        reference.parent.mkdir(parents=True)
        reference.write_text("new reference\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager.edit(candidate["id"], "Change the skill", permission_mode="bypassPermissions")
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    reviewed = manager.detail(candidate["id"])
    assert reviewed["status"] == "review"
    assert reviewed["can_discard_current"]
    assert len(reviewed["changed_files"]) == 2

    extra = worktree / "README.md"
    extra.write_text("manual change\n", encoding="utf-8")
    with pytest.raises(CandidateError, match="非本轮导入"):
        manager.discard_current(candidate["id"])
    assert skill.read_text(encoding="utf-8") == "discard me\n"
    extra.unlink()
    _git(worktree, "add", ".claude/skills/intent/references/clarification.md")

    discarded = manager.discard_current(candidate["id"])
    assert discarded["status"] == "committed"
    assert discarded["ready_for_experiment"]
    assert not discarded["can_discard_current"]
    assert discarded["changed_files"] == []
    assert discarded["candidate_commit"] == previous_commit
    assert skill.read_text(encoding="utf-8") == "accepted\n"
    assert not (worktree / ".claude/skills/intent/references/clarification.md").exists()
    assert _git(worktree, "status", "--porcelain") == ""
    assert _git(remote, "rev-parse", f"refs/heads/{candidate['branch']}") == previous_commit

    manager.edit(candidate["id"], "Try again", permission_mode="bypassPermissions")
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    assert manager.detail(candidate["id"])["status"] == "review"


def test_candidate_suggests_workflow_and_uses_intent_name_once(tmp_path: Path, framework: Path) -> None:
    repo = tmp_path / "cc-workflow"
    framework.rename(repo)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    assert manager.suggested_repository() == str(repo)
    candidate = manager.create(str(repo), "intent-standard")
    assert candidate["branch"].startswith("codex/intent-standard-")
    assert candidate["baseline_commit"] == _git(repo, "rev-parse", "main")


def test_candidate_rejects_wrong_repository_with_clear_error(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    wrong = tmp_path / "cc-eval"
    wrong.mkdir()
    _git(wrong, "init", "-b", "master")
    with pytest.raises(CandidateError, match="没有 .claude/skills/intent/SKILL.md"):
        manager.create(str(wrong), "intent-standard")
    _git(framework, "checkout", "-b", "another")
    _git(framework, "branch", "-D", "main")
    with pytest.raises(CandidateError, match="没有本地 main 分支"):
        manager.create(str(framework), "intent-standard")


def test_candidate_rejects_changes_outside_intent_skill(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "scope")
    Path(candidate["worktree"], "README.md").write_text("outside\n", encoding="utf-8")
    assert manager.detail(candidate["id"])["unexpected_files"] == ["README.md"]
    with pytest.raises(CandidateError, match="范围外"):
        manager.commit(candidate["id"])


def test_agent_edit_keeps_changes_for_review(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    def fake_run(self, task, config, workspace, **kwargs):
        assert "Modify only these repository-relative paths: .claude/skills/intent/" in task.prompt
        (workspace.root / ".claude/skills/intent/SKILL.md").write_text("ask user\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success", output_text="Changed the clarification rule",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "ask first")
    manager.edit(candidate["id"], "Ask the user before deciding")
    deadline = time.time() + 3
    while time.time() < deadline:
        result = manager.detail(candidate["id"])
        if result["status"] != "editing":
            break
        time.sleep(0.02)
    assert result["status"] == "review"
    assert "ask user" in result["diff"]
    assert not result["ready_for_experiment"]


def test_protected_skill_is_edited_via_unprotected_copy(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.permission_mode == "acceptEdits"
        assert "write ONLY to its editable copy" in task.prompt
        settings = json.loads(config.runner_args["settings"])
        hook = settings["hooks"]["PreToolUse"][0]
        allowed = json.loads(shlex.split(hook["hooks"][0]["command"])[-2])
        assert allowed == ["__candidate_editable__/skills/intent/"]
        editable = workspace.root / "__candidate_editable__/skills/intent/SKILL.md"
        assert editable.read_text(encoding="utf-8") == "original\n"
        editable.write_text("clarify before assuming\n", encoding="utf-8")
        reference = editable.parent / "references/clarification.md"
        reference.parent.mkdir(parents=True)
        reference.write_text("Ask the user\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "protected edit")
    manager.edit(candidate["id"], "Clarify intent")
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    result = manager.detail(candidate["id"])
    assert result["status"] == "review"
    assert result["changed_files"] == [
        ".claude/skills/intent/SKILL.md",
        ".claude/skills/intent/references/clarification.md",
    ]
    assert "__candidate_editable__" not in result["diff"]


def test_candidate_can_select_bypass_for_direct_skill_edit(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.permission_mode == "bypassPermissions"
        assert config.allow_bypass_permissions is True
        assert config.runner_args["limit_tools"] is True
        assert not (workspace.root / "__candidate_editable__").exists()
        settings = json.loads(config.runner_args["settings"])
        hook = settings["hooks"]["PreToolUse"][0]
        allowed = json.loads(shlex.split(hook["hooks"][0]["command"])[-2])
        assert allowed == [".claude/skills/intent/"]
        (workspace.root / ".claude/skills/intent/SKILL.md").write_text("direct update\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "direct edit")
    manager.edit(candidate["id"], "Change the skill", permission_mode="bypassPermissions")
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    result = manager.detail(candidate["id"])
    assert result["status"] == "review"
    assert result["permission_mode"] == "bypassPermissions"
    assert result["changed_files"] == [".claude/skills/intent/SKILL.md"]
    assert "direct update" in result["diff"]


def test_candidate_rejects_unknown_permission_mode(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "mode")
    with pytest.raises(CandidateError, match="权限模式"):
        manager.edit(candidate["id"], "Change the skill", permission_mode="default")


def test_agent_edit_exposes_persisted_activity(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    started = threading.Event()
    release = threading.Event()

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.permission_mode == "acceptEdits"
        kwargs["on_event"]({"type": "system", "subtype": "init"})
        kwargs["on_event"]({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit"},
        ]}})
        kwargs["on_event"]({"type": "result", "total_cost_usd": 0.032304, "num_turns": 2})
        started.set()
        release.wait(3)
        (workspace.root / ".claude/skills/intent/SKILL.md").write_text("updated\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success", usage=Usage(source="runtime", cost_usd=0.032304),
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "progress")
    manager.edit(candidate["id"], "Clarify intent")
    try:
        assert started.wait(3)
        active = manager.detail(candidate["id"])
        assert active["status"] == "editing"
        assert active["started_at"]
        assert active["event_count"] == 3
        assert active["reported_cost_usd"] == 0.032304
        assert active["reported_turns"] == 2
        assert [item["label"] for item in active["progress_events"]][-3:] == [
            "Claude Code 已启动", "编辑", "Claude Code 已返回结果",
        ]
        assert manager._read(candidate["id"])["event_count"] == 3
    finally:
        release.set()
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    assert manager.detail(candidate["id"])["status"] == "review"
    assert manager.detail(candidate["id"])["ended_at"]
    assert manager.detail(candidate["id"])["reported_cost_usd"] == 0.032304


def test_edit_surfaces_permission_denial_with_file_path_while_running(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    started = threading.Event()
    release = threading.Event()

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.permission_mode == "acceptEdits"
        path = str(workspace.root / ".claude/skills/intent/SKILL.md")
        kwargs["on_event"]({"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "write-1", "name": "Write", "input": {"file_path": path},
        }]}})
        kwargs["on_event"]({"type": "system", "subtype": "permission_denied",
                               "tool_name": "Write", "tool_use_id": "write-1",
                               "message": "Claude Code is running in don't ask mode"})
        kwargs["on_event"]({"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "write-1", "is_error": True,
            "content": "permission denied",
        }]}})
        started.set()
        release.wait(3)
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="agent_error", error="permission denied",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "denied")
    manager.edit(candidate["id"], "Edit the skill")
    try:
        assert started.wait(3)
        active = manager.detail(candidate["id"])
        assert active["status"] == "editing"
        assert "SKILL.md" in active["permission_denials"][0]
        assert "don't ask mode" in active["permission_denials"][0]
        assert any(item.get("kind") == "error" and "SKILL.md" in item["label"]
                   for item in active["progress_events"])
    finally:
        release.set()
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    assert manager.detail(candidate["id"])["status"] == "failed"


def test_edit_imports_only_selected_paths(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.permission_mode == "acceptEdits"
        assert config.runner_args["limit_tools"] is True
        hook = json.loads(config.runner_args["settings"])["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Write|Edit"
        assert shlex.split(hook["hooks"][0]["command"])[-2] == json.dumps(["docs/"])
        (workspace.root / "docs").mkdir()
        (workspace.root / "docs/notes.md").write_text("allowed\n", encoding="utf-8")
        (workspace.root / "probe_root.txt").write_text("probe\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "scope")
    manager.edit(candidate["id"], "Write notes", ["docs/"])
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    reviewed = manager.detail(candidate["id"])
    assert reviewed["status"] == "review"
    assert reviewed["edit_scope"] == ["docs/"]
    assert reviewed["scope_violations"] == ["probe_root.txt"]
    assert (Path(candidate["worktree"]) / "docs/notes.md").read_text() == "allowed\n"
    assert not (Path(candidate["worktree"]) / "probe_root.txt").exists()
    assert not reviewed["unexpected_files"]
    assert "docs/notes.md" in reviewed["diff"]
    committed = manager.commit(candidate["id"])
    assert committed["ready_for_experiment"]


def test_edit_uses_web_limits_and_compares_every_changed_file(tmp_path: Path, framework: Path, monkeypatch) -> None:
    from cc_eval.dashboard import candidates

    def fake_run(self, task, config, workspace, **kwargs):
        assert config.max_turns == 120
        assert config.timeout_sec == 2400
        assert config.max_budget_usd == 12.5
        skill = workspace.root / ".claude/skills/intent/SKILL.md"
        skill.write_text("updated\n", encoding="utf-8")
        local = workspace.root / ".claude/skills/intent/references/clarification.md"
        local.parent.mkdir(parents=True)
        local.write_text("local reference\n", encoding="utf-8")
        shared = workspace.root / ".claude/references/verification.md"
        shared.parent.mkdir(parents=True)
        shared.write_text("shared reference\n", encoding="utf-8")
        return RunResult(
            run_id="R1", experiment_id="CANDIDATE", case_id="CANDIDATE-EDIT",
            variant="edit", status="success",
        )

    monkeypatch.setattr(candidates.ClaudeRunner, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(candidates.ClaudeRunner, "run", fake_run)
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "references")
    scope = [".claude/skills/intent/", ".claude/references/"]
    limits = {"max_turns": 120, "timeout_sec": 2400, "max_budget_usd": 12.5}
    manager.edit(candidate["id"], "Revise the skill and references", scope, limits)
    deadline = time.time() + 3
    while time.time() < deadline and manager.detail(candidate["id"])["status"] == "editing":
        time.sleep(0.02)
    reviewed = manager.detail(candidate["id"])
    assert reviewed["status"] == "review"
    assert reviewed["edit_limits"] == limits
    assert reviewed["comparison_files"] == [
        ".claude/references/verification.md",
        ".claude/skills/intent/SKILL.md",
        ".claude/skills/intent/references/clarification.md",
    ]
    assert "shared reference" in reviewed["diff"]
    assert "local reference" in reviewed["diff"]
    assert manager.commit(candidate["id"])["ready_for_experiment"]


def test_scope_hook_blocks_other_paths_before_edit(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    denied_log = tmp_path / "denied.jsonl"

    def call(path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cc_eval.dashboard.scope_hook", str(root), json.dumps([".claude/skills/intent/"]), str(denied_log)],
            input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}}),
            capture_output=True, text=True, check=False,
        )

    assert call(str(root / ".claude/skills/intent/SKILL.md")).returncode == 0
    assert call(str(root / "probe_root.txt")).returncode == 2
    assert call(str(tmp_path / "outside.txt")).returncode == 2
    (root / ".claude/skills/intent").mkdir(parents=True)
    (root / ".claude/skills/intent/escape").symlink_to(tmp_path, target_is_directory=True)
    assert call(str(root / ".claude/skills/intent/escape/outside.txt")).returncode == 2
    denied = [json.loads(line)["path"] for line in denied_log.read_text().splitlines()]
    assert any("probe_root.txt" in item for item in denied)
    assert any("工作区外路径" in item for item in denied)


def test_discard_only_untracked_outside_scope(tmp_path: Path, framework: Path) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "cleanup")
    worktree = Path(candidate["worktree"])
    (worktree / "probe_root.txt").write_text("probe\n", encoding="utf-8")
    assert manager.detail(candidate["id"])["unexpected_files"] == ["probe_root.txt"]
    cleaned = manager.discard_unexpected(candidate["id"])
    assert not cleaned["unexpected_files"]
    assert not (worktree / "probe_root.txt").exists()
    (worktree / "README.md").write_text("tracked change\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "add readme")
    (worktree / "README.md").write_text("changed again\n", encoding="utf-8")
    with pytest.raises(CandidateError, match="已跟踪文件"):
        manager.discard_unexpected(candidate["id"])


@pytest.mark.parametrize("path", ["../secret", "/absolute", ".git/config", ".", "docs/*.md"])
def test_edit_rejects_unsafe_scope(tmp_path: Path, framework: Path, path: str) -> None:
    manager = CandidateManager(tmp_path / "eval", [tmp_path])
    candidate = manager.create(str(framework), "invalid scope")
    with pytest.raises(CandidateError, match="改动范围"):
        manager.edit(candidate["id"], "Change text", [path])
