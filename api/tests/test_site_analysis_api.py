import json
import os
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from psycopg_pool import ConnectionPool, PoolTimeout

from app.config import Settings
from app.gis.errors import GisError
from app.gis.repository import SpatialRepository, SpatialStats
from app.gis.router import get_app_settings, get_spatial_repository, get_valhalla_client
from app.gis.scoring import build_scores
from app.gis.schemas import Origin, SiteAnalysisResponse
from app.main import app, lifespan


POLYGON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"contour": 15},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-122.43, 37.76], [-122.40, 37.76], [-122.40, 37.79], [-122.43, 37.79], [-122.43, 37.76]]],
            },
        }
    ],
}
CATEGORIES = ("education", "healthcare", "daily_shopping", "parks", "public_transport")


@contextmanager
def real_repository(database_url):
    pool = ConnectionPool(
        database_url,
        open=False,
        min_size=0,
        max_size=2,
        kwargs={"connect_timeout": 3},
    )
    pool.open()
    try:
        yield SpatialRepository(pool)
    finally:
        pool.close()


def test_public_origin_keeps_approved_service_coverage() -> None:
    with pytest.raises(ValueError):
        Origin(lng=-121.6267689, lat=37.6555575)


def _valid_response_payload():
    scores = {category: 50.0 for category in CATEGORIES}
    metrics = {
        category: {
            "value": 1.0,
            "unit": "hectares_per_1000_people" if category == "parks" else "count_per_1000_people",
            "score": 50.0,
            **({"raw_area_hectares": 2.0} if category == "parks" else {"raw_count": 1}),
        }
        for category in CATEGORIES
    }
    return {
        "origin": {"lng": -122.4194, "lat": 37.7749},
        "catchment": POLYGON,
        "population": 1000.0,
        "population_method": "tract_population_weighted_by_intersection_area",
        "metrics": metrics,
        "facilities": {"type": "FeatureCollection", "features": []},
        "scores": scores,
        "score_status": "ok",
        "data_version": "test-v1",
        "acs_year": 2023,
        "limitations": [],
        "scoring_method": "bay_area_population_weighted_percentile",
    }


def test_site_analysis_openapi_declares_body_and_stable_errors() -> None:
    operation = app.openapi()["paths"]["/gis/site-analysis"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert {"200", "400", "503"} <= set(operation["responses"])
    assert "422" not in operation["responses"]
    assert "GisErrorResponse" in str(operation["responses"]["400"])
    assert "GisErrorResponse" in str(operation["responses"]["503"])
    assert "422" in app.openapi()["paths"]["/gis/isochrone"]["get"]["responses"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_metric",
        "extra_metric",
        "nan_value",
        "bool_value",
        "bool_score",
        "negative_count",
        "bool_count",
        "fractional_count",
        "wrong_point_unit",
        "missing_park_area",
        "bool_park_area",
        "wrong_park_unit",
    ],
)
def test_site_analysis_response_rejects_malformed_metric_contract(mutation) -> None:
    payload = _valid_response_payload()
    if mutation == "missing_metric":
        payload["metrics"].pop("education")
    elif mutation == "extra_metric":
        payload["metrics"]["other"] = payload["metrics"]["education"]
    elif mutation == "nan_value":
        payload["metrics"]["education"]["value"] = float("nan")
    elif mutation == "bool_value":
        payload["metrics"]["education"]["value"] = True
    elif mutation == "bool_score":
        payload["metrics"]["education"]["score"] = True
        payload["scores"]["education"] = True
    elif mutation == "negative_count":
        payload["metrics"]["education"]["raw_count"] = -1
    elif mutation == "bool_count":
        payload["metrics"]["education"]["raw_count"] = True
    elif mutation == "fractional_count":
        payload["metrics"]["education"]["raw_count"] = 1.5
    elif mutation == "wrong_point_unit":
        payload["metrics"]["education"]["unit"] = "hectares_per_1000_people"
    elif mutation == "missing_park_area":
        payload["metrics"]["parks"].pop("raw_area_hectares")
    elif mutation == "bool_park_area":
        payload["metrics"]["parks"]["raw_area_hectares"] = True
    else:
        payload["metrics"]["parks"]["unit"] = "count_per_1000_people"
    with pytest.raises(ValueError):
        SiteAnalysisResponse.model_validate(payload)


