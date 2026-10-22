"""Runner registry."""

from __future__ import annotations

from .base import BaseRunner
from .claude import ClaudeRunner
from .codex import CodexRunner
from .fake import FakeRunner

_RUNNERS: dict[str, type[BaseRunner]] = {
    "fake": FakeRunner,
    "claude": ClaudeRunner,
    "codex": CodexRunner,
}


def get_runner(name: str, **kwargs) -> BaseRunner:
    """Instantiate a runner by name.

    Raises ``KeyError`` with the list of known runners when the name is
    unknown - cc-eval never silently falls back to another runtime.
    """
    try:
        cls = _RUNNERS[name]
    except KeyError:
        raise KeyError(
            f"unknown runner {name!r}; available runners: {', '.join(sorted(_RUNNERS))}"
        ) from None
    return cls(**kwargs)


def available_runners() -> dict[str, tuple[bool, str]]:
    """Probe every known runner and report whether it can run here."""
    status: dict[str, tuple[bool, str]] = {}
    for name, cls in sorted(_RUNNERS.items()):
        runner = cls()
        try:
            status[name] = runner.available()
        except Exception as exc:  # noqa: BLE001 - availability probing must not explode
            status[name] = (False, f"probe failed: {exc}")
    return status
