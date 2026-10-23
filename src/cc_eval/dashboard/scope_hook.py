"""Claude Code PreToolUse hook for candidate edit paths.

This runs before each Write/Edit call. The temporary worktree import check is
still the final boundary for what reaches a candidate branch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(root: Path, scope: list[str], event: dict) -> tuple[bool, str]:
    tool = event.get("tool_name")
    if tool not in {"Write", "Edit"}:
        return False, "未授权的文件修改工具"
    tool_input = event.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return False, "修改操作缺少文件路径"
    target = Path(raw)
    if not target.is_absolute():
        target = root / target
    resolved_root = root.resolve()
    try:
        relative = target.resolve().relative_to(resolved_root).as_posix()
    except ValueError:
        return False, f"工作区外路径：{raw}"
    if ".git" in Path(relative).parts:
        return False, f"Git 元数据不可修改：{relative}"
    if not any(relative.startswith(item) if item.endswith("/") else relative == item for item in scope):
        return False, f"超出本轮允许修改的路径：{relative}"
    return True, relative


def main() -> int:
    try:
        root = Path(sys.argv[1])
        scope = json.loads(sys.argv[2])
        denials = Path(sys.argv[3])
        event = json.load(sys.stdin)
        allowed, reason = check(root, scope, event)
    except (IndexError, OSError, ValueError, TypeError, json.JSONDecodeError):
        print("候选版本范围检查失败，已拒绝修改", file=sys.stderr)
        return 2
    if allowed:
        return 0
    try:
        with denials.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"path": reason}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
