"""Candidate skill branches authored from the console.

Each candidate is a detached working tree of the framework repository. The
baseline is pinned to a commit at creation time; only an explicitly committed
candidate ref is offered to the experiment runner.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ..core.models import RunConfig, TaskBundle, utcnow_iso
from ..isolation.workspace import Workspace
from ..runners.claude import ClaudeRunner

SKILL_PREFIX = ".claude/skills/intent/"
EDIT_MIRROR = "__candidate_editable__"
DEFAULT_EDIT_SCOPE = [SKILL_PREFIX]
DEFAULT_EDIT_LIMITS = {"max_turns": 100, "timeout_sec": 1800, "max_budget_usd": 10.0}
DEFAULT_PERMISSION_MODE = "acceptEdits"
EDIT_PERMISSION_MODES = {"acceptEdits", "bypassPermissions"}
_SLUG = re.compile(r"[^a-z0-9-]+")
_ID = re.compile(r"^[a-f0-9]{12}$")


class CandidateError(ValueError):
    """Invalid candidate request or state."""


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise CandidateError(str(exc)) from exc
    if check and result.returncode:
        raise CandidateError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _git_limited(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a bounded, noninteractive Git operation against local or remote refs."""
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True,
            check=False, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true",
                 "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=8"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidateError("Git 操作超时或无法执行，请检查网络和本地仓库状态") from exc


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout).strip()
    return re.sub(r"(?i)(https?://)[^\s/@]+@", r"\1***@", message)[:1200]


