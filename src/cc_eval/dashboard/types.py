"""Test types: the declarative description of *what kind of test* this is.

The console must serve more than one kind of experiment - the intent skill
today, other skills and workflows tomorrow.  A test type says, in data:

* which **form** the authoring UI renders (what the user fills in);
* which **outputs** the agent produces and how they are shown;
* how a **case** is written to disk.

Everything else - the runner, the registry, the reports - stays generic.  A new
kind of test is a new :class:`TestType` registration, not a new code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

FieldType = Literal["text", "textarea", "tags", "number", "select", "path"]
OutputView = Literal["markdown", "text", "diff"]


@dataclass(frozen=True)
class Field:
    """One field of the authoring form."""

    key: str
    label: str
    type: FieldType = "text"
    required: bool = False
    help: str = ""
    default: Any = None
    options: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "help": self.help,
            "default": self.default,
            "options": list(self.options),
        }


@dataclass(frozen=True)
class OutputSpec:
    """One thing the agent produces, and how the console shows it."""

    key: str
    label: str
    #: A glob, relative to the workspace root, of the produced files.
    collect: str
    view: OutputView = "text"
    #: An objective count over the produced text (e.g. how many intent
    #: documents were written).  A fact, never a score.
    count_pattern: str = ""
    count_label: str = ""


@dataclass(frozen=True)
class TestType:
    """A kind of test the console can author, run and show."""

    name: str
    title: str
    #: The benchmark directory that holds the cases of this type.
    benchmark: Path
    #: The authoring form.
    form: list[Field] = field(default_factory=list)
    #: The produced artefacts and how to show them.
    outputs: list[OutputSpec] = field(default_factory=list)
    #: The prompt template.  ``{input}`` is replaced by the case's input file.
    prompt_template: str = ""
    #: The field key -> the input file it is written to.
    input_files: dict[str, str] = field(default_factory=dict)
    #: The workspace paths whose files count as produced output.
    output_paths: list[str] = field(default_factory=lambda: ["out"])
    #: The tools the agent may use (non-interactive runs have nobody to
    #: approve a permission prompt).
    allowed_tools: list[str] = field(default_factory=list)
    #: The prefix of the generated case ids.
    case_prefix: str = "CASE"
    #: The task_type written into the generated task.yaml.
    task_type: str = "task"
    #: The evaluator used for the per-run metrics.
    evaluator: str = "deterministic-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "form": [f.as_dict() for f in self.form],
            "outputs": [
                {
                    "key": o.key,
                    "label": o.label,
                    "collect": o.collect,
                    "view": o.view,
                    "count_pattern": o.count_pattern,
                    "count_label": o.count_label,
                }
                for o in self.outputs
            ],
            "case_prefix": self.case_prefix,
            "runner": "claude",
        }


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, TestType] = {}


def register(test_type: TestType) -> TestType:
    """Register a test type."""
    _REGISTRY[test_type.name] = test_type
    return test_type


def get(name: str) -> TestType:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown test type: {name}") from exc


def all_types() -> list[TestType]:
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def _builtin() -> None:
    """The test types that ship with cc-eval."""
    register(
        TestType(
            name="intent-skill",
            title="Intent 拆解",
            benchmark=Path("benchmarks/intent_skill"),
            form=[
                Field(
                    key="request",
                    label="Intent 输入（原始用户请求）",
                    type="textarea",
                    required=True,
                    help="粘贴给 agent 的原始输入，不要改写。空行分段。",
                ),
                Field(key="title", label="标题", type="text"),
                Field(key="tags", label="标签", type="tags", default=[]),
            ],
            outputs=[
                OutputSpec(
                    key="intent_doc",
                    label="INTENT.md",
                    collect="docs/intents/*/INTENT.md",
                    view="markdown",
                    count_pattern=r"(?m)^id:\s*INT-\d{3}",
                    count_label="intent 数量",
                )
            ],
            prompt_template=(
                "You are working in a repository that contains the `intent` skill.\n"
                "\n"
                "A user has sent the raw request below.  It is saved at\n"
                "`task/input/request.md` - read it before you answer.\n"
                "\n"
                "Use the `intent` skill from this repository to capture it: decide whether\n"
                "the request is one intent or several, and write the intent document(s)\n"
                "under `docs/intents/`.\n"
                "\n"
                "Rules:\n"
                "- There is no human to answer questions.  Wherever the skill would normally\n"
                "  clarify something with the user, state the assumption inline and mark it\n"
                '  "(assumed)".\n'
                "- Follow the INTENT.md template of the skill exactly.\n"
                "- Do not start a Spec, an Architecture or an implementation.\n"
                "- Do not commit anything.\n"
            ),
            input_files={"request": "request.md"},
            output_paths=["docs/intents"],
            allowed_tools=[
                "Read",
                "Edit",
                "Write",
                "Glob",
                "Grep",
                "Bash(date:*)",
                "Bash(mkdir:*)",
                "Bash(git:*)",
                "WebFetch",
                "WebSearch",
            ],
            case_prefix="INT-S",
            task_type="intent_capture",
            evaluator="rubric:intent-doc-v1",
        )
    )


_builtin()