def test_site_analysis_response_accepts_exact_five_category_metric_contract() -> None:
    assert SiteAnalysisResponse.model_validate(_valid_response_payload()).score_status == "ok"


class StubValhalla:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def isochrone(self, origin, mode, minutes):
        self.calls.append((origin, mode, minutes))
        if self.error:
            raise self.error
        return POLYGON


class StubRepository:
    def __init__(self, population=1000.0, error=None):
        self.error = error
        self.calls = []
        self.stats = SpatialStats(
            population=population,
            counts={category: index + 1 for index, category in enumerate(CATEGORIES) if category != "parks"},
            park_area_hectares=2.5,
            raw_metrics={category: float(index + 1) for index, category in enumerate(CATEGORIES)},
            facilities={"type": "FeatureCollection", "features": []},
        )

    async def analyze_catchment(self, geojson):
        self.calls.append(geojson)
        if self.error:
            raise self.error
        return self.stats

    async def baseline_samples(self):
        return {category: [(float(index + 1), 100)] for index, category in enumerate(CATEGORIES)}


async def post_site(payload, valhalla, repository):
    app.dependency_overrides[get_valhalla_client] = lambda: valhalla
    app.dependency_overrides[get_spatial_repository] = lambda: repository
    app.dependency_overrides[get_app_settings] = lambda: Settings(dataset_version="test-v1", acs_year=2023)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/gis/site-analysis", json=payload)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_site_analysis_always_uses_one_walking_fifteen_minute_contour() -> None:
    valhalla = StubValhalla()
    repository = StubRepository()
    response = await post_site({"origin": {"lng": -122.4194, "lat": 37.7749}}, valhalla, repository)

    assert response.status_code == 200
    body = response.json()
    assert len(valhalla.calls) == 1
    assert valhalla.calls[0][1:] == ("walking", 15)
    assert repository.calls == [POLYGON]
    assert set(body["metrics"]) == set(CATEGORIES)
    assert body["metrics"]["education"]["raw_count"] == 1
    assert body["metrics"]["parks"]["raw_area_hectares"] == 2.5
    assert body["score_status"] == "ok"


@pytest.mark.asyncio
async def test_site_analysis_low_population_returns_raw_supply_without_scores() -> None:
    response = await post_site(
        {"origin": {"lng": -122.4194, "lat": 37.7749}}, StubValhalla(), StubRepository(population=99.9)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["score_status"] == "insufficient_population"
    assert all(value is None for value in body["scores"].values())
    assert body["metrics"]["healthcare"]["raw_count"] == 2


@pytest.mark.asyncio
async def test_site_analysis_maps_valhalla_and_postgis_failures_safely() -> None:
    valhalla_response = await post_site(
        {"origin": {"lng": -122.4194, "lat": 37.7749}},
        StubValhalla(GisError("VALHALLA_UNAVAILABLE", status_code=503)),
        StubRepository(),
    )
    postgis_response = await post_site(
        {"origin": {"lng": -122.4194, "lat": 37.7749}},
        StubValhalla(),
        StubRepository(error=RuntimeError("postgresql://secret/C:/private/query.sql")),
    )

    assert valhalla_response.json()["code"] == "VALHALLA_UNAVAILABLE"
    assert postgis_response.status_code == 503
    assert postgis_response.json()["code"] == "SPATIAL_DATA_UNAVAILABLE"
    assert "secret" not in postgis_response.text
    assert "private" not in postgis_response.text


@pytest.mark.asyncio
async def test_site_analysis_maps_malformed_baseline_to_safe_spatial_error() -> None:
    class MalformedBaselineRepository(StubRepository):
        async def baseline_samples(self):
            return {category: [(float("nan"), 100)] for category in CATEGORIES}

    response = await post_site(
        {"origin": {"lng": -122.4194, "lat": 37.7749}}, StubValhalla(), MalformedBaselineRepository()
    )

    assert response.status_code == 503
    assert response.json()["code"] == "SPATIAL_DATA_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"origin": {"lng": -118.24, "lat": 34.05}},
        {"origin": {"lng": "nan", "lat": 37.77}},
        {"origin": {"lng": -122.41, "lat": 37.77}, "minutes": 30},
    ],
)
async def test_site_analysis_rejects_invalid_or_extra_input_with_stable_error(payload) -> None:
    response = await post_site(payload, StubValhalla(), StubRepository())
    assert response.status_code == 400
    assert response.json()["code"] == "POINT_OUTSIDE_COVERAGE"
    assert set(response.json()) == {"code", "message"}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"[1,2,3]", b"{"])
