from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ScoreResult:
    status: str
    scores: dict[str, float | None]


def weighted_percentile(value: float, samples: Sequence[tuple[float, float]]) -> float:
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    total_weight = 0.0
    selected_weight = 0.0
    for sample_value, weight in samples:
        if not math.isfinite(sample_value) or not math.isfinite(weight) or weight < 0:
            raise ValueError("baseline samples must be finite and non-negative")
        if weight == 0:
            continue
        total_weight += weight
        if sample_value <= value:
            selected_weight += weight
    if total_weight == 0:
        return 0.0
    return selected_weight * 100.0 / total_weight


def build_scores(
    population: float,
    raw_metrics: Mapping[str, float],
    baselines: Mapping[str, Sequence[tuple[float, float]]],
) -> ScoreResult:
    if not math.isfinite(population) or population < 0:
        raise ValueError("population must be finite and non-negative")
    categories = tuple(raw_metrics)
    if population < 100:
        return ScoreResult(
            status="insufficient_population",
            scores={category: None for category in categories},
        )
    scores = {
        category: weighted_percentile(raw_value, baselines[category])
        for category, raw_value in raw_metrics.items()
    }
    return ScoreResult(status="ok", scores=scores)
