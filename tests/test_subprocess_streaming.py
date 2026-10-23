"""Live output used by candidate edit progress."""

from __future__ import annotations

import sys

from cc_eval.isolation.subprocess import run_command


def test_run_command_streams_lines_and_preserves_result(tmp_path) -> None:
    lines: list[str] = []
    result = run_command(
        [sys.executable, "-c", "import sys; print('first', flush=True); print('warning', file=sys.stderr); print('second', flush=True)"],
        cwd=tmp_path,
        on_stdout_line=lines.append,
    )

    assert result.ok
    assert lines == ["first", "second"]
    assert result.stdout == "first\nsecond\n"
    assert result.stderr == "warning\n"
