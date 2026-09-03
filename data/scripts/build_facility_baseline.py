from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app.config import Settings  # noqa: E402
from app.gis.baseline import (  # noqa: E402
    BASELINE_HASH_ALGORITHM,
    BASELINE_HASH_FORMAT,
    BASELINE_VERSION,
    CATEGORIES,
    baseline_sha256,
    validate_baseline_snapshot,
    validated_resume_geoids,
)
from app.gis.repository import SpatialRepository  # noqa: E402
from app.gis.schemas import Origin  # noqa: E402
from app.gis.valhalla import ValhallaClient  # noqa: E402


EXPECTED_COUNT = 1772
EXPECTED_ZERO_WEIGHT = 11
TRACT_SPATIAL_EXTENT = (-123.632497, 36.893033, -121.208228, 38.863994)


@dataclass(frozen=True)
class BuildResult:
    processed: int
    skipped: int
    failures: list[dict[str, str]]
    exit_code: int


def _ready_metadata(
    record_count: int,
    zero_weight_count: int,
    version: str,
    rows,
) -> dict[str, Any]:
    return {
        "baseline_ready": True,
        "record_count": record_count,
        "zero_weight_count": zero_weight_count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "mode": "pedestrian",
        "minutes": 15,
        "resume": True,
        "sha256": baseline_sha256(rows),
        "hash_algorithm": BASELINE_HASH_ALGORITHM,
        "hash_format": BASELINE_HASH_FORMAT,
    }


class MemoryBaselineStore:
    def __init__(self, tracts, existing=None, metadata=None):
        self._tracts = list(tracts)
        self._rows = dict(existing or {})
        self.upserts = []
        self.commit_count = 0
        self.ready_values = []
        self.metadata = dict(metadata or {})

    def tracts(self):
        return sorted(self._tracts)

    def existing_rows(self):
        return dict(self._rows)

    def snapshot(self):
        tracts = {geoid: population for geoid, population, _, _ in self._tracts}
        return tracts, self.existing_rows(), dict(self.metadata)

    def set_not_ready(self):
        self.ready_values.append(False)
        self.metadata["baseline_ready"] = False

    def upsert(self, geoid, weight, metrics):
        copied = dict(metrics)
        self.upserts.append((geoid, weight, copied))
        self._rows[geoid] = (weight, copied)
        self.commit_count += 1

    def validate_and_mark_ready(self, expected_count, expected_zero_weight, version):
        tracts, rows, _ = self.snapshot()
        metadata = _ready_metadata(expected_count, expected_zero_weight, version, rows)
        if not validate_baseline_snapshot(
            tracts,
            rows,
            metadata,
            expected_count=expected_count,
            expected_zero_weight=expected_zero_weight,
        ):
            return False
        self.metadata = metadata
        self.ready_values.append(True)
        return True


class PostgresBaselineStore:
    def __init__(self, database_url: str):
        self.pool = ConnectionPool(
            conninfo=database_url,
            open=False,
            min_size=0,
            max_size=4,
            kwargs={"connect_timeout": 3},
        )
        self.pool.open()

    def close(self):
        self.pool.close()

    @staticmethod
    def _configure(cursor, statement_ms=3000, lock_ms=750):
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{statement_ms}ms",))
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", (f"{lock_ms}ms",))

    def tracts(self):
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute(
                    "SELECT geoid, population, ST_X(representative_point), ST_Y(representative_point) "
                    "FROM tracts ORDER BY geoid"
                )
                return cursor.fetchall()

    def existing_rows(self):
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute(
                    "SELECT geoid, population_weight, education, healthcare, daily_shopping, parks, public_transport "
                    "FROM facility_baseline_samples ORDER BY geoid"
                )
                return {
                    row[0]: (row[1], dict(zip(CATEGORIES, row[2:])))
                    for row in cursor.fetchall()
                }

    def snapshot(self):
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute("SELECT geoid, population FROM tracts ORDER BY geoid")
                tracts = dict(cursor.fetchall())
                cursor.execute(
                    "SELECT geoid, population_weight, education, healthcare, daily_shopping, parks, public_transport "
                    "FROM facility_baseline_samples ORDER BY geoid"
                )
                rows = {
                    row[0]: (row[1], dict(zip(CATEGORIES, row[2:])))
                    for row in cursor.fetchall()
                }
                cursor.execute("SELECT value FROM dataset_metadata WHERE key='facility_baseline'")
                metadata_row = cursor.fetchone()
                return tracts, rows, metadata_row[0] if metadata_row else None

    def set_not_ready(self):
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute(
                    "INSERT INTO dataset_metadata (key, value) VALUES ('facility_baseline', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = dataset_metadata.value || EXCLUDED.value",
                    (Jsonb({"baseline_ready": False}),),
                )

    def upsert(self, geoid, weight, metrics):
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute(
                    "INSERT INTO facility_baseline_samples "
                    "(geoid, population_weight, education, healthcare, daily_shopping, parks, public_transport) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (geoid) DO UPDATE SET population_weight=EXCLUDED.population_weight, "
                    "education=EXCLUDED.education, healthcare=EXCLUDED.healthcare, "
                    "daily_shopping=EXCLUDED.daily_shopping, parks=EXCLUDED.parks, "
                    "public_transport=EXCLUDED.public_transport",
                    (geoid, weight, *(metrics[category] for category in CATEGORIES)),
                )

    def validate_and_mark_ready(self, expected_count, expected_zero_weight, version):
        tracts, rows, _ = self.snapshot()
        metadata = _ready_metadata(expected_count, expected_zero_weight, version, rows)
        if not validate_baseline_snapshot(
            tracts,
            rows,
            metadata,
            expected_count=expected_count,
            expected_zero_weight=expected_zero_weight,
        ):
            return False
        with self.pool.connection(timeout=3.0) as connection:
            with connection.cursor() as cursor:
                self._configure(cursor)
                cursor.execute(
                    "INSERT INTO dataset_metadata (key, value) VALUES ('facility_baseline', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                    (Jsonb(metadata),),
                )
        return True


