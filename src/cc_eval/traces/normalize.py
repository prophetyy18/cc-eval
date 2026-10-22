"""Normalisation of runtime-specific event streams into one trace format.

Claude Code and Codex emit completely different event vocabularies.  This
module maps both onto :class:`~cc_eval.core.models.TraceEvent` while keeping
the original payload available for manual review.

Two principles:

* **Nothing is invented.**  If a runtime does not report tokens, cost or tool
  durations, the normalised event carries ``None`` and the run's trace lists
  the field under ``missing_fields``.
* **Raw is kept.**  The raw event stream is written next to the normalised
  trace so a human can audit what cc-eval interpreted.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..core.models import CapabilityStatus, Trace, TraceEvent

# Normalised event types used across all runtimes.
EV_SESSION = "session"
EV_TOOL_CALL = "tool_call"
EV_TOOL_RESULT = "tool_result"
EV_AGENT_OUTPUT = "agent_output"
EV_REASONING = "reasoning"
EV_ERROR = "error"
EV_RESULT = "result"
EV_USER = "user_input"


def _digest(text: str) -> str:
    """Short, stable digest of a (possibly sensitive) payload."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(obj)


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse a JSONL stream, skipping malformed lines but recording them."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #


def normalise_claude(
    raw_events: Iterable[dict[str, Any]],
    *,
    run_id: str,
    experiment_id: str,
    case_id: str,
    variant: str,
    repetition: int = 1,
) -> Trace:
    """Normalise a Claude Code ``stream-json`` event stream.

    Verified against Claude Code 2.1.308 output shape:
    ``system/init`` (carries ``skills`` and ``tools``), ``assistant`` messages
    carrying ``tool_use`` blocks, ``user`` messages carrying ``tool_result``
    blocks, and a final ``result`` object with usage/cost.
    """
    events: list[TraceEvent] = []
    seq = 0
    missing: set[str] = set()
    available_skills: list[str] = []
    invoked_skills: list[str] = []
    tool_names: list[str] = []
    saw_init = False
    saw_result = False

    def _next() -> int:
        nonlocal seq
        seq += 1
        return seq

    for raw in raw_events:
        etype = raw.get("type")

        if etype == "system" and raw.get("subtype") == "init":
            saw_init = True
            available_skills = [str(s) for s in raw.get("skills", []) or []]
            tool_names = [str(s) for s in raw.get("tools", []) or []]
            events.append(
                TraceEvent(
                    seq=_next(),
                    ts=raw.get("timestamp"),
                    type=EV_SESSION,
                    name="init",
                    status="ok",
                    details={
                        "cwd": raw.get("cwd"),
                        "session_id": raw.get("session_id"),
                        "model": raw.get("model"),
                        "permission_mode": raw.get("permissionMode"),
                        "claude_code_version": raw.get("claude_code_version"),
                        "api_key_source": raw.get("apiKeySource"),
                        "tools": tool_names,
                        "skills": available_skills,
                        "plugins": [p.get("name") for p in raw.get("plugins", []) or []],
                        "slash_commands": raw.get("slash_commands"),
                    },
                )
            )
            continue

        if etype == "assistant":
            message = raw.get("message") or {}
            usage = message.get("usage") or {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    name = str(block.get("name", ""))
                    tool_names.append(name)
                    payload = block.get("input") or {}
                    if name == "Skill":
                        skill = payload.get("skill") or payload.get("name")
                        if skill:
                            invoked_skills.append(str(skill))
                    events.append(
                        TraceEvent(
                            seq=_next(),
                            ts=raw.get("timestamp"),
                            type=EV_TOOL_CALL,
                            tool=name,
                            name=name,
                            status="initiated",
                            input_digest=_digest(_safe_json(payload)),
                            details={
                                "tool_use_id": block.get("id"),
                                "arguments": payload,
                            },
                        )
                    )
                elif btype == "text":
                    text = str(block.get("text", ""))
                    events.append(
                        TraceEvent(
                            seq=_next(),
                            ts=raw.get("timestamp"),
                            type=EV_AGENT_OUTPUT,
                            name="assistant",
                            status="ok",
                            output_digest=_digest(text),
                            bytes_out=len(text.encode("utf-8")),
                            details={"text": text},
                        )
                    )
            if usage:
                events.append(
                    TraceEvent(
                        seq=_next(),
                        ts=raw.get("timestamp"),
                        type="model_usage",
                        name=str(message.get("model") or raw.get("model") or "unknown"),
                        details={
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                        },
                    )
                )
            continue

        if etype == "user":
            message = raw.get("message") or {}
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                is_error = bool(block.get("is_error"))
                content = block.get("content")
                text = content if isinstance(content, str) else _safe_json(content)
                events.append(
                    TraceEvent(
                        seq=_next(),
                        ts=raw.get("timestamp"),
                        type=EV_TOOL_RESULT,
                        tool=None,
                        status="error" if is_error else "success",
                        output_digest=_digest(text),
                        bytes_out=len(text.encode("utf-8")),
                        error=text if is_error else None,
                        details={"tool_use_id": block.get("tool_use_id")},
                    )
                )
            continue

        if etype == "result":
            saw_result = True
            subtype = raw.get("subtype")
            is_error = bool(raw.get("is_error"))
            usage = raw.get("usage") or {}
            model_usage = raw.get("modelUsage") or {}
            events.append(
                TraceEvent(
                    seq=_next(),
                    ts=None,
                    type=EV_RESULT,
                    name=str(subtype or "result"),
                    status="error" if is_error else "ok",
                    duration_ms=raw.get("duration_ms"),
                    error=(raw.get("errors") or [None])[0] if is_error else None,
                    details={
                        "subtype": subtype,
                        "is_error": is_error,
                        "num_turns": raw.get("num_turns"),
                        "total_cost_usd": raw.get("total_cost_usd"),
                        # A denied tool call is the difference between "the
                        # agent decided not to write" and "the agent was not
                        # allowed to write".  It must be visible.
                        "permission_denials": raw.get("permission_denials") or [],
                        "session_id": raw.get("session_id"),
                        "usage": usage,
                        "model_usage": model_usage,
                        "result_text": raw.get("result"),
                    },
                )
            )
            continue

        if etype:
            events.append(
                TraceEvent(
                    seq=_next(),
                    ts=raw.get("timestamp"),
                    type=str(etype),
                    name=raw.get("subtype"),
                    status=raw.get("status"),
                    details={k: v for k, v in raw.items() if k not in {"type", "timestamp"}},
                )
            )

    if not saw_init:
        missing.add("runtime_init")
    if not saw_result:
        missing.add("result_event")
    missing.add("tool_duration_ms")
    missing.add("per_tool_token_usage")

    return Trace(
        run_id=run_id,
        experiment_id=experiment_id,
        case_id=case_id,
        variant=variant,
        repetition=repetition,
        events=events,
        missing_fields=sorted(missing),
        available_capabilities=sorted(set(available_skills)),
        invoked_capabilities=sorted(set(invoked_skills)),
    )


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #


def normalise_codex(
    raw_events: Iterable[dict[str, Any]],
    *,
    run_id: str,
    experiment_id: str,
    case_id: str,
    variant: str,
    repetition: int = 1,
) -> Trace:
    """Normalise a Codex ``exec --json`` event stream.

    Verified against codex-cli 0.160.1 output shape: ``thread.started``,
    ``turn.started``, ``item.started`` / ``item.completed`` (item types
    ``agent_message``, ``command_execution``, ``file_change``, ...) and
    ``turn.completed`` (usage only - Codex does not report cost).
    """
    events: list[TraceEvent] = []
    seq = 0
    missing: set[str] = set()
    tool_names: list[str] = []
    invoked: list[str] = []

    def _next() -> int:
        nonlocal seq
        seq += 1
        return seq

    for raw in raw_events:
        etype = raw.get("type")

        if etype == "thread.started":
            events.append(
                TraceEvent(
                    seq=_next(),
                    type=EV_SESSION,
                    name="thread_started",
                    status="ok",
                    details={"thread_id": raw.get("thread_id")},
                )
            )
            continue

        if etype == "turn.started":
            events.append(TraceEvent(seq=_next(), type="turn_start", status="ok"))
            continue

        if etype in {"item.started", "item.completed"}:
            item = raw.get("item") or {}
            itype = str(item.get("type", "unknown"))
            phase = "started" if etype == "item.started" else "completed"
            status = item.get("status")
            if itype == "agent_message":
                text = str(item.get("text", ""))
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type=EV_AGENT_OUTPUT,
                        name="agent_message",
                        status=status or "ok",
                        output_digest=_digest(text),
                        bytes_out=len(text.encode("utf-8")),
                        details={"text": text, "phase": phase, "item_id": item.get("id")},
                    )
                )
            elif itype == "command_execution":
                tool_names.append("Bash")
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type=EV_TOOL_CALL,
                        tool="Bash",
                        name=item.get("command"),
                        status=status or phase,
                        exit_code=item.get("exit_code"),
                        details={
                            "command": item.get("command"),
                            "phase": phase,
                            "item_id": item.get("id"),
                            "output": item.get("aggregated_output"),
                        },
                    )
                )
            elif itype in {"file_change", "patch_apply"}:
                tool_names.append("Edit")
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type=EV_TOOL_CALL,
                        tool="Edit",
                        name=itype,
                        status=status or phase,
                        details={"phase": phase, "item_id": item.get("id"), "changes": item.get("changes")},
                    )
                )
            elif itype == "mcp_tool_call":
                tool_names.append(str(item.get("tool") or "mcp"))
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type=EV_TOOL_CALL,
                        tool=str(item.get("tool") or "mcp"),
                        name=str(item.get("server") or ""),
                        status=status or phase,
                        details={"phase": phase, "item_id": item.get("id")},
                    )
                )
            elif itype == "reasoning":
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type=EV_REASONING,
                        name="reasoning",
                        status=status or phase,
                        details={"phase": phase, "item_id": item.get("id")},
                    )
                )
            else:
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type="item",
                        name=itype,
                        status=status,
                        details={"phase": phase, "item_id": item.get("id")},
                    )
                )
            continue

        if etype == "turn.completed":
            usage = raw.get("usage") or {}
            events.append(
                TraceEvent(
                    seq=_next(),
                    type=EV_RESULT,
                    name="turn.completed",
                    status="ok",
                    details={"usage": usage},
                )
            )
            continue

        if etype == "turn.failed":
            events.append(
                TraceEvent(
                    seq=_next(),
                    type=EV_ERROR,
                    name="turn.failed",
                    status="error",
                    error=_safe_json(raw.get("error") or raw),
                    details=raw,
                )
            )
            continue

        if etype:
            events.append(
                TraceEvent(seq=_next(), type=str(etype), details=raw)
            )

    missing.add("cost_usd")
    missing.add("tool_duration_ms")
    missing.add("per_tool_token_usage")
    missing.add("runtime_init")

    return Trace(
        run_id=run_id,
        experiment_id=experiment_id,
        case_id=case_id,
        variant=variant,
        repetition=repetition,
        events=events,
        missing_fields=sorted(missing),
        available_capabilities=sorted(set(invoked)),
        invoked_capabilities=sorted(set(invoked)),
    )


