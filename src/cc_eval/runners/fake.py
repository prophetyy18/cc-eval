"""The deterministic Fake Runner.

The fake runner is not a stub that returns canned strings.  It is a *simulator
of an agent that executes the framework's skills*: it loads the skill manifest
from the materialised framework, calls each skill that applies to the case,
merges their findings and writes the structured output the evaluator expects.

That design matters for the validity of the A/B comparison.  When the
baseline and candidate frameworks ship different skill implementations, the
fake runner produces genuinely different results - the difference comes from
the framework content, not from a label attached to the experiment.

The fake runner needs no API key, no network and no agent runtime, which makes
it the backbone of the test suite and of CI.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from ..core.models import RunResult, RuntimeInfo, TaskBundle, Trace, TraceEvent, Usage
from .base import BaseRunner, RunConfig

SKILLS_MANIFEST = "skills.manifest.json"


class FakeRunnerError(RuntimeError):
    """Raised when the materialised framework cannot be executed."""


class FakeRunner(BaseRunner):
    """Runs a task by executing the framework's skills locally."""

    name = "fake"

    def available(self) -> tuple[bool, str]:
        return True, "the fake runner has no external dependencies"

    # -- skill discovery ---------------------------------------------------- #
    @staticmethod
    def load_manifest(framework_root: Path) -> dict[str, Any]:
        manifest_path = framework_root / SKILLS_MANIFEST
        if not manifest_path.exists():
            raise FakeRunnerError(
                f"no {SKILLS_MANIFEST} found in {framework_root}; the fake runner needs "
                "a framework that declares its skills"
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FakeRunnerError(f"invalid {SKILLS_MANIFEST}: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise FakeRunnerError(f"invalid {SKILLS_MANIFEST}: expected a 'skills' list")
        return data

    @staticmethod
    def _import_skill(framework_root: Path, module_rel: str, module_name: str):
        path = (framework_root / module_rel).resolve()
        if not str(path).startswith(str(framework_root.resolve())):
            raise FakeRunnerError(f"skill module escapes the framework root: {module_rel}")
        if not path.exists():
            raise FakeRunnerError(f"skill module not found: {path}")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise FakeRunnerError(f"cannot load skill module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - surfaced as an agent error
            raise FakeRunnerError(f"skill module {module_rel} failed to load: {exc}") from exc
        return module

    # -- execution ---------------------------------------------------------- #
    def run(
        self,
        task: TaskBundle,
        config: RunConfig,
        workspace: Any,
        *,
        experiment_id: str = "",
        variant: str = "",
        repetition: int = 1,
    ) -> RunResult:
        run_id = f"{experiment_id or 'EXP'}-{variant or 'base'}-{task.case_id}-r{repetition}"
        result = self._base_result(
            task=task,
            config=config,
            experiment_id=experiment_id,
            variant=variant,
            repetition=repetition,
            run_id=run_id,
        )
        framework_root = Path(workspace.root)
        events: list[TraceEvent] = []
        seq = 0

        def _next() -> int:
            nonlocal seq
            seq += 1
            return seq

        events.append(
            TraceEvent(
                seq=_next(),
                type="session",
                name="fake_session",
                status="ok",
                details={"framework_root": str(framework_root), "runner": self.name},
            )
        )

        try:
            manifest = self.load_manifest(framework_root)
        except FakeRunnerError as exc:
            result.status = "agent_error"
            result.error = str(exc)
            result.usage = Usage(source="unavailable")
            return result

        applicable = [
            skill
            for skill in manifest["skills"]
            if task.task_type in (skill.get("task_types") or []) or "*" in (skill.get("task_types") or [])
        ]
        if not applicable:
            result.status = "agent_error"
            result.error = (
                f"no skill in {SKILLS_MANIFEST} declares task_type={task.task_type!r}"
            )
            return result

        findings: list[dict[str, Any]] = []
        extras: dict[str, Any] = {}
        invoked: list[str] = []
        available = [str(s.get("name")) for s in manifest["skills"] if s.get("name")]

        for index, skill in enumerate(applicable):
            name = str(skill.get("name") or f"skill-{index}")
            module_rel = str(skill.get("module") or "")
            entrypoint = str(skill.get("entrypoint") or "run")
            module = self._import_skill(framework_root, module_rel, f"_cc_eval_skill_{index}_{name}")
            func = getattr(module, entrypoint, None)
            if func is None or not callable(func):
                result.status = "agent_error"
                result.error = f"skill {name} has no callable entrypoint {entrypoint!r}"
                return result

            payload = {
                "case_id": task.case_id,
                "task_type": task.task_type,
                "prompt": task.prompt,
                "input_files": dict(task.input_files),
                "seed": config.seed,
            }
            events.append(
                TraceEvent(
                    seq=_next(),
                    type="tool_call",
                    tool="Skill",
                    name=name,
                    status="initiated",
                    details={"skill": name, "module": module_rel, "entrypoint": entrypoint},
                )
            )
            try:
                output = func(payload)
            except Exception as exc:  # noqa: BLE001 - a broken skill is an agent error
                events.append(
                    TraceEvent(
                        seq=_next(),
                        type="tool_result",
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                        details={"skill": name},
                    )
                )
                result.status = "agent_error"
                result.error = f"skill {name} raised {type(exc).__name__}: {exc}"
                return result

            if not isinstance(output, dict):
                output = {"findings": list(output or [])}
            skill_findings = output.get("findings") or []
            findings.extend(skill_findings)
            extras.update(output.get("extra") or {})
            invoked.append(name)
            events.append(
                TraceEvent(
                    seq=_next(),
                    type="tool_result",
                    status="success",
                    bytes_out=len(json.dumps(skill_findings, ensure_ascii=False).encode("utf-8")),
                    details={"skill": name, "finding_count": len(skill_findings)},
                )
            )

        # De-duplicate findings by id, keeping first occurrence order.
        seen_ids: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in findings:
            key = str(item.get("id") or json.dumps(item, sort_keys=True))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            unique.append(item)

        document = {
            "case_id": task.case_id,
            "task_type": task.task_type,
            "findings": unique,
            "summary": f"{len(unique)} finding(s) produced by {len(invoked)} skill(s)",
            "extra": extras,
        }
        out_path = workspace.resolve_output(task.expected_output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

        events.append(
            TraceEvent(
                seq=_next(),
                type="result",
                name="fake_result",
                status="ok",
                details={"finding_count": len(unique), "skills": invoked},
            )
        )

        trace = Trace(
            run_id=run_id,
            experiment_id=experiment_id,
            case_id=task.case_id,
            variant=variant,
            repetition=repetition,
            events=events,
            missing_fields=["cost_usd", "real_model_tokens"],
            available_capabilities=sorted(set(available)),
            invoked_capabilities=sorted(set(invoked)),
        )

        result.status = "success"
        result.output_text = document["summary"]
        result.output_files = workspace.collect_outputs()
        result.duration_ms = 0
        result.runtime = RuntimeInfo(
            name=self.name,
            version="1",
            model=config.model or "simulated",
            model_reported_by="fake",
            command=[],
            seed_supported=True,
            seed_note=(
                "The fake runner is deterministic for a given framework content and seed; "
                "it simulates skill execution, not model sampling."
            ),
            extra={"skills_invoked": invoked},
        )
        result.determinism.supported = True
        result.determinism.note = (
            "Fake runner: identical framework content + identical case => identical output."
        )
        result.usage = Usage(
            source="estimated",
            input_tokens=_estimate_tokens(task),
            output_tokens=_estimate_tokens_from_document(document),
            total_tokens=_estimate_tokens(task) + _estimate_tokens_from_document(document),
            cost_usd=None,
        )
        result.provenance.update(
            {
                "skills_manifest": SKILLS_MANIFEST,
                "skills_available": available,
                "skills_invoked": invoked,
                "usage_formula": "ceil(chars/4); estimated by cc-eval, not reported by a model",
            }
        )
        result.trace = trace
        return result


def _estimate_tokens(task: TaskBundle) -> int:
    chars = len(task.prompt) + sum(len(v) for v in task.input_files.values())
    return max(1, chars // 4)


def _estimate_tokens_from_document(document: dict[str, Any]) -> int:
    chars = len(json.dumps(document, ensure_ascii=False))
    return max(1, chars // 4)