async def test_site_analysis_body_validation_never_returns_bare_422(content) -> None:
    app.dependency_overrides[get_valhalla_client] = StubValhalla
    app.dependency_overrides[get_spatial_repository] = StubRepository
    app.dependency_overrides[get_app_settings] = lambda: Settings(dataset_version="test-v1")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/gis/site-analysis",
                content=content,
                headers={"content-type": "application/json"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == "POINT_OUTSIDE_COVERAGE"
    assert set(response.json()) == {"code", "message"}


@pytest.mark.asyncio
async def test_postgis_failure_does_not_break_isochrone() -> None:
    valhalla = StubValhalla()
    app.dependency_overrides[get_valhalla_client] = lambda: valhalla
    app.dependency_overrides[get_spatial_repository] = lambda: StubRepository(error=RuntimeError("db down"))
    app.dependency_overrides[get_app_settings] = lambda: Settings(dataset_version="test-v1")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post("/gis/site-analysis", json={"origin": {"lng": -122.4194, "lat": 37.7749}})
            working = await client.get(
                "/gis/isochrone",
                params={"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 15},
            )
    finally:
        app.dependency_overrides.clear()

    assert failed.json()["code"] == "SPATIAL_DATA_UNAVAILABLE"
    assert working.status_code == 200


@pytest.mark.asyncio
async def test_config_reports_fixed_product_options_and_component_state(tmp_path) -> None:
    population_path = tmp_path / "population.geojson"
    population_path.write_text("{}", encoding="utf-8")

    class HealthyRepository(StubRepository):
        async def check(self):
            return True

        async def baseline_ready(self):
            return False

    class HealthyHttp:
        async def get(self, *args, **kwargs):
            return httpx.Response(200)

    app.state.http_client = HealthyHttp()
    app.dependency_overrides[get_spatial_repository] = HealthyRepository
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        dataset_version="test-v1", population_density_path=population_path
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/gis/config")
    finally:
        app.dependency_overrides.clear()
        app.state._state.pop("http_client", None)

    body = response.json()
    assert body["modes"] == {"walking": "available", "driving": "available", "public_transit": "coming_soon"}
    assert body["durations"] == [15, 30, 45, 60]
    assert [item["default_weight"] for item in body["facilities"]] == [20] * 5
    assert body["components"] == {"valhalla": "ready", "postgis": "ready", "population_density": "ready", "baseline": "unavailable"}


@pytest.mark.asyncio
async def test_health_reports_degraded_components_without_internal_errors(tmp_path) -> None:
    class Repository:
        async def check(self):
            return False

        async def baseline_ready(self):
            return False

    class FailedHttp:
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("http://secret/C:/private")

    app.state.settings = Settings(population_density_path=tmp_path / "missing.geojson")
    app.state.spatial_repository = Repository()
    app.state.http_client = FailedHttp()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/gis/health")
    finally:
        for key in ("settings", "spatial_repository", "http_client"):
            app.state._state.pop(key, None)

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "service": "homeorbit-api",
        "components": {
            "api": "ready",
            "valhalla": "unavailable",
            "postgis": "unavailable",
            "population_density": "unavailable",
            "baseline": "unavailable",
        },
    }
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_repository_uses_shared_pool_with_finite_sql_and_lock_timeouts() -> None:
    class Cursor:
        statements = []
        row = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=None):
            self.statements.append((str(statement), params))
            if "WITH input_features" in str(statement):
                self.row = (
                    True,
                    1000.0,
                    1,
                    2,
                    3,
                    4.0,
                    5,
                    {"type": "FeatureCollection", "features": []},
                )

        def fetchone(self):
            return self.row

    class Connection:
        cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

    class Context:
        connection = Connection()

        def __enter__(self):
            return self.connection

        def __exit__(self, *args):
            return False

    class Pool:
        acquisitions = []
        context = Context()

        def connection(self, timeout):
            self.acquisitions.append(timeout)
            return self.context

    pool = Pool()
    repository = SpatialRepository(pool)
    await repository.analyze_catchment(POLYGON)
    await repository.analyze_catchment(POLYGON)

    assert pool.acquisitions == [3.0, 3.0]
    sql = "\n".join(statement for statement, _ in pool.context.connection.cursor_instance.statements)
    assert "statement_timeout" in sql
    assert "lock_timeout" in sql
    analyze_calls = [item for item in pool.context.connection.cursor_instance.statements if "WITH input_features" in item[0]]
    assert len(analyze_calls) == 2
    assert all(call[1] and len(call[1]) == 1 for call in analyze_calls)


