import hashlib
import math

import pytest

from app.gis import baseline
from app.gis.baseline import validate_baseline_snapshot, validated_resume_geoids
from app.gis.scoring import build_scores, weighted_percentile


CATEGORIES = (
    "education",
    "healthcare",
    "daily_shopping",
    "parks",
    "public_transport",
)
FIXTURE_CANONICAL_BYTES = (
    b"homeorbit-facility-baseline-jsonl-v2\n"
    b'["001",100,"i:1","i:1","i:1","i:1","i:1"]\n'
    b'["002",0,"i:2","i:2","i:2","i:2","i:2"]\n'
)
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_CANONICAL_BYTES).hexdigest()
RAW_METRICS = {category: float(index + 1) for index, category in enumerate(CATEGORIES)}
BASELINES = {
    category: [(value - 1.0, 100), (value, 300), (value + 1.0, 600)]
    for category, value in RAW_METRICS.items()
}


class StringSubclass(str):
    pass


def test_baseline_canonical_bytes_are_order_and_numeric_representation_stable() -> None:
    rows = {
        "002": (0, {category: 2 for category in reversed(CATEGORIES)}),
        "001": (100, {category: 1.0 for category in reversed(CATEGORIES)}),
    }

    assert baseline.canonical_baseline_bytes(rows) == FIXTURE_CANONICAL_BYTES


def test_baseline_canonical_bytes_normalize_signed_zero_and_exact_integral_float() -> None:
    zero_int = {"001": (1, {category: 0 for category in CATEGORIES})}
    negative_zero_float = {"001": (1, {category: -0.0 for category in CATEGORIES})}
    large_int = {"001": (1, {category: 2**100 for category in CATEGORIES})}
    exactly_equal_float = {"001": (1, {category: float(2**100) for category in CATEGORIES})}

    assert baseline.canonical_baseline_bytes(zero_int) == baseline.canonical_baseline_bytes(
        negative_zero_float
    )
    assert baseline.canonical_baseline_bytes(large_int) == baseline.canonical_baseline_bytes(
        exactly_equal_float
    )


def test_baseline_canonical_bytes_do_not_collapse_distinct_large_integers() -> None:
    exactly_representable = {
        "001": (1, {category: 2**53 for category in CATEGORIES})
    }
    next_integer = {"001": (1, {category: 2**53 + 1 for category in CATEGORIES})}

    assert baseline.canonical_baseline_bytes(
        exactly_representable
    ) != baseline.canonical_baseline_bytes(next_integer)


def test_baseline_sha256_hashes_the_versioned_canonical_bytes() -> None:
    _, rows, _ = _baseline_fixture()

    assert baseline.baseline_sha256(rows) == FIXTURE_SHA256


@pytest.mark.parametrize(
    "weight,metric",
    [
        (True, 1.0),
        (-1, 1.0),
        (1, True),
        (1, float("nan")),
        (1, float("inf")),
        (1, -1.0),
    ],
)
def test_baseline_canonical_bytes_reject_invalid_numeric_values(weight, metric) -> None:
    rows = {"001": (weight, {category: metric for category in CATEGORIES})}

    with pytest.raises(ValueError):
        baseline.canonical_baseline_bytes(rows)


def test_population_weighted_percentile_includes_tied_values() -> None:
    assert weighted_percentile(2.0, [(1.0, 100), (2.0, 300), (3.0, 600)]) == 40.0


def test_zero_weight_samples_do_not_change_percentile() -> None:
    samples = [(0.0, 0), (1.0, 100), (2.0, 0), (3.0, 300)]
    assert weighted_percentile(2.0, samples) == 25.0


def test_zero_total_weight_has_deterministic_safe_result() -> None:
    assert weighted_percentile(2.0, [(1.0, 0), (2.0, 0)]) == 0.0


@pytest.mark.parametrize(
    "samples",
    [[(math.nan, 1)], [(1.0, -1)], [(1.0, math.inf)]],
)
def test_weighted_percentile_rejects_invalid_samples(samples) -> None:
    with pytest.raises(ValueError):
        weighted_percentile(1.0, samples)


def test_low_population_has_no_scores() -> None:
    result = build_scores(population=99.9, raw_metrics=RAW_METRICS, baselines=BASELINES)

    assert result.status == "insufficient_population"
    assert all(score is None for score in result.scores.values())


def test_population_threshold_builds_all_five_scores() -> None:
    result = build_scores(population=100.0, raw_metrics=RAW_METRICS, baselines=BASELINES)

    assert result.status == "ok"
    assert result.scores == {category: 40.0 for category in CATEGORIES}


def _baseline_fixture():
    tracts = {"001": 100, "002": 0}
    rows = {
        "001": (100, {category: 1.0 for category in CATEGORIES}),
        "002": (0, {category: 2.0 for category in CATEGORIES}),
    }
    metadata = {
        "baseline_ready": True,
        "record_count": 2,
        "zero_weight_count": 1,
        "mode": "pedestrian",
        "minutes": 15,
        "version": "facility-baseline-v1",
        "generated_at": "2026-09-01T04:47:30.094022+00:00",
        "sha256": FIXTURE_SHA256,
        "hash_algorithm": "sha256",
        "hash_format": "homeorbit-facility-baseline-jsonl-v2",
    }
    return tracts, rows, metadata


