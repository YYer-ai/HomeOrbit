from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any


CATEGORIES = (
    "education",
    "healthcare",
    "daily_shopping",
    "parks",
    "public_transport",
)
BASELINE_VERSION = "facility-baseline-v1"
BASELINE_HASH_ALGORITHM = "sha256"
BASELINE_HASH_FORMAT = "homeorbit-facility-baseline-jsonl-v2"


def canonical_baseline_bytes(
    rows: Mapping[str, tuple[Any, Mapping[str, Any]]],
) -> bytes:
    lines = [BASELINE_HASH_FORMAT]
    for geoid in sorted(rows):
        weight, metrics = rows[geoid]
        if (
            type(geoid) is not str
            or not geoid
            or type(weight) is not int
            or weight < 0
            or not isinstance(metrics, Mapping)
            or set(metrics) != set(CATEGORIES)
            or any(not _valid_number(metrics[category]) for category in CATEGORIES)
        ):
            raise ValueError("invalid baseline row")
        metric_values = [_canonical_number(metrics[category]) for category in CATEGORIES]
        values = [geoid, weight, *metric_values]
        lines.append(json.dumps(values, ensure_ascii=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8")


def baseline_sha256(rows: Mapping[str, tuple[Any, Mapping[str, Any]]]) -> str:
    return hashlib.sha256(canonical_baseline_bytes(rows)).hexdigest()


def _valid_number(value: Any) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and value >= 0 and math.isfinite(value)


def _canonical_number(value: int | float) -> str:
    if type(value) is int or value.is_integer():
        return f"i:{int(value)}"
    return f"f:{value.hex()}"


def _valid_generated_at(value: Any) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_baseline_row(
    geoid: str,
    tract_population: int,
    population_weight: Any,
    metrics: Mapping[str, Any],
) -> bool:
    return (
        isinstance(geoid, str)
        and bool(geoid)
        and isinstance(tract_population, int)
        and not isinstance(tract_population, bool)
        and isinstance(population_weight, int)
        and not isinstance(population_weight, bool)
        and population_weight == tract_population
        and set(metrics) == set(CATEGORIES)
        and all(_valid_number(metrics[category]) for category in CATEGORIES)
    )


def validated_resume_geoids(
    tracts: Mapping[str, int],
    rows: Mapping[str, tuple[Any, Mapping[str, Any]]],
) -> set[str]:
    return {
        geoid
        for geoid, (weight, metrics) in rows.items()
        if geoid in tracts and validate_baseline_row(geoid, tracts[geoid], weight, metrics)
    }


def validate_baseline_snapshot(
    tracts: Mapping[str, int],
    rows: Mapping[str, tuple[Any, Mapping[str, Any]]],
    metadata: Mapping[str, Any] | None,
    *,
    expected_count: int = 1772,
    expected_zero_weight: int = 11,
) -> bool:
    if (
        len(tracts) != expected_count
        or len(rows) != expected_count
        or set(rows) != set(tracts)
        or sum(population == 0 for population in tracts.values()) != expected_zero_weight
    ):
        return False
    if validated_resume_geoids(tracts, rows) != set(tracts):
        return False
    try:
        expected_sha256 = baseline_sha256(rows)
    except (KeyError, TypeError, ValueError):
        return False
    sha256 = metadata.get("sha256") if isinstance(metadata, Mapping) else None
    return (
        isinstance(metadata, Mapping)
        and metadata.get("baseline_ready") is True
        and type(metadata.get("record_count")) is int
        and metadata["record_count"] == expected_count
        and type(metadata.get("zero_weight_count")) is int
        and metadata["zero_weight_count"] == expected_zero_weight
        and type(metadata.get("mode")) is str
        and metadata["mode"] == "pedestrian"
        and type(metadata.get("minutes")) is int
        and metadata["minutes"] == 15
        and type(metadata.get("version")) is str
        and metadata["version"] == BASELINE_VERSION
        and _valid_generated_at(metadata.get("generated_at"))
        and type(sha256) is str
        and len(sha256) == 64
        and all(character in "0123456789abcdef" for character in sha256)
        and sha256 == expected_sha256
        and type(metadata.get("hash_algorithm")) is str
        and metadata["hash_algorithm"] == BASELINE_HASH_ALGORITHM
        and type(metadata.get("hash_format")) is str
        and metadata["hash_format"] == BASELINE_HASH_FORMAT
    )
