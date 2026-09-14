from __future__ import annotations

from typing import cast

import httpx
import json
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.gis.errors import GisError
from app.gis.repository import CATEGORIES, SpatialRepository
from app.gis.schemas import (
    ALLOWED_DURATIONS,
    GisErrorResponse,
    IsochroneQuery,
    IsochroneResponse,
    Origin,
    SiteAnalysisQuery,
    SiteAnalysisResponse,
    TravelMode,
    TravelDirection,
)
from app.gis.service import build_isochrone_response, build_site_analysis_response
from app.gis.valhalla import ValhallaClient


router = APIRouter(prefix="/gis")


def get_valhalla_client(request: Request) -> ValhallaClient:
    return request.app.state.valhalla_client


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", get_settings())


def get_spatial_repository(request: Request) -> SpatialRepository:
    return request.app.state.spatial_repository


def _parse_query(lng: str | None, lat: str | None, mode: str | None, minutes: str | None, direction: str = "outbound") -> IsochroneQuery:
    try:
        origin = Origin(lng=float(lng), lat=float(lat))
    except (TypeError, ValueError, ValidationError) as exc:
        raise GisError("POINT_OUTSIDE_COVERAGE", status_code=400) from exc

    if mode not in {"walking", "driving"}:
        raise GisError("INVALID_MODE", status_code=400)
    try:
        parsed_minutes = int(minutes)
    except (TypeError, ValueError) as exc:
        raise GisError("INVALID_DURATION", status_code=400) from exc
    if parsed_minutes not in ALLOWED_DURATIONS:
        raise GisError("INVALID_DURATION", status_code=400)
    if direction not in {"outbound", "inbound"}:
        raise GisError("INVALID_DIRECTION", status_code=400)

    return IsochroneQuery(
        origin=origin,
        mode=cast(TravelMode, mode),
        minutes=parsed_minutes,
        direction=cast(TravelDirection, direction),
    )


@router.get("/isochrone", response_model=IsochroneResponse)
async def isochrone(
    lng: str | None = None,
    lat: str | None = None,
    mode: str | None = None,
    minutes: str | None = None,
    direction: str = "outbound",
    client: ValhallaClient = Depends(get_valhalla_client),
    settings: Settings = Depends(get_app_settings),
) -> IsochroneResponse:
    query = _parse_query(lng, lat, mode, minutes, direction)
    return await build_isochrone_response(query, client, settings)


@router.post(
    "/site-analysis",
    response_model=SiteAnalysisResponse,
    responses={400: {"model": GisErrorResponse}, 503: {"model": GisErrorResponse}},
)
async def site_analysis(
    query: SiteAnalysisQuery,
    client: ValhallaClient = Depends(get_valhalla_client),
    repository: SpatialRepository = Depends(get_spatial_repository),
    settings: Settings = Depends(get_app_settings),
) -> SiteAnalysisResponse:
    return await build_site_analysis_response(query, client, repository, settings)


@router.get("/config")
async def config(
    request: Request,
    repository: SpatialRepository = Depends(get_spatial_repository),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    return {
        "modes": {"walking": "available", "driving": "available", "public_transit": "coming_soon"},
        "durations": [15, 30, 45, 60],
        "facilities": [
            {"id": category, "name": name, "default_weight": 20}
            for category, name in zip(CATEGORIES, ("教育", "医疗", "日常购物", "公园绿地", "公共交通站点"))
        ],
        "data": {
            "coverage": "San Francisco Bay Area",
            "acs_year": settings.acs_year,
            "osm_version": settings.osm_version,
            "version": settings.dataset_version,
            "generated_at": _dataset_generated_at(settings),
        },
        "components": {
            "valhalla": "ready" if await _valhalla_ready(request, settings) else "unavailable",
            "postgis": "ready" if await repository.check() else "unavailable",
            "population_density": "ready" if settings.population_density_path.is_file() else "unavailable",
            "baseline": "ready" if await repository.baseline_ready() else "unavailable",
        },
    }


async def _valhalla_ready(request: Request, settings: Settings) -> bool:
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        return False
    try:
        response = await client.get(
            f"{settings.valhalla_url.rstrip('/')}/status",
            timeout=min(settings.valhalla_timeout_seconds, 1.0),
        )
        return response.status_code == 200
    except Exception:
        return False


def _dataset_generated_at(settings: Settings) -> str | None:
    try:
        metadata = json.loads(
            (settings.data_root / "processed" / "spatial-data-metadata.json").read_text(encoding="utf-8")
        )
        value = metadata.get("generated_at")
        return value if isinstance(value, str) else None
    except (OSError, ValueError, TypeError):
        return None
