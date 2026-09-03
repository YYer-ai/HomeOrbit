from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Final

from psycopg_pool import ConnectionPool

from app.gis.baseline import CATEGORIES, validate_baseline_snapshot
from app.gis.errors import GisError
from app.gis.geojson import is_polygonal_feature_collection


POOL_ACQUIRE_SECONDS: Final = 3.0
ANALYSIS_END_TO_END_SECONDS: Final = 6.0
BASELINE_END_TO_END_SECONDS: Final = 4.0
HEALTH_END_TO_END_SECONDS: Final = 2.0


@dataclass(frozen=True)
class SpatialStats:
    population: float
    counts: dict[str, int]
    park_area_hectares: float
    raw_metrics: dict[str, float]
    facilities: dict[str, Any]


ANALYZE_SQL = """
WITH input_features AS (
  SELECT value AS feature
  FROM jsonb_array_elements(%s::jsonb -> 'features')
), input_geometries AS (
  SELECT ST_SetSRID(ST_GeomFromGeoJSON((feature -> 'geometry')::text), 4326) AS geom
  FROM input_features
), input_status AS (
  SELECT count(*) > 0 AND bool_and(
    NOT ST_IsEmpty(geom)
    AND ST_IsValid(geom)
    AND GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
  ) AS valid
  FROM input_geometries
), raw_catchment AS (
  SELECT ST_UnaryUnion(ST_Collect(geom)) AS geom
  FROM input_geometries
), catchment AS (
  SELECT ST_Multi(raw_catchment.geom)::geometry(MultiPolygon, 4326) AS geom
  FROM raw_catchment CROSS JOIN input_status
  WHERE input_status.valid
    AND NOT ST_IsEmpty(raw_catchment.geom)
    AND ST_IsValid(raw_catchment.geom)
    AND GeometryType(raw_catchment.geom) IN ('POLYGON', 'MULTIPOLYGON')
), population_stats AS (
  SELECT COALESCE(SUM(
    CASE WHEN tract.population = 0 THEN 0.0 ELSE
      tract.population * ST_Area(ST_Intersection(catchment.geom, tract.geom)::geography)
      / NULLIF(ST_Area(tract.geom::geography), 0.0)
    END
  ), 0.0) AS population
  FROM catchment
  JOIN tracts AS tract ON ST_Intersects(catchment.geom, tract.geom)
), point_matches AS (
  SELECT facility.osm_key, facility.category, facility.name, facility.analysis_point AS geom
  FROM catchment
  JOIN facilities AS facility
    ON facility.category <> 'parks' AND ST_Covers(catchment.geom, facility.analysis_point)
), park_matches AS (
  SELECT facility.osm_key, facility.category, facility.name,
         ST_Intersection(catchment.geom, facility.geom) AS geom
  FROM catchment
  JOIN facilities AS facility
    ON facility.category = 'parks' AND ST_Intersects(catchment.geom, facility.geom)
), park_stats AS (
  SELECT COALESCE(
    ST_Area(ST_UnaryUnion(ST_Collect(geom))::geography) / 10000.0,
    0.0
  ) AS hectares
  FROM park_matches
  WHERE NOT ST_IsEmpty(geom)
), matched AS (
  SELECT * FROM point_matches
  UNION ALL
  SELECT * FROM park_matches WHERE NOT ST_IsEmpty(geom)
), facility_collection AS (
  SELECT jsonb_build_object(
    'type', 'FeatureCollection',
    'features', COALESCE(
      jsonb_agg(
        jsonb_build_object(
          'type', 'Feature',
          'geometry', ST_AsGeoJSON(geom, 7)::jsonb,
          'properties', jsonb_build_object('osm_key', osm_key, 'category', category, 'name', name)
        ) ORDER BY category, osm_key
      ),
      '[]'::jsonb
    )
  ) AS geojson
  FROM matched
)
SELECT
  EXISTS (SELECT 1 FROM catchment),
  population_stats.population,
  (SELECT count(*) FROM point_matches WHERE category = 'education'),
  (SELECT count(*) FROM point_matches WHERE category = 'healthcare'),
  (SELECT count(*) FROM point_matches WHERE category = 'daily_shopping'),
  park_stats.hectares,
  (SELECT count(*) FROM point_matches WHERE category = 'public_transport'),
  facility_collection.geojson
FROM population_stats CROSS JOIN park_stats CROSS JOIN facility_collection
"""


