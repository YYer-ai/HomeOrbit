from __future__ import annotations

import json
import math
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
        *,
        preserve_holes: bool = False,
        reverse: bool = False,
        require_nearby_road: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "locations": [{"lon": origin.lng, "lat": origin.lat}],
            "costing": "auto" if mode == "driving" else "pedestrian",
            "contours": [{"time": minutes}],
            "polygons": True,
            "show_locations": True,
        }
        if preserve_holes:
            # 默认 denoise=1 会移除内部空洞；设施评分仍需匹配既有基准口径。
            payload["denoise"] = 0
            # 正反向轮廓均出现过简化自交，通勤图保留原始轮廓。
            payload["generalize"] = 0
        if reverse:
            payload["reverse"] = True
            payload["generalize"] = 0
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
        snapped = []
        for feature in features:
            if (
                not isinstance(feature, dict)
                or feature.get("type") != "Feature"
                or not isinstance(feature.get("geometry"), dict)
                or not isinstance(feature["geometry"].get("type"), str)
                or not isinstance(feature.get("properties"), dict)
            ):
                raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
            if feature["properties"].get("type") == "snapped":
                coordinates = feature["geometry"].get("coordinates")
                if feature["geometry"]["type"] != "MultiPoint" or not isinstance(coordinates, list):
                    raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
                snapped.extend(coordinates)
            if feature["properties"].get("contour") != minutes:
                continue
            if not is_polygonal_geometry(feature["geometry"]):
                raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
            contours.append(feature)
        if not contours:
            raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
        if require_nearby_road:
            if not snapped:
                raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
            for point in snapped:
                if (
                    not isinstance(point, list) or len(point) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
                    or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90
                ):
                    raise GisError("VALHALLA_UNAVAILABLE", status_code=503)
                lng, lat = map(math.radians, point)
                origin_lng, origin_lat = map(math.radians, (origin.lng, origin.lat))
                haversine = (
                    math.sin((lat - origin_lat) / 2) ** 2
                    + math.cos(lat) * math.cos(origin_lat) * math.sin((lng - origin_lng) / 2) ** 2
                )
                distance_m = 2 * 6371008.8 * math.asin(math.sqrt(min(1, haversine)))
                if distance_m > 100:
                    raise GisError("POINT_TOO_FAR_FROM_ROAD", status_code=422)
        return {"type": "FeatureCollection", "features": contours}
