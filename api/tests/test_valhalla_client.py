import json

import httpx
import pytest

from app.config import Settings
from app.gis.errors import GisError
from app.gis.schemas import Origin
from app.gis.valhalla import ValhallaClient


POLYGON_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"contour": 15},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-122.42, 37.77], [-122.41, 37.77], [-122.42, 37.78], [-122.42, 37.77]]],
            },
        }
    ],
}


@pytest.mark.asyncio
async def test_client_maps_driving_to_auto(httpx_mock) -> None:
    httpx_mock.add_response(json=POLYGON_COLLECTION)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        result = await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "driving", 15)

    request = httpx_mock.get_request()
    payload = json.loads(request.url.params["json"])
    assert request.method == "POST"
    assert request.url.path == "/isochrone"
    assert payload == {
        "locations": [{"lon": -122.4194, "lat": 37.7749}],
        "costing": "auto",
        "contours": [{"time": 15}],
        "polygons": True,
        "show_locations": True,
    }
    assert result == POLYGON_COLLECTION


@pytest.mark.asyncio
async def test_client_maps_timeout_to_safe_dependency_error(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("secret upstream URL"))
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"
    assert "secret" not in captured.value.message


@pytest.mark.asyncio
async def test_client_maps_walking_to_pedestrian_and_uses_configured_timeout(httpx_mock) -> None:
    collection = json.loads(json.dumps(POLYGON_COLLECTION))
    collection["features"][0]["properties"]["contour"] = 30
    httpx_mock.add_response(json=collection)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(
            http_client,
            Settings(valhalla_url="http://valhalla.test/", valhalla_timeout_seconds=1.25),
        )
        await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 30)

    request = httpx_mock.get_request()
    payload = json.loads(request.url.params["json"])
    assert payload["costing"] == "pedestrian"
    assert payload["contours"] == [{"time": 30}]
    assert request.extensions["timeout"] == {
        "connect": 1.25,
        "read": 1.25,
        "write": 1.25,
        "pool": 1.25,
    }


@pytest.mark.asyncio
async def test_client_maps_connection_failure_to_safe_dependency_error(httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("C:/internal/valhalla"))
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.as_dict() == {
        "code": "VALHALLA_UNAVAILABLE",
        "message": "路网分析服务暂不可用",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (503, '{"error":"http://private-host/C:/secret"}'),
        (200, "not json"),
    ],
)
async def test_client_maps_bad_http_response_to_safe_dependency_error(
    httpx_mock,
    status_code: int,
    body: str,
) -> None:
    httpx_mock.add_response(status_code=status_code, text=body)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"
    assert captured.value.message == "路网分析服务暂不可用"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"type": "FeatureCollection", "features": []},
        {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
        },
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}},
    ],
)
async def test_client_rejects_response_without_polygon_geometry(httpx_mock, payload) -> None:
    httpx_mock.add_response(json=payload)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_client_accepts_multipolygon_geometry(httpx_mock) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"contour": 15},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [
                            [
                                [-122.42, 37.77],
                                [-122.41, 37.77],
                                [-122.42, 37.78],
                                [-122.42, 37.77],
                            ]
                        ]
                    ],
                },
            }
        ],
    }
    httpx_mock.add_response(json=collection)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        assert await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15) == collection


@pytest.mark.asyncio
async def test_client_only_returns_matching_contour_features(httpx_mock) -> None:
    contour = POLYGON_COLLECTION["features"][0]
    collection = {
        "type": "FeatureCollection",
        "features": [
            contour,
            {
                "type": "Feature",
                "properties": {"type": "snapped"},
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[-122.4194, 37.7749]],
                },
            },
            {
                "type": "Feature",
                "properties": {"type": "input"},
                "geometry": {"type": "Point", "coordinates": [-122.4194, 37.7749]},
            },
        ],
    }
    httpx_mock.add_response(json=collection)
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        result = await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert result == {"type": "FeatureCollection", "features": [contour]}


@pytest.mark.asyncio
async def test_client_rejects_empty_polygon_coordinates(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"contour": 15},
                    "geometry": {"type": "Polygon", "coordinates": []},
                }
            ],
        }
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"
    assert captured.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coordinates",
    [
        [[]],
        [[[-122.42, 37.77], [-122.41, 37.77], [-122.42, 37.77]]],
        [[[-122.42, 37.77], [-122.41, 37.77], [-122.42, 37.78], [-122.41, 37.78]]],
        [[[-122.42, 37.77], [-122.41, 37.77], ["not-finite", 37.78], [-122.42, 37.77]]],
    ],
)
async def test_client_rejects_malformed_polygon_rings(httpx_mock, coordinates) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"contour": 15},
                    "geometry": {"type": "Polygon", "coordinates": coordinates},
                }
            ],
        }
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coordinates",
    [
        [],
        [[]],
        [[[]]],
        [[[[-122.42, 37.77], [-122.41, 37.77], [-122.42, 37.77]]]],
    ],
)
async def test_client_rejects_malformed_multipolygon_coordinates(httpx_mock, coordinates) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"contour": 15},
                    "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
                }
            ],
        }
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_client_rejects_malformed_feature_even_with_valid_contour(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [POLYGON_COLLECTION["features"][0], None],
        }
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("properties", [{}, {"contour": 30}, None])
async def test_client_rejects_missing_or_mismatched_contour(httpx_mock, properties) -> None:
    httpx_mock.add_response(
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": POLYGON_COLLECTION["features"][0]["geometry"],
                }
            ],
        }
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_client_rejects_non_finite_coordinate_from_json(httpx_mock) -> None:
    httpx_mock.add_response(
        content=(
            '{"type":"FeatureCollection","features":[{"type":"Feature",'
            '"properties":{"contour":15},"geometry":{"type":"Polygon",'
            '"coordinates":[[[-122.42,37.77],[-122.41,37.77],'
            '[1e400,37.78],[-122.42,37.77]]]}}]}'
        ),
        headers={"content-type": "application/json"},
    )
    async with httpx.AsyncClient() as http_client:
        client = ValhallaClient(http_client, Settings(valhalla_url="http://valhalla.test"))
        with pytest.raises(GisError) as captured:
            await client.isochrone(Origin(lng=-122.4194, lat=37.7749), "walking", 15)

    assert captured.value.code == "VALHALLA_UNAVAILABLE"