# --------------------------------------------------------------------------- #
# Capability verification
# --------------------------------------------------------------------------- #


def build_capabilities(
    *,
    expected: Iterable[str],
    available: Iterable[str],
    invoked: Iterable[str],
    tools: Iterable[str],
    source: str,
) -> list[CapabilityStatus]:
    """Decide whether each expected capability was actually exercised.

    A capability counts as *verified* only when there is positive evidence
    that the framework under test exposed it **and** the run used it.  A YAML
    flag alone proves nothing (ADR-007).
    """
    expected_list = [str(e) for e in expected]
    available_set = {str(a) for a in available}
    invoked_set = {str(i) for i in invoked}
    tool_set = {str(t) for t in tools}

    statuses: list[CapabilityStatus] = []
    for name in expected_list:
        in_available = name in available_set
        in_invoked = name in invoked_set
        if in_available and in_invoked:
            statuses.append(
                CapabilityStatus(
                    name=name,
                    status="verified",
                    evidence=f"listed as available and invoked in {source}",
                    source=source,
                )
            )
        elif in_available and not in_invoked:
            statuses.append(
                CapabilityStatus(
                    name=name,
                    status="not_invoked",
                    evidence=f"available in {source} but never invoked during the run",
                    source=source,
                )
            )
        elif in_invoked:
            statuses.append(
                CapabilityStatus(
                    name=name,
                    status="verified",
                    evidence=f"invoked during the run (evidence from {source})",
                    source=source,
                )
            )
        elif tool_set and name in tool_set:
            statuses.append(
                CapabilityStatus(
                    name=name,
                    status="verified",
                    evidence=f"tool {name} was used during the run",
                    source=source,
                )
            )
        else:
            statuses.append(
                CapabilityStatus(
                    name=name,
                    status="contradicted" if available_set else "unverified",
                    evidence=(
                        f"not present in the runtime capability inventory ({source})"
                        if available_set
                        else f"the runtime exposes no capability inventory ({source})"
                    ),
                    source=source,
                )
            )
    return statuses
