import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_facility_baseline.py"
CATEGORIES = ("education", "healthcare", "daily_shopping", "parks", "public_transport")
FIXTURE_CANONICAL_BYTES = (
    b"homeorbit-facility-baseline-jsonl-v2\n"
    b'["001",100,"i:1","i:1","i:1","i:1","i:1"]\n'
    b'["002",0,"i:1","i:1","i:1","i:1","i:1"]\n'
)
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_CANONICAL_BYTES).hexdigest()


def metrics(value=1.0):
    return {category: value for category in CATEGORIES}


def load_builder():
    spec = importlib.util.spec_from_file_location("build_facility_baseline", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_resume_skips_existing_samples_and_commits_each_success() -> None:
    builder = load_builder()
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
        existing={"001": (100, metrics())},
    )

    class Analyzer:
        calls = []

        async def analyze(self, lng, lat):
            self.calls.append((lng, lat))
            return {category: 1.0 for category in builder.CATEGORIES}

    analyzer = Analyzer()
    result = builder.run_build(store, analyzer, resume=True, expected_count=2, expected_zero_weight=1)

    assert result.processed == 1
    assert result.skipped == 1
    assert result.failures == []
    assert analyzer.calls == [(-122.3, 37.8)]
    assert store.upserts[0][0:2] == ("002", 0)
    assert store.commit_count == 1


def test_resume_recomputes_wrong_weight_and_non_finite_existing_rows() -> None:
    builder = load_builder()
    bad_metrics = metrics()
    bad_metrics["parks"] = float("nan")
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
        existing={"001": (99, metrics()), "002": (0, bad_metrics)},
    )

    class Analyzer:
        calls = []

        async def analyze(self, lng, lat):
            self.calls.append((lng, lat))
            return metrics(3.0)

    result = builder.run_build(
        store, Analyzer(), resume=True, expected_count=2, expected_zero_weight=1
    )

    assert result.exit_code == 0
    assert result.processed == 2
    assert result.skipped == 0
    assert [row[0] for row in store.upserts] == ["001", "002"]


def test_complete_valid_resume_is_read_only_and_does_not_toggle_ready() -> None:
    builder = load_builder()
    metadata = {
        "baseline_ready": True,
        "record_count": 2,
        "zero_weight_count": 1,
        "mode": "pedestrian",
        "minutes": 15,
        "version": "facility-baseline-v1",
        "generated_at": "2026-09-01T04:47:30Z",
        "sha256": FIXTURE_SHA256,
        "hash_algorithm": "sha256",
        "hash_format": "homeorbit-facility-baseline-jsonl-v2",
    }
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
        existing={"001": (100, metrics()), "002": (0, metrics())},
        metadata=metadata,
    )

    class Analyzer:
        async def analyze(self, lng, lat):
            raise AssertionError("complete resume must not analyze")

    result = builder.run_build(
        store, Analyzer(), resume=True, expected_count=2, expected_zero_weight=1
    )

    assert result == builder.BuildResult(processed=0, skipped=2, failures=[], exit_code=0)
    assert store.ready_values == []
    assert store.commit_count == 0
    assert store.metadata == metadata


def test_resume_upgrades_legacy_metadata_without_analyzing_or_rewriting_samples() -> None:
    builder = load_builder()
    legacy_metadata = {
        "baseline_ready": True,
        "record_count": 2,
        "zero_weight_count": 1,
        "mode": "pedestrian",
        "minutes": 15,
        "version": "facility-baseline-v1",
        "generated_at": "2026-09-01T04:47:30Z",
    }
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
        existing={"001": (100, metrics()), "002": (0, metrics())},
        metadata=legacy_metadata,
    )

    class Analyzer:
        async def analyze(self, lng, lat):
            raise AssertionError("metadata-only resume must not call Valhalla")

    result = builder.run_build(
        store, Analyzer(), resume=True, expected_count=2, expected_zero_weight=1
    )

    assert result == builder.BuildResult(processed=0, skipped=2, failures=[], exit_code=0)
    assert store.upserts == []
    assert store.commit_count == 0
    assert store.metadata["sha256"] == FIXTURE_SHA256
    assert store.metadata["hash_algorithm"] == "sha256"
    assert store.metadata["hash_format"] == "homeorbit-facility-baseline-jsonl-v2"