class LiveAnalyzer:
    def __init__(self, client, repository):
        self.client = client
        self.repository = repository

    async def analyze(self, lng, lat):
        catchment = await self.client.isochrone(baseline_origin(lng, lat), "walking", 15)
        return (await self.repository.analyze_catchment(catchment)).raw_metrics


def baseline_origin(lng: float, lat: float) -> Origin:
    values = (float(lng), float(lat))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("baseline coordinate must be finite")
    min_lng, min_lat, max_lng, max_lat = TRACT_SPATIAL_EXTENT
    if not (min_lng <= values[0] <= max_lng and min_lat <= values[1] <= max_lat):
        raise ValueError("baseline coordinate must be within tract spatial extent")
    return Origin.model_construct(lng=values[0], lat=values[1])


def _valid_metrics(metrics: Any) -> bool:
    return (
        isinstance(metrics, dict)
        and set(metrics) == set(CATEGORIES)
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
            for value in metrics.values()
        )
    )


def run_build(
    store,
    analyzer,
    resume=False,
    expected_count=EXPECTED_COUNT,
    expected_zero_weight=EXPECTED_ZERO_WEIGHT,
    limit=None,
    dry_run=False,
):
    return asyncio.run(
        _run_build(store, analyzer, resume, expected_count, expected_zero_weight, limit, dry_run)
    )


async def _run_build(store, analyzer, resume, expected_count, expected_zero_weight, limit, dry_run):
    tract_rows = list(store.tracts())
    tracts = {row[0]: row[1] for row in tract_rows}
    if len(tract_rows) != expected_count or len(tracts) != expected_count:
        return BuildResult(0, 0, [{"geoid": "*", "reason": "TRACT_SET_INCOMPLETE"}], 1)

    existing_rows = store.existing_rows() if resume else {}
    if resume:
        snapshot_tracts, snapshot_rows, snapshot_metadata = store.snapshot()
        if validate_baseline_snapshot(
            snapshot_tracts,
            snapshot_rows,
            snapshot_metadata,
            expected_count=expected_count,
            expected_zero_weight=expected_zero_weight,
        ):
            return BuildResult(0, expected_count, [], 0)
    valid_existing = validated_resume_geoids(tracts, existing_rows) if resume else set()
    candidates = [row for row in tract_rows if row[0] not in valid_existing]
    if limit is not None:
        candidates = candidates[:limit]
    if not dry_run:
        store.set_not_ready()

    processed = 0
    failures = []
    for geoid, population_weight, lng, lat in candidates:
        try:
            metrics = await analyzer.analyze(lng, lat)
            if not _valid_metrics(metrics):
                raise ValueError("invalid metrics")
            if not dry_run:
                store.upsert(geoid, population_weight, metrics)
            processed += 1
        except Exception:
            failures.append({"geoid": geoid, "reason": "ANALYSIS_FAILED"})
    if failures:
        return BuildResult(processed, len(valid_existing), failures, 1)
    if dry_run:
        return BuildResult(processed, len(valid_existing), [], 0)
    if not store.validate_and_mark_ready(expected_count, expected_zero_weight, BASELINE_VERSION):
        return BuildResult(processed, len(valid_existing), [{"geoid": "*", "reason": "BASELINE_INCOMPLETE"}], 1)
    return BuildResult(processed, len(valid_existing), [], 0)


async def _run_live(store, settings, args):
    async with httpx.AsyncClient(trust_env=False) as http_client:
        analyzer = LiveAnalyzer(
            ValhallaClient(http_client, settings),
            SpatialRepository(store.pool),
        )
        return await _run_build(
            store,
            analyzer,
            args.resume,
            EXPECTED_COUNT,
            EXPECTED_ZERO_WEIGHT,
            args.limit,
            args.dry_run,
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings()
    store = PostgresBaselineStore(settings.database_url)
    try:
        result = asyncio.run(_run_live(store, settings, args))
    finally:
        store.close()
    print(f"processed={result.processed} skipped={result.skipped} failures={len(result.failures)}")
    for failure in result.failures[:20]:
        print(f"failed geoid={failure['geoid']} reason={failure['reason']}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
