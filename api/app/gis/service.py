from __future__ import annotations

from app.config import Settings
from app.gis.errors import GisError
from app.gis.repository import CATEGORIES, SpatialRepository
from app.gis.scoring import build_scores
from app.gis.schemas import IsochroneQuery, IsochroneResponse, SiteAnalysisQuery, SiteAnalysisResponse
from app.gis.valhalla import ValhallaClient


async def build_isochrone_response(
    query: IsochroneQuery,
    client: ValhallaClient,
    settings: Settings,
) -> IsochroneResponse:
    geometry = await client.isochrone(query.origin, query.mode, query.minutes, preserve_holes=True)
    return IsochroneResponse(
        origin=query.origin,
        mode=query.mode,
        minutes=query.minutes,
        geometry=geometry,
        data_version=settings.dataset_version,
    )


async def build_site_analysis_response(
    query: SiteAnalysisQuery,
    client: ValhallaClient,
    repository: SpatialRepository,
    settings: Settings,
) -> SiteAnalysisResponse:
    catchment = await client.isochrone(query.origin, "walking", 15)
    try:
        stats = await repository.analyze_catchment(catchment)
        baselines = await repository.baseline_samples() if stats.population >= 100 else {}
    except GisError:
        raise
    except Exception as exc:
        raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503) from exc
    try:
        score_result = build_scores(stats.population, stats.raw_metrics, baselines)
    except (KeyError, TypeError, ValueError) as exc:
        raise GisError("SPATIAL_DATA_UNAVAILABLE", status_code=503) from exc
    metrics = {}
    for category in CATEGORIES:
        metric = {
            "value": stats.raw_metrics[category],
            "unit": "hectares_per_1000_people" if category == "parks" else "count_per_1000_people",
            "score": score_result.scores[category],
        }
        if category == "parks":
            metric["raw_area_hectares"] = stats.park_area_hectares
        else:
            metric["raw_count"] = stats.counts[category]
        metrics[category] = metric
    return SiteAnalysisResponse(
        origin=query.origin,
        catchment=catchment,
        population=stats.population,
        population_method="tract_population_weighted_by_intersection_area",
        metrics=metrics,
        facilities=stats.facilities,
        scores=score_result.scores,
        score_status=score_result.status,
        data_version=settings.dataset_version,
        acs_year=settings.acs_year,
        limitations=[
            "人口按 tract 内均匀分布假设估算",
            "设施数据完整度受 OSM 数据源限制",
            "相对分数不是法规或公共服务达标结论",
        ],
        scoring_method="bay_area_population_weighted_percentile",
    )