def test_strict_baseline_snapshot_accepts_only_complete_matching_distribution() -> None:
    tracts, rows, metadata = _baseline_fixture()
    assert validate_baseline_snapshot(tracts, rows, metadata, expected_count=2, expected_zero_weight=1)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_geoid",
        "wrong_weight",
        "nan",
        "positive_infinity",
        "negative_infinity",
        "wrong_zero_count",
        "metadata_count",
        "metadata_zero",
        "metadata_mode",
        "metadata_minutes",
        "metadata_version",
        "metadata_not_ready",
        "metadata_ready_integer",
        "metadata_count_float",
        "metadata_zero_float",
        "metadata_zero_bool",
        "metadata_minutes_float",
        "metadata_mode_non_exact_string",
        "metadata_version_non_exact_string",
        "metadata_generated_at_missing",
        "metadata_generated_at_empty",
        "metadata_generated_at_nonstr",
        "metadata_generated_at_malformed",
        "metadata_generated_at_naive",
        "metadata_hash_missing",
        "metadata_hash_wrong",
        "metadata_hash_type",
        "metadata_hash_algorithm",
        "metadata_hash_format",
        "row_metric_tamper",
    ],
)
def test_strict_baseline_snapshot_rejects_every_incomplete_or_malformed_state(mutation) -> None:
    tracts, rows, metadata = _baseline_fixture()
    if mutation == "missing_geoid":
        rows.pop("002")
    elif mutation == "wrong_weight":
        rows["001"] = (99, rows["001"][1])
    elif mutation in {"nan", "positive_infinity", "negative_infinity"}:
        bad = {"nan": float("nan"), "positive_infinity": float("inf"), "negative_infinity": -float("inf")}[mutation]
        rows["001"][1]["parks"] = bad
    elif mutation == "wrong_zero_count":
        tracts["002"] = 1
        rows["002"] = (1, rows["002"][1])
    elif mutation == "metadata_count":
        metadata["record_count"] = 1
    elif mutation == "metadata_zero":
        metadata["zero_weight_count"] = 0
    elif mutation == "metadata_mode":
        metadata["mode"] = "auto"
    elif mutation == "metadata_minutes":
        metadata["minutes"] = 30
    elif mutation == "metadata_version":
        metadata["version"] = "old"
    elif mutation == "metadata_ready_integer":
        metadata["baseline_ready"] = 1
    elif mutation == "metadata_count_float":
        metadata["record_count"] = 2.0
    elif mutation == "metadata_zero_float":
        metadata["zero_weight_count"] = 1.0
    elif mutation == "metadata_zero_bool":
        metadata["zero_weight_count"] = True
    elif mutation == "metadata_minutes_float":
        metadata["minutes"] = 15.0
    elif mutation == "metadata_mode_non_exact_string":
        metadata["mode"] = StringSubclass("pedestrian")
    elif mutation == "metadata_version_non_exact_string":
        metadata["version"] = StringSubclass("facility-baseline-v1")
    elif mutation == "metadata_generated_at_missing":
        metadata.pop("generated_at")
    elif mutation == "metadata_generated_at_empty":
        metadata["generated_at"] = ""
    elif mutation == "metadata_generated_at_nonstr":
        metadata["generated_at"] = 1
    elif mutation == "metadata_generated_at_malformed":
        metadata["generated_at"] = "not-a-time"
    elif mutation == "metadata_generated_at_naive":
        metadata["generated_at"] = "2026-09-01T04:47:30"
    elif mutation == "metadata_hash_missing":
        metadata.pop("sha256")
    elif mutation == "metadata_hash_wrong":
        metadata["sha256"] = "0" * 64
    elif mutation == "metadata_hash_type":
        metadata["sha256"] = 1
    elif mutation == "metadata_hash_algorithm":
        metadata["hash_algorithm"] = "sha512"
    elif mutation == "metadata_hash_format":
        metadata["hash_format"] = "legacy"
    elif mutation == "row_metric_tamper":
        rows["001"][1]["parks"] = 1.25
    else:
        metadata["baseline_ready"] = False
    assert not validate_baseline_snapshot(tracts, rows, metadata, expected_count=2, expected_zero_weight=1)


def test_strict_baseline_snapshot_accepts_z_timezone_timestamp() -> None:
    tracts, rows, metadata = _baseline_fixture()
    metadata["generated_at"] = "2026-09-01T04:47:30Z"

    assert validate_baseline_snapshot(tracts, rows, metadata, expected_count=2, expected_zero_weight=1)


def test_resume_only_skips_rows_with_matching_weight_and_finite_metrics() -> None:
    tracts, rows, _ = _baseline_fixture()
    rows["001"] = (99, rows["001"][1])
    rows["002"][1]["parks"] = float("nan")

    assert validated_resume_geoids(tracts, rows) == set()
