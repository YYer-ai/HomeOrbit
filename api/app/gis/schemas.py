from __future__ import annotations

import math
from typing import Annotated, Any, Literal, TypeAlias, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TravelMode: TypeAlias = Literal["walking", "driving"]
TravelDirection: TypeAlias = Literal["outbound", "inbound"]
FacilityCategory: TypeAlias = Literal[
    "education",
    "healthcare",
    "daily_shopping",
    "parks",
    "public_transport",
]
Duration: TypeAlias = Literal[15, 30, 45, 60]
ScoreStatus: TypeAlias = Literal["ok", "insufficient_population"]
Score: TypeAlias = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False, strict=True)] | None

ALLOWED_DURATIONS = frozenset({15, 30, 45, 60})
BAY_AREA_BBOX = (-123.3, 37.2, -121.7, 38.0)

GeoJSON: TypeAlias = dict[str, Any]


class GisErrorResponse(BaseModel):
    code: str
    message: str


class Origin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lng: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)

    @field_validator("lng", "lat")
    @classmethod
    def reject_non_finite_coordinates(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("坐标必须是有限数值")
        return value

    @model_validator(mode="after")
    def require_bay_area_coordinate(self) -> "Origin":
        min_lng, min_lat, max_lng, max_lat = BAY_AREA_BBOX
        if not (min_lng <= self.lng <= max_lng and min_lat <= self.lat <= max_lat):
            raise ValueError("坐标必须位于湾区覆盖范围内")
        return self


class IsochroneQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Origin
    mode: TravelMode
    minutes: Duration
    direction: TravelDirection = "outbound"


class SiteAnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Origin


class IsochroneResponse(BaseModel):
    """真实路网等时圈响应的稳定字段。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    origin: Origin
    mode: TravelMode
    minutes: Duration
    direction: TravelDirection = "outbound"
    geometry: GeoJSON = Field(validation_alias="geojson")
    data_version: str
    traffic_assumption: Literal["static_network_cost"] = "static_network_cost"


class FacilityMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=0, allow_inf_nan=False, strict=True)
    unit: Literal["count_per_1000_people", "hectares_per_1000_people"]
    score: Score
    raw_count: int | None = Field(default=None, ge=0, strict=True)
    raw_area_hectares: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)


class SiteAnalysisResponse(BaseModel):
    """当前选点的固定 15 分钟步行设施分析响应。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    origin: Origin
    catchment: GeoJSON
    population: float = Field(ge=0, allow_inf_nan=False)
    population_method: str
    metrics: dict[FacilityCategory, FacilityMetric]
    facilities: GeoJSON
    scores: dict[FacilityCategory, Score]
    score_status: ScoreStatus = "ok"
    data_version: str
    acs_year: int
    limitations: list[str] = Field(default_factory=list)
    scoring_method: str

    @model_validator(mode="after")
    def validate_score_contract(self) -> "SiteAnalysisResponse":
        expected_categories = set(get_args(FacilityCategory))
        if set(self.metrics) != expected_categories or set(self.scores) != expected_categories:
            raise ValueError("metrics 和 scores 必须恰好包含五类设施")

        for category, metric in self.metrics.items():
            if category == "parks":
                if (
                    metric.unit != "hectares_per_1000_people"
                    or metric.raw_area_hectares is None
                    or metric.raw_count is not None
                ):
                    raise ValueError("parks 指标字段或单位无效")
            elif (
                metric.unit != "count_per_1000_people"
                or metric.raw_count is None
                or metric.raw_area_hectares is not None
            ):
                raise ValueError("点设施指标字段或单位无效")
            if metric.score != self.scores[category]:
                raise ValueError("metrics.score 必须与 scores 一致")

        has_missing_score = any(score is None for score in self.scores.values())
        if self.score_status == "ok" and has_missing_score:
            raise ValueError("ok 状态下五类分数必须都是数值")
        if self.score_status == "insufficient_population" and not has_missing_score:
            raise ValueError("样本不足状态下五类分数必须都是 None")
        if self.score_status == "insufficient_population" and any(
            score is not None for score in self.scores.values()
        ):
            raise ValueError("样本不足状态下五类分数必须都是 None")
        return self
