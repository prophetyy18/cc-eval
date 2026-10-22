"""Secret redaction for traces, agent output and reports.

Two layers of defence are implemented, and the distinction matters:

1. **Text redaction** (this module) scrubs credentials that appear in strings.
   This is a *hygiene* measure for logs and reports.
2. **Process isolation** (see :mod:`cc_eval.isolation.subprocess`) removes
   credentials from the environment handed to an agent process.  This is the
   *real* access control.

A prompt that says "do not read secrets" is not access control; the env
sanitiser is.
"""

from __future__ import annotations

import re
from typing import Any

# (name, pattern) pairs.  Order matters: more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer", re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}")),
    (
        "generic_secret_assignment",
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|secret|password|passwd|token|access[_-]?key|"
            r"private[_-]?key|credential)[A-Za-z0-9_]*)\s*[:=]\s*[\"']?([^\s\"',;]{6,})[\"']?"
        ),
    ),
    ("url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^/\s@]{3,})@")),
]

_PLACEHOLDER = "[REDACTED:{kind}]"

# Values that are almost always benign but would trip the generic matcher.
_ALLOWLIST = {
    "none",
    "null",
    "true",
    "false",
    "changeme",
    "placeholder",
    "example",
    "redacted",
    "your-api-key",
    "xxx",
    "***",
}


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Redact secrets from a string.

    Returns the redacted text and a per-kind count so the caller can report
    how much was scrubbed (a report should never silently contain secrets).
    """
    if not text:
        return text, {}
    stats: dict[str, int] = {}
    out = text
    for kind, pattern in _PATTERNS:
        if kind == "generic_secret_assignment":

            def _sub_assignment(m: re.Match[str]) -> str:
                name, value = m.group(1), m.group(2)
                if value.strip().lower() in _ALLOWLIST:
                    return m.group(0)
                stats[kind] = stats.get(kind, 0) + 1
                return f"{name}={_PLACEHOLDER.format(kind=kind)}"

            out = pattern.sub(_sub_assignment, out)
        elif kind == "url_credentials":

            def _sub_url(m: re.Match[str]) -> str:
                stats[kind] = stats.get(kind, 0) + 1
                return f"{m.group(1)}:{_PLACEHOLDER.format(kind=kind)}@"

            out = pattern.sub(_sub_url, out)
        else:
            out, n = pattern.subn(_PLACEHOLDER.format(kind=kind), out)
            if n:
                stats[kind] = stats.get(kind, 0) + n
    return out, stats


def redact_obj(obj: Any) -> tuple[Any, dict[str, int]]:
    """Recursively redact strings inside a JSON-compatible structure."""
    stats: dict[str, int] = {}

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            red, s = redact_text(node)
            for k, v in s.items():
                stats[k] = stats.get(k, 0) + v
            return red
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(v) for v in node]
        return node

    return _walk(obj), stats


def merge_stats(target: dict[str, int], extra: dict[str, int]) -> dict[str, int]:
    for k, v in extra.items():
        target[k] = target.get(k, 0) + v
    return target
