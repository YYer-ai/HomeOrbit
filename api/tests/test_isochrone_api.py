import math
import json

import httpx
import pytest
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

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

    async def isochrone(self, origin, mode, minutes, *, preserve_holes=False, reverse=False, require_nearby_road=False):
        assert require_nearby_road
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
        "direction": "outbound",
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
@pytest.mark.parametrize("direction,reverse", [("outbound", False), ("inbound", True)])
@pytest.mark.parametrize("mode", ["walking", "driving"])
async def test_direction_reaches_routing_engine_and_response(direction, reverse, mode) -> None:
    captured = []
    async def transport(request):
        captured.append(json.loads(request.url.params["json"]))
        return httpx.Response(200, json={**POLYGON_COLLECTION, "features": [
            *POLYGON_COLLECTION["features"],
            {"type": "Feature", "properties": {"type": "snapped", "location_index": 0},
             "geometry": {"type": "MultiPoint", "coordinates": [[-122.4194, 37.7749]]}},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as upstream:
        response = await request_with_stub(
            {"lng": -122.4194, "lat": 37.7749, "mode": mode, "minutes": 15, "direction": direction},
            ValhallaClient(upstream, Settings()),
        )
    assert response.status_code == 200
    assert response.json()["direction"] == direction
    assert captured[0].get("reverse", False) is reverse
    assert captured[0]["generalize"] == 0
    assert captured[0]["denoise"] == 0
    assert captured[0]["costing"] == ("auto" if mode == "driving" else "pedestrian")


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["sideways", "", "INBOUND"])
async def test_invalid_direction_is_rejected_before_routing(direction) -> None:
    stub = StubValhallaClient()
    response = await request_with_stub(
        {"lng": -122.4194, "lat": 37.7749, "mode": "walking", "minutes": 15, "direction": direction}, stub,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DIRECTION"
    assert not stub.calls


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


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["outbound", "inbound"])
async def test_real_mountain_click_cannot_silently_start_448m_away(direction) -> None:
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            far = await client.get("/gis/isochrone", params={
                "lng": -122.42760492773664, "lat": 37.687316698297295,
                "mode": "walking", "minutes": 30, "direction": direction,
            })
            near = await client.get("/gis/isochrone", params={
                "lng": -122.430374, "lat": 37.683942,
                "mode": "walking", "minutes": 30, "direction": direction,
            })
    assert far.status_code == 422
    assert far.json()["code"] == "POINT_TOO_FAR_FROM_ROAD"
    assert "geometry" not in far.json()
    assert near.status_code == 200
    assert_valid_contour_collection(near.json()["geometry"], 30)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_commute_does_not_fill_holes_reported_by_the_routing_engine() -> None:
    """回归：默认 denoise 会填回路由引擎产生的内部空洞。"""
    settings = Settings()
    async with httpx.AsyncClient(trust_env=False) as upstream:
        reference = await upstream.post(
            f"{settings.valhalla_url}/isochrone",
            params={"json": json.dumps({
                "locations": [{"lon": -122.4194, "lat": 37.7749}],
                "costing": "auto", "contours": [{"time": 15}],
                "polygons": True, "denoise": 0, "generalize": 0,
            }, separators=(",", ":"))},
            timeout=10,
        )
    reference.raise_for_status()
    polygons = []
    for feature in reference.json()["features"]:
        geom = shape(feature["geometry"])
        polygons.extend(geom.geoms if geom.geom_type == "MultiPolygon" else [geom])
    holes = [Polygon(ring) for polygon in polygons for ring in polygon.interiors]
    assert holes, "本地路网测试点必须包含内部空洞，避免空断言"
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/gis/isochrone", params={
                "lng": -122.4194, "lat": 37.7749, "mode": "driving", "minutes": 15,
            })
    assert response.status_code == 200
    actual = unary_union([shape(f["geometry"]) for f in response.json()["geometry"]["features"]])
    for hole in sorted(holes, key=lambda g: g.area, reverse=True)[:5]:
        assert actual.intersection(hole).area < hole.area * 0.001


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["walking", "driving"])
@pytest.mark.parametrize("minutes", [15, 30, 45, 60])
async def test_real_inbound_matches_reverse_engine_for_every_mode_and_duration(mode, minutes) -> None:
    async with httpx.AsyncClient(trust_env=False) as upstream:
        reference = await upstream.post(f"{Settings().valhalla_url}/isochrone", json={
            "locations": [{"lon": -122.4194, "lat": 37.7749}],
            "costing": "auto" if mode == "driving" else "pedestrian",
            "contours": [{"time": minutes}], "polygons": True, "denoise": 0, "reverse": True, "generalize": 0,
        }, timeout=10)
    reference.raise_for_status()
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            responses = [await client.get("/gis/isochrone", params={
                "lng": -122.4194, "lat": 37.7749, "mode": mode, "minutes": minutes, "direction": direction,
            }) for direction in ("outbound", "inbound")]
    geometries = []
    for direction, response in zip(("outbound", "inbound"), responses):
        assert response.status_code == 200
        assert response.json()["direction"] == direction
        collection = response.json()["geometry"]
        assert_valid_contour_collection(collection, minutes)
        geometry = unary_union([shape(f["geometry"]) for f in collection["features"]])
        assert geometry.is_valid and not geometry.is_empty
        geometries.append(geometry)
    expected = unary_union([shape(f["geometry"]) for f in reference.json()["features"]])
    assert geometries[1].equals(expected)
    if mode == "driving":
        assert geometries[0].symmetric_difference(geometries[1]).area > 0