class SpatialRepository:
    def __init__(
        self,
        pool: ConnectionPool,
        *,
        expected_baseline_count: int = 1772,
        expected_zero_weight: int = 11,
    ) -> None:
        self._pool = pool
        self._expected_baseline_count = expected_baseline_count
        self._expected_zero_weight = expected_zero_weight

    async def analyze_catchment(self, geojson: dict[str, Any]) -> SpatialStats:
        if not is_polygonal_feature_collection(geojson):
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503)
        try:
            row = await asyncio.wait_for(
                asyncio.to_thread(self._analyze_row, geojson),
                timeout=ANALYSIS_END_TO_END_SECONDS,
            )
        except GisError:
            raise
        except Exception as exc:
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503) from exc
        if row is None or row[0] is not True:
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503)

        population = float(row[1])
        counts = {
            "education": int(row[2]),
            "healthcare": int(row[3]),
            "daily_shopping": int(row[4]),
            "public_transport": int(row[6]),
        }
        park_area_hectares = float(row[5])
        denominator = population / 1000.0
        raw_metrics = {
            category: (counts[category] / denominator if denominator > 0 else 0.0)
            for category in counts
        }
        raw_metrics["parks"] = park_area_hectares / denominator if denominator > 0 else 0.0
        values = [population, park_area_hectares, *raw_metrics.values()]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503)
        facilities = row[7]
        if not isinstance(facilities, dict):
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503)
        return SpatialStats(population, counts, park_area_hectares, raw_metrics, facilities)

    def _configure_transaction(self, cursor, statement_ms: int, lock_ms: int) -> None:
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{statement_ms}ms",))
        cursor.execute("SELECT set_config('lock_timeout', %s, true)", (f"{lock_ms}ms",))

    def _analyze_row(self, geojson: dict[str, Any]):
        with self._pool.connection(timeout=POOL_ACQUIRE_SECONDS) as connection:
            with connection.cursor() as cursor:
                self._configure_transaction(cursor, 4500, 1000)
                cursor.execute(ANALYZE_SQL, (json.dumps(geojson, separators=(",", ":")),))
                return cursor.fetchone()

    async def baseline_samples(self) -> dict[str, list[tuple[float, float]]]:
        try:
            tracts, rows, metadata = await asyncio.wait_for(
                asyncio.to_thread(self._baseline_snapshot_sync),
                timeout=BASELINE_END_TO_END_SECONDS,
            )
        except Exception as exc:
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503) from exc
        if not self._snapshot_valid(tracts, rows, metadata):
            raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503)
        samples = {category: [] for category in CATEGORIES}
        for geoid in sorted(rows):
            weight, metrics = rows[geoid]
            for category in CATEGORIES:
                samples[category].append((float(metrics[category]), float(weight)))
        return samples

    def _snapshot_valid(self, tracts, rows, metadata) -> bool:
        return validate_baseline_snapshot(
            tracts,
            rows,
            metadata,
            expected_count=self._expected_baseline_count,
            expected_zero_weight=self._expected_zero_weight,
        )

    def _baseline_snapshot_sync(self):
        with self._pool.connection(timeout=POOL_ACQUIRE_SECONDS) as connection:
            with connection.cursor() as cursor:
                self._configure_transaction(cursor, 2000, 750)
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
                cursor.execute("SELECT value FROM dataset_metadata WHERE key = 'facility_baseline'")
                metadata_row = cursor.fetchone()
                return tracts, rows, metadata_row[0] if metadata_row else None

    async def check(self) -> bool:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._check_sync),
                timeout=HEALTH_END_TO_END_SECONDS,
            )
        except Exception:
            return False

    def _check_sync(self):
        with self._pool.connection(timeout=1.0) as connection:
            with connection.cursor() as cursor:
                self._configure_transaction(cursor, 500, 250)
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)

    async def baseline_ready(self) -> bool:
        try:
            tracts, rows, metadata = await asyncio.wait_for(
                asyncio.to_thread(self._baseline_snapshot_sync),
                timeout=HEALTH_END_TO_END_SECONDS,
            )
            return self._snapshot_valid(tracts, rows, metadata)
        except Exception:
            return False
