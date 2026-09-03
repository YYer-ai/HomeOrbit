import importlib.util
import hashlib
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
BASELINE_HASH_ALGORITHM = "sha256"
BASELINE_HASH_FORMAT = "homeorbit-facility-baseline-jsonl-v2"
CATEGORIES = (
    "education",
    "healthcare",
    "daily_shopping",
    "parks",
    "public_transport",
)


SCHEMA_PATH = ROOT / "data" / "sql" / "001_spatial_schema.sql"
LOADER_PATH = ROOT / "data" / "scripts" / "load_spatial_data.py"
TRACTS_PATH = ROOT / "data" / "processed" / "bay-area-tracts.ndjson"
FACILITIES_PATH = ROOT / "data" / "processed" / "bay-area-facilities.ndjson"
METADATA_PATH = ROOT / "data" / "processed" / "spatial-data-metadata.json"


def _independent_baseline_sha256(rows):
    lines = [BASELINE_HASH_FORMAT]
    for geoid in sorted(rows):
        weight, metrics = rows[geoid]
        canonical_metrics = []
        for category in CATEGORIES:
            value = metrics[category]
            assert not isinstance(value, bool)
            assert isinstance(value, (int, float)) and value >= 0 and math.isfinite(value)
            if isinstance(value, int) or value.is_integer():
                canonical_metrics.append(f"i:{int(value)}")
            else:
                canonical_metrics.append(f"f:{value.hex()}")
        lines.append(
            json.dumps(
                [geoid, weight, *canonical_metrics],
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_module():
    spec = importlib.util.spec_from_file_location("load_spatial_data", LOADER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {LOADER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loader = _load_module()


def test_schema_has_required_tables_constraints_and_indexes():
    sql = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for table in (
        "tracts",
        "facilities",
        "facility_baseline_samples",
        "dataset_metadata",
    ):
        assert f"create table if not exists {table}" in sql

    assert "check (aland_m2 >= 0)" in sql
    assert "check (aland_m2 > 0 or population = 0)" in sql
    assert "daily_shopping" in sql
    assert sql.count("using gist") == 3


def test_read_metadata_uses_current_product_counts():
    metadata = loader.read_metadata(METADATA_PATH)
    assert metadata["products"]["tracts"]["record_count"] == 1772
    assert metadata["products"]["facilities"]["record_count"] == 35052


@pytest.fixture(scope="module")
def database_url():
    value = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not value:
        pytest.skip("HOMEORBIT_DATABASE_URL is required for integration tests")
    return value


def _replace_first_record(source_path, destination_path, transform):
    original = None
    replacement = None
    with source_path.open(encoding="utf-8") as source, destination_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as destination:
        for line in source:
            record = json.loads(line)
            if original is None:
                original = record
                replacement = transform(dict(record))
                record = replacement
            destination.write(json.dumps(record, separators=(",", ":")) + "\n")
    return original, replacement


def _database_snapshot(database_url, geoid, osm_key):
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM tracts")
            tract_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM facilities")
            facility_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT county_name, population, aland_m2, "
                "ST_AsEWKB(geom), ST_AsEWKB(representative_point) "
                "FROM tracts WHERE geoid = %s",
                (geoid,),
            )
            tract = cursor.fetchone()
            cursor.execute(
                "SELECT category, name, ST_AsEWKB(geom), ST_AsEWKB(analysis_point) "
                "FROM facilities WHERE osm_key = %s",
                (osm_key,),
            )
            facility = cursor.fetchone()
            cursor.execute("SELECT key, value FROM dataset_metadata ORDER BY key")
            metadata = cursor.fetchall()
    return {
        "tract_count": tract_count,
        "facility_count": facility_count,
        "tract": tract,
        "facility": facility,
        "metadata": metadata,
    }


@contextmanager
def _fail_metadata_writes(database_url, message, required_tract=None):
    function_name = "task3_test_fail_metadata_write"
    trigger_name = "task3_test_fail_metadata_write"
    prerequisite = sql.SQL("")
    if required_tract:
        geoid, county_name = required_tract
        prerequisite = sql.SQL(
            """
            IF (SELECT tracts.county_name FROM tracts WHERE geoid = {})
               IS DISTINCT FROM {} THEN
              RAISE EXCEPTION 'test trigger fired before expected tract upsert';
            END IF;
            """
        ).format(sql.Literal(geoid), sql.Literal(county_name))
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = %s)",
                (function_name,),
            )
            assert cursor.fetchone()[0] is False
            cursor.execute(
                sql.SQL(
                    """
                CREATE FUNCTION {}() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                  {}
                  RAISE EXCEPTION {};
                END
                $$;
                CREATE TRIGGER {}
                BEFORE INSERT OR UPDATE ON dataset_metadata
                FOR EACH ROW EXECUTE FUNCTION {}();
                """
                ).format(
                    sql.Identifier(function_name),
                    prerequisite,
                    sql.Literal(message),
                    sql.Identifier(trigger_name),
                    sql.Identifier(function_name),
                )
            )
    try:
        yield
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DROP TRIGGER IF EXISTS {trigger_name} ON dataset_metadata; "
                    f"DROP FUNCTION IF EXISTS {function_name}()"
                )
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = %s), "
                    "EXISTS (SELECT 1 FROM pg_proc WHERE proname = %s)",
                    (trigger_name, function_name),
                )
                assert cursor.fetchone() == (False, False)