class CandidateManager:
    def __init__(self, root: Path, allowed_roots: list[Path]) -> None:
        self.root = root / "candidates"
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed_roots = [path.expanduser().resolve() for path in allowed_roots]
        self._lock = threading.RLock()
        self._active: set[str] = set()

    def _record_path(self, candidate_id: str) -> Path:
        if not _ID.fullmatch(candidate_id):
            raise CandidateError("invalid candidate id")
        return self.root / f"{candidate_id}.json"

    def _read(self, candidate_id: str) -> dict[str, Any]:
        path = self._record_path(candidate_id)
        if not path.exists():
            raise CandidateError("candidate not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, record: dict[str, Any]) -> None:
        path = self._record_path(record["id"])
        target = path.with_suffix(".tmp")
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        target.replace(path)

    @staticmethod
    def _validate_scope(raw: Any) -> list[str]:
        if not isinstance(raw, list) or not 1 <= len(raw) <= 20:
            raise CandidateError("请填写 1–20 个允许修改的仓库相对路径")
        scope: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                raise CandidateError("改动范围必须是仓库相对路径")
            path = value.strip()
            parts = PurePosixPath(path).parts
            if (
                not path or len(path) > 200 or path.startswith("/") or "\\" in path
                or any(part in {".", "..", ".git"} for part in parts)
                or (parts and parts[0] == EDIT_MIRROR)
                or path.rstrip("/") in {"", "."}
                or any(char in path for char in "*?[]{}")
                or any(ord(char) < 32 for char in path)
            ):
                raise CandidateError("改动范围只能填写具体文件或以 / 结尾的目录，不能使用绝对路径、..、.git 或通配符")
            normalized = "/".join(parts) + ("/" if path.endswith("/") else "")
            if normalized not in scope:
                scope.append(normalized)
        return scope

    @staticmethod
    def _in_scope(path: str, scope: list[str]) -> bool:
        return any(path.startswith(item) if item.endswith("/") else path == item for item in scope)

    @staticmethod
    def _editable_path(path: str) -> str:
        """Put Claude configuration files at an unprotected path for editing."""
        if path.startswith(".claude/"):
            return f"{EDIT_MIRROR}/{path[len('.claude/') :]}"
        return path

    @staticmethod
    def _original_path(path: str) -> str:
        if path.startswith(f"{EDIT_MIRROR}/"):
            return ".claude/" + path[len(EDIT_MIRROR) + 1:]
        return path

    @classmethod
    def _prepare_edit_mirror(cls, stage: Path, scope: list[str]) -> list[str]:
        if (stage / EDIT_MIRROR).exists():
            raise CandidateError(f"仓库已有保留路径 {EDIT_MIRROR}，无法安全创建编辑副本")
        editable_scope = [cls._editable_path(path) for path in scope]
        for original, editable in zip(scope, editable_scope, strict=True):
            if original == editable:
                continue
            source = stage / original.rstrip("/")
            target = stage / editable.rstrip("/")
            if source.is_dir():
                for file in source.rglob("*"):
                    if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(stage.resolve()):
                        dest = target / file.relative_to(source)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file, dest)
            elif source.is_file() and not source.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            elif original.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
        return editable_scope

    @classmethod
    def _mirror_changes(cls, stage: Path, scope: list[str]) -> list[tuple[str, str]]:
        """Return (editable source, original destination) for changed files."""
        mirror = stage / EDIT_MIRROR
        if not mirror.exists():
            return []
        paths: set[str] = set()
        for source in mirror.rglob("*"):
            if source.is_file() or source.is_symlink():
                paths.add(cls._original_path(source.relative_to(stage).as_posix()))
        for item in scope:
            if not item.startswith(".claude/"):
                continue
            source = stage / item.rstrip("/")
            if source.is_file():
                paths.add(item)
            elif source.is_dir():
                paths.update(file.relative_to(stage).as_posix() for file in source.rglob("*") if file.is_file())
        changed = []
        for original in sorted(paths):
            editable = cls._editable_path(original)
            source = stage / editable
            baseline = stage / original
            if baseline.exists() and not source.exists() and not _git(
                stage, "ls-files", "--error-unmatch", "--", original, check=False,
            ).strip():
                # A direct new file appeared after mirror preparation; it was
                # never in the editable copy, so its absence is not a deletion.
                continue
            if source.is_symlink() or (source.exists() != baseline.exists()):
                changed.append((editable, original))
            elif source.is_file() and baseline.is_file() and source.read_bytes() != baseline.read_bytes():
                changed.append((editable, original))
        return changed

    @staticmethod
    def _validate_limits(raw: Any) -> dict[str, int | float]:
        if not isinstance(raw, dict) or set(raw) != set(DEFAULT_EDIT_LIMITS):
            raise CandidateError("请设置修改轮数、最长运行时间和预算")
        turns = raw["max_turns"]
        timeout = raw["timeout_sec"]
        budget = raw["max_budget_usd"]
        if isinstance(turns, bool) or not isinstance(turns, int) or not 1 <= turns <= 200:
            raise CandidateError("最大轮数须为 1–200 的整数")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 60 <= timeout <= 3600:
            raise CandidateError("最长运行时间须为 60–3600 秒")
        if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or not 0.1 <= budget <= 50:
            raise CandidateError("预算须为 0.1–50 美元")
        return {"max_turns": turns, "timeout_sec": timeout, "max_budget_usd": float(budget)}

    def _check_repo(self, raw: str) -> Path:
        if not raw.strip():
            raise CandidateError("请填写 cc-workflow 仓库路径")
        repo = Path(raw).expanduser().resolve()
        if self.allowed_roots and not any(
            repo == root or repo.is_relative_to(root) for root in self.allowed_roots
        ):
            raise CandidateError("repository is outside the allowed framework roots")
        if _git(repo, "rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
            raise CandidateError("repository is not a Git checkout")
        if Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve() != repo:
            raise CandidateError("请填写 Git 仓库根目录")
        if not (repo / SKILL_PREFIX / "SKILL.md").is_file():
            raise CandidateError("所选仓库没有 .claude/skills/intent/SKILL.md；请填写 cc-workflow 仓库根目录")
        return repo

    def suggested_repository(self) -> str:
        for root in self.allowed_roots:
            for repo in (root, root / "cc-workflow"):
                if (repo / SKILL_PREFIX / "SKILL.md").is_file() and _git(
                    repo, "rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}", check=False,
                ).strip():
                    return str(repo)
        return ""

    def create(self, repository: str, name: str) -> dict[str, Any]:
        repo = self._check_repo(repository)
        base = _git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}", check=False).strip()
        if not base:
            raise CandidateError("所选仓库没有本地 main 分支；请使用包含 main 的 cc-workflow 仓库")
        slug = _SLUG.sub("-", name.lower()).strip("-")[:28] or "intent"
        candidate_id = uuid4().hex[:12]
        branch_name = slug if slug == "intent" or slug.startswith("intent-") else f"intent-{slug}"
        branch = f"codex/{branch_name}-{candidate_id[:6]}"
        worktree = self.root / candidate_id / "worktree"
        worktree.parent.mkdir(parents=True)
        try:
            _git(repo, "worktree", "add", "-b", branch, str(worktree), base)
        except Exception:
            worktree.parent.rmdir()
            raise
        record = {
            "id": candidate_id,
            "name": name.strip() or "Intent skill candidate",
            "repository": str(repo),
            "worktree": str(worktree),
            "branch": branch,
            "baseline_commit": base,
            "candidate_commit": base,
            "pushed_commit": "",
            "push_error": "",
            "status": "ready",
            "instruction": "",
            "agent_summary": "",
            "error": "",
            "edit_scope": list(DEFAULT_EDIT_SCOPE),
            "edit_limits": dict(DEFAULT_EDIT_LIMITS),
            "permission_mode": DEFAULT_PERMISSION_MODE,
        }
        with self._lock:
            self._write(record)
        return self.detail(candidate_id)

    def list(self) -> list[dict[str, Any]]:
        return [self.detail(path.stem) for path in sorted(self.root.glob("*.json"))]

    def detail(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read(candidate_id)
            if record["status"] == "editing" and candidate_id not in self._active:
                record["status"] = "interrupted"
                record["ended_at"] = utcnow_iso()
                record["error"] = "控制台服务重启中断了这次修改；请检查文件变更后再发起下一轮"
                self._write(record)
        worktree = Path(record["worktree"])
        if not worktree.exists():
            raise CandidateError("candidate worktree is missing")
        changed = self._changed_paths(worktree)
        untracked = [p for p in _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]
        comparison_files = sorted(set(
            p for p in _git(worktree, "diff", record["baseline_commit"], "--name-only", "-z").split("\0") if p
        ) | set(p for p in _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p))
        record["changed_files"] = changed
        record["untracked_files"] = untracked
        record["can_discard_current"] = (
            bool(changed) and record["status"] in {"review", "failed", "interrupted"}
            and set(changed).issubset(set(record.get("imported_files") or []))
        )
        record["comparison_files"] = comparison_files
        scope = record.get("edit_scope") or DEFAULT_EDIT_SCOPE
        record["unexpected_files"] = [p for p in changed if not self._in_scope(p, scope)]
        record["can_discard_unexpected"] = bool(record["unexpected_files"]) and all(
            path in untracked for path in record["unexpected_files"]
        )
        record["diff"] = self._diff(worktree, comparison_files, record["baseline_commit"])
        record["edit_limits"] = record.get("edit_limits") or dict(DEFAULT_EDIT_LIMITS)
        record["permission_mode"] = record.get("permission_mode") or DEFAULT_PERMISSION_MODE
        record["candidate_commit"] = _git(worktree, "rev-parse", "HEAD").strip()
        record["push_status"] = (
            "pushed" if record.get("pushed_commit") == record["candidate_commit"]
            else "failed" if record.get("push_error")
            else "pending" if record["candidate_commit"] != record["baseline_commit"]
            else "not_committed"
        )
        record["ready_for_experiment"] = (
            record["status"] != "editing" and not changed
            and record["candidate_commit"] != record["baseline_commit"]
        )
        return record

    def publish_preview(self, candidate_id: str) -> dict[str, Any]:
        """Check both worktrees and remote refs before a local Git merge."""
        candidate = self.detail(candidate_id)
        worktree = Path(candidate["worktree"])
        repository = Path(candidate["repository"])
        remote = _git(worktree, "remote", "get-url", "origin", check=False).strip()
        if not remote:
            raise CandidateError("仓库没有配置 origin，无法合并并推送 main")
        result = _git_limited(
            worktree, "ls-remote", "origin", "refs/heads/main",
            f"refs/heads/{candidate['branch']}", timeout=15,
        )
        if result.returncode:
            raise CandidateError("无法读取 origin 的 main 和候选分支，请检查网络与 Git 访问权限")
        refs = dict(line.split("\t", 1)[::-1] for line in result.stdout.splitlines() if "\t" in line)
        main_sha = refs.get("refs/heads/main", "")
        remote_head = refs.get(f"refs/heads/{candidate['branch']}", "")
        if not main_sha:
            raise CandidateError("origin/main 不存在，无法合并到 main")
        if not re.fullmatch(r"[0-9a-f]{40,64}", main_sha):
            raise CandidateError("origin/main 返回了无效的 Git 提交")
        local_main_available = _git_limited(worktree, "cat-file", "-e", f"{main_sha}^{{commit}}").returncode == 0
        if not local_main_available:
            fetched = _git_limited(repository, "fetch", "--no-tags", "origin", "main", timeout=90)
            local_main_available = fetched.returncode == 0 and _git_limited(
                worktree, "cat-file", "-e", f"{main_sha}^{{commit}}",
            ).returncode == 0
        local_main = _git(repository, "rev-parse", "--verify", "refs/heads/main", check=False).strip()
        main_branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
        main_clean = not _git(repository, "status", "--porcelain", "--untracked-files=all").strip()
        local_main_ancestor = bool(local_main and local_main_available and _git_limited(
            repository, "merge-base", "--is-ancestor", local_main, main_sha,
        ).returncode == 0)
        already_merged = bool(local_main_available and _git_limited(
            repository, "merge-base", "--is-ancestor", candidate["candidate_commit"], main_sha,
        ).returncode == 0)
        local_main_contains_candidate = bool(local_main and _git_limited(
            repository, "merge-base", "--is-ancestor", candidate["candidate_commit"], local_main,
        ).returncode == 0)
        changed_files: list[str] = []
        ahead = behind = None
        if local_main_available:
            changed_files = [path for path in _git(
                worktree, "diff", "--name-only", "-z", f"{main_sha}...{candidate['candidate_commit']}", "--",
            ).split("\0") if path]
            counts = _git(worktree, "rev-list", "--left-right", "--count",
                          f"{main_sha}...{candidate['candidate_commit']}").split()
            behind, ahead = map(int, counts)
        reasons = []
        if candidate["status"] == "editing":
            reasons.append("Agent 正在修改候选版本")
        if candidate["changed_files"]:
            reasons.append("候选工作区还有未提交的改动")
        if candidate["candidate_commit"] == candidate["baseline_commit"]:
            reasons.append("候选分支尚无提交")
        if not remote_head or remote_head != candidate["candidate_commit"]:
            reasons.append("候选分支尚未把当前提交推送到 origin")
        if not local_main_available:
            reasons.append("无法读取最新 origin/main 的提交，请检查网络后重新检查")
        if local_main_available and ahead == 0:
            reasons.append("候选分支相对当前 main 没有新提交")
        if not local_main_ancestor:
            reasons.append("本地 main 与 origin/main 已分叉，需要先人工处理 main")
        if main_branch != "main":
            reasons.append("仓库根目录当前没有检出 main 分支")
        if not main_clean:
            reasons.append("main 工作区有未提交或未跟踪文件，请先处理")
        return {
            "candidate_id": candidate_id,
            "branch": candidate["branch"],
            "candidate_commit": candidate["candidate_commit"],
            "remote_candidate_commit": remote_head,
            "main_commit": main_sha,
            "repository": str(repository),
            "local_main_commit": local_main,
            "local_main_behind": local_main_ancestor and local_main != main_sha,
            "already_merged": already_merged,
            "local_main_contains_candidate": local_main_contains_candidate,
            "changed_files": changed_files,
            "ahead": ahead,
            "behind": behind,
            "ready": not reasons,
            "reasons": reasons,
        }

    def merge_to_main(
        self, candidate_id: str, expected_main_commit: str, expected_candidate_commit: str,
    ) -> dict[str, Any]:
        """Merge in a temporary worktree; push first, then fast-forward local main."""
        with self._lock:
            preview = self.publish_preview(candidate_id)
            if not preview["ready"]:
                raise CandidateError("合并前检查未通过：" + "；".join(preview["reasons"]))
            if (expected_main_commit != preview["main_commit"]
                    or expected_candidate_commit != preview["candidate_commit"]):
                raise CandidateError("main 或候选提交已变化，请重新点击“准备合并到 main”核对")
            record = self._read(candidate_id)
            repository = Path(record["repository"])
            merge_worktree = self.root / candidate_id / f"merge-{uuid4().hex[:10]}"
            added = False
            try:
                result = _git_limited(
                    repository, "worktree", "add", "--detach", str(merge_worktree),
                    preview["main_commit"],
                )
                if result.returncode:
                    raise CandidateError("无法创建临时合并工作区：" + _git_error(result))
                added = True
                result = _git_limited(
                    merge_worktree, "-c", "user.name=cc-eval", "-c", "user.email=cc-eval@localhost",
                    "merge", "--no-ff", "--no-edit", "-m",
                    f"Merge {preview['branch']} into main", preview["candidate_commit"], timeout=120,
                )
                if result.returncode:
                    conflicts = _git(
                        merge_worktree, "diff", "--name-only", "--diff-filter=U", check=False,
                    ).strip()
                    detail = "、".join(conflicts.splitlines()) if conflicts else _git_error(result)
                    raise CandidateError("自动合并失败，main 未改动。冲突文件：" + detail)
                merged_commit = _git(merge_worktree, "rev-parse", "HEAD").strip()
                # A normal push rejects a concurrent main update; never force-push main.
                result = _git_limited(
                    merge_worktree, "push", "origin", f"{merged_commit}:refs/heads/main",
                    timeout=90,
                )
                if result.returncode:
                    raise CandidateError("推送 main 失败，本地 main 未改动：" + _git_error(result))
            finally:
                if added:
                    _git_limited(repository, "worktree", "remove", "--force", str(merge_worktree))
            local_sync_error = ""
            if (_git(repository, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip() == "main"
                    and not _git(repository, "status", "--porcelain", "--untracked-files=all").strip()):
                sync = _git_limited(repository, "merge", "--ff-only", merged_commit)
                if sync.returncode:
                    local_sync_error = _git_error(sync)
            else:
                local_sync_error = "main 工作区在推送期间发生变化，请手动同步本地 main"
            record["merged_commit"] = merged_commit
            record["merged_at"] = utcnow_iso()
            record["merge_main_before"] = preview["main_commit"]
            record["local_main_sync_error"] = local_sync_error
            self._write(record)
            return {
                "merged_commit": merged_commit,
                "main_before": preview["main_commit"],
                "candidate_commit": preview["candidate_commit"],
                "remote_pushed": True,
                "local_main_updated": not local_sync_error,
                "local_main_sync_error": local_sync_error,
            }

    def sync_local_main(self, candidate_id: str) -> dict[str, Any]:
        """Recover a clean local main after the remote already contains the candidate."""
        with self._lock:
            preview = self.publish_preview(candidate_id)
            if not preview["already_merged"]:
                raise CandidateError("远端 main 尚未包含候选提交，不能只同步本地 main")
            repository = Path(preview["repository"])
            if _git_limited(
                repository, "merge-base", "--is-ancestor",
                preview["local_main_commit"], preview["main_commit"],
            ).returncode != 0:
                raise CandidateError("本地 main 与远端 main 已分叉，不能自动快进")
            if _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip() != "main":
                raise CandidateError("仓库根目录当前没有检出 main 分支")
            if _git(repository, "status", "--porcelain", "--untracked-files=all").strip():
                raise CandidateError("main 工作区有未提交或未跟踪文件，不能自动同步")
            result = _git_limited(repository, "merge", "--ff-only", preview["main_commit"])
            if result.returncode:
                raise CandidateError("同步本地 main 失败：" + _git_error(result))
            record = self._read(candidate_id)
            record["local_main_sync_error"] = ""
            self._write(record)
            return {"main_commit": preview["main_commit"], "local_main_updated": True}

    def _changed_paths(self, worktree: Path) -> list[str]:
        tracked = _git(worktree, "diff", "HEAD", "--name-only", "-z").split("\0")
        untracked = _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        return sorted({p for p in tracked + untracked if p})

    def _diff(self, worktree: Path, changed: list[str], baseline_commit: str) -> str:
        output = _git(worktree, "diff", baseline_commit, "--no-ext-diff")
        for relative in changed:
            if _git(worktree, "ls-files", "--", relative):
                continue
            content = (worktree / relative).read_text(encoding="utf-8", errors="replace")
            output += "".join(difflib.unified_diff(
                [], content.splitlines(keepends=True),
                fromfile="/dev/null", tofile=f"b/{relative}",
            ))
        return output

    def edit(
        self, candidate_id: str, instruction: str,
        allowed_paths: list[str] | None = None, limits: dict[str, Any] | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        if not instruction.strip():
            raise CandidateError("修改指令不能为空")
        with self._lock:
            record = self._read(candidate_id)
            scope = self._validate_scope(allowed_paths if allowed_paths is not None else record.get("edit_scope", DEFAULT_EDIT_SCOPE))
            edit_limits = self._validate_limits(limits if limits is not None else record.get("edit_limits", DEFAULT_EDIT_LIMITS))
            mode = permission_mode if permission_mode is not None else record.get("permission_mode", DEFAULT_PERMISSION_MODE)
            if mode not in EDIT_PERMISSION_MODES:
                raise CandidateError("候选编辑权限模式只能选 acceptEdits 或 bypassPermissions")
            if candidate_id in self._active or record["status"] == "editing":
                raise CandidateError("candidate is already being edited")
            if self._changed_paths(Path(record["worktree"])):
                raise CandidateError("请先审核并提交已有改动，再发起下一轮修改")
            started_at = utcnow_iso()
            record.update(
                status="editing", instruction=instruction.strip(), error="", agent_summary="",
                started_at=started_at, ended_at="", last_activity_at=started_at,
                last_action_at=started_at,
                event_count=0, turn_count=0,
                reported_cost_usd=None, reported_turns=None, permission_denials=[],
                progress_events=[{"at": started_at, "kind": "system", "label": "正在检查 Claude Code 环境"}],
                edit_scope=scope, edit_limits=edit_limits, permission_mode=mode, scope_violations=[],
                imported_files=[], discarded_at="",
            )
            self._active.add(candidate_id)
            self._write(record)
        threading.Thread(
            target=self._edit_worker, args=(candidate_id, instruction.strip()), daemon=True,
            name=f"candidate-{candidate_id}",
        ).start()
        return self.detail(candidate_id)

    def _edit_worker(self, candidate_id: str, instruction: str) -> None:
        stage: Path | None = None
        denials_path: Path | None = None
        tool_calls: dict[str, tuple[str, str]] = {}

        def display_path(raw: Any) -> str:
            if not isinstance(raw, str) or not raw:
                return ""
            if stage is not None:
                try:
                    return self._original_path(Path(raw).relative_to(stage).as_posix())[:180]
                except ValueError:
                    pass
            return self._original_path(raw)[:180]

        def on_event(event: dict[str, Any]) -> None:
            entries: list[dict[str, str]] = []
            denial = ""
            if event.get("type") == "system" and event.get("subtype") == "init":
                entries.append({"kind": "system", "label": "Claude Code 已启动"})
            elif event.get("type") == "assistant":
                message = event.get("message")
                content = message.get("content") or [] if isinstance(message, dict) else []
                names = {"Read": "读取", "Edit": "编辑", "Write": "写入", "Glob": "查找", "Grep": "搜索"}
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = str(block.get("name") or "未知工具")
                    inputs = block.get("input") or {}
                    if not isinstance(inputs, dict):
                        inputs = {}
                    raw_path = inputs.get("file_path") or inputs.get("path") or inputs.get("glob")
                    if not raw_path and name == "Glob":
                        raw_path = inputs.get("pattern")
                    target = display_path(raw_path)
                    call_id = block.get("id")
                    if isinstance(call_id, str):
                        if call_id in tool_calls:
                            continue
                        tool_calls[call_id] = (name, target)
                    entries.append({"kind": "tool", "label": names.get(name, name) + (" · " + target if target else "")})
            elif event.get("type") == "user":
                message = event.get("message")
                content = message.get("content") or [] if isinstance(message, dict) else []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result" or not block.get("is_error"):
                        continue
                    name, target = tool_calls.get(str(block.get("tool_use_id") or ""), ("工具", ""))
                    raw_error = block.get("content")
                    if isinstance(raw_error, list):
                        raw_error = " ".join(str(item.get("text") or "") for item in raw_error if isinstance(item, dict))
                    entries.append({"kind": "error", "label": name + (" · " + target if target else "") + " 执行失败", "detail": str(raw_error or "")[:400]})
            elif event.get("type") == "result":
                errors = event.get("errors") or []
                detail = "; ".join(str(item) for item in errors)[:400]
                entries.append({"kind": "error" if event.get("is_error") else "system", "label": "Claude Code 返回错误" if event.get("is_error") else "Claude Code 已返回结果", "detail": detail})
            elif event.get("type") == "system" and event.get("subtype") == "permission_denied":
                name = str(event.get("tool_name") or "未知工具")
                _, target = tool_calls.get(str(event.get("tool_use_id") or ""), (name, ""))
                reason = str(event.get("message") or event.get("decision_reason") or "权限被拒绝")[:400]
                denial = name + (" · " + target if target else "") + "：" + reason
                entries.append({"kind": "error", "label": name + (" · " + target if target else "") + " 权限被拒绝", "detail": reason})
            elif event.get("type") == "system" and "error" in str(event.get("subtype") or "").lower():
                entries.append({"kind": "error", "label": "Claude Code 错误 · " + str(event.get("subtype")), "detail": str(event.get("message") or "")[:400]})
            with self._lock:
                record = self._read(candidate_id)
                if record["status"] != "editing":
                    return
                now = utcnow_iso()
                record["last_activity_at"] = now
                if entries:
                    record["last_action_at"] = now
                record["event_count"] = record.get("event_count", 0) + 1
                if event.get("type") == "assistant":
                    record["turn_count"] = record.get("turn_count", 0) + 1
                if denial:
                    record.setdefault("permission_denials", []).append(denial)
                    record["permission_denials"] = record["permission_denials"][-20:]
                if event.get("type") == "result":
                    reported_cost = event.get("total_cost_usd")
                    if isinstance(reported_cost, (int, float)) and math.isfinite(reported_cost) and reported_cost >= 0:
                        record["reported_cost_usd"] = round(float(reported_cost), 6)
                    reported_turns = event.get("num_turns")
                    if type(reported_turns) is int and reported_turns >= 0:
                        record["reported_turns"] = reported_turns
                for entry in entries:
                    record.setdefault("progress_events", []).append({"at": now, **entry})
                record["progress_events"] = record.get("progress_events", [])[-100:]
                self._write(record)

        try:
            record = self._read(candidate_id)
            worktree = Path(record["worktree"])
            scope = record.get("edit_scope") or DEFAULT_EDIT_SCOPE
            edit_limits = record.get("edit_limits") or DEFAULT_EDIT_LIMITS
            mode = record.get("permission_mode", DEFAULT_PERMISSION_MODE)
            runner = ClaudeRunner()
            available, reason = runner.available()
            if not available:
                if "was not found on PATH" in reason:
                    reason = "控制台服务找不到 Claude Code CLI（claude）；请将其安装目录加入服务 PATH 后重启控制台"
                raise CandidateError(reason)
            stage = self.root / candidate_id / f"edit-{uuid4().hex[:8]}"
            _git(worktree, "worktree", "add", "--detach", str(stage), "HEAD")
            editable_scope = self._prepare_edit_mirror(stage, scope) if mode == "acceptEdits" else scope
            denials_path = stage.with_name(stage.name + "-denials.jsonl")
            hook_command = shlex.join([
                sys.executable, "-m", "cc_eval.dashboard.scope_hook",
                str(stage), json.dumps(editable_scope), str(denials_path),
            ])
            settings = json.dumps({"hooks": {"PreToolUse": [{
                "matcher": "Write|Edit",
                "hooks": [{"type": "command", "command": hook_command, "onFailure": "block"}],
            }]}})
            task = TaskBundle(
                case_id="CANDIDATE-EDIT",
                task_type="skill_edit",
                title="Edit intent skill",
                prompt=(
                    "IMPORTANT: Edit only these working paths: " + ", ".join(editable_scope) + ". "
                    "Modify only these repository-relative paths: " + ", ".join(scope) + ". "
                    + (
                        "Files under .claude/ are protected by Claude Code. Editable copies of those paths "
                        "are under " + EDIT_MIRROR + "/ instead: " + ", ".join(editable_scope) + ". "
                        "For every .claude/ change, write ONLY to its editable copy; the console maps reviewed "
                        "changes back to .claude/. The original .claude/ files are for reading only. "
                        if mode == "acceptEdits" else
                        "You may edit the selected .claude/ paths directly in this temporary worktree. "
                    )
                    + "Do not commit, create branches, or edit any other files. "
                    "Do not create probe files or helper scripts; edit the requested files directly. "
                    "If Write or Edit is denied, stop immediately and report the permission error. "
                    "If the requested change needs files outside the allowed paths, stop and report those paths. "
                    "Read the existing skill and implement this requested change:\n\n"
                    + instruction
                    + "\n\nAt the end, summarize the behavior change and files edited."
                ),
                allowed_tools=["Read", "Edit", "Write", "Glob", "Grep"],
                output_paths=[path.rstrip("/") for path in editable_scope],
            )
            result = runner.run(
                task,
                RunConfig(
                    runtime="claude", timeout_sec=edit_limits["timeout_sec"],
                    max_turns=edit_limits["max_turns"],
                    max_budget_usd=edit_limits["max_budget_usd"],
                    permission_mode=mode, allow_bypass_permissions=mode == "bypassPermissions",
                    inherit_env=False,
                    runner_args={"limit_tools": True, "settings": settings, "setting_sources": "user"},
                ),
                Workspace(root=stage, run_id=f"candidate-{candidate_id}"),
                experiment_id="CANDIDATE", variant="edit",
                on_event=on_event,
            )
            changed = [path for path in self._changed_paths(stage) if not path.startswith(f"{EDIT_MIRROR}/")]
            mirrored = self._mirror_changes(stage, scope) if mode == "acceptEdits" else []
            denied = []
            if denials_path.exists():
                for line in denials_path.read_text(encoding="utf-8").splitlines():
                    try:
                        denied.append(str(json.loads(line)["path"]))
                    except (ValueError, KeyError, TypeError):
                        continue
            violations = list(dict.fromkeys(
                denied + [path for path in changed if not self._in_scope(path, scope)]
                + [original for _, original in mirrored if not self._in_scope(original, scope)]
            ))
            imported = []
            for source_path, relative in [(path, path) for path in changed] + mirrored:
                if relative in violations:
                    continue
                if relative in imported:
                    violations.append(relative)
                    continue
                source = stage / source_path
                target = worktree / relative
                if source.is_symlink() or not source.resolve().is_relative_to(stage.resolve()) or not target.resolve().is_relative_to(worktree.resolve()):
                    violations.append(relative)
                    continue
                if source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                elif not source.exists() and target.exists():
                    target.unlink()
                imported.append(relative)
            with self._lock:
                record = self._read(candidate_id)
                record["agent_summary"] = result.output_text[:12_000]
                record["runtime_status"] = result.status
                record["exit_code"] = result.exit_code
                record["stderr_tail"] = str(result.provenance.get("stderr_tail") or "")[:2000]
                if result.usage.cost_usd is not None:
                    record["reported_cost_usd"] = round(result.usage.cost_usd, 6)
                record["status"] = "review" if result.ok and imported else "failed"
                record["error"] = result.error or ("Claude Code 未产生允许范围内的改动" if result.ok and not imported else "")
                record["scope_violations"] = violations
                record["imported_files"] = imported
                record["ended_at"] = utcnow_iso()
                record.setdefault("progress_events", []).append({
                    "at": record["ended_at"], "kind": "system",
                    "label": f"本轮结束：导入 {len(imported)} 个允许范围内的改动文件",
                })
                record["progress_events"] = record["progress_events"][-100:]
                self._write(record)
        except Exception as exc:  # noqa: BLE001 - surface agent failures in the UI
            with self._lock:
                record = self._read(candidate_id)
                record.update(status="failed", error=str(exc), ended_at=utcnow_iso())
                self._write(record)
        finally:
            try:
                if stage is not None and stage.exists():
                    try:
                        _git(Path(record["worktree"]), "worktree", "remove", "--force", str(stage), check=False)
                    except CandidateError:
                        pass
                if denials_path is not None:
                    denials_path.unlink(missing_ok=True)
            finally:
                with self._lock:
                    self._active.discard(candidate_id)

    @staticmethod
    def _push(worktree: Path, branch: str) -> None:
        if not _git(worktree, "remote", "get-url", "origin", check=False).strip():
            raise CandidateError("cc-workflow 仓库未配置 origin 远端；配置后可在此重试推送")
        try:
            result = subprocess.run(
                ["git", "-C", str(worktree), "push", "--set-upstream", "origin",
                 f"refs/heads/{branch}:refs/heads/{branch}"],
                capture_output=True, text=True, check=False, timeout=90,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise CandidateError("推送超过 90 秒；请检查远端连接后重试") from exc
        except OSError as exc:
            raise CandidateError(str(exc)) from exc
        if result.returncode:
            message = re.sub(r"(?i)(https?://)[^\s/@]+@", r"\1***@", result.stderr.strip())
            raise CandidateError(message or "git push failed")

    def commit(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read(candidate_id)
            if candidate_id in self._active or record["status"] == "editing":
                raise CandidateError("agent is still editing")
            worktree = Path(record["worktree"])
            if _git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD").strip() != record["branch"]:
                raise CandidateError("候选工作区当前分支与记录不一致，已停止提交和推送")
            if _git(worktree, "rev-parse", "HEAD").strip() != record["candidate_commit"]:
                raise CandidateError("候选分支的提交与审核记录不一致，已停止提交和推送")
            changed = self._changed_paths(worktree)
            if changed:
                scope = record.get("edit_scope") or DEFAULT_EDIT_SCOPE
                unexpected = [p for p in changed if not self._in_scope(p, scope)]
                if unexpected:
                    raise CandidateError("发现 skill 范围外改动，不能提交: " + ", ".join(unexpected))
                _git(worktree, "add", "-A", "--", *changed)
                _git(
                    worktree, "-c", "user.name=cc-eval", "-c", "user.email=cc-eval@localhost",
                    "commit", "-m", "Test intent skill candidate",
                )
                record["status"] = "committed"
                record["candidate_commit"] = _git(worktree, "rev-parse", "HEAD").strip()
                record["imported_files"] = []
                record["push_error"] = ""
                self._write(record)
            elif (record["candidate_commit"] == record["baseline_commit"]
                  or record.get("pushed_commit") == record["candidate_commit"]):
                raise CandidateError("没有待提交或待推送的改动")
            try:
                self._push(worktree, record["branch"])
            except CandidateError as exc:
                record["push_error"] = str(exc)
            else:
                record["pushed_commit"] = record["candidate_commit"]
                record["push_error"] = ""
            self._write(record)
        return self.detail(candidate_id)

    def discard_current(self, candidate_id: str) -> dict[str, Any]:
        """Drop only the last agent edit's uncommitted files; preserve HEAD."""
        with self._lock:
            record = self._read(candidate_id)
            if candidate_id in self._active or record["status"] == "editing":
                raise CandidateError("Claude Code 仍在修改，结束后才能放弃本轮改动")
            worktree = Path(record["worktree"])
            if _git(worktree, "symbolic-ref", "--quiet", "--short", "HEAD").strip() != record["branch"]:
                raise CandidateError("候选工作区当前分支与记录不一致，已停止回退")
            if _git(worktree, "rev-parse", "HEAD").strip() != record["candidate_commit"]:
                raise CandidateError("候选分支的提交与审核记录不一致，已停止回退")
            changed = self._changed_paths(worktree)
            imported = set(record.get("imported_files") or [])
            if not changed or record["status"] not in {"review", "failed", "interrupted"}:
                raise CandidateError("没有可放弃的本轮未提交改动")
            if not set(changed).issubset(imported):
                raise CandidateError("工作区还包含非本轮导入的改动，已停止回退；请先人工检查")
            tracked = [path for path in changed if _git(worktree, "ls-tree", "HEAD", "--", path).strip()]
            new_files = set(changed) - set(tracked)
            for relative in new_files:
                target = worktree / relative
                if not target.parent.resolve().is_relative_to(worktree.resolve()) or target.is_dir():
                    raise CandidateError("本轮文件路径不安全，已停止回退")
            if tracked:
                _git(worktree, "restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked)
            if new_files:
                _git(worktree, "rm", "--cached", "--force", "--ignore-unmatch", "--", *sorted(new_files))
            for relative in new_files:
                target = worktree / relative
                if target.exists() or target.is_symlink():
                    target.unlink()
            record["status"] = "committed" if record["candidate_commit"] != record["baseline_commit"] else "ready"
            record["imported_files"] = []
            record["agent_summary"] = ""
            record["error"] = ""
            record["permission_denials"] = []
            record["scope_violations"] = []
            record["stderr_tail"] = ""
            record["discarded_at"] = utcnow_iso()
            record.setdefault("progress_events", []).append({
                "at": record["discarded_at"], "kind": "system",
                "label": "已放弃本轮未提交改动，候选分支保持在上一提交",
            })
            record["progress_events"] = record["progress_events"][-100:]
            self._write(record)
        return self.detail(candidate_id)

    def discard_unexpected(self, candidate_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._read(candidate_id)
            if candidate_id in self._active or record["status"] == "editing":
                raise CandidateError("修改仍在进行，不能清理文件")
            worktree = Path(record["worktree"])
            scope = record.get("edit_scope") or DEFAULT_EDIT_SCOPE
            unexpected = [path for path in self._changed_paths(worktree) if not self._in_scope(path, scope)]
            untracked = set(_git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
            if any(path not in untracked for path in unexpected):
                raise CandidateError("范围外存在已跟踪文件的改动，请人工检查后处理")
            for relative in unexpected:
                target = worktree / relative
                if not target.parent.resolve().is_relative_to(worktree.resolve()):
                    raise CandidateError("范围外文件路径不安全，未清理")
                target.unlink()
        return self.detail(candidate_id)
