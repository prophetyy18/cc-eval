"""The cc-eval console.

A local web application over the workspace: author test cases, start
experiments, watch the results live, browse the history.

The console never becomes a second source of truth - what it writes are
ordinary cc-eval case files, and what it runs is the ordinary
:class:`~cc_eval.core.experiment.ExperimentRunner`.
"""

from __future__ import annotations

from .authoring import AuthoringError, delete_case, list_cases, write_case
from .runs import RunManager, RunRequestError
from .server import ConsoleConfig, ConsoleServer
from .types import Field, OutputSpec, TestType, all_types, get, register

__all__ = [
    "AuthoringError",
    "ConsoleConfig",
    "ConsoleServer",
    "Field",
    "OutputSpec",
    "RunManager",
    "RunRequestError",
    "TestType",
    "all_types",
    "delete_case",
    "get",
    "list_cases",
    "register",
    "write_case",
]