@pytest.mark.integration
def test_real_import_is_idempotent_and_complete(database_url):
    first = loader.load_spatial_data(database_url=database_url)
    second = loader.load_spatial_data(database_url=database_url)

    assert first == {"tracts": 1772, "facilities": 35052, "metadata": 3}
    assert second == first

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*), count(DISTINCT geoid) FROM tracts")
            assert cursor.fetchone() == (1772, 1772)
            cursor.execute("SELECT count(*), count(DISTINCT osm_key) FROM facilities")
            assert cursor.fetchone() == (35052, 35052)
            cursor.execute(
                "SELECT count(*) FROM tracts "
                "WHERE NOT ST_Contains(geom, representative_point)"
            )
            assert cursor.fetchone()[0] == 0


@pytest.mark.integration
def test_database_constraints_accept_only_valid_domain_rows(database_url):
    with psycopg.connect(database_url) as connection:
        with connection.transaction(force_rollback=True):
            with connection.cursor() as cursor:
                with pytest.raises(psycopg.errors.CheckViolation):
                    with connection.transaction():
                        cursor.execute(
                            "INSERT INTO facilities "
                            "(osm_key, category, geom, analysis_point) VALUES "
                            "('invalid-category', 'unknown', "
                            "ST_SetSRID(ST_Point(0, 0), 4326), "
                            "ST_SetSRID(ST_Point(0, 0), 4326))"
                        )

                with pytest.raises(psycopg.errors.CheckViolation):
                    with connection.transaction():
                        cursor.execute(
                            "INSERT INTO tracts "
                            "(geoid, county_name, population, aland_m2, geom, representative_point) "
                            "VALUES ('invalid-water', 'Test', 1, 0, "
                            "ST_Multi(ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',4326)), "
                            "ST_SetSRID(ST_Point(0.5,0.5),4326))"
                        )

                cursor.execute(
                    "INSERT INTO tracts "
                    "(geoid, county_name, population, aland_m2, geom, representative_point) "
                    "VALUES ('valid-water', 'Test', 0, 0, "
                    "ST_Multi(ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',4326)), "
                    "ST_SetSRID(ST_Point(0.5,0.5),4326))"
                )


@pytest.mark.integration
def test_metadata_is_latest_and_spatial_indexes_exist(database_url):
    expected_products = loader.read_metadata(METADATA_PATH)["products"]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT key, value FROM dataset_metadata ORDER BY key")
            actual = {key: value for key, value in cursor.fetchall()}
            expected_keys = {"tracts", "facilities", "tract_density_geojson"}
            assert expected_keys == set(expected_products)
            assert expected_keys <= set(actual)
            assert set(actual) <= expected_keys | {"facility_baseline"}
            assert {key: actual[key] for key in expected_keys} == expected_products

            if "facility_baseline" in actual:
                baseline = actual["facility_baseline"]
                assert baseline["baseline_ready"] is True
                assert type(baseline["record_count"]) is int
                assert baseline["record_count"] == 1772
                assert type(baseline["zero_weight_count"]) is int
                assert baseline["zero_weight_count"] == 11
                assert type(baseline["mode"]) is str
                assert baseline["mode"] == "pedestrian"
                assert type(baseline["minutes"]) is int
                assert baseline["minutes"] == 15
                assert type(baseline["version"]) is str
                assert baseline["version"] == "facility-baseline-v1"
                assert type(baseline["generated_at"]) is str
                generated_at = datetime.fromisoformat(baseline["generated_at"])
                assert generated_at.tzinfo is not None
                assert generated_at.utcoffset() is not None

            cursor.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = current_schema() AND indexdef ILIKE '%USING gist%'"
            )
            indexes = {row[0] for row in cursor.fetchall()}
            assert {
                "idx_tracts_geom",
                "idx_facilities_analysis_point",
                "idx_facilities_geom",
            } <= indexes


