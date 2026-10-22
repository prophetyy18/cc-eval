"""Lazy re-exports for package ``__init__`` modules.

Eager re-exports create import cycles: ``cc_eval.core`` -> ``core.experiment``
-> ``cc_eval.evaluators`` -> ``evaluators.deterministic`` -> ``cc_eval.core``,
and the cycle breaks depending on which module is imported first.

A lazy ``__getattr__`` (PEP 562) keeps ``from cc_eval.core import
ExperimentSpec`` working while importing the submodules only when they are
actually used.
"""

from __future__ import annotations

import importlib
from typing import Any


def lazy_exports(mapping: dict[str, str]) -> Any:
    """Return a module-level ``__getattr__`` for the given name -> target map.

    Each target is a dotted path ``package.module.attribute``.
    """

    def __getattr__(name: str) -> Any:
        target = mapping.get(name)
        if target is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        module_path, _, attribute = target.rpartition(".")
        module = importlib.import_module(module_path)
        value = getattr(module, attribute)
        globals()[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted(set(globals()) | set(mapping))

    return __getattr__
