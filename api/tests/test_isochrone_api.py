import math

import httpx
import pytest

from app.config import Settings
from app.gis.errors import GisError
from app.gis.router import get_app_settings, get_valhalla_client
from app.gis.valhalla import ValhallaClient
from app.main import app, lifespan

from test_valhalla_client import POLYGON_COLLECTION


class StubValhallaClient:
    def __init__(self, result=POLYGON_COLLECTION, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    async def isochrone(self, origin, mode, minutes):
        self.calls.append((origin, mode, minutes))
        if self.error:
            raise self.error
        return self.result


def assert_valid_contour_collection(collection: dict, minutes: int) -> None:
    assert collection["type"] == "FeatureCollection"
    assert collection["features"]
    for feature in collection["features"]:
        assert feature["type"] == "Feature"
        assert feature["properties"]["contour"] == minutes
        geometry = feature["geometry"]
        assert geometry["type"] in {"Polygon", "MultiPolygon"}
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        assert polygons
        for polygon in polygons:
            assert polygon
            for ring in polygon:
                assert len(ring) >= 4
                assert ring[0] == ring[-1]
                assert all(
                    len(position) >= 2
                    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in position)
                    for position in ring
                )


@pytest.mark.asyncio
async def test_isochrone_api_returns_real_geometry_and_stable_metadata() -> None:
    stub = StubValhallaClient()
    app.dependency_overrides[get_valhalla_client] = lambda: stub
    app.dependency_overrides[get_app_settings] = lambda: Settings(dataset_version="test-v1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/gis/isochrone",
                params={"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 15},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "origin": {"lng": -122.4194, "lat": 37.7749},
        "mode": "walking",
        "minutes": 15,
        "geometry": POLYGON_COLLECTION,
        "data_version": "test-v1",
        "traffic_assumption": "static_network_cost",
    }
    assert stub.calls[0][1:] == ("walking", 15)


async def request_with_stub(params: dict[str, object], stub: StubValhallaClient):
    app.dependency_overrides[get_valhalla_client] = lambda: stub
    app.dependency_overrides[get_app_settings] = lambda: Settings(dataset_version="test-v1")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/gis/isochrone", params=params)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected_code"),
    [
        (
            {"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 20},
            "INVALID_DURATION",
        ),
        (
            {"lng": -122.4194, "lat": 37.7749, "mode": "public_transit", "minutes": 15},
            "INVALID_MODE",
        ),
        (
            {"lng": -118.2437, "lat": 34.0522, "mode": "walking", "minutes": 15},
            "POINT_OUTSIDE_COVERAGE",
        ),
        (
            {"lng": "nan", "lat": 37.7749, "mode": "walking", "minutes": 15},
            "POINT_OUTSIDE_COVERAGE",
        ),
        (
            {"lat": 37.7749, "mode": "walking", "minutes": 15},
            "POINT_OUTSIDE_COVERAGE",
        ),
        (
            {"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": "fifteen"},
            "INVALID_DURATION",
        ),
    ],
)
async def test_isochrone_api_maps_business_validation_to_stable_error(params, expected_code) -> None:
    response = await request_with_stub(params, StubValhallaClient())

    assert response.status_code == 400
    assert response.json()["code"] == expected_code
    assert set(response.json()) == {"code", "message"}


@pytest.mark.asyncio
async def test_isochrone_api_maps_valhalla_timeout_without_leaking_details() -> None:
    async def timeout_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("http://private-host/C:/internal/path", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_transport)) as upstream:
        valhalla = ValhallaClient(upstream, Settings(valhalla_url="http://private-host"))
        response = await request_with_stub(
            {"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 15},
            valhalla,
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "VALHALLA_UNAVAILABLE",
        "message": "路网分析服务暂不可用",
    }
    assert "private-host" not in response.text
    assert "internal" not in response.text


@pytest.mark.asyncio
async def test_health_and_cors_are_preserved() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/gis/health",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.json() == {"status": "ok", "service": "homeorbit-api"}
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.asyncio
async def test_lifespan_creates_and_closes_shared_resources(monkeypatch) -> None:
    import importlib

    main_module = importlib.import_module("app.main")

    class FakeHttpClient:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakePool:
        opened = False
        closed = False

        def open(self) -> None:
            self.opened = True

        def close(self) -> None:
            self.closed = True

    fake_http = FakeHttpClient()
    fake_pool = FakePool()
    http_client_kwargs = {}

    def build_http_client(**kwargs):
        http_client_kwargs.update(kwargs)
        return fake_http

    monkeypatch.setattr(main_module.httpx, "AsyncClient", build_http_client)
    pool_kwargs = {}

    def build_pool(**kwargs):
        pool_kwargs.update(kwargs)
        return fake_pool

    monkeypatch.setattr(main_module, "ConnectionPool", build_pool)

    async with main_module.lifespan(app):
        assert http_client_kwargs == {"trust_env": False}
        assert app.state.http_client is fake_http
        assert app.state.db_pool is fake_pool
        assert app.state.spatial_repository._pool is fake_pool
        assert app.state.valhalla_client._http_client is fake_http
        assert fake_pool.opened is True
        assert fake_http.closed is False
        assert fake_pool.closed is False

    assert fake_http.closed is True
    assert fake_pool.closed is True
    assert pool_kwargs["min_size"] == 0
    assert pool_kwargs["max_size"] == 4
    assert pool_kwargs["kwargs"]["connect_timeout"] == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_valhalla_returns_distinct_walking_and_driving_polygons() -> None:
    async with lifespan(app):
        responses = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            for mode in ("walking", "driving"):
                responses.append(
                    await client.get(
                        "/gis/isochrone",
                        params={
                            "lng": -122.4194,
                            "lat": 37.7749,
                            "mode": mode,
                            "minutes": 15,
                        },
                    )
                )

    actual_responses = [(response.status_code, response.text[:200]) for response in responses]
    assert [response.status_code for response in responses] == [200, 200], actual_responses
    geometries = [response.json()["geometry"] for response in responses]
    for geometry in geometries:
        assert_valid_contour_collection(geometry, 15)
    assert geometries[0] != geometries[1]
