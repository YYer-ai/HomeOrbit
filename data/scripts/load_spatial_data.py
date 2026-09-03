import json
import os
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACTS_PATH = ROOT / "data" / "processed" / "bay-area-tracts.ndjson"
DEFAULT_FACILITIES_PATH = ROOT / "data" / "processed" / "bay-area-facilities.ndjson"
DEFAULT_METADATA_PATH = ROOT / "data" / "processed" / "spatial-data-metadata.json"
SCHEMA_PATH = ROOT / "data" / "sql" / "001_spatial_schema.sql"
EXPECTED_TRACT_COUNT = 1772


def read_metadata(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _read_ndjson(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSON at {path}:{line_number}") from error


def _tract_rows(path: Path) -> Iterator[tuple]:
    for record in _read_ndjson(path):
        yield (
            record["geoid"],
            record["county_name"],
            record["population"],
            record["aland_m2"],
            json.dumps(record["geometry"], separators=(",", ":")),
        )


def _facility_rows(path: Path) -> Iterator[tuple]:
    for record in _read_ndjson(path):
        yield (
            record["osm_key"],
            record["category"],
            record.get("name"),
            json.dumps(record["geometry"], separators=(",", ":")),
            json.dumps(record["analysis_point"], separators=(",", ":")),
        )


def _reject_geometry_failures(
    cursor, product: str, query: str, labels: tuple[str, ...]
):
    cursor.execute(query)
    counts = cursor.fetchone()
    failures = [
        f"{label}={count}" for label, count in zip(labels, counts) if count > 0
    ]
    if failures:
        raise ValueError(
            f"{product} staging geometry validation failed: {', '.join(failures)}"
        )


def load_spatial_data(
    database_url: str | None = None,
    tracts_path: Path = DEFAULT_TRACTS_PATH,
    facilities_path: Path = DEFAULT_FACILITIES_PATH,
    metadata_path: Path = DEFAULT_METADATA_PATH,
) -> dict[str, int]:
    database_url = database_url or os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        raise ValueError("HOMEORBIT_DATABASE_URL is required")

    metadata = read_metadata(Path(metadata_path))
    products = metadata["products"]
    expected_tracts = products["tracts"]["record_count"]
    expected_facilities = products["facilities"]["record_count"]
    if expected_tracts != EXPECTED_TRACT_COUNT:
        raise ValueError(
            f"tract metadata count must be {EXPECTED_TRACT_COUNT}, got {expected_tracts}"
        )

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(schema_sql)
                cursor.execute(
                    """
                    CREATE TEMP TABLE staging_tracts (
                      geoid text PRIMARY KEY,
                      county_name text NOT NULL,
                      population integer NOT NULL CHECK (population >= 0),
                      aland_m2 bigint NOT NULL CHECK (aland_m2 >= 0),
                      geom geometry(MultiPolygon, 4326) NOT NULL,
                      representative_point geometry(Point, 4326) NOT NULL,
                      CHECK (aland_m2 > 0 OR population = 0)
                    ) ON COMMIT DROP;
                    CREATE TEMP TABLE staging_facilities (
                      osm_key text PRIMARY KEY,
                      category text NOT NULL CHECK (
                        category IN (
                          'education','healthcare','daily_shopping','parks','public_transport'
                        )
                      ),
                      name text,
                      geom geometry(Geometry, 4326) NOT NULL,
                      analysis_point geometry(Point, 4326) NOT NULL
                    ) ON COMMIT DROP;
                    """
                )
                cursor.executemany(
                    """
                    INSERT INTO staging_tracts
                      (geoid, county_name, population, aland_m2, geom, representative_point)
                    SELECT %s, %s, %s, %s, parsed.geom, ST_PointOnSurface(parsed.geom)
                    FROM (
                      SELECT ST_Multi(
                        ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)
                      )::geometry(MultiPolygon, 4326) AS geom
                    ) AS parsed
                    """,
                    _tract_rows(Path(tracts_path)),
                )
                cursor.execute("SELECT count(*) FROM staging_tracts")
                tract_count = cursor.fetchone()[0]
                if tract_count != EXPECTED_TRACT_COUNT:
                    raise ValueError(
                        f"tract staging count must be {EXPECTED_TRACT_COUNT}, got {tract_count}"
                    )
                _reject_geometry_failures(
                    cursor,
                    "tract",
                    """
                    SELECT
                      count(*) FILTER (WHERE ST_IsEmpty(geom)),
                      count(*) FILTER (WHERE NOT ST_IsValid(geom)),
                      count(*) FILTER (WHERE ST_IsEmpty(representative_point)),
                      count(*) FILTER (WHERE NOT ST_IsValid(representative_point)),
                      count(*) FILTER (
                        WHERE NOT ST_Contains(geom, representative_point)
                      )
                    FROM staging_tracts
                    """,
                    (
                        "empty_geom",
                        "invalid_geom",
                        "empty_representative_point",
                        "invalid_representative_point",
                        "representative_point_not_inside",
                    ),
                )

                cursor.executemany(
                    """
                    INSERT INTO staging_facilities
                      (osm_key, category, name, geom, analysis_point)
                    VALUES (
                      %s, %s, %s,
                      ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                      ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)::geometry(Point, 4326)
                    )
                    """,
                    _facility_rows(Path(facilities_path)),
                )
                cursor.execute("SELECT count(*) FROM staging_facilities")
                facility_count = cursor.fetchone()[0]
                if facility_count != expected_facilities:
                    raise ValueError(
                        "facility staging count "
                        f"must be {expected_facilities}, got {facility_count}"
                    )
                _reject_geometry_failures(
                    cursor,
                    "facility",
                    """
                    SELECT
                      count(*) FILTER (WHERE ST_IsEmpty(geom)),
                      count(*) FILTER (WHERE NOT ST_IsValid(geom)),
                      count(*) FILTER (WHERE ST_IsEmpty(analysis_point)),
                      count(*) FILTER (WHERE NOT ST_IsValid(analysis_point))
                    FROM staging_facilities
                    """,
                    (
                        "empty_geom",
                        "invalid_geom",
                        "empty_analysis_point",
                        "invalid_analysis_point",
                    ),
                )

                cursor.execute(
                    """
                    INSERT INTO tracts
                      (geoid, county_name, population, aland_m2, geom, representative_point)
                    SELECT geoid, county_name, population, aland_m2, geom, representative_point
                    FROM staging_tracts
                    ON CONFLICT (geoid) DO UPDATE SET
                      county_name = EXCLUDED.county_name,
                      population = EXCLUDED.population,
                      aland_m2 = EXCLUDED.aland_m2,
                      geom = EXCLUDED.geom,
                      representative_point = EXCLUDED.representative_point
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO facilities (osm_key, category, name, geom, analysis_point)
                    SELECT osm_key, category, name, geom, analysis_point
                    FROM staging_facilities
                    ON CONFLICT (osm_key) DO UPDATE SET
                      category = EXCLUDED.category,
                      name = EXCLUDED.name,
                      geom = EXCLUDED.geom,
                      analysis_point = EXCLUDED.analysis_point
                    """
                )
                cursor.executemany(
                    """
                    INSERT INTO dataset_metadata (key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    ((key, Jsonb(value)) for key, value in products.items()),
                )

                cursor.execute("SELECT count(*) FROM tracts")
                live_tract_count = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM facilities")
                live_facility_count = cursor.fetchone()[0]
                if live_tract_count != EXPECTED_TRACT_COUNT:
                    raise ValueError(
                        "live tract count "
                        f"must be {EXPECTED_TRACT_COUNT}, got {live_tract_count}"
                    )
                if live_facility_count != expected_facilities:
                    raise ValueError(
                        "live facility count "
                        f"must be {expected_facilities}, got {live_facility_count}"
                    )

    return {
        "tracts": live_tract_count,
        "facilities": live_facility_count,
        "metadata": len(products),
    }


if __name__ == "__main__":
    result = load_spatial_data()
    print(
        "Imported "
        f"tracts={result['tracts']} facilities={result['facilities']} "
        f"metadata={result['metadata']}"
    )
