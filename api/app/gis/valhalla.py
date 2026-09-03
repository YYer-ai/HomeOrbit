from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.gis.errors import GisError
from app.gis.geojson import is_polygonal_geometry
from app.gis.schemas import Origin, TravelMode


class ValhallaClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._http_client = http_client
        self._settings = settings

    async def isochrone(
        self,
        origin: Origin,
        mode: TravelMode,
        minutes: int,
    ) -> dict[str, Any]:
        payload = {
            "locations": [{"lon": origin.lng, "lat": origin.lat}],
            "costing": "auto" if mode == "driving" else "pedestrian",
            "contours": [{"time": minutes}],
            "polygons": True,
            "show_locations": True,
        }
        try:
            response = await self._http_client.post(
                f"{self._settings.valhalla_url.rstrip('/')}/isochrone",
                params={"json": json.dumps(payload, separators=(",", ":"))},
                timeout=self._settings.valhalla_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503) from exc
        try:
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503) from exc

        if not isinstance(result, dict):
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
        features = result.get("features")
        if result.get("type") != "FeatureCollection" or not isinstance(features, list):
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
        contours = []
        for feature in features:
            if (
                not isinstance(feature, dict)
                or feature.get("type") != "Feature"
                or not isinstance(feature.get("geometry"), dict)
                or not isinstance(feature["geometry"].get("type"), str)
                or not isinstance(feature.get("properties"), dict)
            ):
                raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
            if feature["properties"].get("contour") != minutes:
                continue
            if not is_polygonal_geometry(feature["geometry"]):
                raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
            contours.append(feature)
        if not contours:
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
        return {"type": "FeatureCollection", "features": contours}
