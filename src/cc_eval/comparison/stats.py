"""Small, dependency-free statistics helpers.

Everything here is deterministic given a seed: the same inputs always produce
the same numbers, which matters because a report has to be reproducible.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def mean(values: Sequence[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def stdev(values: Sequence[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    avg = sum(clean) / len(clean)
    variance = sum((v - avg) ** 2 for v in clean) / (len(clean) - 1)
    return math.sqrt(variance)


def variance(values: Sequence[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    avg = sum(clean) / len(clean)
    return sum((v - avg) ** 2 for v in clean) / (len(clean) - 1)


def median(values: Sequence[float]) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2


def paired_bootstrap_ci(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    alpha: float = 0.05,
    iterations: int = 2000,
    seed: int = 20240101,
) -> tuple[float | None, float | None]:
    """Percentile bootstrap CI for the paired mean difference.

    ``baseline`` and ``candidate`` are paired per observation (here: per
    benchmark case).  The bootstrap resamples *cases*, which is the unit that
    the experiment actually generalises over.
    """
    pairs = [(b, c) for b, c in zip(baseline, candidate) if b is not None and c is not None]
    if len(pairs) < 2:
        return None, None
    differences = [c - b for b, c in pairs]
    rng = random.Random(seed)
    n = len(differences)
    samples: list[float] = []
    for _ in range(max(100, iterations)):
        total = 0.0
        for _ in range(n):
            total += differences[rng.randrange(n)]
        samples.append(total / n)
    samples.sort()
    low_index = int((alpha / 2) * len(samples))
    high_index = min(len(samples) - 1, int((1 - alpha / 2) * len(samples)))
    return samples[low_index], samples[high_index]


def relative_delta(baseline: float | None, candidate: float | None) -> float | None:
    """Relative change in percent, or ``None`` when the baseline is zero."""
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (candidate - baseline) / abs(baseline) * 100.0