@pytest.mark.asyncio
async def test_repository_pool_timeout_is_bounded_and_safely_mapped() -> None:
    class TimeoutPool:
        def connection(self, timeout):
            assert 0 < timeout <= 3
            raise PoolTimeout("postgresql://secret/C:/private")

    repository = SpatialRepository(TimeoutPool())
    with pytest.raises(GisError) as analyze_error:
        await repository.analyze_catchment(POLYGON)
    with pytest.raises(GisError) as baseline_error:
        await repository.baseline_samples()

    assert analyze_error.value.code == "SPATIAL_DATA_UNAVAILABLE"
    assert baseline_error.value.code == "SPATIAL_DATA_UNAVAILABLE"
    assert await repository.check() is False
    assert await repository.baseline_ready() is False


@pytest.mark.asyncio
async def test_repository_baseline_read_and_health_share_strict_snapshot_validator(monkeypatch) -> None:
    metrics = {category: 1.0 for category in CATEGORIES}
    tracts = {"001": 100, "002": 0}
    rows = {"001": (100, metrics), "002": (0, metrics)}
    metadata = {
        "baseline_ready": True,
        "record_count": 2.0,
        "zero_weight_count": 1,
        "mode": "pedestrian",
        "minutes": 15,
        "version": "facility-baseline-v1",
        "generated_at": "2026-09-01T04:47:30Z",
    }
    repository = SpatialRepository(object(), expected_baseline_count=2, expected_zero_weight=1)
    monkeypatch.setattr(repository, "_baseline_snapshot_sync", lambda: (tracts, rows, metadata))

    with pytest.raises(GisError):
        await repository.baseline_samples()
    assert await repository.baseline_ready() is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_postgis_analysis_matches_independent_aggregates() -> None:
    database_url = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        pytest.skip("HOMEORBIT_DATABASE_URL is required")
    with real_repository(database_url) as repository:
        first = await repository.analyze_catchment(POLYGON)
        second = await repository.analyze_catchment(POLYGON)

    geometry = json.dumps(POLYGON["features"][0]["geometry"], separators=(",", ":"))
    with psycopg.connect(database_url, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "WITH c AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) geom) "
                "SELECT COALESCE(SUM(CASE WHEN t.population=0 THEN 0 ELSE "
                "t.population * ST_Area(ST_Intersection(c.geom,t.geom)::geography) / "
                "NULLIF(ST_Area(t.geom::geography),0) END),0) FROM c JOIN tracts t ON ST_Intersects(c.geom,t.geom)",
                (geometry,),
            )
            expected_population = float(cursor.fetchone()[0])
            cursor.execute(
                "WITH c AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) geom) "
                "SELECT category,count(*) FROM c JOIN facilities f ON f.category<>'parks' "
                "AND ST_Covers(c.geom,f.analysis_point) GROUP BY category",
                (geometry,),
            )
            expected_counts = dict(cursor.fetchall())
            cursor.execute(
                "WITH c AS (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) geom), "
                "p AS (SELECT ST_Intersection(c.geom,f.geom) geom FROM c JOIN facilities f "
                "ON f.category='parks' AND ST_Intersects(c.geom,f.geom)) "
                "SELECT COALESCE(ST_Area(ST_UnaryUnion(ST_Collect(geom))::geography)/10000,0) FROM p",
                (geometry,),
            )
            expected_parks = float(cursor.fetchone()[0])

    assert first == second
    assert first.population == pytest.approx(expected_population)
    assert first.counts == {category: expected_counts.get(category, 0) for category in CATEGORIES if category != "parks"}
    assert first.park_area_hectares == pytest.approx(expected_parks)
    assert set(first.raw_metrics) == set(CATEGORIES)
    assert first.facilities["type"] == "FeatureCollection"
    assert [
        (feature["properties"]["category"], feature["properties"]["osm_key"])
        for feature in first.facilities["features"]
    ] == sorted(
        (feature["properties"]["category"], feature["properties"]["osm_key"])
        for feature in first.facilities["features"]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_temp_fixture_proves_multifeature_union_counts_and_overlapping_park_deduplication() -> None:
    database_url = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        pytest.skip("HOMEORBIT_DATABASE_URL is required")
    pool = ConnectionPool(
        database_url,
        open=False,
        min_size=0,
        max_size=1,
        kwargs={"connect_timeout": 3},
    )
    pool.open()
    try:
        with pool.connection(timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMP TABLE tracts (geoid text PRIMARY KEY, population integer, geom geometry(MultiPolygon,4326));"
                    "CREATE TEMP TABLE facilities (osm_key text PRIMARY KEY, category text, name text, "
                    "geom geometry(Geometry,4326), analysis_point geometry(Point,4326))"
                )
                cursor.execute(
                    "INSERT INTO tracts VALUES ('fixture',1000,ST_Multi(ST_GeomFromText(%s,4326)))",
                    ("POLYGON((-122.42 37.77,-122.40 37.77,-122.40 37.78,-122.42 37.78,-122.42 37.77))",),
                )
                point_rows = [
                    ("education-1", "education", -122.419, 37.775),
                    ("healthcare-1", "healthcare", -122.415, 37.775),
                    ("shopping-1", "daily_shopping", -122.409, 37.775),
                    ("transport-1", "public_transport", -122.401, 37.775),
                ]
                cursor.executemany(
                    "INSERT INTO facilities VALUES (%s,%s,NULL,ST_SetSRID(ST_Point(%s,%s),4326),ST_SetSRID(ST_Point(%s,%s),4326))",
                    [(key, category, lng, lat, lng, lat) for key, category, lng, lat in point_rows],
                )
                park_rows = [
                    ("park-a", "POLYGON((-122.42 37.77,-122.408 37.77,-122.408 37.775,-122.42 37.775,-122.42 37.77))"),
                    ("park-b", "POLYGON((-122.414 37.77,-122.40 37.77,-122.40 37.775,-122.414 37.775,-122.414 37.77))"),
                ]
                cursor.executemany(
                    "INSERT INTO facilities VALUES (%s,'parks',NULL,ST_GeomFromText(%s,4326),ST_PointOnSurface(ST_GeomFromText(%s,4326)))",
                    [(key, wkt, wkt) for key, wkt in park_rows],
                )
                cursor.execute(
                    "SELECT ST_Area(ST_GeomFromText(%s,4326)::geography)/10000",
                    ("POLYGON((-122.42 37.77,-122.40 37.77,-122.40 37.775,-122.42 37.775,-122.42 37.77))",),
                )
                expected_park_hectares = float(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT SUM(ST_Area(geom::geography))/10000 FROM facilities WHERE category='parks'"
                )
                overlapping_sum_hectares = float(cursor.fetchone()[0])

        catchment = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[-122.42,37.77],[-122.41,37.77],[-122.41,37.78],[-122.42,37.78],[-122.42,37.77]]]}},
                {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPolygon", "coordinates": [[[[-122.41,37.77],[-122.40,37.77],[-122.40,37.78],[-122.41,37.78],[-122.41,37.77]]]]}},
            ],
        }
        result = await SpatialRepository(pool).analyze_catchment(catchment)
    finally:
        pool.close()

    assert result.population == pytest.approx(1000.0, rel=1e-9)
    assert result.counts == {"education": 1, "healthcare": 1, "daily_shopping": 1, "public_transport": 1}
    assert result.park_area_hectares == pytest.approx(expected_park_hectares, rel=2e-6)
    assert result.park_area_hectares < overlapping_sum_hectares
    assert len(result.facilities["features"]) == 6


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_postgis_tiny_catchment_triggers_low_population_guard() -> None:
    database_url = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        pytest.skip("HOMEORBIT_DATABASE_URL is required")
    lng, lat, delta = -122.4194, 37.7749, 0.000001
    tiny = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"contour": 15},
            "geometry": {"type": "Polygon", "coordinates": [[
                [lng-delta, lat-delta], [lng+delta, lat-delta], [lng+delta, lat+delta],
                [lng-delta, lat+delta], [lng-delta, lat-delta],
            ]]},
        }],
    }

    with real_repository(database_url) as repository:
        stats = await repository.analyze_catchment(tiny)
    result = build_scores(stats.population, stats.raw_metrics, {})

    assert stats.population < 100
    assert result.status == "insufficient_population"
    assert all(score is None for score in result.scores.values())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_postgis_rejects_empty_polygon_safely() -> None:
    database_url = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        pytest.skip("HOMEORBIT_DATABASE_URL is required")
    empty = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": []}}],
    }

    with real_repository(database_url) as repository, pytest.raises(GisError) as captured:
        await repository.analyze_catchment(empty)

    assert captured.value.code == "SPATIAL_DATA_UNAVAILABLE"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Polygon", "coordinates": [[]]},
        {"type": "Polygon", "coordinates": [[[-122.42, 37.77], [-122.41, 37.77], [-122.42, 37.77]]]},
        {"type": "Polygon", "coordinates": [[[-122.42, 37.77], [-122.41, 37.77], [-122.41, 37.78], [-122.42, 37.78]]]},
        {"type": "Polygon", "coordinates": [[[-122.42, 37.77], [-122.41, 37.78], [-122.42, 37.78], [-122.41, 37.77], [-122.42, 37.77]]]},
        {"type": "MultiPolygon", "coordinates": []},
        {"type": "MultiPolygon", "coordinates": [[]]},
        {"type": "MultiPolygon", "coordinates": [[[]]]},
    ],
)
async def test_real_postgis_rejects_nested_empty_or_invalid_polygon_safely(geometry) -> None:
    database_url = os.environ.get("HOMEORBIT_DATABASE_URL")
    if not database_url:
        pytest.skip("HOMEORBIT_DATABASE_URL is required")
    collection = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry}]}
    with real_repository(database_url) as repository, pytest.raises(GisError) as captured:
        await repository.analyze_catchment(collection)
    assert captured.value.code == "SPATIAL_DATA_UNAVAILABLE"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unreachable_postgis_does_not_block_lifespan_or_isochrone(monkeypatch) -> None:
    monkeypatch.setenv("HOMEORBIT_DATABASE_URL", "postgresql://invalid:invalid@127.0.0.1:1/invalid")
    monkeypatch.setenv("HOMEORBIT_VALHALLA_URL", "http://127.0.0.1:8002")

    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/gis/isochrone",
                params={"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 15},
            )

    assert response.status_code == 200
    assert response.json()["geometry"]["features"]