@pytest.mark.integration
def test_facility_baseline_metadata_sha_matches_canonical_rows(database_url):
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT value FROM dataset_metadata WHERE key='facility_baseline'")
            metadata = cursor.fetchone()[0]
            hash_keys = {"sha256", "hash_algorithm", "hash_format"}
            present_hash_keys = hash_keys & set(metadata)
            if not present_hash_keys:
                pytest.skip("live facility_baseline metadata SHA upgrade is reserved for the controller")
            assert present_hash_keys == hash_keys

            cursor.execute(
                "SELECT geoid, population_weight, education, healthcare, daily_shopping, parks, public_transport "
                "FROM facility_baseline_samples ORDER BY geoid"
            )
            rows = {
                row[0]: (row[1], dict(zip(CATEGORIES, row[2:])))
                for row in cursor.fetchall()
            }

    assert type(metadata["sha256"]) is str
    assert len(metadata["sha256"]) == 64
    assert all(character in "0123456789abcdef" for character in metadata["sha256"])
    assert metadata["hash_algorithm"] == BASELINE_HASH_ALGORITHM
    assert metadata["hash_format"] == BASELINE_HASH_FORMAT
    assert metadata["sha256"] == _independent_baseline_sha256(rows)


@pytest.mark.integration
def test_count_mismatch_rolls_back_without_changing_live_data(database_url, tmp_path):
    metadata = loader.read_metadata(METADATA_PATH)
    metadata["products"]["facilities"]["record_count"] -= 1
    bad_metadata_path = tmp_path / "spatial-data-metadata.json"
    bad_metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM tracts")
            before_tracts = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM facilities")
            before_facilities = cursor.fetchone()[0]
            cursor.execute("SELECT key, value FROM dataset_metadata ORDER BY key")
            before_metadata = cursor.fetchall()

    with pytest.raises(ValueError, match="facility staging count"):
        loader.load_spatial_data(database_url=database_url, metadata_path=bad_metadata_path)

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM tracts")
            assert cursor.fetchone()[0] == before_tracts
            cursor.execute("SELECT count(*) FROM facilities")
            assert cursor.fetchone()[0] == before_facilities
            cursor.execute("SELECT key, value FROM dataset_metadata ORDER BY key")
            assert cursor.fetchall() == before_metadata


@pytest.mark.integration
@pytest.mark.parametrize("corrupt_product", ["tracts", "facilities"])
def test_invalid_staging_geometry_rolls_back_all_live_data(
    database_url, tmp_path, corrupt_product
):
    first_tract = json.loads(TRACTS_PATH.read_text(encoding="utf-8").splitlines()[0])
    first_facility = json.loads(
        FACILITIES_PATH.read_text(encoding="utf-8").splitlines()[0]
    )
    tracts_path = TRACTS_PATH
    facilities_path = FACILITIES_PATH

    if corrupt_product == "tracts":
        tracts_path = tmp_path / "tracts.ndjson"

        def corrupt_tract(record):
            record["geometry"] = {"type": "Polygon", "coordinates": []}
            return record

        _replace_first_record(TRACTS_PATH, tracts_path, corrupt_tract)
    else:
        facilities_path = tmp_path / "facilities.ndjson"

        def corrupt_facility(record):
            record["geometry"] = {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
            }
            record["analysis_point"] = {"type": "Point", "coordinates": []}
            return record

        _replace_first_record(FACILITIES_PATH, facilities_path, corrupt_facility)

    before = _database_snapshot(
        database_url, first_tract["geoid"], first_facility["osm_key"]
    )
    expected_product = "tract" if corrupt_product == "tracts" else "facility"
    with _fail_metadata_writes(database_url, "test guard: metadata write reached"):
        with pytest.raises(
            ValueError, match=f"{expected_product} staging geometry"
        ) as error:
            loader.load_spatial_data(
                database_url=database_url,
                tracts_path=tracts_path,
                facilities_path=facilities_path,
            )
    if corrupt_product == "tracts":
        assert "empty_geom=1" in str(error.value)
        assert "empty_representative_point=1" in str(error.value)
    else:
        assert "invalid_geom=1" in str(error.value)
        assert "empty_analysis_point=1" in str(error.value)
    after = _database_snapshot(
        database_url, first_tract["geoid"], first_facility["osm_key"]
    )
    assert after == before


@pytest.mark.integration
def test_late_metadata_failure_rolls_back_prior_live_upserts(database_url, tmp_path):
    changed_tracts_path = tmp_path / "tracts.ndjson"

    def change_county_name(record):
        record["county_name"] = record["county_name"] + " rollback-test"
        return record

    original, changed = _replace_first_record(
        TRACTS_PATH, changed_tracts_path, change_county_name
    )
    first_facility = json.loads(
        FACILITIES_PATH.read_text(encoding="utf-8").splitlines()[0]
    )
    before = _database_snapshot(
        database_url, original["geoid"], first_facility["osm_key"]
    )
    assert before["tract"][0] == original["county_name"]
    assert changed["county_name"] != original["county_name"]

    with _fail_metadata_writes(
        database_url,
        "test late metadata failure",
        required_tract=(original["geoid"], changed["county_name"]),
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="late metadata failure"):
            loader.load_spatial_data(
                database_url=database_url, tracts_path=changed_tracts_path
            )

    after = _database_snapshot(
        database_url, original["geoid"], first_facility["osm_key"]
    )
    assert after == before