def test_resume_rejects_complete_snapshot_with_float_metadata_count() -> None:
    builder = load_builder()
    metadata = {
        "baseline_ready": True,
        "record_count": 2.0,
        "zero_weight_count": 1,
        "mode": "pedestrian",
        "minutes": 15,
        "version": "facility-baseline-v1",
        "generated_at": "2026-09-01T04:47:30Z",
        "sha256": FIXTURE_SHA256,
        "hash_algorithm": "sha256",
        "hash_format": "homeorbit-facility-baseline-jsonl-v2",
    }
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
        existing={"001": (100, metrics()), "002": (0, metrics())},
        metadata=metadata,
    )

    class Analyzer:
        calls = []

        async def analyze(self, lng, lat):
            self.calls.append((lng, lat))
            return metrics()

    result = builder.run_build(
        store, Analyzer(), resume=True, expected_count=2, expected_zero_weight=1
    )

    assert result.exit_code == 0
    assert result.processed == 0
    assert result.skipped == 2
    assert store.ready_values == [False, True]
    assert type(store.metadata["record_count"]) is int


def test_baseline_origin_accepts_tract_extent_without_expanding_public_contract() -> None:
    builder = load_builder()

    origin = builder.baseline_origin(-121.6267689, 37.6555575)

    assert origin.lng == pytest.approx(-121.6267689)
    assert origin.lat == pytest.approx(37.6555575)


@pytest.mark.parametrize("lng,lat", [(float("nan"), 37.7), (-124.0, 37.7), (-122.0, 39.0)])
def test_baseline_origin_rejects_non_finite_or_outside_tract_extent(lng, lat) -> None:
    builder = load_builder()
    with pytest.raises(ValueError):
        builder.baseline_origin(lng, lat)


def test_failure_continues_but_never_marks_baseline_ready() -> None:
    builder = load_builder()
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 200, -122.3, 37.8)],
    )

    class Analyzer:
        async def analyze(self, lng, lat):
            if lng == -122.4:
                raise RuntimeError("postgresql://secret/C:/private")
            return {category: 2.0 for category in builder.CATEGORIES}

    result = builder.run_build(
        store, Analyzer(), resume=False, expected_count=2, expected_zero_weight=1
    )

    assert result.exit_code != 0
    assert result.failures == [{"geoid": "001", "reason": "ANALYSIS_FAILED"}]
    assert store.ready_values == [False]
    assert [row[0] for row in store.upserts] == ["002"]
    assert "secret" not in repr(result)


def test_completion_requires_exact_geoid_set_finite_metrics_and_zero_weight_rows() -> None:
    builder = load_builder()
    store = builder.MemoryBaselineStore(
        tracts=[("001", 100, -122.4, 37.7), ("002", 0, -122.3, 37.8)],
    )

    class Analyzer:
        async def analyze(self, lng, lat):
            return {category: 1.0 for category in builder.CATEGORIES}

    result = builder.run_build(
        store, Analyzer(), resume=True, expected_count=2, expected_zero_weight=1
    )

    assert result.exit_code == 0
    assert store.ready_values == [False, True]
    assert store.metadata["baseline_ready"] is True
    assert store.metadata["record_count"] == 2
    assert store.metadata["zero_weight_count"] == 1
    assert store.metadata["sha256"] == FIXTURE_SHA256


@pytest.mark.parametrize("bad_value", [-1.0, float("nan"), float("inf")])
def test_invalid_metric_fails_closed(bad_value) -> None:
    builder = load_builder()
    store = builder.MemoryBaselineStore(tracts=[("001", 100, -122.4, 37.7)])

    class Analyzer:
        async def analyze(self, lng, lat):
            values = {category: 1.0 for category in builder.CATEGORIES}
            values["parks"] = bad_value
            return values

    result = builder.run_build(store, Analyzer(), expected_count=1, expected_zero_weight=0)

    assert result.exit_code != 0
    assert store.ready_values == [False]
    assert store.upserts == []
